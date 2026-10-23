import cors from '@fastify/cors';
import helmet from '@fastify/helmet';
import jwt from '@fastify/jwt';
import multipart from '@fastify/multipart';
import rateLimit from '@fastify/rate-limit';
import swagger from '@fastify/swagger';
import swaggerUi from '@fastify/swagger-ui';
import Fastify, { type FastifyInstance } from 'fastify';
import {
  hasZodFastifySchemaValidationErrors,
  jsonSchemaTransform,
  serializerCompiler,
  validatorCompiler,
} from 'fastify-type-provider-zod';
import type { Config } from './config.js';
import type { Ctx } from './context.js';
import { createDb } from './db.js';
import { Worker } from './jobs/queue.js';
import { registerJobs } from './jobs/handlers.js';
import { AppError } from './lib/errors.js';
import { createMailer } from './lib/mailer.js';
import { LocalStorage } from './lib/storage.js';
import { adminRoutes } from './modules/admin/routes.js';
import { authRoutes } from './modules/auth/routes.js';
import { createBilling } from './modules/billing/provider.js';
import { billingRoutes } from './modules/billing/routes.js';
import { meRoutes } from './modules/me/routes.js';
import { privacyRoutes } from './modules/privacy/routes.js';
import { searchRoutes } from './modules/searches/routes.js';
import { uploadRoutes } from './modules/uploads/routes.js';
import authPlugin from './plugins/auth.js';
import { createSearchProvider } from './providers/index.js';

export interface BuildOptions {
  cfg: Config;
  /** Replace any dependency (e.g. an alternative provider). */
  overrides?: Partial<Ctx>;
  logger?: boolean;
}

export async function buildApp({ cfg, overrides = {}, logger = true }: BuildOptions): Promise<{ app: FastifyInstance; ctx: Ctx }> {
  const app = Fastify({
    logger: logger && {
      level: cfg.LOG_LEVEL,
      redact: ['req.headers.authorization', 'req.headers.cookie', 'req.headers["stripe-signature"]'],
    },
    trustProxy: true,
  });
  app.setValidatorCompiler(validatorCompiler);
  app.setSerializerCompiler(serializerCompiler);

  const db = overrides.db ?? createDb(cfg.DATABASE_URL);
  const ctx: Ctx = {
    cfg,
    db,
    mailer: overrides.mailer ?? createMailer(cfg, app.log),
    storage: overrides.storage ?? new LocalStorage(cfg.STORAGE_DIR),
    billing: overrides.billing ?? createBilling(cfg, db),
    searchProvider: overrides.searchProvider ?? createSearchProvider(cfg),
    worker: overrides.worker ?? new Worker(db, app.log),
  };
  registerJobs(ctx);

  await app.register(helmet);
  await app.register(cors, { origin: cfg.CORS_ORIGINS.length ? cfg.CORS_ORIGINS : false, credentials: true });
  await app.register(rateLimit, { max: cfg.RATE_LIMIT_PER_MIN, timeWindow: '1 minute' });
  await app.register(jwt, { secret: cfg.JWT_SECRET });
  await app.register(multipart, { limits: { fileSize: cfg.MAX_UPLOAD_MB * 1024 * 1024, files: 1 }, throwFileSizeLimit: true });
  await app.register(authPlugin, { db });

  await app.register(swagger, {
    openapi: {
      info: { title: 'SaaS Search API', version: '1.0.0' },
      components: { securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer', bearerFormat: 'JWT' } } },
    },
    transform: jsonSchemaTransform,
  });
  await app.register(swaggerUi, { routePrefix: '/docs' });

  app.setErrorHandler((err: any, req, reply) => {
    if (err instanceof AppError) {
      return reply.code(err.statusCode).send({ error: { code: err.code, message: err.message, details: err.details } });
    }
    if (hasZodFastifySchemaValidationErrors(err)) {
      return reply.code(400).send({
        error: {
          code: 'validation_error',
          message: 'Request validation failed',
          details: err.validation.map((v) => ({ path: v.instancePath, message: v.message })),
        },
      });
    }
    const status = typeof err.statusCode === 'number' ? err.statusCode : 500;
    if (status < 500) {
      return reply.code(status).send({ error: { code: err.code ?? 'bad_request', message: err.message } });
    }
    req.log.error({ err }, 'unhandled error');
    return reply.code(500).send({ error: { code: 'internal', message: 'Internal server error' } });
  });
  app.setNotFoundHandler((_req, reply) => reply.code(404).send({ error: { code: 'not_found', message: 'Route not found' } }));

  app.get('/healthz', { schema: { hide: true } }, async () => ({ ok: true }));
  app.get('/readyz', { schema: { hide: true } }, async (_req, reply) => {
    try {
      await db.query('SELECT 1');
      return { ok: true };
    } catch {
      return reply.code(503).send({ ok: false });
    }
  });

  await app.register(
    async (v1) => {
      await v1.register(authRoutes(ctx), { prefix: '/auth' });
      await v1.register(meRoutes(ctx), { prefix: '/me' });
      await v1.register(billingRoutes(ctx), { prefix: '/billing' });
      await v1.register(uploadRoutes(ctx), { prefix: '/uploads' });
      await v1.register(searchRoutes(ctx), { prefix: '/searches' });
      await v1.register(privacyRoutes(ctx), { prefix: '/privacy' });
      await v1.register(adminRoutes(ctx), { prefix: '/admin' });
    },
    { prefix: '/v1' },
  );

  app.addHook('onClose', async () => {
    await ctx.worker.stop();
    if (!overrides.db) await db.end();
  });

  return { app, ctx };
}
