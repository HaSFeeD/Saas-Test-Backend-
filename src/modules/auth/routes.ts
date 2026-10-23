import type { FastifyInstance } from 'fastify';
import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import type { Ctx } from '../../context.js';
import { withTx } from '../../db.js';
import type { Queryable } from '../../db.js';
import { enqueue } from '../../jobs/queue.js';
import { hashPassword, newToken, normalizeEmail, sha256, verifyPassword } from '../../lib/crypto.js';
import { conflict, unauthorized, badRequest } from '../../lib/errors.js';
import { ACCESS_TOKEN_TTL } from '../../plugins/auth.js';

const REFRESH_TTL_DAYS = 30;
const RESET_TTL_MIN = 60;

const Email = z.email().max(254);
const Password = z.string().min(8).max(128);

const Tokens = z.object({
  accessToken: z.string(),
  refreshToken: z.string(),
  user: z.object({ id: z.string(), email: z.string(), role: z.string() }),
});

// Compared against when the email is unknown, so login timing doesn't reveal which emails exist.
const dummyHash = hashPassword('dummy-password-for-timing');

export async function issueTokens(app: FastifyInstance, q: Queryable, user: { id: string; email: string; role: string }) {
  const refreshToken = newToken();
  await q.query(
    `INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES ($1,$2, now() + make_interval(days => $3))`,
    [user.id, sha256(refreshToken), REFRESH_TTL_DAYS],
  );
  const accessToken = app.jwt.sign({ sub: user.id, role: user.role }, { expiresIn: ACCESS_TOKEN_TTL });
  return { accessToken, refreshToken, user: { id: user.id, email: user.email, role: user.role } };
}

export const authRoutes =
  (ctx: Ctx): FastifyPluginAsyncZod =>
  async (app) => {
    const { db, cfg } = ctx;
    const strict = { rateLimit: { max: cfg.AUTH_RATE_LIMIT_PER_MIN, timeWindow: '1 minute' } };

    app.post(
      '/register',
      {
        config: strict,
        schema: {
          tags: ['auth'],
          body: z.object({
            email: Email,
            password: Password,
            acceptTerms: z.literal(true, { error: 'You must accept the Terms of Service' }),
          }),
          response: { 201: Tokens },
        },
      },
      async (req, reply) => {
        const email = normalizeEmail(req.body.email);
        const hash = await hashPassword(req.body.password);
        const out = await withTx(db, async (tx) => {
          const r = await tx.query(
            `INSERT INTO users (email, password_hash, tos_accepted_at) VALUES ($1,$2,now())
             ON CONFLICT DO NOTHING RETURNING id, email, role`,
            [email, hash],
          );
          if (!r.rows[0]) throw conflict('email_taken', 'An account with this email already exists');
          return issueTokens(app, tx, r.rows[0]);
        });
        return reply.code(201).send(out);
      },
    );

    app.post(
      '/login',
      {
        config: strict,
        schema: { tags: ['auth'], body: z.object({ email: Email, password: z.string().max(128) }), response: { 200: Tokens } },
      },
      async (req) => {
        const r = await db.query(
          'SELECT id, email, role, password_hash FROM users WHERE lower(email) = $1 AND deleted_at IS NULL',
          [normalizeEmail(req.body.email)],
        );
        const user = r.rows[0];
        const ok = await verifyPassword(req.body.password, user?.password_hash ?? (await dummyHash));
        if (!user || !ok) throw unauthorized('Invalid email or password');
        return issueTokens(app, db, user);
      },
    );

    app.post(
      '/refresh',
      { config: strict, schema: { tags: ['auth'], body: z.object({ refreshToken: z.string() }), response: { 200: Tokens } } },
      async (req) => {
        return withTx(db, async (tx) => {
          const r = await tx.query(
            `SELECT t.id, t.user_id, t.revoked_at, t.expires_at < now() AS expired, u.email, u.role, u.deleted_at
             FROM refresh_tokens t JOIN users u ON u.id = t.user_id
             WHERE t.token_hash = $1 FOR UPDATE OF t`,
            [sha256(req.body.refreshToken)],
          );
          const t = r.rows[0];
          if (!t || t.expired || t.deleted_at) throw unauthorized('Invalid refresh token');
          if (t.revoked_at) {
            // A rotated token was replayed: assume theft and kill every session of this user.
            await tx.query('UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL', [t.user_id]);
            return { reuse: true } as const;
          }
          await tx.query('UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1', [t.id]);
          return issueTokens(app, tx, { id: t.user_id, email: t.email, role: t.role });
        }).then((out) => {
          // Thrown after commit so the mass revocation is persisted.
          if ('reuse' in out) throw unauthorized('Invalid refresh token');
          return out;
        });
      },
    );

    app.post(
      '/logout',
      { schema: { tags: ['auth'], body: z.object({ refreshToken: z.string() }) } },
      async (req, reply) => {
        await db.query('UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL', [
          sha256(req.body.refreshToken),
        ]);
        return reply.code(204).send();
      },
    );

    app.post(
      '/forgot-password',
      { config: strict, schema: { tags: ['auth'], body: z.object({ email: Email }) } },
      async (req, reply) => {
        const r = await db.query('SELECT id, email FROM users WHERE lower(email) = $1 AND deleted_at IS NULL', [
          normalizeEmail(req.body.email),
        ]);
        const user = r.rows[0];
        if (user) {
          const token = newToken();
          await withTx(db, async (tx) => {
            await tx.query(
              `INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES ($1,$2, now() + make_interval(mins => $3))`,
              [user.id, sha256(token), RESET_TTL_MIN],
            );
            await enqueue(tx, 'send_email', {
              to: user.email,
              subject: 'Reset your password',
              text: `Reset your password: ${cfg.APP_URL}/reset-password?token=${token}\nThe link expires in ${RESET_TTL_MIN} minutes.`,
            });
          });
        }
        // Same response either way: don't reveal which emails are registered.
        return reply.code(202).send();
      },
    );

    app.post(
      '/reset-password',
      { config: strict, schema: { tags: ['auth'], body: z.object({ token: z.string(), password: Password }) } },
      async (req, reply) => {
        const hash = await hashPassword(req.body.password);
        await withTx(db, async (tx) => {
          const r = await tx.query(
            `UPDATE password_resets SET used_at = now()
             WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now() RETURNING user_id`,
            [sha256(req.body.token)],
          );
          const userId = r.rows[0]?.user_id;
          if (!userId) throw badRequest('invalid_token', 'Reset link is invalid or expired');
          await tx.query('UPDATE users SET password_hash = $2 WHERE id = $1', [userId, hash]);
          await tx.query('UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL', [userId]);
        });
        return reply.code(204).send();
      },
    );
  };
