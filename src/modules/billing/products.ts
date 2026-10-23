import type { Config } from '../../config.js';

export interface Product {
  id: 'pack_1' | 'pack_5' | 'pack_15' | 'pro_monthly';
  kind: 'pack' | 'subscription';
  title: string;
  /** Credits granted once (pack) or on every paid billing period (subscription). */
  credits: number;
  /** Display only — Stripe's Price is the source of truth for what is charged. */
  priceCents: number;
  currency: 'usd';
  stripePriceId?: string;
}

export function getProducts(cfg: Config): Product[] {
  return [
    { id: 'pack_1', kind: 'pack', title: '1 report', credits: 1, priceCents: 999, currency: 'usd', stripePriceId: cfg.STRIPE_PRICE_PACK_1 },
    { id: 'pack_5', kind: 'pack', title: '5 reports', credits: 5, priceCents: 2999, currency: 'usd', stripePriceId: cfg.STRIPE_PRICE_PACK_5 },
    { id: 'pack_15', kind: 'pack', title: '15 reports', credits: 15, priceCents: 5999, currency: 'usd', stripePriceId: cfg.STRIPE_PRICE_PACK_15 },
    { id: 'pro_monthly', kind: 'subscription', title: 'Pro — 20 reports / month', credits: 20, priceCents: 2999, currency: 'usd', stripePriceId: cfg.STRIPE_PRICE_PRO_MONTHLY },
  ];
}

export const findProduct = (cfg: Config, id: string) => getProducts(cfg).find((p) => p.id === id);
