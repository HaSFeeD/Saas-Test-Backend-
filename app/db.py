from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_ADVISORY_LOCK = 727274


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Work with jsonb columns as Python dicts/lists transparently.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def _normalize_dsn(url: str) -> str:
    # asyncpg accepts postgres:// and postgresql:// but not the +driver suffixes some tools add.
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def create_pool(database_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        _normalize_dsn(database_url), min_size=1, max_size=10, init=_init_connection
    )


@asynccontextmanager
async def tx(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """Run a block in a transaction, committing on success and rolling back on error."""
    async with pool.acquire() as conn, conn.transaction():
        yield conn


async def migrate(pool: asyncpg.Pool, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply *.sql files once each, in filename order, guarded by an advisory lock."""
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
            for path in sorted(directory.glob("*.sql")):
                if path.name in done:
                    continue
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
                applied.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK)
    return applied
