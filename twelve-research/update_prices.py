#!/usr/bin/env python3
"""Pull the latest closing prices into the cache.

    .venv/bin/python update_prices.py

One call covers the whole US market, so this is cheap enough to run nightly on a
schedule. Pages also refresh prices themselves when the cache is over 12 hours
old, so this is a convenience rather than a requirement.
"""

from dotenv import load_dotenv

load_dotenv()

import db      # noqa: E402  (after load_dotenv so DATABASE_URL is set)
import prices  # noqa: E402

if __name__ == "__main__":
    db.init()
    if not prices.configured():
        raise SystemExit("MARKET_DATA_KEY isn't set, so there is nothing to fetch.")
    stored = prices.refresh(force=True)
    print(f"Stored closes for {stored:,} tickers.")
