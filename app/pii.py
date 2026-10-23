from __future__ import annotations

import hashlib
import hmac

import asyncpg

IdentifierKind = str  # "email" | "phone" | "username"


def normalize_identifier(kind: IdentifierKind, value: str) -> str:
    v = value.strip().lower()
    if kind == "phone":
        return "".join(ch for ch in v if ch.isdigit())
    if kind == "username":
        return v.lstrip("@")
    return v


def hash_identifier(pepper: str, kind: IdentifierKind, value: str) -> str:
    """Keyed hash so the opt-out table cannot be reversed with a dictionary of emails/phones."""
    msg = f"{kind}:{normalize_identifier(kind, value)}".encode()
    return hmac.new(pepper.encode(), msg, hashlib.sha256).hexdigest()


async def opted_out(conn: asyncpg.Connection, pepper: str, ids: dict[IdentifierKind, str | None]) -> list[IdentifierKind]:
    pairs = [(k, v) for k, v in ids.items() if v]
    if not pairs:
        return []
    hashes = [hash_identifier(pepper, k, v) for k, v in pairs]  # type: ignore[arg-type]
    rows = await conn.fetch(
        "SELECT kind FROM opt_outs WHERE confirmed_at IS NOT NULL AND value_hash = ANY($1)", hashes
    )
    return [r["kind"] for r in rows]
