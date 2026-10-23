import type { Ctx } from '../../context.js';
import type { Queryable } from '../../db.js';
import { optedOut } from '../../lib/pii.js';
import type { SearchInput } from '../../providers/types.js';

const PROVIDER_TIMEOUT_MS = 60_000;

export interface SearchQuery {
  firstName: string;
  lastName: string;
  age?: number;
  city?: string;
  region?: string;
  country?: string;
  email?: string;
  phone?: string;
  username?: string;
}

interface ResultRow {
  id: string;
  platform: string;
  profile_url: string;
  display_name: string;
  age: number | null;
  location: string | null;
  photo_url: string | null;
  confidence: number;
  last_active_at: Date | null;
}

const confidenceLabel = (c: number) => (c >= 0.85 ? 'high' : c >= 0.65 ? 'medium' : 'low');

/** "Jessica Smith" -> "J****** S." — enough for a teaser, not enough to identify anyone. */
export const maskName = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .map((p, i) => (i === 0 ? p[0] + '*'.repeat(Math.max(2, p.length - 1)) : `${p[0]}.`))
    .join(' ');

/** Job handler: runs the provider and stores results. Safe to retry. */
export async function runSearchJob(ctx: Ctx, searchId: string, isLastAttempt: boolean) {
  const { db, storage, searchProvider, cfg } = ctx;
  const r = await db.query(
    `UPDATE searches SET status='running', started_at=COALESCE(started_at, now())
     WHERE id=$1 AND status IN ('queued','running') RETURNING query, photo_id`,
    [searchId],
  );
  const s = r.rows[0];
  if (!s) return; // already finished or deleted

  try {
    const q = s.query as SearchQuery;
    const input: SearchInput = { ...q };
    if (s.photo_id) {
      const up = await db.query('SELECT storage_key, mime FROM uploads WHERE id=$1', [s.photo_id]);
      if (up.rows[0]) input.photo = { data: await storage.get(up.rows[0].storage_key), mime: up.rows[0].mime };
    }
    const matches = await searchProvider.search(input, AbortSignal.timeout(PROVIDER_TIMEOUT_MS));

    // Drop profiles whose owners opted out after the search was queued.
    const kept = [];
    for (const m of matches) {
      if (m.username && (await optedOut(db, cfg.PII_PEPPER, { username: m.username })).length) continue;
      kept.push(m);
    }

    const client = await db.connect();
    try {
      await client.query('BEGIN');
      await client.query('DELETE FROM search_results WHERE search_id=$1', [searchId]);
      for (const m of kept) {
        await client.query(
          `INSERT INTO search_results (search_id, platform, profile_url, display_name, age, location, photo_url, confidence, last_active_at, raw)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
          [searchId, m.platform, m.profileUrl, m.displayName, m.age ?? null, m.location ?? null, m.photoUrl ?? null,
           m.confidence, m.lastActiveAt ?? null, m.raw ? JSON.stringify(m.raw) : null],
        );
      }
      await client.query(
        `UPDATE searches SET status='done', result_count=$2, finished_at=now(), error=NULL WHERE id=$1`,
        [searchId, kept.length],
      );
      await client.query('COMMIT');
    } catch (e) {
      await client.query('ROLLBACK');
      throw e;
    } finally {
      client.release();
    }
  } catch (e) {
    if (isLastAttempt) {
      await db.query(`UPDATE searches SET status='failed', error=$2, finished_at=now() WHERE id=$1`, [
        searchId,
        'The search could not be completed. Please try again later.',
      ]);
    }
    throw e;
  }
}

export async function getSearchView(q: Queryable, userId: string, id: string) {
  const r = await q.query(
    `SELECT id, status, query, photo_id, result_count, error, unlocked_at, created_at, finished_at
     FROM searches WHERE id=$1 AND user_id=$2`,
    [id, userId],
  );
  const s = r.rows[0];
  if (!s) return null;
  const locked = !s.unlocked_at;
  let results: unknown[] = [];
  if (s.status === 'done') {
    const rr = await q.query<ResultRow>(
      `SELECT id, platform, profile_url, display_name, age, location, photo_url, confidence, last_active_at
       FROM search_results WHERE search_id=$1 ORDER BY confidence DESC`,
      [id],
    );
    results = rr.rows.map((x) =>
      locked
        ? { id: x.id, platform: x.platform, displayName: maskName(x.display_name), match: confidenceLabel(x.confidence), locked: true }
        : {
            id: x.id,
            platform: x.platform,
            displayName: x.display_name,
            match: confidenceLabel(x.confidence),
            locked: false,
            confidence: x.confidence,
            profileUrl: x.profile_url,
            photoUrl: x.photo_url,
            age: x.age,
            location: x.location,
            lastActiveAt: x.last_active_at,
          },
    );
  }
  return {
    id: s.id,
    status: s.status,
    query: s.query,
    photoId: s.photo_id,
    resultCount: s.result_count,
    platforms: [...new Set((results as { platform: string }[]).map((x) => x.platform))],
    locked,
    error: s.error,
    createdAt: s.created_at,
    finishedAt: s.finished_at,
    results,
  };
}
