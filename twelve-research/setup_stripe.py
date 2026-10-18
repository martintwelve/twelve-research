#!/usr/bin/env python3
"""Create the two Twelve Research membership prices in Stripe, once.

    .venv/bin/python setup_stripe.py

Reads STRIPE_SECRET_KEY from .env, creates (or finds) the products and monthly
prices, and prints the two lines to paste into .env. Safe to re-run: it looks up
existing prices by lookup key instead of creating duplicates.
"""

import os

import stripe
from dotenv import load_dotenv

import billing

load_dotenv()
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

PLANS = {
    "research": ("Twelve Research — Research", "Every research report in full, plus the archive."),
    "intelligence": ("Twelve Research — Twelve Intelligence",
                     "Live entries by text and email with thesis and rationale, plus everything in Research."),
}


def ensure(tier):
    key = f"twelve_{tier}_monthly"
    existing = stripe.Price.list(lookup_keys=[key], limit=1).data
    if existing:
        print(f"  found existing price for {tier}: {existing[0].id}")
        return existing[0].id

    name, description = PLANS[tier]
    product = stripe.Product.create(name=name, description=description)
    price = stripe.Price.create(
        product=product.id, lookup_key=key, currency="usd",
        unit_amount=billing.PRICE_CENTS[tier], recurring={"interval": "month"})
    print(f"  created {tier}: {price.id} (${billing.PRICE_CENTS[tier] / 100:.2f}/month)")
    return price.id


def main():
    if not stripe.api_key:
        raise SystemExit("Set STRIPE_SECRET_KEY in .env first.")
    mode = "LIVE" if stripe.api_key.startswith("sk_live") else "test"
    print(f"Setting up prices in {mode} mode…")
    ids = {tier: ensure(tier) for tier in billing.TIERS}

    print("\nPaste these into .env (and into Render's environment):\n")
    for tier, price_id in ids.items():
        print(f"STRIPE_PRICE_{tier.upper()}={price_id}")


if __name__ == "__main__":
    main()
