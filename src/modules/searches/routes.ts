import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import type { Ctx } from '../../context.js';
import { withTx } from '../../db.js';
import { enqueue } from '../../jobs/queue.js';
import { applyCredits } from '../../lib/credits.js';
import { AppError, badRequest, conflict, notFound, tooMany } from '../../lib/errors.js';
import { optedOut } from '../../lib/pii.js';
import { bearer, IdParams, Pagination } from '../../lib/schemas.js';
import { authUser } from '../../plugins/auth.js';
import { hasActiveSubscription } from '../billing/fulfillment.js';
import { getSearchView } from './service.js';

/** Fair-use cap for subscribers (non-subscribers get FREE_SEARCHES_PER_DAY). */
const SUBSCRIBER_SEARCHES_PER_DAY = 50;

const name = z.string().trim().min(1).max(60);
const opt = (s: z.ZodString) => s.optional();

const CreateSearch = z.object({
  firstName: name,
  lastName: name,
  age: z.number().int().min(18).max(100).optional(),
  city: opt(z.string().trim().max(80)),
  region: opt(z.string().trim().max(80)),
  country: z.string().length(2).toUpperCase().optional(),
  email: z.email().optional(),
  phone: opt(z.string().regex(/^\+?[\d\s().-]{6,20}$/, 'Invalid phone number')),
  username: opt(z.string().trim().max(60)),
  photoId: z.uuid().optional(),
});

export async function searchesUsedToday(q: Ctx['db'], userId: string) {
  const r = await q.query<{ n: number }>(
    `SELECT count(*)::int AS n FROM searches WHERE user_id=$1 AND created_at > now() - interval '24 hours'`,
    [userId],
  );
  return r.rows[0]!.n;
}

export async function dailySearchLimit(ctx: Ctx, userId: string) {
  return (await hasActiveSubscription(ctx.db, userId)) ? SUBSCRIBER_SEARCHES_PER_DAY : ctx.cfg.FREE_SEARCHES_PER_DAY;
}

export const searchRoutes =
  (ctx: Ctx): FastifyPluginAsyncZod =>
  async (app) => {
    const { db, cfg } = ctx;
    app.addHook('onRequest', app.authenticate);

    app.post(
      '/',
      {
        schema: {
          tags: ['searches'],
          security: bearer,
          description: 'Starts an async search. Running a search is free (daily limit); viewing full results costs 1 credit.',
          body: CreateSearch,
        },
      },
      async (req, reply) => {
        const user = authUser(req);
        const { photoId, ...query } = req.body;

        const blocked = await optedOut(db, cfg.PII_PEPPER, { email: query.email, phone: query.phone, username: query.username });
        if (blocked.length) {
          throw new AppError(422, 'subject_opted_out', 'This person has opted out of our search results');
        }
        if (photoId) {
          const up = await db.query('SELECT 1 FROM uploads WHERE id=$1 AND user_id=$2', [photoId, user.id]);
          if (!up.rowCount) throw badRequest('invalid_photo', 'Photo not found');
        }

        const id = await withTx(db, async (tx) => {
          // Serialize this user's search creation so the daily limit can't be raced.
          await tx.query('SELECT 1 FROM users WHERE id=$1 FOR UPDATE', [user.id]);
          const limit = await dailySearchLimit(ctx, user.id);
          const used = (
            await tx.query<{ n: number }>(
              `SELECT count(*)::int AS n FROM searches WHERE user_id=$1 AND created_at > now() - interval '24 hours'`,
              [user.id],
            )
          ).rows[0]!.n;
          if (used >= limit) throw tooMany(`Daily search limit reached (${limit}). Upgrade to Pro for more.`);

          const r = await tx.query<{ id: string }>(
            'INSERT INTO searches (user_id, query, photo_id) VALUES ($1,$2,$3) RETURNING id',
            [user.id, JSON.stringify(query), photoId ?? null],
          );
          const searchId = r.rows[0]!.id;
          await enqueue(tx, 'run_search', { searchId }, { maxAttempts: 3 });
          return searchId;
        });
        return reply.code(202).send(await getSearchView(db, user.id, id));
      },
    );

    app.get(
      '/',
      { schema: { tags: ['searches'], security: bearer, querystring: Pagination } },
      async (req) => {
        const user = authUser(req);
        const r = await db.query(
          `SELECT id, status, query->>'firstName' AS "firstName", query->>'lastName' AS "lastName",
                  result_count AS "resultCount", unlocked_at IS NULL AS locked, created_at AS "createdAt"
           FROM searches WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3`,
          [user.id, req.query.limit, req.query.offset],
        );
        return { items: r.rows };
      },
    );

    app.get(
      '/:id',
      { schema: { tags: ['searches'], security: bearer, params: IdParams } },
      async (req) => {
        const view = await getSearchView(db, authUser(req).id, req.params.id);
        if (!view) throw notFound('Search not found');
        return view;
      },
    );

    app.post(
      '/:id/unlock',
      {
        schema: {
          tags: ['searches'],
          security: bearer,
          description: 'Spends 1 credit to reveal full results. Free when there are no results. Idempotent.',
          params: IdParams,
        },
      },
      async (req) => {
        const user = authUser(req);
        await withTx(db, async (tx) => {
          const r = await tx.query(
            'SELECT status, result_count, unlocked_at FROM searches WHERE id=$1 AND user_id=$2 FOR UPDATE',
            [req.params.id, user.id],
          );
          const s = r.rows[0];
          if (!s) throw notFound('Search not found');
          if (s.unlocked_at) return;
          if (s.status !== 'done') throw conflict('search_not_ready', 'Search is not finished yet');
          if (s.result_count > 0) await applyCredits(tx, user.id, -1, 'unlock', req.params.id);
          await tx.query('UPDATE searches SET unlocked_at=now() WHERE id=$1', [req.params.id]);
        });
        return getSearchView(db, user.id, req.params.id);
      },
    );
  };
