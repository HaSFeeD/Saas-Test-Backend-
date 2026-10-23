import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import type { Ctx } from '../../context.js';
import { badRequest, conflict } from '../../lib/errors.js';
import { bearer, Pagination } from '../../lib/schemas.js';
import { authUser } from '../../plugins/auth.js';
import { hasActiveSubscription } from './fulfillment.js';
import { findProduct, getProducts } from './products.js';
import { handleStripeEvent } from './webhook.js';

export const billingRoutes =
  (ctx: Ctx): FastifyPluginAsyncZod =>
  async (app) => {
    const { db, cfg, billing } = ctx;

    app.get('/products', { schema: { tags: ['billing'] } }, async () => ({
      provider: billing.name,
      items: getProducts(cfg).map(({ stripePriceId: _omit, ...p }) => p),
    }));

    app.post(
      '/checkout',
      {
        onRequest: app.authenticate,
        schema: { tags: ['billing'], security: bearer, body: z.object({ productId: z.string() }) },
      },
      async (req) => {
        const auth = authUser(req);
        const product = findProduct(cfg, req.body.productId);
        if (!product) throw badRequest('unknown_product', 'Unknown product');
        if (product.kind === 'subscription' && (await hasActiveSubscription(db, auth.id))) {
          throw conflict('already_subscribed', 'You already have an active subscription. Use the billing portal to manage it.');
        }
        const u = (await db.query('SELECT stripe_customer_id FROM users WHERE id=$1', [auth.id])).rows[0];
        const { url, customerId } = await billing.createCheckout(
          { id: auth.id, email: auth.email, stripeCustomerId: u.stripe_customer_id },
          product,
        );
        if (customerId) await db.query('UPDATE users SET stripe_customer_id=$2 WHERE id=$1', [auth.id, customerId]);
        return { url };
      },
    );

    app.post(
      '/portal',
      { onRequest: app.authenticate, schema: { tags: ['billing'], security: bearer } },
      async (req) => {
        const u = (await db.query('SELECT stripe_customer_id FROM users WHERE id=$1', [authUser(req).id])).rows[0];
        if (!u.stripe_customer_id && billing.name === 'stripe') throw badRequest('no_customer', 'No billing account yet');
        return billing.createPortal(u.stripe_customer_id);
      },
    );

    app.get(
      '/ledger',
      { onRequest: app.authenticate, schema: { tags: ['billing'], security: bearer, querystring: Pagination } },
      async (req) => {
        const r = await db.query(
          `SELECT id, delta, balance_after AS "balanceAfter", reason, created_at AS "createdAt"
           FROM credit_ledger WHERE user_id=$1 ORDER BY id DESC LIMIT $2 OFFSET $3`,
          [authUser(req).id, req.query.limit, req.query.offset],
        );
        return { items: r.rows };
      },
    );

    // Stripe needs the exact raw bytes to verify the signature, so this scope keeps JSON unparsed.
    await app.register(async (hook) => {
      hook.removeContentTypeParser('application/json');
      hook.addContentTypeParser('application/json', { parseAs: 'buffer' }, (_req, body, done) => done(null, body));
      hook.post('/webhook', { config: { rateLimit: false }, schema: { hide: true } }, async (req) => {
        const event = billing.verifyWebhook(req.body as Buffer, req.headers['stripe-signature'] as string | undefined);
        const res = await handleStripeEvent(db, cfg, req.log, event);
        return { received: true, ...res };
      });
    });
  };
