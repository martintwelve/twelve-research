"""Storage for live alerts and members' phone numbers.

Postgres in production (DATABASE_URL), SQLite on this Mac. Stripe stays the
source of truth for who is a member and which tier they are on; this database
only holds the alerts themselves and the phone numbers members have confirmed
for SMS, with a record of their consent.
"""

import datetime as dt
import os
import secrets
from pathlib import Path

from sqlalchemy import create_engine, text

CODE_TTL = dt.timedelta(minutes=15)


def _url():
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres://"):  # Render hands out the older prefix
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url or f"sqlite:///{Path(__file__).parent / 'twelve.db'}"


engine = create_engine(_url(), pool_pre_ping=True, future=True)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TIMESTAMP NOT NULL,
        ticker TEXT NOT NULL,
        action TEXT NOT NULL,
        level TEXT,
        thesis TEXT NOT NULL,
        rationale TEXT,
        author TEXT,
        sent_email INTEGER DEFAULT 0,
        sent_sms INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        name TEXT,
        created_at TIMESTAMP NOT NULL,
        last_seen_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT NOT NULL,
        ticker TEXT NOT NULL,
        shares REAL NOT NULL,
        cost_basis REAL NOT NULL,
        opened_on DATE NOT NULL,
        closed_on DATE,
        close_price REAL,
        alert_id INTEGER,
        note TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS closes (
        ticker TEXT PRIMARY KEY,
        close REAL NOT NULL,
        asof DATE NOT NULL,
        fetched_at TIMESTAMP NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS phones (
        email TEXT PRIMARY KEY,
        phone TEXT NOT NULL,
        consent_at TIMESTAMP,
        consent_ip TEXT,
        verified_at TIMESTAMP,
        code TEXT,
        code_expires TIMESTAMP,
        opted_out_at TIMESTAMP
    )""",
]

# Postgres spells the autoincrementing key differently.
SCHEMA_PG = [s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY") for s in SCHEMA]


def init():
    statements = SCHEMA_PG if engine.dialect.name == "postgresql" else SCHEMA
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ----------------------------------------------------------------- alerts

def add_alert(ticker, action, level, thesis, rationale, author):
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO alerts (created_at, ticker, action, level, thesis, rationale, author)
            VALUES (:created_at, :ticker, :action, :level, :thesis, :rationale, :author)
            RETURNING id"""),
            {"created_at": now(), "ticker": ticker.upper().strip(), "action": action,
             "level": level, "thesis": thesis, "rationale": rationale, "author": author}).first()
    return row[0]


def mark_sent(alert_id, emails, texts):
    with engine.begin() as conn:
        conn.execute(text("UPDATE alerts SET sent_email = :e, sent_sms = :s WHERE id = :id"),
                     {"e": emails, "s": texts, "id": alert_id})


def _row(mapping):
    """SQLite hands timestamps back as strings; Postgres as datetimes. Normalise."""
    row = dict(mapping)
    for key in ("created_at", "consent_at", "verified_at", "code_expires", "opted_out_at"):
        if isinstance(row.get(key), str):
            row[key] = dt.datetime.fromisoformat(row[key])
    return row


def recent_alerts(limit=50):
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT :limit"), {"limit": limit})
        return [_row(r._mapping) for r in rows]


def delete_alert(alert_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alerts WHERE id = :id"), {"id": alert_id})


# ----------------------------------------------------------------- phones

def get_phone(email):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT * FROM phones WHERE email = :email"),
                           {"email": email.lower()}).first()
        return _row(row._mapping) if row else None


def start_verification(email, phone, ip):
    """Store the number unverified and return the code to text to it."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    values = {"email": email.lower(), "phone": phone, "consent_at": now(), "consent_ip": ip,
              "code": code, "code_expires": now() + CODE_TTL}
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM phones WHERE email = :email"), {"email": values["email"]}).first()
        if exists:
            conn.execute(text("""UPDATE phones SET phone = :phone, consent_at = :consent_at,
                consent_ip = :consent_ip, code = :code, code_expires = :code_expires,
                verified_at = NULL, opted_out_at = NULL WHERE email = :email"""), values)
        else:
            conn.execute(text("""INSERT INTO phones (email, phone, consent_at, consent_ip, code, code_expires)
                VALUES (:email, :phone, :consent_at, :consent_ip, :code, :code_expires)"""), values)
    return code


def confirm_verification(email, code):
    record = get_phone(email)
    if not record or not record["code"] or record["code"] != code.strip():
        return False
    if record["code_expires"] and record["code_expires"] < now():
        return False
    with engine.begin() as conn:
        conn.execute(text("UPDATE phones SET verified_at = :now, code = NULL WHERE email = :email"),
                     {"now": now(), "email": email.lower()})
    return True


def opt_out(email):
    with engine.begin() as conn:
        conn.execute(text("UPDATE phones SET opted_out_at = :now WHERE email = :email"),
                     {"now": now(), "email": email.lower()})


def opt_out_by_phone(phone):
    """Used when someone replies STOP."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE phones SET opted_out_at = :now WHERE phone = :phone"),
                     {"now": now(), "phone": phone})


def verified_numbers(emails):
    """Confirmed, still-opted-in numbers belonging to the given members."""
    if not emails:
        return {}
    lowered = [e.lower() for e in emails]
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT email, phone FROM phones WHERE verified_at IS NOT NULL AND opted_out_at IS NULL"))
        return {r.email: r.phone for r in rows if r.email in lowered}


# ------------------------------------------------------------------ users

def upsert_user(email, name=None):
    """Create the account on first sign-in; keep the name if we already have one."""
    email = email.lower()
    with engine.begin() as conn:
        row = conn.execute(text("SELECT name FROM users WHERE email = :email"), {"email": email}).first()
        if row:
            conn.execute(text("UPDATE users SET last_seen_at = :now, name = COALESCE(:name, name) WHERE email = :email"),
                         {"now": now(), "name": name or None, "email": email})
            return name or row[0]
        conn.execute(text("INSERT INTO users (email, name, created_at, last_seen_at) VALUES (:email, :name, :now, :now)"),
                     {"email": email, "name": name, "now": now()})
        return name


def get_user(email):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT * FROM users WHERE email = :email"), {"email": email.lower()}).first()
        return _row(row._mapping) if row else None


def set_name(email, name):
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET name = :name WHERE email = :email"),
                     {"name": name, "email": email.lower()})


# -------------------------------------------------------------- positions

BOOK = "book"   # owner value for the Twelve model portfolio


def add_position(owner, ticker, shares, cost_basis, opened_on, alert_id=None, note=""):
    with engine.begin() as conn:
        row = conn.execute(text("""
            INSERT INTO positions (owner, ticker, shares, cost_basis, opened_on, alert_id, note)
            VALUES (:owner, :ticker, :shares, :cost_basis, :opened_on, :alert_id, :note)
            RETURNING id"""),
            {"owner": owner.lower(), "ticker": ticker.upper().strip(), "shares": float(shares),
             "cost_basis": float(cost_basis), "opened_on": opened_on, "alert_id": alert_id,
             "note": note}).first()
    return row[0]


def positions_for(owner):
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT * FROM positions WHERE owner = :owner ORDER BY closed_on IS NULL DESC, opened_on DESC"),
            {"owner": owner.lower()})
        return [_row(r._mapping) for r in rows]


def close_position(position_id, owner, price, closed_on):
    with engine.begin() as conn:
        conn.execute(text("""UPDATE positions SET closed_on = :closed_on, close_price = :price
                             WHERE id = :id AND owner = :owner"""),
                     {"closed_on": closed_on, "price": float(price), "id": position_id, "owner": owner.lower()})


def delete_position(position_id, owner):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM positions WHERE id = :id AND owner = :owner"),
                     {"id": position_id, "owner": owner.lower()})


# ----------------------------------------------------------------- closes

def save_closes(rows, asof):
    stamp = now()
    with engine.begin() as conn:
        for ticker, close in rows:
            existing = conn.execute(text("SELECT 1 FROM closes WHERE ticker = :t"), {"t": ticker}).first()
            params = {"t": ticker, "c": float(close), "a": asof, "f": stamp}
            if existing:
                conn.execute(text("UPDATE closes SET close = :c, asof = :a, fetched_at = :f WHERE ticker = :t"), params)
            else:
                conn.execute(text("INSERT INTO closes (ticker, close, asof, fetched_at) VALUES (:t, :c, :a, :f)"), params)


def get_closes(tickers):
    if not tickers:
        return {}
    wanted = [t.upper() for t in tickers]
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT ticker, close, asof FROM closes"))
        out = {}
        for r in rows:
            if r.ticker in wanted:
                asof = dt.date.fromisoformat(r.asof) if isinstance(r.asof, str) else r.asof
                out[r.ticker] = (r.close, asof)
        return out


def closes_stale(max_age):
    """True when the newest cached close is older than max_age (or there are none)."""
    with engine.begin() as conn:
        row = conn.execute(text("SELECT MAX(fetched_at) FROM closes")).first()
    newest = row[0] if row else None
    if isinstance(newest, str):
        newest = dt.datetime.fromisoformat(newest)
    return newest is None or (now() - newest) > max_age
