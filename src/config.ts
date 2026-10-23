import { z } from 'zod';

const bool = z.enum(['true', 'false']).transform((v) => v === 'true');
const optional = z.string().optional().transform((v) => (v ? v : undefined));

const schema = z.object({
  NODE_ENV: z.enum(['development', 'production']).default('development'),
  PORT: z.coerce.number().default(3000),
  LOG_LEVEL: z.string().default('info'),
  PUBLIC_URL: z.string().default('http://localhost:3000'),
  APP_URL: z.string().default('http://localhost:5173'),
  CORS_ORIGINS: z.string().default('').transform((v) => v.split(',').map((s) => s.trim()).filter(Boolean)),
  DATABASE_URL: z.string().default('postgres://postgres:postgres@localhost:5432/saas'),
  JWT_SECRET: z.string().min(32, 'JWT_SECRET must be at least 32 characters'),
  RUN_WORKER: bool.default(true),
  /** Secret used to HMAC PII (opt-out identifiers) before storing. Never rotate casually. */
  PII_PEPPER: z.string().min(16).default('dev-only-pepper-change-me'),

  STRIPE_SECRET_KEY: optional,
  STRIPE_WEBHOOK_SECRET: optional,
  STRIPE_PRICE_PACK_1: optional,
  STRIPE_PRICE_PACK_5: optional,
  STRIPE_PRICE_PACK_15: optional,
  STRIPE_PRICE_PRO_MONTHLY: optional,
  CHECKOUT_SUCCESS_URL: z.string().default('http://localhost:5173/billing/success'),
  CHECKOUT_CANCEL_URL: z.string().default('http://localhost:5173/billing/cancel'),

  MAIL_FROM: z.string().default('Support <no-reply@example.com>'),
  RESEND_API_KEY: optional,

  RATE_LIMIT_PER_MIN: z.coerce.number().int().default(120),
  AUTH_RATE_LIMIT_PER_MIN: z.coerce.number().int().default(10),

  FREE_SEARCHES_PER_DAY: z.coerce.number().int().min(0).default(3),
  SEARCH_PROVIDER: z.string().default('mock'),
  STORAGE_DIR: z.string().default('./storage'),
  MAX_UPLOAD_MB: z.coerce.number().default(8),
});

export type Config = z.infer<typeof schema>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const parsed = schema.safeParse(env);
  if (!parsed.success) {
    const msg = parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('\n');
    throw new Error(`Invalid environment:\n${msg}`);
  }
  const cfg = parsed.data;
  if (cfg.NODE_ENV === 'production' && !cfg.STRIPE_SECRET_KEY) {
    throw new Error('STRIPE_SECRET_KEY is required in production');
  }
  if (cfg.NODE_ENV === 'production' && cfg.PII_PEPPER === 'dev-only-pepper-change-me') {
    throw new Error('PII_PEPPER must be set in production');
  }
  return cfg;
}
