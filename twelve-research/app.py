"""Twelve Research — research site with two Stripe membership tiers.

    Research             $10/month — every report, full archive
    Twelve Intelligence  $25/month — live entries by text and email, plus the above

Articles are Markdown files in content/articles. Stripe is the record of who is
a member and on which tier; the database (db.py) holds only the live alerts and
the phone numbers members have confirmed for SMS.
"""

import base64
import hashlib
import hmac
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

import billing
import charts
import db
import markdown
import notify
import portfolio as portfolio_lib
import prices
import stripe
from dotenv import load_dotenv
from flask import (Flask, abort, flash, has_request_context, redirect,
                   render_template, request, send_from_directory, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

BASE_DIR = Path(__file__).parent
ARTICLES_DIR = BASE_DIR / "content" / "articles"

SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000").rstrip("/")
LOGIN_LINK_MAX_AGE = 30 * 60             # magic links expire after 30 minutes
RECHECK_SUBSCRIPTION_EVERY = 6 * 60 * 60  # re-verify the tier with Stripe every 6 hours
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAIL", "").split(",") if e.strip()}
# Pre-launch holding page. Set COMING_SOON=false in .env to open the site up.
COMING_SOON = os.environ.get("COMING_SOON", "true").strip().lower() not in ("false", "0", "no", "")
# What a visitor can still reach while the site is closed. Signing in stays open
# so the desk is reachable; everything else goes back to the holding page.
OPEN_WHILE_CLOSED = {"home", "static", "data_file", "notify_me",
                     "login", "login_verify", "logout", "sms_inbound"}
ACTIONS = ("entry", "add", "trim", "exit", "watch")
# The Twelve Intelligence promise. Change it here and it changes everywhere.
PRO_TAGLINE = "Institutional-grade trade research and ideas, delivered fast and personally."

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SITE_URL.startswith("https://"),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)
login_tokens = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="login-link")
db.init()


# ---------------------------------------------------------------- articles

def parse_front_matter(text):
    """Split '---' delimited key: value front matter from the Markdown body."""
    meta, body = {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip().strip('"')
        body = match.group(2)
    return meta, body


def load_article(path):
    meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    # Everything above <!-- paywall --> is the free preview.
    preview_md, _, rest_md = body.partition("<!-- paywall -->")
    md = markdown.Markdown(extensions=["extra", "sane_lists", "smarty"])
    render = lambda s: md.reset().convert(s)
    return {
        "slug": path.stem,
        "title": meta.get("title", path.stem.replace("-", " ").title()),
        "date": datetime.strptime(meta["date"], "%Y-%m-%d").date() if meta.get("date") else date.today(),
        "summary": meta.get("summary", ""),
        "tickers": [t.strip() for t in meta.get("tickers", "").split(",") if t.strip()],
        "access": meta.get("access", "members"),  # members | free
        "seeking_alpha_url": meta.get("seeking_alpha_url", ""),
        "substack_url": meta.get("substack_url", ""),
        "preview_html": render(preview_md),
        "full_html": render(body.replace("<!-- paywall -->", "")),
        "has_paywall": bool(rest_md.strip()),
        "draft": meta.get("draft", "false").lower() == "true",
    }


def all_articles():
    # Re-read on each request so new files appear without a restart; cheap at this scale.
    articles = [load_article(p) for p in ARTICLES_DIR.glob("*.md")]
    return sorted((a for a in articles if not a["draft"]), key=lambda a: a["date"], reverse=True)


def get_article(slug):
    return next((a for a in all_articles() if a["slug"] == slug), None)


# -------------------------------------------------------------- membership

def tier():
    return session.get("tier", "none")


def at_least(level):
    return billing.RANK[tier()] >= billing.RANK[level]


def is_admin():
    return bool(session.get("email")) and session["email"].lower() in ADMIN_EMAILS


def sign_in(email, customer_id, member_tier, name=None):
    name = db.upsert_user(email, name)
    session.permanent = True
    session.update(email=email.lower(), customer_id=customer_id, name=name,
                   tier=member_tier, checked_at=time.time())


@app.before_request
def hold_the_door():
    """While COMING_SOON is on, the public sees the holding page and nothing else."""
    if not COMING_SOON or is_admin():
        return
    if request.endpoint in OPEN_WHILE_CLOSED or request.endpoint is None:
        return
    return redirect(url_for("home"))


@app.before_request
def refresh_membership():
    """Periodically re-check the tier with Stripe, so cancellations and upgrades land."""
    email = session.get("email")
    if not email or time.time() - session.get("checked_at", 0) < RECHECK_SUBSCRIPTION_EVERY:
        return
    try:
        customer_id, member_tier = billing.find_membership(email)
    except stripe.StripeError:
        return  # Stripe unreachable: keep the current state, try again next request
    session.update(tier=member_tier, checked_at=time.time(),
                   customer_id=customer_id or session.get("customer_id"))


@app.context_processor
def inject_globals():
    if not has_request_context():
        # Email templates are rendered from the same app, sometimes with no request.
        return {"current_email": None, "name": None, "first_name": "",
                "tier": "none", "is_member": False, "is_pro": False,
                "is_admin": False, "tier_label": billing.LABEL,
                "price": billing.PRICE_CENTS["research"] // 100,
                "pro_price": billing.PRICE_CENTS["intelligence"] // 100,
                "pro_tagline": PRO_TAGLINE, "coming_soon": COMING_SOON,
            "year": date.today().year}
    return {"current_email": session.get("email"), "name": session.get("name"),
            "first_name": (session.get("name") or "").split(" ")[0],
            "tier": tier(),
            "is_member": at_least("research"), "is_pro": at_least("intelligence"),
            "is_admin": is_admin(), "tier_label": billing.LABEL,
            "price": billing.PRICE_CENTS["research"] // 100,
            "pro_price": billing.PRICE_CENTS["intelligence"] // 100,
            "pro_tagline": PRO_TAGLINE, "coming_soon": COMING_SOON,
            "year": date.today().year}


# ---------------------------------------------------------------- email

def send_login_email(email, link):
    notify.send_email(
        email, "Your sign-in link — Twelve Research",
        render_template("email/login.html", link=link),
        f"Sign in to Twelve Research: {link}\n\nThis link expires in 30 minutes.")


def render_alert_email(alert, address):
    return (render_template("email/alert.html", alert=alert, site=SITE_URL),
            render_template("email/alert.txt", alert=alert, site=SITE_URL))


# ---------------------------------------------------------------- pages

@app.route("/")
def home():
    try:
        figures = charts.homepage_charts()
    except (OSError, ValueError, KeyError) as exc:
        # A missing or half-written data file must never take the homepage down.
        app.logger.warning("charts unavailable: %s", exc)
        figures = None
    template = "coming_soon.html" if (COMING_SOON and not is_admin()) else "index.html"
    return render_template(template, articles=all_articles()[:6], figures=figures,
                           alerts=db.recent_alerts(3))


@app.route("/data/<path:filename>")
def data_file(filename):
    """Serve the chart data so readers can check the numbers themselves."""
    if not filename.endswith((".csv", ".json")):
        abort(404)
    return send_from_directory(charts.DATA, filename, mimetype="text/plain")


@app.route("/notify", methods=["POST"])
def notify_me():
    """Pre-launch list: just an email, kept in the same accounts table."""
    email = request.form.get("email", "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("That doesn't look like an email address.")
        return redirect(url_for("home"))
    db.upsert_user(email)
    flash("Thank you — we'll write to you when Twelve Research opens.")
    return redirect(url_for("home"))


@app.route("/research")
def research():
    return render_template("research.html", articles=all_articles())


@app.route("/research/<slug>")
def article(slug):
    post = get_article(slug)
    if not post:
        abort(404)
    unlocked = post["access"] == "free" or at_least("research") or not post["has_paywall"]
    # Premium text is only ever sent to members; everyone else gets the preview.
    body = post["full_html"] if unlocked else post["preview_html"]
    return render_template("article.html", post=post, body=body, locked=not unlocked)


@app.route("/about")
def about():
    return render_template("about.html")


# --------------------------------------------------------- live intelligence

@app.route("/intelligence")
def intelligence():
    """The live feed. Pro members see the notes; everyone else sees the headers only."""
    alerts = db.recent_alerts(60)
    phone = db.get_phone(session["email"]) if session.get("email") else None
    return render_template("intelligence.html", alerts=alerts, phone=phone)


@app.route("/admin", methods=["GET"])
def admin():
    if not is_admin():
        abort(404)  # don't advertise the page's existence
    return render_template("admin.html", alerts=db.recent_alerts(20), actions=ACTIONS,
                           book=portfolio_lib.build(db.BOOK), prices_on=prices.configured())


@app.route("/admin/publish", methods=["POST"])
def admin_publish():
    if not is_admin():
        abort(404)
    ticker = request.form.get("ticker", "").strip()
    action = request.form.get("action", "").strip().lower()
    thesis = request.form.get("thesis", "").strip()
    if not ticker or action not in ACTIONS or not thesis:
        flash("A ticker, an action and a thesis are required.")
        return redirect(url_for("admin"))

    alert_id = db.add_alert(ticker, action, request.form.get("level", "").strip(),
                            thesis, request.form.get("rationale", "").strip(), session["email"])
    alert = next(a for a in db.recent_alerts(5) if a["id"] == alert_id)

    if request.form.get("to_book"):
        try:
            shares = float(request.form.get("book_shares") or 0)
            level = float(request.form.get("level") or 0)
        except ValueError:
            shares = level = 0
        if shares > 0 and level > 0:
            db.add_position(db.BOOK, ticker, shares, level, date.today().isoformat(), alert_id)

    if request.form.get("notify"):
        try:
            emails = billing.members_on("intelligence")
        except stripe.StripeError as exc:
            app.logger.warning("could not list members: %s", exc)
            emails = []
        phones = list(db.verified_numbers(emails).values())
        sent_email, sent_sms = notify.broadcast(alert, emails, phones, render_alert_email)
        db.mark_sent(alert_id, sent_email, sent_sms)
        flash(f"Published. Sent {sent_email} emails and {sent_sms} texts.")
    else:
        flash("Published to the feed without notifying anyone.")
    return redirect(url_for("admin"))


@app.route("/admin/delete/<int:alert_id>", methods=["POST"])
def admin_delete(alert_id):
    if not is_admin():
        abort(404)
    db.delete_alert(alert_id)
    flash("Alert deleted from the feed. Any texts and emails already sent cannot be recalled.")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------- portfolio

@app.route("/portfolio")
def portfolio():
    """Your own positions, free with any account. The Twelve book is for members."""
    if not session.get("email"):
        return redirect(url_for("signup", next=url_for("portfolio")))
    mine = portfolio_lib.build(session["email"])
    book = portfolio_lib.build(db.BOOK) if at_least("research") else None
    prefill = {}
    alert_id = request.args.get("from_alert", type=int)
    if alert_id and at_least("intelligence"):
        source = next((a for a in db.recent_alerts(60) if a["id"] == alert_id), None)
        if source:
            prefill = {"ticker": source["ticker"], "price": source["level"], "alert_id": alert_id}
    return render_template("portfolio.html", mine=mine, book=book, prefill=prefill,
                           prices_on=prices.configured(), today=date.today().isoformat())


@app.route("/portfolio/add", methods=["POST"])
def portfolio_add():
    owner = db.BOOK if (request.form.get("book") and is_admin()) else session.get("email")
    if not owner:
        abort(403)
    try:
        shares = float(request.form.get("shares", "0"))
        cost = float(request.form.get("cost_basis", "0"))
    except ValueError:
        flash("Shares and price need to be numbers.")
        return redirect(url_for("portfolio"))
    ticker = request.form.get("ticker", "").strip()
    if not ticker or shares <= 0 or cost <= 0:
        flash("A ticker, a share count and a price are all needed.")
        return redirect(url_for("portfolio"))
    opened = request.form.get("opened_on") or date.today().isoformat()
    db.add_position(owner, ticker, shares, cost, opened,
                    request.form.get("alert_id", type=int), request.form.get("note", "")[:200])
    flash(f"Added {ticker.upper()} to {'the Twelve book' if owner == db.BOOK else 'your portfolio'}.")
    return redirect(url_for("admin") if owner == db.BOOK else url_for("portfolio"))


@app.route("/portfolio/close/<int:position_id>", methods=["POST"])
def portfolio_close(position_id):
    owner = db.BOOK if (request.form.get("book") and is_admin()) else session.get("email")
    if not owner:
        abort(403)
    try:
        price = float(request.form.get("price", "0"))
    except ValueError:
        price = 0
    if price <= 0:
        flash("Enter the price you closed at.")
    else:
        db.close_position(position_id, owner, price, date.today().isoformat())
        flash("Position closed. The result stays in the realised column.")
    return redirect(url_for("admin") if owner == db.BOOK else url_for("portfolio"))


@app.route("/portfolio/delete/<int:position_id>", methods=["POST"])
def portfolio_delete(position_id):
    owner = db.BOOK if (request.form.get("book") and is_admin()) else session.get("email")
    if not owner:
        abort(403)
    db.delete_position(position_id, owner)
    flash("Position deleted.")
    return redirect(url_for("admin") if owner == db.BOOK else url_for("portfolio"))


# ---------------------------------------------------------------- subscribe

@app.route("/subscribe")
def subscribe():
    return render_template("subscribe.html")


@app.route("/subscribe/checkout", methods=["POST"])
def checkout():
    wanted = request.form.get("tier", "research")
    if wanted not in billing.TIERS:
        abort(400)
    if not billing.configured():
        flash("Payments aren't configured yet. Add STRIPE_SECRET_KEY to .env.")
        return redirect(url_for("subscribe"))
    if at_least("research"):
        # Already paying: plan changes belong in the billing portal, not a second subscription.
        return redirect(url_for("account"))

    params = {
        "mode": "subscription",
        "line_items": [billing.line_item(wanted)],
        "allow_promotion_codes": True,
        "success_url": f"{SITE_URL}{url_for('checkout_success')}?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{SITE_URL}{url_for('subscribe')}",
    }
    if session.get("customer_id"):
        params["customer"] = session["customer_id"]
    elif session.get("email"):
        params["customer_email"] = session["email"]

    checkout_session = stripe.checkout.Session.create(**params)
    return redirect(checkout_session.url, code=303)


@app.route("/subscribe/success")
def checkout_success():
    session_id = request.args.get("session_id", "")
    try:
        cs = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except (stripe.StripeError, ValueError):
        abort(400)
    if cs.status != "complete" or not cs.customer_details or not cs.customer_details.email:
        flash("We couldn't confirm that payment. If you were charged, sign in with your email.")
        return redirect(url_for("subscribe"))
    new_tier = "none"
    if cs.subscription and cs.subscription.status in billing.ACTIVE_STATUSES:
        new_tier = billing.tier_of(cs.subscription)
    sign_in(cs.customer_details.email, cs.customer, new_tier,
            getattr(cs.customer_details, "name", None))
    return render_template("welcome.html")


# ---------------------------------------------------------------- sign in

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Please enter a valid email address.")
            return redirect(url_for("login"))
        token = login_tokens.dumps({"email": email, "next": request.form.get("next", "")})
        send_login_email(email, f"{SITE_URL}{url_for('login_verify', token=token)}")
        return render_template("check_email.html", email=email)
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Create an account: name plus email, then the same one-time link. No passwords."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80]
        email = request.form.get("email", "").strip().lower()
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Please give us a name and a valid email address.")
            return redirect(url_for("signup"))
        token = login_tokens.dumps({"email": email, "name": name,
                                    "next": request.form.get("next", url_for("portfolio"))})
        send_login_email(email, f"{SITE_URL}{url_for('login_verify', token=token)}")
        return render_template("check_email.html", email=email)
    return render_template("signup.html", next=request.args.get("next", ""))


@app.route("/login/verify/<token>")
def login_verify(token):
    try:
        data = login_tokens.loads(token, max_age=LOGIN_LINK_MAX_AGE)
    except SignatureExpired:
        flash("That sign-in link has expired. Request a new one below.")
        return redirect(url_for("login"))
    except BadSignature:
        abort(400)
    customer_id, member_tier = billing.find_membership(data["email"])
    sign_in(data["email"], customer_id, member_tier, data.get("name"))
    next_url = data.get("next") or ""
    # Only allow redirects back into this site.
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = url_for("account")
    return redirect(next_url)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


# ---------------------------------------------------------------- account

@app.route("/account")
def account():
    if not session.get("email"):
        return redirect(url_for("login", next=url_for("account")))
    return render_template("account.html", phone=db.get_phone(session["email"]),
                           user=db.get_user(session["email"]))


@app.route("/account/billing", methods=["POST"])
def billing_portal():
    """Stripe-hosted page where members change plan, update cards, or cancel."""
    if not session.get("customer_id"):
        return redirect(url_for("subscribe"))
    portal = stripe.billing_portal.Session.create(
        customer=session["customer_id"], return_url=f"{SITE_URL}{url_for('account')}")
    return redirect(portal.url, code=303)


def normalise_phone(raw):
    """Accept what people actually type; return E.164 or None."""
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = "+1" + digits if len(digits) == 10 else "+" + digits
    return digits if re.fullmatch(r"\+[1-9]\d{7,14}", digits) else None


@app.route("/account/phone", methods=["POST"])
def account_phone():
    """Take a number plus explicit consent, then text a code to confirm it."""
    if not at_least("intelligence"):
        return redirect(url_for("subscribe"))
    phone = normalise_phone(request.form.get("phone", ""))
    if not phone:
        flash("That doesn't look like a phone number. Include the country code, e.g. +1 415 555 0123.")
        return redirect(url_for("account"))
    if not request.form.get("consent"):
        flash("Please tick the box to agree to receive alert texts.")
        return redirect(url_for("account"))

    code = db.start_verification(session["email"], phone, request.remote_addr or "")
    notify.send_sms(phone, f"Twelve Research: your confirmation code is {code}. "
                           f"Msg & data rates may apply. Reply STOP to end.")
    flash("We texted you a 6-digit code. Enter it below to switch on alerts.")
    return redirect(url_for("account"))


@app.route("/account/phone/verify", methods=["POST"])
def account_phone_verify():
    if not session.get("email"):
        abort(403)
    if db.confirm_verification(session["email"], request.form.get("code", "")):
        flash("Your number is confirmed. You'll get live entries by text.")
    else:
        flash("That code didn't match, or it expired. Send yourself a new one.")
    return redirect(url_for("account"))


@app.route("/account/phone/remove", methods=["POST"])
def account_phone_remove():
    if not session.get("email"):
        abort(403)
    db.opt_out(session["email"])
    flash("Texts switched off. You'll still get alerts by email and on the site.")
    return redirect(url_for("account"))


@app.route("/sms/inbound", methods=["POST"])
def sms_inbound():
    """Twilio posts replies here. Carriers honour STOP themselves; we record it too."""
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not token:
        abort(403)
    expected = request.headers.get("X-Twilio-Signature", "")
    payload = SITE_URL + request.path + "".join(
        k + request.form[k] for k in sorted(request.form))
    digest = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    if not hmac.compare_digest(digest, expected):
        abort(403)

    body = request.form.get("Body", "").strip().upper()
    if body in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
        db.opt_out_by_phone(request.form.get("From", ""))
    return ("<?xml version='1.0' encoding='UTF-8'?><Response></Response>", 200,
            {"Content-Type": "application/xml"})


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="Page not found."), 404


@app.errorhandler(400)
def bad_request(_):
    return render_template("error.html", code=400, message="That link isn't valid."), 400


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 8000)))
