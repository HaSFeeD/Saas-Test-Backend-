from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..context import Context
from ..credits import apply_credits
from ..deps import AuthUser, get_context, require_admin
from ..errors import not_found

router = APIRouter(prefix="/admin", tags=["admin"])


class CreditBody(BaseModel):
    amount: int = Field(ge=-1000, le=1000)


@router.get("/stats")
async def stats(ctx: Context = Depends(get_context), _: AuthUser = Depends(require_admin)) -> dict:
    users = await ctx.pool.fetchrow(
        "SELECT count(*)::int AS total, "
        "count(*) FILTER (WHERE created_at > now() - interval '24 hours')::int AS last24h "
        "FROM users WHERE deleted_at IS NULL"
    )
    active_subs = await ctx.pool.fetchval(
        "SELECT count(*)::int FROM subscriptions WHERE status IN ('active','trialing')"
    )
    searches = await ctx.pool.fetch("SELECT status, count(*)::int AS n FROM searches GROUP BY status")
    unlocks = await ctx.pool.fetchval(
        "SELECT count(*)::int FROM searches WHERE unlocked_at > now() - interval '24 hours'"
    )
    credits_sold = await ctx.pool.fetchval(
        "SELECT COALESCE(sum(delta),0)::int FROM credit_ledger WHERE delta > 0 "
        "AND (reason LIKE 'purchase:%' OR reason LIKE 'subscription:%') AND created_at > now() - interval '30 days'"
    )
    queue = await ctx.pool.fetch(
        "SELECT type, status, count(*)::int AS n FROM jobs WHERE status <> 'done' GROUP BY type, status"
    )
    return {
        "users": dict(users),
        "activeSubscriptions": active_subs,
        "searches": [dict(r) for r in searches],
        "unlocks24h": unlocks,
        "creditsSold30d": credits_sold,
        "queue": [dict(r) for r in queue],
    }


@router.get("/users")
async def users(
    q: str | None = None, limit: int = 20, offset: int = 0,
    ctx: Context = Depends(get_context), _: AuthUser = Depends(require_admin),
) -> dict:
    limit = max(1, min(100, limit))
    rows = await ctx.pool.fetch(
        "SELECT id::text, email, role, credits, created_at AS \"createdAt\", deleted_at AS \"deletedAt\" "
        "FROM users WHERE ($1::text IS NULL OR email ILIKE '%' || $1 || '%') "
        "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        q, limit, max(0, offset),
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/users/{user_id}/credits")
async def grant_credits(
    user_id: str, body: CreditBody,
    ctx: Context = Depends(get_context), admin: AuthUser = Depends(require_admin),
) -> dict:
    if body.amount == 0:
        from ..errors import bad_request

        raise bad_request("invalid_amount", "amount must not be 0")
    exists = await ctx.pool.fetchval("SELECT 1 FROM users WHERE id=$1 AND deleted_at IS NULL", user_id)
    if not exists:
        raise not_found("User not found")
    async with ctx.pool.acquire() as conn, conn.transaction():
        res = await apply_credits(conn, user_id, body.amount, f"admin:{admin.id}", str(uuid.uuid4()))
    return {"balance": res.balance}
