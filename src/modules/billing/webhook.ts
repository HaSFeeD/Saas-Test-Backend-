import type { FastifyBaseLogger } from 'fastify';
import type Stripe from 'stripe';
import type { Config } from '../../config.js';
import type { Db, Tx } from '../../db.js';
import { withTx } from '../../db.js';
import { grantPack, grantSubscriptionPeriod, upsertSubscription } from './fulfillment.js';
import { getProducts } from './products.js';

/**
 * Applies a verified Stripe event. Idempotent: the event id is recorded in the same transaction
 * as its effects, so a retry either sees the effects + the record, or neither.
 * Throwing => rollback => Stripe retries later.
 */
export async function handleStripeEvent(db: Db, cfg: Config, log: FastifyBaseLogger, event: Stripe.Event) {
  return withTx(db, async (tx) => {
    const seen = await tx.query(
      'INSERT INTO webhook_events (id, type) VALUES ($1,$2) ON CONFLICT DO NOTHING',
      [event.id, event.type],
    );
    if (!seen.rowCount) return { duplicate: true };

    switch (event.type) {
      case 'checkout.session.completed':
      case 'checkout.session.async_payment_succeeded':
        await onCheckoutCompleted(tx, cfg, log, event.data.object);
        break;
      case 'invoice.paid':
        await onInvoicePaid(tx, cfg, log, event.data.object);
        break;
      case 'customer.subscription.created':
      case 'customer.subscription.updated':
      case 'customer.subscription.deleted':
        await onSubscriptionChanged(tx, cfg, log, event.data.object);
        break;
      default:
        break;
    }
    return { duplicate: false };
  });
}

async function userIdByCustomer(tx: Tx, customer: string | { id: string } | null | undefined) {
  const id = typeof customer === 'string' ? customer : customer?.id;
  if (!id) return null;
  const r = await tx.query<{ id: string }>('SELECT id FROM users WHERE stripe_customer_id = $1', [id]);
  return r.rows[0]?.id ?? null;
}

async function onCheckoutCompleted(tx: Tx, cfg: Config, log: FastifyBaseLogger, s: Stripe.Checkout.Session) {
  // Subscriptions are fulfilled from invoice.paid; only one-time payments are handled here.
  if (s.mode !== 'payment' || s.payment_status !== 'paid') return;
  const userId = s.metadata?.user_id ?? (await userIdByCustomer(tx, s.customer));
  const productId = s.metadata?.product_id;
  if (!userId || !productId) {
    log.warn({ session: s.id }, 'checkout session without user/product metadata, ignored');
    return;
  }
  await grantPack(tx, cfg, userId, productId, s.id);
}

async function onInvoicePaid(tx: Tx, cfg: Config, log: FastifyBaseLogger, inv: Stripe.Invoice) {
  if (!inv.amount_paid || !inv.id) return;
  const userId = await userIdByCustomer(tx, inv.customer);
  if (!userId) {
    log.warn({ invoice: inv.id }, 'invoice.paid for unknown customer, ignored');
    return;
  }
  const priceIds = inv.lines.data.map((l) => l.pricing?.price_details?.price).filter(Boolean);
  const product = getProducts(cfg).find((p) => p.kind === 'subscription' && p.stripePriceId && priceIds.includes(p.stripePriceId));
  if (!product) {
    log.warn({ invoice: inv.id }, 'invoice.paid for a price that is not a known subscription, ignored');
    return;
  }
  await grantSubscriptionPeriod(tx, cfg, userId, product.id, inv.id);
}

async function onSubscriptionChanged(tx: Tx, cfg: Config, log: FastifyBaseLogger, sub: Stripe.Subscription) {
  const userId = sub.metadata?.user_id ?? (await userIdByCustomer(tx, sub.customer));
  if (!userId) {
    log.warn({ subscription: sub.id }, 'subscription for unknown user, ignored');
    return;
  }
  const item = sub.items.data[0];
  const priceId = item?.price.id;
  const plan = getProducts(cfg).find((p) => p.kind === 'subscription' && p.stripePriceId === priceId)?.id ?? sub.metadata?.product_id ?? 'unknown';
  const periodEnd = item?.current_period_end;
  await upsertSubscription(tx, {
    userId,
    stripeSubscriptionId: sub.id,
    plan,
    status: sub.status,
    currentPeriodEnd: periodEnd ? new Date(periodEnd * 1000) : null,
    cancelAtPeriodEnd: sub.cancel_at_period_end,
  });
}
