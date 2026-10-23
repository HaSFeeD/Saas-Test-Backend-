import { createHmac } from 'node:crypto';
import type { Queryable } from '../db.js';

export type IdentifierKind = 'email' | 'phone' | 'username';

export function normalizeIdentifier(kind: IdentifierKind, value: string): string {
  const v = value.trim().toLowerCase();
  if (kind === 'phone') return v.replace(/[^\d]/g, '');
  if (kind === 'username') return v.replace(/^@/, '');
  return v;
}

/** Keyed hash so the opt-out table can't be reversed with a dictionary of emails/phones. */
export const hashIdentifier = (pepper: string, kind: IdentifierKind, value: string) =>
  createHmac('sha256', pepper).update(`${kind}:${normalizeIdentifier(kind, value)}`).digest('hex');

/** Returns the identifier kinds from `ids` that the subject has opted out of. */
export async function optedOut(
  q: Queryable,
  pepper: string,
  ids: Partial<Record<IdentifierKind, string | undefined>>,
): Promise<IdentifierKind[]> {
  const pairs = (Object.entries(ids) as [IdentifierKind, string | undefined][]).filter(([, v]) => v);
  if (!pairs.length) return [];
  const hashes = pairs.map(([k, v]) => hashIdentifier(pepper, k, v!));
  const r = await q.query<{ kind: IdentifierKind }>(
    'SELECT kind FROM opt_outs WHERE confirmed_at IS NOT NULL AND value_hash = ANY($1)',
    [hashes],
  );
  return r.rows.map((x) => x.kind);
}
