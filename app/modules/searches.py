from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field, field_validator

from ..billing_service import has_active_subscription
from ..context import Context
from ..credits import apply_credits
from ..deps import AuthUser, get_context, require_user
from ..errors import bad_request, conflict, not_found, too_many, unprocessable
from ..jobs.queue import enqueue
from ..pii import opted_out
from ..search_service import get_search_view

router = APIRouter(prefix="/searches", tags=["searches"])

SUBSCRIBER_SEARCHES_PER_DAY = 50


class CreateSearch(BaseModel):
    first_name: str = Field(alias="firstName", min_length=1, max_length=60)
    last_name: str = Field(alias="lastName", min_length=1, max_length=60)
    age: int | None = Field(default=None, ge=18, le=100)
    city: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, pattern=r"^\+?[\d\s().-]{6,20}$")
    username: str | None = Field(default=None, max_length=60)
    photo_id: str | None = Field(default=None, alias="photoId")

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


async def daily_search_limit(ctx: Context, user_id: str) -> int:
    if await has_active_subscription(ctx.pool, user_id):
        return SUBSCRIBER_SEARCHES_PER_DAY
    return ctx.settings.free_searches_per_day


async def searches_used_today(ctx: Context, user_id: str) -> int:
    return await ctx.pool.fetchval(
        "SELECT count(*)::int FROM searches WHERE user_id=$1 AND created_at > now() - interval '24 hours'", user_id
    )


@router.post("", status_code=202)
async def create_search(
    body: CreateSearch, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    data = body.model_dump(by_alias=True, exclude_none=True)
    photo_id = data.pop("photoId", None)

    blocked = await opted_out(
        ctx.pool,
        ctx.settings.pii_pepper,
        {"email": data.get("email"), "phone": data.get("phone"), "username": data.get("username")},
    )
    if blocked:
        raise unprocessable("subject_opted_out", "This person has opted out of our search results")

    if photo_id:
        owns = await ctx.pool.fetchval("SELECT 1 FROM uploads WHERE id=$1 AND user_id=$2", photo_id, user.id)
        if not owns:
            raise bad_request("invalid_photo", "Photo not found")

    async with ctx.pool.acquire() as conn, conn.transaction():
            # Serialize this user's search creation so the daily limit can't be raced.
            await conn.execute("SELECT 1 FROM users WHERE id=$1 FOR UPDATE", user.id)
            limit = await daily_search_limit(ctx, user.id)
            used = await conn.fetchval(
                "SELECT count(*)::int FROM searches WHERE user_id=$1 AND created_at > now() - interval '24 hours'",
                user.id,
            )
            if used >= limit:
                raise too_many(f"Daily search limit reached ({limit}). Upgrade to Pro for more.")
            search_id = await conn.fetchval(
                "INSERT INTO searches (user_id, query, photo_id) VALUES ($1,$2,$3) RETURNING id::text",
                user.id, data, photo_id,
            )
            await enqueue(conn, "run_search", {"searchId": search_id}, max_attempts=3)

    return await get_search_view(ctx.pool, user.id, search_id)


@router.get("")
async def list_searches(
    limit: int = 20, offset: int = 0, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    limit = max(1, min(100, limit))
    rows = await ctx.pool.fetch(
        "SELECT id::text, status, query->>'firstName' AS \"firstName\", query->>'lastName' AS \"lastName\", "
        "result_count AS \"resultCount\", unlocked_at IS NULL AS locked, created_at AS \"createdAt\" "
        "FROM searches WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        user.id, limit, max(0, offset),
    )
    return {"items": [dict(r) for r in rows]}


@router.get("/{search_id}")
async def get_search(
    search_id: str, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    view = await get_search_view(ctx.pool, user.id, search_id)
    if view is None:
        raise not_found("Search not found")
    return view


@router.post("/{search_id}/unlock")
async def unlock_search(
    search_id: str, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    async with ctx.pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT status, result_count, unlocked_at FROM searches WHERE id=$1 AND user_id=$2 FOR UPDATE",
            search_id, user.id,
        )
        if row is None:
            raise not_found("Search not found")
        if row["unlocked_at"] is None:
            if row["status"] != "done":
                raise conflict("search_not_ready", "Search is not finished yet")
            if row["result_count"] > 0:
                await apply_credits(conn, user.id, -1, "unlock", search_id)
            await conn.execute("UPDATE searches SET unlocked_at=now() WHERE id=$1", search_id)
    return await get_search_view(ctx.pool, user.id, search_id)
