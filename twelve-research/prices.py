"""End-of-day closing prices.

One provider (Massive, formerly Polygon). Its daily market summary returns every
US ticker for a date in a single call, so a day's prices cost one request — well
inside the free plan. Closes are cached in the database and refreshed once a day.

Without MARKET_DATA_KEY the site still works: portfolios show cost basis and say
prices aren't configured yet, rather than showing wrong numbers.
"""

import datetime as dt
import os

import requests

import db

HOST = os.environ.get("MARKET_DATA_HOST", "https://api.massive.com")
GROUPED = "/v2/aggs/grouped/locale/us/market/stocks/{date}"
PREV_CLOSE = "/v2/aggs/ticker/{ticker}/prev"
STALE_AFTER = dt.timedelta(hours=12)


def configured():
    return bool(os.environ.get("MARKET_DATA_KEY"))


def _get(path, **params):
    key = os.environ.get("MARKET_DATA_KEY")
    r = requests.get(HOST + path, params={"adjusted": "true", **params},
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _last_session(today=None):
    """The most recent weekday. Holidays just come back empty and we step further back."""
    day = (today or dt.date.today()) - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def refresh(tickers=None, force=False):
    """Update cached closes. Returns the number of tickers stored."""
    if not configured():
        return 0
    if not force and not db.closes_stale(STALE_AFTER):
        return 0

    day = _last_session()
    for _ in range(5):  # walk back over holidays
        try:
            payload = _get(GROUPED.format(date=day.isoformat()))
        except requests.RequestException as exc:
            print(f"[prices] {day}: {exc}", flush=True)
            return 0
        if payload.get("resultsCount"):
            break
        day -= dt.timedelta(days=1)
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
    else:
        return 0

    wanted = {t.upper() for t in tickers} if tickers else None
    rows = [(row["T"], row["c"]) for row in payload.get("results", [])
            if row.get("c") and (wanted is None or row["T"] in wanted)]
    db.save_closes(rows, day)
    return len(rows)


def fetch_one(ticker):
    """Previous close for a single ticker — used for one a daily summary missed."""
    if not configured():
        return None
    try:
        payload = _get(PREV_CLOSE.format(ticker=ticker.upper()))
    except requests.RequestException:
        return None
    results = payload.get("results") or []
    if not results or not results[0].get("c"):
        return None
    close = results[0]["c"]
    asof = dt.datetime.fromtimestamp(results[0]["t"] / 1000, dt.timezone.utc).date()
    db.save_closes([(ticker.upper(), close)], asof)
    return close


def closes_for(tickers):
    """{TICKER: (close, asof)} for the tickers we can price."""
    tickers = sorted({t.upper() for t in tickers})
    if not tickers:
        return {}
    refresh(tickers)
    known = db.get_closes(tickers)
    for ticker in tickers:
        if ticker not in known and configured():
            if fetch_one(ticker) is not None:
                known = db.get_closes(tickers)
    return known
