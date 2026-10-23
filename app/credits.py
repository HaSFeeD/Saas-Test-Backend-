from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from .errors import payment_required


@dataclass
class CreditResult:
    #: False when (reason, ref_id) was already recorded — the call was an idempotent replay.
    applied: bool
    balance: int


async def apply_credits(conn: asyncpg.Connection, user_id: str, delta: int, reason: str, ref_id: str) -> CreditResult:
    """
    The only place a balance changes. Must run inside a transaction.

    Locks the user row so concurrent spends cannot overdraw, and is idempotent
    per (reason, ref_id) via the unique constraint on credit_ledger.
    """
    current = await conn.fetchval("SELECT credits FROM users WHERE id = $1 FOR UPDATE", user_id)
    if current is None:
        raise RuntimeError(f"user {user_id} not found")

    duplicate = await conn.fetchval(
        "SELECT 1 FROM credit_ledger WHERE reason = $1 AND ref_id = $2", reason, ref_id
    )
    if duplicate:
        return CreditResult(applied=False, balance=current)

    balance = current + delta
    if balance < 0:
        raise payment_required()

    await conn.execute("UPDATE users SET credits = $2 WHERE id = $1", user_id, balance)
    await conn.execute(
        "INSERT INTO credit_ledger (user_id, delta, balance_after, reason, ref_id) VALUES ($1,$2,$3,$4,$5)",
        user_id, delta, balance, reason, ref_id,
    )
    return CreditResult(applied=True, balance=balance)
