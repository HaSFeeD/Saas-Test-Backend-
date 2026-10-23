import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import type { Ctx } from '../../context.js';
import { withTx } from '../../db.js';
import { verifyPassword } from '../../lib/crypto.js';
import { unauthorized } from '../../lib/errors.js';
import { bearer } from '../../lib/schemas.js';
import { authUser } from '../../plugins/auth.js';
import { dailySearchLimit, searchesUsedToday } from '../searches/routes.js';

export const meRoutes =
  (ctx: Ctx): FastifyPluginAsyncZod =>
  async (app) => {
    const { db, storage, billing } = ctx;
    app.addHook('onRequest', app.authenticate);

    app.get('/', { schema: { tags: ['me'], security: bearer } }, async (req) => {
      const { id } = authUser(req);
      const u = (await db.query('SELECT id, email, role, credits, created_at AS "createdAt" FROM users WHERE id=$1', [id])).rows[0];
      const sub = (
        await db.query(
          `SELECT plan, status, current_period_end AS "currentPeriodEnd", cancel_at_period_end AS "cancelAtPeriodEnd"
           FROM subscriptions WHERE user_id=$1 ORDER BY updated_at DESC LIMIT 1`,
          [id],
        )
      ).rows[0];
      const [limit, used] = await Promise.all([dailySearchLimit(ctx, id), searchesUsedToday(db, id)]);
      return { ...u, subscription: sub ?? null, searches: { dailyLimit: limit, usedToday: used, remainingToday: Math.max(0, limit - used) } };
    });

    // GDPR art. 15/20: everything we hold about the user, machine-readable.
    app.get('/export', { schema: { tags: ['me'], security: bearer } }, async (req, reply) => {
      const { id } = authUser(req);
      const q = async (sql: string) => (await db.query(sql, [id])).rows;
      const data = {
        exportedAt: new Date().toISOString(),
        user: (await q('SELECT id, email, role, credits, tos_accepted_at, created_at FROM users WHERE id=$1'))[0],
        subscriptions: await q('SELECT plan, status, current_period_end, created_at FROM subscriptions WHERE user_id=$1'),
        creditLedger: await q('SELECT delta, balance_after, reason, created_at FROM credit_ledger WHERE user_id=$1 ORDER BY id'),
        uploads: await q('SELECT id, mime, size, created_at FROM uploads WHERE user_id=$1'),
        searches: await q(
          `SELECT s.id, s.status, s.query, s.created_at, s.unlocked_at,
                  COALESCE(json_agg(r.*) FILTER (WHERE r.id IS NOT NULL AND s.unlocked_at IS NOT NULL), '[]') AS results
           FROM searches s LEFT JOIN search_results r ON r.search_id = s.id
           WHERE s.user_id=$1 GROUP BY s.id ORDER BY s.created_at`,
        ),
      };
      return reply.header('Content-Disposition', 'attachment; filename="my-data.json"').send(data);
    });

    // GDPR art. 17. Financial ledger rows stay (legal retention) but point to an anonymized user.
    app.delete(
      '/',
      { schema: { tags: ['me'], security: bearer, body: z.object({ password: z.string() }) } },
      async (req, reply) => {
        const { id } = authUser(req);
        const u = (await db.query('SELECT password_hash, stripe_customer_id FROM users WHERE id=$1', [id])).rows[0];
        if (!(await verifyPassword(req.body.password, u.password_hash))) throw unauthorized('Wrong password');
        if (u.stripe_customer_id) await billing.cancelCustomerSubscriptions(u.stripe_customer_id);

        const keys = await withTx(db, async (tx) => {
          const up = await tx.query<{ storage_key: string }>('DELETE FROM uploads WHERE user_id=$1 RETURNING storage_key', [id]);
          await tx.query('DELETE FROM searches WHERE user_id=$1', [id]);
          await tx.query('DELETE FROM refresh_tokens WHERE user_id=$1', [id]);
          await tx.query('DELETE FROM password_resets WHERE user_id=$1', [id]);
          await tx.query(
            `UPDATE users SET email = 'deleted+' || id || '@deleted.invalid', password_hash = '!', deleted_at = now() WHERE id=$1`,
            [id],
          );
          return up.rows.map((r) => r.storage_key);
        });
        await Promise.allSettled(keys.map((k) => storage.delete(k)));
        return reply.code(204).send();
      },
    );
  };
