from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..billing_products import find_product, get_products
from ..billing_service import (
    grant_pack,
    grant_subscription_period,
    handle_stripe_event,
    has_active_subscription,
    upsert_subscription,
)
from ..context import Context
from ..deps import AuthUser, get_context, require_user
from ..errors import bad_request, conflict

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutBody(BaseModel):
    product_id: str = Field(alias="productId")


@router.get("/products")
async def products(ctx: Context = Depends(get_context)) -> dict:
    items = [
        {"id": p.id, "kind": p.kind, "title": p.title, "credits": p.credits, "priceCents": p.price_cents, "currency": p.currency}
        for p in get_products(ctx.settings)
    ]
    return {"provider": ctx.billing_provider_name, "items": items}


@router.post("/checkout")
async def checkout(
    body: CheckoutBody, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    product = find_product(ctx.settings, body.product_id)
    if not product:
        raise bad_request("unknown_product", "Unknown product")
    if product.kind == "subscription" and await has_active_subscription(ctx.pool, user.id):
        raise conflict("already_subscribed", "You already have an active subscription. Use the billing portal.")

    customer_id = await ctx.pool.fetchval("SELECT stripe_customer_id FROM users WHERE id=$1", user.id)

    if ctx.stripe is None:
        # Dev billing: no Stripe account; grant the purchase immediately.
        return await _dev_fulfil(ctx, user.id, product)

    if not product.stripe_price_id:
        raise bad_request("product_unavailable", f"Price for {product.id} is not configured")
    if not customer_id:
        customer_id = await ctx.stripe.create_customer(user.email, user.id)
        await ctx.pool.execute("UPDATE users SET stripe_customer_id=$2 WHERE id=$1", user.id, customer_id)

    url = await ctx.stripe.create_checkout_session(
        customer_id=customer_id,
        price_id=product.stripe_price_id,
        mode="payment" if product.kind == "pack" else "subscription",
        metadata={"user_id": user.id, "product_id": product.id},
        success_url=ctx.settings.checkout_success_url,
        cancel_url=ctx.settings.checkout_cancel_url,
    )
    return {"url": url}


async def _dev_fulfil(ctx: Context, user_id: str, product) -> dict:
    async with ctx.pool.acquire() as conn, conn.transaction():
            if product.kind == "pack":
                await grant_pack(conn, ctx.settings, user_id, product.id, f"dev_{uuid.uuid4()}")
            else:
                await upsert_subscription(
                    conn,
                    user_id=user_id,
                    stripe_subscription_id=f"dev_sub_{uuid.uuid4()}",
                    plan=product.id,
                    status="active",
                    current_period_end=datetime.now(UTC).replace(microsecond=0),
                    cancel_at_period_end=False,
                )
                await grant_subscription_period(conn, ctx.settings, user_id, product.id, f"dev_inv_{uuid.uuid4()}")
    return {"url": ctx.settings.checkout_success_url}


@router.post("/portal")
async def portal(ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)) -> dict:
    customer_id = await ctx.pool.fetchval("SELECT stripe_customer_id FROM users WHERE id=$1", user.id)
    if ctx.stripe is None:
        return {"url": ctx.settings.app_url}
    if not customer_id:
        raise bad_request("no_customer", "No billing account yet")
    url = await ctx.stripe.create_billing_portal_session(customer_id, ctx.settings.app_url)
    return {"url": url}


@router.get("/ledger")
async def ledger(
    limit: int = 20, offset: int = 0, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    limit = max(1, min(100, limit))
    rows = await ctx.pool.fetch(
        "SELECT id, delta, balance_after AS \"balanceAfter\", reason, created_at AS \"createdAt\" "
        "FROM credit_ledger WHERE user_id=$1 ORDER BY id DESC LIMIT $2 OFFSET $3",
        user.id, limit, max(0, offset),
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/webhook")
async def webhook(request: Request, ctx: Context = Depends(get_context)) -> dict:
    if ctx.stripe is None:
        raise bad_request("webhooks_disabled", "Webhooks are not available with the dev billing provider")
    raw = await request.body()
    event = ctx.stripe.verify_webhook(raw, request.headers.get("stripe-signature"))
    result = await handle_stripe_event(ctx, event)
    return {"received": True, **result}
