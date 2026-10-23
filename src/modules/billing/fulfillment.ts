import type { Config } from '../../config.js';
import type { Db, Tx } from '../../db.js';
import { withTx } from '../../db.js';
import { applyCredits } from '../../lib/credits.js';
import { findProduct } from './products.js';

/** One-time pack purchase. `refId` = the checkout session id => replays are no-ops. */
export async function grantPack(tx: Tx, cfg: Config, userId: string, productId: string, refId: string) {
  const product = findProduct(cfg, productId);
  if (!product || product.kind !== 'pack') throw new Error(`unknown pack "${productId}"`);
  return applyCredits(tx, userId, product.credits, `purchase:${product.id}`, refId);
}

/** Credits for one paid subscription period. `refId` = invoice id. */
export async function grantSubscriptionPeriod(tx: Tx, cfg: Config, userId: string, productId: string, refId: string) {
  const product = findProduct(cfg, productId);
  if (!product || product.kind !== 'subscription') throw new Error(`unknown subscription "${productId}"`);
  return applyCredits(tx, userId, product.credits, `subscription:${product.id}`, refId);
}

export async function upsertSubscription(
  q: Pick<Tx, 'query'>,
  s: {
    userId: string;
    stripeSubscriptionId: string;
    plan: string;
    status: string;
    currentPeriodEnd: Date | null;
    cancelAtPeriodEnd: boolean;
  },
) {
  await q.query(
    `INSERT INTO subscriptions (user_id, stripe_subscription_id, plan, status, current_period_end, cancel_at_period_end)
     VALUES ($1,$2,$3,$4,$5,$6)
     ON CONFLICT (stripe_subscription_id) DO UPDATE
       SET status=EXCLUDED.status, current_period_end=EXCLUDED.current_period_end,
           cancel_at_period_end=EXCLUDED.cancel_at_period_end, plan=EXCLUDED.plan, updated_at=now()`,
    [s.userId, s.stripeSubscriptionId, s.plan, s.status, s.currentPeriodEnd, s.cancelAtPeriodEnd],
  );
}

export async function hasActiveSubscription(db: Pick<Db, 'query'>, userId: string): Promise<boolean> {
  const r = await db.query(
    `SELECT 1 FROM subscriptions WHERE user_id=$1 AND status IN ('active','trialing') LIMIT 1`,
    [userId],
  );
  return r.rowCount! > 0;
}

export { withTx };
