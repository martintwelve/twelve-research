"""Portfolio maths: positions in, P&L out.

Two books share this code: the Twelve model portfolio (owner "book", managed
from the Desk) and each member's own positions. Prices are end-of-day closes,
so every number here is stated as of a close, never as a live quote.
"""

import db
import prices


def _row(position, closes):
    ticker = position["ticker"]
    shares, cost = position["shares"], position["cost_basis"]
    cost_value = shares * cost
    closed = position["closed_on"] is not None

    if closed:
        last, asof = position["close_price"], position["closed_on"]
    else:
        last, asof = closes.get(ticker, (None, None))

    value = shares * last if last is not None else None
    pnl = value - cost_value if value is not None else None
    return {
        **position,
        "cost_value": cost_value,
        "last": last,
        "asof": asof,
        "value": value,
        "pnl": pnl,
        "pnl_pct": (last / cost - 1) * 100 if last and cost else None,
        "closed": closed,
    }


def build(owner):
    """Rows plus totals for one owner's book."""
    positions = db.positions_for(owner)
    open_tickers = [p["ticker"] for p in positions if p["closed_on"] is None]
    closes = prices.closes_for(open_tickers) if open_tickers else {}

    rows = [_row(p, closes) for p in positions]
    live = [r for r in rows if not r["closed"]]
    done = [r for r in rows if r["closed"]]

    cost = sum(r["cost_value"] for r in live)
    value = sum(r["value"] for r in live if r["value"] is not None)
    priced = [r for r in live if r["value"] is not None]
    unrealised = sum(r["pnl"] for r in priced)
    realised = sum(r["pnl"] for r in done if r["pnl"] is not None)
    priced_cost = sum(r["cost_value"] for r in priced)
    asof = max((r["asof"] for r in priced if r["asof"]), default=None)

    return {
        "rows": rows, "open": live, "closed": done,
        "cost": cost, "value": value,
        "unrealised": unrealised, "realised": realised,
        # Return on cost, not a time-weighted return: it says what the money put
        # in has done, which is the honest claim for a book of entries.
        "return_pct": (unrealised / priced_cost * 100) if priced_cost else None,
        "unpriced": [r["ticker"] for r in live if r["value"] is None],
        "asof": asof,
        "priced": bool(priced),
    }
