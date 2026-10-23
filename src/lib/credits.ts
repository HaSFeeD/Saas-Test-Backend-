import type { Tx } from '../db.js';
import { paymentRequired } from './errors.js';

export interface CreditResult {
  /** false when (reason, refId) was already recorded — the call was a no-op replay. */
  applied: boolean;
  balance: number;
}

/**
 * The only place balances change. Must run inside a transaction.
 * Locks the user row, so concurrent spends can't overdraw, and is idempotent per (reason, refId).
 */
export async function applyCredits(
  tx: Tx,
  userId: string,
  delta: number,
  reason: string,
  refId: string,
): Promise<CreditResult> {
  const user = await tx.query<{ credits: number }>('SELECT credits FROM users WHERE id = $1 FOR UPDATE', [userId]);
  const row = user.rows[0];
  if (!row) throw new Error(`user ${userId} not found`);

  const dup = await tx.query('SELECT 1 FROM credit_ledger WHERE reason = $1 AND ref_id = $2', [reason, refId]);
  if (dup.rowCount) return { applied: false, balance: row.credits };

  const balance = row.credits + delta;
  if (balance < 0) throw paymentRequired();

  await tx.query('UPDATE users SET credits = $2 WHERE id = $1', [userId, balance]);
  await tx.query(
    'INSERT INTO credit_ledger (user_id, delta, balance_after, reason, ref_id) VALUES ($1,$2,$3,$4,$5)',
    [userId, delta, balance, reason, refId],
  );
  return { applied: true, balance };
}
