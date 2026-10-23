from __future__ import annotations

import asyncpg

from .context import Context
from .pii import opted_out
from .providers.base import SearchInput


def mask_name(name: str) -> str:
    """'Jessica Smith' -> 'J****** S.' - enough for a teaser, not enough to identify anyone."""
    parts = [p for p in name.split() if p]
    out = []
    for i, p in enumerate(parts):
        if i == 0:
            out.append(p[0] + "*" * max(2, len(p) - 1))
        else:
            out.append(f"{p[0]}.")
    return " ".join(out)


def _confidence_label(c: float) -> str:
    return "high" if c >= 0.85 else "medium" if c >= 0.65 else "low"


async def run_search_job(ctx: Context, search_id: str, is_last_attempt: bool) -> None:
    """Job handler: run the provider and store results. Safe to retry."""
    row = await ctx.pool.fetchrow(
        "UPDATE searches SET status='running', started_at=COALESCE(started_at, now()) "
        "WHERE id=$1 AND status IN ('queued','running') RETURNING query, photo_id::text",
        search_id,
    )
    if row is None:
        return  # already finished or deleted

    try:
        query = row["query"]
        payload = SearchInput(
            first_name=query["firstName"],
            last_name=query["lastName"],
            age=query.get("age"),
            city=query.get("city"),
            region=query.get("region"),
            country=query.get("country"),
            email=query.get("email"),
            phone=query.get("phone"),
            username=query.get("username"),
        )
        if row["photo_id"]:
            up = await ctx.pool.fetchrow("SELECT storage_key, mime FROM uploads WHERE id=$1", row["photo_id"])
            if up:
                payload.photo = await ctx.storage.get(up["storage_key"])
                payload.photo_mime = up["mime"]

        matches = await ctx.search_provider.search(payload)

        # Drop profiles whose owners opted out after the search was queued.
        kept = []
        for m in matches:
            if m.username and await opted_out(ctx.pool, ctx.settings.pii_pepper, {"username": m.username}):
                continue
            kept.append(m)

        async with ctx.pool.acquire() as conn, conn.transaction():
                await conn.execute("DELETE FROM search_results WHERE search_id=$1", search_id)
                for m in kept:
                    await conn.execute(
                        "INSERT INTO search_results "
                        "(search_id, platform, profile_url, display_name, username, age, location, "
                        " photo_url, confidence, last_active_at, raw) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                        search_id, m.platform, m.profile_url, m.display_name, m.username, m.age,
                        m.location, m.photo_url, m.confidence, m.last_active_at, m.raw or {},
                    )
                await conn.execute(
                    "UPDATE searches SET status='done', result_count=$2, finished_at=now(), error=NULL WHERE id=$1",
                    search_id, len(kept),
                )
    except Exception:
        if is_last_attempt:
            await ctx.pool.execute(
                "UPDATE searches SET status='failed', error=$2, finished_at=now() WHERE id=$1",
                search_id, "The search could not be completed. Please try again later.",
            )
        raise


async def get_search_view(conn_or_pool: asyncpg.Connection | asyncpg.Pool, user_id: str, search_id: str) -> dict | None:
    row = await conn_or_pool.fetchrow(
        "SELECT id::text, status, query, photo_id::text, result_count, error, unlocked_at, created_at, finished_at "
        "FROM searches WHERE id=$1 AND user_id=$2",
        search_id, user_id,
    )
    if row is None:
        return None

    locked = row["unlocked_at"] is None
    results: list[dict] = []
    if row["status"] == "done":
        rrows = await conn_or_pool.fetch(
            "SELECT id::text, platform, profile_url, display_name, age, location, photo_url, "
            "confidence, last_active_at FROM search_results WHERE search_id=$1 ORDER BY confidence DESC",
            search_id,
        )
        for r in rrows:
            if locked:
                results.append(
                    {
                        "id": r["id"],
                        "platform": r["platform"],
                        "displayName": mask_name(r["display_name"]),
                        "match": _confidence_label(r["confidence"]),
                        "locked": True,
                    }
                )
            else:
                results.append(
                    {
                        "id": r["id"],
                        "platform": r["platform"],
                        "displayName": r["display_name"],
                        "match": _confidence_label(r["confidence"]),
                        "locked": False,
                        "confidence": r["confidence"],
                        "profileUrl": r["profile_url"],
                        "photoUrl": r["photo_url"],
                        "age": r["age"],
                        "location": r["location"],
                        "lastActiveAt": r["last_active_at"].isoformat() if r["last_active_at"] else None,
                    }
                )

    return {
        "id": row["id"],
        "status": row["status"],
        "query": row["query"],
        "photoId": row["photo_id"],
        "resultCount": row["result_count"],
        "platforms": sorted({r["platform"] for r in results}),
        "locked": locked,
        "error": row["error"],
        "createdAt": row["created_at"].isoformat(),
        "finishedAt": row["finished_at"].isoformat() if row["finished_at"] else None,
        "results": results,
    }
