"""Stripe: the two membership tiers and who is on them.

    research      $10/month — every report, full archive
    intelligence  $25/month — live entries by text and email, plus everything above

Stripe remains the only record of who is a member. Set the two price IDs in the
environment (setup_stripe.py creates them); without them every paying member is
treated as Research, so the site degrades to one tier rather than breaking.
"""

import os

import stripe

TIERS = ("research", "intelligence")
RANK = {"none": 0, "research": 1, "intelligence": 2}
ACTIVE_STATUSES = {"active", "trialing"}

PRICE_CENTS = {"research": 1000, "intelligence": 2500}
LABEL = {"research": "Research", "intelligence": "Twelve Intelligence", "none": "None"}


def price_id(tier):
    return os.environ.get(f"STRIPE_PRICE_{tier.upper()}")


def configured():
    return bool(stripe.api_key)


def line_item(tier):
    """A configured Price when there is one, otherwise priced inline at checkout."""
    if price_id(tier):
        return {"price": price_id(tier), "quantity": 1}
    return {"quantity": 1, "price_data": {
        "currency": "usd",
        "unit_amount": PRICE_CENTS[tier],
        "recurring": {"interval": "month"},
        "product_data": {"name": f"Twelve Research — {LABEL[tier]}"},
    }}


def tier_of(subscription):
    """Which tier a subscription is on, read from the price it bills."""
    ids = {price_id(t): t for t in TIERS if price_id(t)}
    for item in subscription["items"]["data"]:
        price = item["price"]
        if price["id"] in ids:
            return ids[price["id"]]
        # No configured IDs (or a price created inline): fall back to the amount.
        if not ids and price.get("unit_amount"):
            return "intelligence" if price["unit_amount"] >= PRICE_CENTS["intelligence"] else "research"
    return "research"


def find_membership(email):
    """(customer_id, tier) for an email address; tier is 'none' when not subscribed."""
    if not configured() or not email:
        return None, "none"
    best, customer_id = "none", None
    for customer in stripe.Customer.list(email=email, limit=10).auto_paging_iter():
        for sub in stripe.Subscription.list(customer=customer.id, status="all", limit=10).auto_paging_iter():
            if sub.status in ACTIVE_STATUSES:
                tier = tier_of(sub)
                if RANK[tier] >= RANK[best]:
                    best, customer_id = tier, customer.id
    return customer_id, best


def members_on(tier):
    """Email addresses with an active subscription at this tier or above."""
    if not configured():
        return []
    emails = []
    for sub in stripe.Subscription.list(status="active", limit=100, expand=["data.customer"]).auto_paging_iter():
        if RANK[tier_of(sub)] >= RANK[tier]:
            email = getattr(sub.customer, "email", None)
            if email:
                emails.append(email.lower())
    return sorted(set(emails))
