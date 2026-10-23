import { randomUUID } from 'node:crypto';
import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import type { Ctx } from '../../context.js';
import { withTx } from '../../db.js';
import { applyCredits } from '../../lib/credits.js';
import { notFound } from '../../lib/errors.js';
import { bearer, IdParams, Pagination } from '../../lib/schemas.js';

export const adminRoutes =
  (ctx: Ctx): FastifyPluginAsyncZod =>
  async (app) => {
    const { db } = ctx;
    app.addHook('onRequest', app.requireAdmin);

    app.get('/stats', { schema: { tags: ['admin'], security: bearer } }, async () => {
      const one = async (sql: string) => (await db.query(sql)).rows[0];
      const many = async (sql: string) => (await db.query(sql)).rows;
      return {
        users: await one(`SELECT count(*)::int AS total,
                                 count(*) FILTER (WHERE created_at > now() - interval '24 hours')::int AS last24h
                          FROM users WHERE deleted_at IS NULL`),
        activeSubscriptions: (await one(`SELECT count(*)::int AS n FROM subscriptions WHERE status IN ('active','trialing')`)).n,
        searches: await many(`SELECT status, count(*)::int AS n FROM searches GROUP BY status`),
        unlocks24h: (await one(`SELECT count(*)::int AS n FROM searches WHERE unlocked_at > now() - interval '24 hours'`)).n,
        creditsSold30d: (await one(
          `SELECT COALESCE(sum(delta),0)::int AS n FROM credit_ledger
           WHERE delta > 0 AND (reason LIKE 'purchase:%' OR reason LIKE 'subscription:%') AND created_at > now() - interval '30 days'`,
        )).n,
        queue: await many(`SELECT type, status, count(*)::int AS n FROM jobs WHERE status <> 'done' GROUP BY type, status`),
      };
    });

    app.get(
      '/users',
      {
        schema: { tags: ['admin'], security: bearer, querystring: Pagination.extend({ q: z.string().optional() }) },
      },
      async (req) => {
        const { limit, offset, q } = req.query;
        const r = await db.query(
          `SELECT id, email, role, credits, created_at AS "createdAt", deleted_at AS "deletedAt"
           FROM users WHERE ($1::text IS NULL OR email ILIKE '%' || $1 || '%')
           ORDER BY created_at DESC LIMIT $2 OFFSET $3`,
          [q ?? null, limit, offset],
        );
        return { items: r.rows };
      },
    );

    app.post(
      '/users/:id/credits',
      {
        schema: {
          tags: ['admin'],
          security: bearer,
          params: IdParams,
          body: z.object({ amount: z.number().int().min(-1000).max(1000).refine((n) => n !== 0, 'amount must not be 0') }),
        },
      },
      async (req) => {
        const exists = await db.query('SELECT 1 FROM users WHERE id=$1 AND deleted_at IS NULL', [req.params.id]);
        if (!exists.rowCount) throw notFound('User not found');
        const res = await withTx(db, (tx) =>
          applyCredits(tx, req.params.id, req.body.amount, `admin:${req.auth!.id}`, randomUUID()),
        );
        return { balance: res.balance };
      },
    );
  };
