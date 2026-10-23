import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import type { Ctx } from '../../context.js';
import { withTx } from '../../db.js';
import { enqueue } from '../../jobs/queue.js';
import { newToken, normalizeEmail, sha256 } from '../../lib/crypto.js';
import { badRequest } from '../../lib/errors.js';
import { hashIdentifier } from '../../lib/pii.js';

/**
 * Public "remove me" flow. Anyone can ask not to be searchable by an identifier.
 * The request only takes effect after the link sent to contactEmail is clicked.
 */
export const privacyRoutes =
  (ctx: Ctx): FastifyPluginAsyncZod =>
  async (app) => {
    const { db, cfg } = ctx;
    const strict = { rateLimit: { max: cfg.AUTH_RATE_LIMIT_PER_MIN, timeWindow: '1 minute' } };

    app.post(
      '/opt-out',
      {
        config: strict,
        schema: {
          tags: ['privacy'],
          body: z
            .object({
              kind: z.enum(['email', 'phone', 'username']),
              value: z.string().trim().min(1).max(254),
              contactEmail: z.email(),
            })
            .refine((b) => b.kind !== 'email' || normalizeEmail(b.value) === normalizeEmail(b.contactEmail), {
              message: 'To opt out an email address, the confirmation must be sent to that same address',
              path: ['contactEmail'],
            }),
        },
      },
      async (req, reply) => {
        const { kind, value, contactEmail } = req.body;
        const token = newToken();
        await withTx(db, async (tx) => {
          const r = await tx.query(
            `INSERT INTO opt_outs (kind, value_hash, token_hash) VALUES ($1,$2,$3)
             ON CONFLICT (kind, value_hash) DO UPDATE SET token_hash = EXCLUDED.token_hash
               WHERE opt_outs.confirmed_at IS NULL
             RETURNING id`,
            [kind, hashIdentifier(cfg.PII_PEPPER, kind, value), sha256(token)],
          );
          if (!r.rowCount) return; // already confirmed
          await enqueue(tx, 'send_email', {
            to: contactEmail,
            subject: 'Confirm your removal request',
            text: `Confirm that we should exclude this ${kind} from all search results:\n${cfg.PUBLIC_URL}/v1/privacy/opt-out/confirm?token=${token}`,
          });
        });
        return reply.code(202).send({ message: 'If the request is valid, a confirmation email has been sent.' });
      },
    );

    app.get(
      '/opt-out/confirm',
      { config: strict, schema: { tags: ['privacy'], querystring: z.object({ token: z.string() }) } },
      async (req) => {
        const r = await db.query(
          `UPDATE opt_outs SET confirmed_at = now(), token_hash = NULL WHERE token_hash = $1 AND confirmed_at IS NULL`,
          [sha256(req.query.token)],
        );
        if (!r.rowCount) throw badRequest('invalid_token', 'This link is invalid or was already used');
        return { message: 'Done. This identifier is excluded from all future search results.' };
      },
    );
  };
