import type { FastifyInstance, FastifyRequest } from 'fastify';
import fp from 'fastify-plugin';
import type { Db } from '../db.js';
import { forbidden, unauthorized } from '../lib/errors.js';

export interface AuthUser {
  id: string;
  email: string;
  role: 'user' | 'admin';
}

declare module 'fastify' {
  interface FastifyRequest {
    auth: AuthUser | null;
  }
  interface FastifyInstance {
    authenticate: (req: FastifyRequest) => Promise<void>;
    requireAdmin: (req: FastifyRequest) => Promise<void>;
  }
}

export const ACCESS_TOKEN_TTL = '15m';

/**
 * Verifies the Bearer access token and loads the user (so deleted/demoted users are cut off
 * immediately instead of when their token expires).
 */
export default fp(async (app: FastifyInstance, opts: { db: Db }) => {
  app.decorateRequest('auth', null);

  app.decorate('authenticate', async (req: FastifyRequest) => {
    const header = req.headers.authorization;
    if (!header?.startsWith('Bearer ')) throw unauthorized();
    let sub: string;
    try {
      sub = app.jwt.verify<{ sub: string }>(header.slice(7)).sub;
    } catch {
      throw unauthorized('Invalid or expired token');
    }
    const r = await opts.db.query<AuthUser>(
      'SELECT id, email, role FROM users WHERE id = $1 AND deleted_at IS NULL',
      [sub],
    );
    if (!r.rows[0]) throw unauthorized('Invalid or expired token');
    req.auth = r.rows[0];
  });

  app.decorate('requireAdmin', async (req: FastifyRequest) => {
    await app.authenticate(req);
    if (req.auth?.role !== 'admin') throw forbidden();
  });
});

/** For handlers behind `authenticate`. */
export function authUser(req: FastifyRequest): AuthUser {
  if (!req.auth) throw unauthorized();
  return req.auth;
}
