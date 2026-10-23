import { randomUUID } from 'node:crypto';
import Stripe from 'stripe';
import type { Config } from '../../config.js';
import type { Db } from '../../db.js';
import { withTx } from '../../db.js';
import { badRequest } from '../../lib/errors.js';
import { grantPack, grantSubscriptionPeriod, upsertSubscription } from './fulfillment.js';
import type { Product } from './products.js';

export interface CheckoutUser {
  id: string;
  email: string;
  stripeCustomerId: string | null;
}

export interface BillingProvider {
  readonly name: 'stripe' | 'dev';
  /** Returns the URL to send the user to. May also return a newly created customer id to persist. */
  createCheckout(user: CheckoutUser, product: Product): Promise<{ url: string; customerId?: string }>;
  createPortal(customerId: string): Promise<{ url: string }>;
  /** Verifies the signature and parses the event. Throws on a bad signature. */
  verifyWebhook(raw: Buffer, signature: string | undefined): Stripe.Event;
  cancelCustomerSubscriptions(customerId: string): Promise<void>;
}

export class StripeBilling implements BillingProvider {
  readonly name = 'stripe' as const;
  readonly stripe: Stripe;

  constructor(private cfg: Config, stripe?: Stripe) {
    this.stripe = stripe ?? new Stripe(cfg.STRIPE_SECRET_KEY!);
  }

  async createCheckout(user: CheckoutUser, product: Product) {
    if (!product.stripePriceId) throw badRequest('product_unavailable', `Price for ${product.id} is not configured`);
    let customerId = user.stripeCustomerId ?? undefined;
    let created: string | undefined;
    if (!customerId) {
      const c = await this.stripe.customers.create({ email: user.email, metadata: { user_id: user.id } });
      customerId = created = c.id;
    }
    const metadata = { user_id: user.id, product_id: product.id };
    const session = await this.stripe.checkout.sessions.create({
      mode: product.kind === 'pack' ? 'payment' : 'subscription',
      customer: customerId,
      client_reference_id: user.id,
      line_items: [{ price: product.stripePriceId, quantity: 1 }],
      success_url: this.cfg.CHECKOUT_SUCCESS_URL,
      cancel_url: this.cfg.CHECKOUT_CANCEL_URL,
      metadata,
      ...(product.kind === 'subscription' ? { subscription_data: { metadata } } : {}),
    });
    if (!session.url) throw new Error('Stripe returned no checkout URL');
    return { url: session.url, customerId: created };
  }

  async createPortal(customerId: string) {
    const s = await this.stripe.billingPortal.sessions.create({ customer: customerId, return_url: this.cfg.APP_URL });
    return { url: s.url };
  }

  verifyWebhook(raw: Buffer, signature: string | undefined) {
    if (!signature || !this.cfg.STRIPE_WEBHOOK_SECRET) throw badRequest('bad_signature', 'Missing webhook signature');
    try {
      return this.stripe.webhooks.constructEvent(raw, signature, this.cfg.STRIPE_WEBHOOK_SECRET);
    } catch {
      throw badRequest('bad_signature', 'Invalid webhook signature');
    }
  }

  async cancelCustomerSubscriptions(customerId: string) {
    const subs = await this.stripe.subscriptions.list({ customer: customerId, status: 'active' });
    for (const s of subs.data) await this.stripe.subscriptions.cancel(s.id);
  }
}

/**
 * Local development provider: no Stripe account needed. "Checkout" succeeds immediately and
 * grants the credits. Never used in production (config enforces STRIPE_SECRET_KEY there).
 */
export class DevBilling implements BillingProvider {
  readonly name = 'dev' as const;
  constructor(private cfg: Config, private db: Db) {}

  async createCheckout(user: CheckoutUser, product: Product) {
    await withTx(this.db, async (tx) => {
      if (product.kind === 'pack') {
        await grantPack(tx, this.cfg, user.id, product.id, `dev_${randomUUID()}`);
      } else {
        const subId = `dev_sub_${randomUUID()}`;
        await upsertSubscription(tx, {
          userId: user.id,
          stripeSubscriptionId: subId,
          plan: product.id,
          status: 'active',
          currentPeriodEnd: new Date(Date.now() + 30 * 86_400_000),
          cancelAtPeriodEnd: false,
        });
        await grantSubscriptionPeriod(tx, this.cfg, user.id, product.id, `dev_inv_${randomUUID()}`);
      }
    });
    return { url: this.cfg.CHECKOUT_SUCCESS_URL };
  }
  async createPortal() {
    return { url: this.cfg.APP_URL };
  }
  verifyWebhook(): never {
    throw badRequest('webhooks_disabled', 'Webhooks are not available with the dev billing provider');
  }
  async cancelCustomerSubscriptions() {}
}

export function createBilling(cfg: Config, db: Db): BillingProvider {
  return cfg.STRIPE_SECRET_KEY ? new StripeBilling(cfg) : new DevBilling(cfg, db);
}
