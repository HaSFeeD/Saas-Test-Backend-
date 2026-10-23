from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

import asyncpg

from .billing_products import find_product, get_products
from .config import Settings
from .context import Context
from .credits import apply_credits

log = logging.getLogger("billing")


async def grant_pack(conn: asyncpg.Connection, s: Settings, user_id: str, product_id: str, ref_id: str) -> None:
    product = find_product(s, product_id)
    if not product or product.kind != "pack":
        raise RuntimeError(f'unknown pack "{product_id}"')
    await apply_credits(conn, user_id, product.credits, f"purchase:{product.id}", ref_id)


async def grant_subscription_period(conn: asyncpg.Connection, s: Settings, user_id: str, product_id: str, ref_id: str) -> None:
    product = find_product(s, product_id)
    if not product or product.kind != "subscription":
        raise RuntimeError(f'unknown subscription "{product_id}"')
    await apply_credits(conn, user_id, product.credits, f"subscription:{product.id}", ref_id)


async def upsert_subscription(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    stripe_subscription_id: str,
    plan: str,
    status: str,
    current_period_end: Any,
    cancel_at_period_end: bool,
) -> None:
    await conn.execute(
        "INSERT INTO subscriptions "
        "(user_id, stripe_subscription_id, plan, status, current_period_end, cancel_at_period_end) "
        "VALUES ($1,$2,$3,$4,$5,$6) "
        "ON CONFLICT (stripe_subscription_id) DO UPDATE SET "
        "status=EXCLUDED.status, current_period_end=EXCLUDED.current_period_end, "
        "cancel_at_period_end=EXCLUDED.cancel_at_period_end, plan=EXCLUDED.plan, updated_at=now()",
        user_id, stripe_subscription_id, plan, status, current_period_end, cancel_at_period_end,
    )


async def has_active_subscription(conn: asyncpg.Connection, user_id: str) -> bool:
    found = await conn.fetchval(
        "SELECT 1 FROM subscriptions WHERE user_id=$1 AND status IN ('active','trialing') LIMIT 1", user_id
    )
    return found is not None


async def _user_id_by_customer(conn: asyncpg.Connection, customer: Any) -> str | None:
    customer_id = customer if isinstance(customer, str) else (customer or {}).get("id")
    if not customer_id:
        return None
    return await conn.fetchval("SELECT id::text FROM users WHERE stripe_customer_id = $1", customer_id)


async def handle_stripe_event(ctx: Context, event: dict) -> dict:
    """
    Apply a verified Stripe event idempotently: the event id is recorded in the same
    transaction as its effects, so a retry sees either both or neither. Raising rolls back,
    which makes Stripe retry later.
    """
    async with ctx.pool.acquire() as conn, conn.transaction():
        inserted = await conn.fetchval(
            "INSERT INTO webhook_events (id, type) VALUES ($1,$2) ON CONFLICT DO NOTHING RETURNING id",
            event["id"], event["type"],
        )
        if inserted is None:
            return {"duplicate": True}

        obj = event["data"]["object"]
        etype = event["type"]
        if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            await _on_checkout_completed(conn, ctx.settings, obj)
        elif etype == "invoice.paid":
            await _on_invoice_paid(conn, ctx.settings, obj)
        elif etype in (
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        ):
            await _on_subscription_changed(conn, ctx.settings, obj)
        return {"duplicate": False}


async def _on_checkout_completed(conn: asyncpg.Connection, s: Settings, obj: dict) -> None:
    if obj.get("mode") != "payment" or obj.get("payment_status") != "paid":
        return  # subscriptions are fulfilled from invoice.paid
    meta = obj.get("metadata") or {}
    user_id = meta.get("user_id") or await _user_id_by_customer(conn, obj.get("customer"))
    product_id = meta.get("product_id")
    if not user_id or not product_id:
        log.warning("checkout session %s without user/product metadata, ignored", obj.get("id"))
        return
    await grant_pack(conn, s, user_id, product_id, obj["id"])


async def _on_invoice_paid(conn: asyncpg.Connection, s: Settings, obj: dict) -> None:
    if not obj.get("amount_paid") or not obj.get("id"):
        return
    user_id = await _user_id_by_customer(conn, obj.get("customer"))
    if not user_id:
        log.warning("invoice.paid for unknown customer, ignored")
        return
    price_ids = {
        (line.get("pricing") or {}).get("price_details", {}).get("price")
        or (line.get("price") or {}).get("id")
        for line in obj.get("lines", {}).get("data", [])
    }
    product = next(
        (p for p in get_products(s) if p.kind == "subscription" and p.stripe_price_id in price_ids), None
    )
    if not product:
        log.warning("invoice.paid for a non-subscription price, ignored")
        return
    await grant_subscription_period(conn, s, user_id, product.id, obj["id"])


async def _on_subscription_changed(conn: asyncpg.Connection, s: Settings, obj: dict) -> None:
    meta = obj.get("metadata") or {}
    user_id = meta.get("user_id") or await _user_id_by_customer(conn, obj.get("customer"))
    if not user_id:
        log.warning("subscription %s for unknown user, ignored", obj.get("id"))
        return
    items = obj.get("items", {}).get("data", [])
    price_id = items[0]["price"]["id"] if items else None
    plan = next(
        (p.id for p in get_products(s) if p.kind == "subscription" and p.stripe_price_id == price_id),
        meta.get("product_id", "unknown"),
    )
    period_end = items[0].get("current_period_end") if items else None
    from datetime import datetime

    await upsert_subscription(
        conn,
        user_id=user_id,
        stripe_subscription_id=obj["id"],
        plan=plan,
        status=obj["status"],
        current_period_end=datetime.fromtimestamp(period_end, UTC) if period_end else None,
        cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
    )
