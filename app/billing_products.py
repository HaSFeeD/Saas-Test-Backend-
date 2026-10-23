from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass
class Product:
    id: str  # pack_1 | pack_5 | pack_15 | pro_monthly
    kind: str  # "pack" | "subscription"
    title: str
    credits: int  # granted once (pack) or per paid billing period (subscription)
    price_cents: int  # display only; Stripe's Price is the source of truth
    currency: str
    stripe_price_id: str | None


def get_products(s: Settings) -> list[Product]:
    return [
        Product("pack_1", "pack", "1 report", 1, 999, "usd", s.stripe_price_pack_1),
        Product("pack_5", "pack", "5 reports", 5, 2999, "usd", s.stripe_price_pack_5),
        Product("pack_15", "pack", "15 reports", 15, 5999, "usd", s.stripe_price_pack_15),
        Product("pro_monthly", "subscription", "Pro - 20 reports / month", 20, 2999, "usd", s.stripe_price_pro_monthly),
    ]


def find_product(s: Settings, product_id: str) -> Product | None:
    return next((p for p in get_products(s) if p.id == product_id), None)
