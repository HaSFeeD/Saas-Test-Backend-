from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ..context import Context
from ..deps import AuthUser, get_context, require_user
from ..errors import bad_request, unauthorized
from ..security import new_token, verify_password

router = APIRouter(prefix="/me", tags=["me"])


class DeleteBody(BaseModel):
    password: str


class WebhookBody(BaseModel):
    url: str = Field(pattern=r"^https://.+")


class TelegramLinkBody(BaseModel):
    chat_id: str = Field(alias="chatId", min_length=1, max_length=40)


@router.get("")
async def me(ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)) -> dict:
    from .searches import daily_search_limit, searches_used_today

    u = await ctx.pool.fetchrow(
        "SELECT id::text, email, role, credits, telegram_chat_id IS NOT NULL AS \"telegramLinked\", "
        "webhook_url AS \"webhookUrl\", created_at AS \"createdAt\" FROM users WHERE id=$1",
        user.id,
    )
    sub = await ctx.pool.fetchrow(
        "SELECT plan, status, current_period_end AS \"currentPeriodEnd\", "
        "cancel_at_period_end AS \"cancelAtPeriodEnd\" "
        "FROM subscriptions WHERE user_id=$1 ORDER BY updated_at DESC LIMIT 1",
        user.id,
    )
    limit = await daily_search_limit(ctx, user.id)
    used = await searches_used_today(ctx, user.id)
    return {
        **dict(u),
        "subscription": dict(sub) if sub else None,
        "searches": {"dailyLimit": limit, "usedToday": used, "remainingToday": max(0, limit - used)},
    }


@router.put("/webhook")
async def set_webhook(
    body: WebhookBody, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    """Register an HTTPS endpoint to receive signed events (e.g. search.completed). Returns the secret once."""
    secret = new_token()
    await ctx.pool.execute(
        "UPDATE users SET webhook_url=$2, webhook_secret=$3 WHERE id=$1", user.id, body.url, secret
    )
    return {
        "url": body.url,
        "secret": secret,
        "note": "Store this secret. Verify X-Webhook-Signature as t=<ts>,v1=HMAC_SHA256(secret, '<ts>.'+body).",
    }


@router.delete("/webhook", status_code=204)
async def delete_webhook(ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)) -> Response:
    await ctx.pool.execute("UPDATE users SET webhook_url=NULL, webhook_secret=NULL WHERE id=$1", user.id)
    return Response(status_code=204)


@router.post("/telegram/link")
async def link_telegram(
    body: TelegramLinkBody, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    """Connect a Telegram chat for notifications. Sends a confirmation message through the Bot API."""
    if ctx.telegram is None:
        raise bad_request("telegram_disabled", "Telegram notifications are not configured on this server")
    await ctx.pool.execute("UPDATE users SET telegram_chat_id=$2 WHERE id=$1", user.id, body.chat_id)
    await ctx.telegram.send_message(body.chat_id, "✅ This chat is now linked to your account.")
    return {"linked": True}


@router.get("/export")
async def export(ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)) -> dict:
    from datetime import datetime

    async def q(sql: str) -> list[dict]:
        return [dict(r) for r in await ctx.pool.fetch(sql, user.id)]

    u = await ctx.pool.fetchrow(
        "SELECT id::text, email, role, credits, tos_accepted_at, created_at FROM users WHERE id=$1", user.id
    )
    return {
        "exportedAt": datetime.now(UTC).isoformat(),
        "user": dict(u),
        "subscriptions": await q("SELECT plan, status, current_period_end, created_at FROM subscriptions WHERE user_id=$1"),
        "creditLedger": await q(
            "SELECT delta, balance_after, reason, created_at FROM credit_ledger WHERE user_id=$1 ORDER BY id"
        ),
        "uploads": await q("SELECT id::text, mime, size, created_at FROM uploads WHERE user_id=$1"),
        "searches": await q(
            "SELECT s.id::text, s.status, s.query, s.created_at, s.unlocked_at, "
            "COALESCE(json_agg(r.*) FILTER (WHERE r.id IS NOT NULL AND s.unlocked_at IS NOT NULL), '[]') AS results "
            "FROM searches s LEFT JOIN search_results r ON r.search_id = s.id "
            "WHERE s.user_id=$1 GROUP BY s.id ORDER BY s.created_at"
        ),
    }


@router.delete("", status_code=204)
async def delete_account(
    body: DeleteBody, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> Response:
    row = await ctx.pool.fetchrow("SELECT password_hash, stripe_customer_id FROM users WHERE id=$1", user.id)
    if not verify_password(body.password, row["password_hash"]):
        raise unauthorized("Wrong password")
    if row["stripe_customer_id"] and ctx.stripe is not None:
        await ctx.stripe.cancel_customer_subscriptions(row["stripe_customer_id"])

    async with ctx.pool.acquire() as conn, conn.transaction():
        keys = [
            r["storage_key"]
            for r in await conn.fetch("DELETE FROM uploads WHERE user_id=$1 RETURNING storage_key", user.id)
        ]
        await conn.execute("DELETE FROM searches WHERE user_id=$1", user.id)
        await conn.execute("DELETE FROM refresh_tokens WHERE user_id=$1", user.id)
        await conn.execute("DELETE FROM password_resets WHERE user_id=$1", user.id)
        # Financial ledger rows stay (legal retention) but point to an anonymized user.
        await conn.execute(
            "UPDATE users SET email = 'deleted+' || id || '@deleted.invalid', password_hash = '!', "
            "telegram_chat_id=NULL, webhook_url=NULL, webhook_secret=NULL, deleted_at = now() WHERE id=$1",
            user.id,
        )
    for key in keys:
        try:
            await ctx.storage.delete(key)
        except Exception:  # noqa: BLE001, S110 - best-effort file cleanup, errors are non-fatal
            pass
    return Response(status_code=204)
