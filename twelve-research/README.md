# [Twelve Research]

Research website with two membership tiers, built with Flask, Stripe and Twilio.

| Tier | Price | What it includes |
|---|---|---|
| Research | $10/month | Every report in full, the archive |
| Twelve Intelligence | $25/month | Live entries by text and email with thesis and rationale, plus everything above |

Anyone can create a free account to track their own portfolio; paying tiers add the research and the live entries. Stripe is the record of who is a member and on which tier. Sign-in is a one-time email link, so there are no passwords to store. The database holds accounts, portfolio positions, live alerts, cached closing prices, and the phone numbers members have confirmed for SMS.

## Run locally

```bash
cd ~/Public/Code/twelve-research
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill it in (see below)
.venv/bin/python app.py     # http://localhost:8000
```

Without `RESEND_API_KEY`, sign-in links are printed in the terminal instead of emailed.

## Publishing research

Add a Markdown file to `content/articles/`. The filename becomes the URL (`my-report.md` → `/research/my-report`).

```markdown
---
title: Why the Market Is Wrong About XYZ
date: 2026-10-01
summary: One-sentence dek shown in lists and under the title.
tickers: XYZ, ABC
access: members            # members (paywalled) or free
seeking_alpha_url:         # optional "Read on Seeking Alpha" link
substack_url:              # optional "Read on Substack" link
draft: false               # true hides it
---

Free preview paragraphs go here.

<!-- paywall -->

Everything below the marker is only sent to paying members.
```

If there is no `<!-- paywall -->` marker, the whole article is public.

## Day to day: write, preview, publish

```bash
python3 new.py "Why The Market Is Wrong About XYZ" --tickers "XYZ"   # creates and opens the file
.venv/bin/python app.py                                              # preview at localhost:8000
./publish "Add XYZ report"                                           # push it live
```

New reports start as `draft: true`, so they stay invisible until you set `draft: false`. `./publish` reminds you about any drafts it sees, commits everything, and pushes. The live site rebuilds itself within a minute.

## Homepage charts

Three charts sit under "What we're watching", drawn from Epoch AI's public data:
capability (ECI) over time, compute in tracked AI data centres, and how long those
data centres take to build.

```bash
.venv/bin/python prepare_data.py            # re-download and rebuild the chart data
.venv/bin/python prepare_data.py --offline  # rebuild from the cached download
```

`prepare_data.py` downloads Epoch's CSVs to `content/data/raw/`, computes the series
and the fitted trends, and writes `content/data/*.csv` plus `stats.json`. `charts.py`
renders those into inline SVG — no chart library, no JavaScript, no network calls when
a reader loads the page. Headline numbers in the captions come from `stats.json`, so
they update when the data does. Readers can download each series at `/data/<file>.csv`.

Run it monthly or so; Epoch updates its datasets regularly. Nothing breaks if you
don't — the charts just go stale, and the "Updated" date on the section shows it.

**Attribution is required.** Epoch AI publishes under CC BY 4.0, and the credit line
under the charts is what satisfies it. Keep it there.

## Accounts and portfolios

Signing up at `/signup` takes a name and an email and sends a sign-in link — no passwords anywhere in the system. Paying through Checkout creates the same account, using the name Stripe collected.

Every account gets `/portfolio`: positions they enter themselves, marked at the last close, with unrealised and realised P&L. It's free, which makes it a reason to hand over an email before paying for anything.

Members also see **the Twelve book** there: the positions you've taken from your own entries, read-only. You manage it from the Desk — tick "add to the Twelve book" when posting an entry, or add one by hand — and close positions at an exit price, which moves the result into the realised column.

P&L is stated as **return on cost**, not a time-weighted return. It says what the money put in has done, which is the honest claim for a book of entries; it is not a fund-style performance figure and shouldn't be presented as one.

## Prices

End-of-day closes from [Massive](https://massive.com) (formerly Polygon). Create a free account, take the API key, and put it in `MARKET_DATA_KEY`. The free Stocks Basic plan is enough: one call to the daily market summary covers every US ticker, and closes are cached in the database.

```bash
.venv/bin/python update_prices.py     # pull the latest closes (good as a nightly cron)
```

Pages also refresh prices by themselves when the cache is more than 12 hours old, so the cron is a convenience. Without a key the site still works — portfolios show cost basis and say prices aren't connected.

**On live prices:** showing real-time quotes to members is redistribution, and the cheap API tiers are licensed for personal use only. End-of-day data is the simplest licence to stand on. If you ever move to intraday quotes, get the vendor to confirm in writing that your plan covers displaying data to your members, and label the delay on screen.

## Posting a live entry

Sign in at `/login` with the address in `ADMIN_EMAIL`, then open **Desk** in the nav (`/admin`) — it is built for a phone. Fill in ticker, action, level, thesis and rationale, leave "Text and email members" ticked, and publish. The note appears on `/intelligence` immediately and goes out by SMS and email to Twelve Intelligence members.

The first ~160 characters of the thesis go into the text message, so put the point first. Deleting a note removes it from the feed; it cannot recall texts already sent.

## Stripe setup

1. Create a Stripe account and copy the **secret key** from Developers → API keys into `STRIPE_SECRET_KEY`. Start with the `sk_test_...` key.
2. Run `.venv/bin/python setup_stripe.py` once. It creates both products and prices and prints the two `STRIPE_PRICE_*` lines for `.env`. Re-running is safe.
3. Turn on the **Customer Portal** (Settings → Billing → Customer portal) and allow cancellations and card updates. Members open it from "Manage billing & cancel".
4. In the Customer Portal settings, allow customers to **switch plans** between the two prices — that is how members upgrade from Research to Twelve Intelligence.
5. To test, subscribe with card `4242 4242 4242 4242`, any future date, and any CVC.
6. To go live, switch to the `sk_live_...` key and run `setup_stripe.py` again to create live prices.

## SMS setup (Twilio)

1. Create a Twilio account, buy a number, and put the Account SID, Auth Token and number in `.env` (`TWILIO_FROM` takes either a `+1...` number or a Messaging Service SID).
2. **Register for A2P 10DLC** in the Twilio console before sending to US numbers. Carriers block unregistered application-to-person traffic, and approval takes days to weeks. Start this early.
3. Point your Twilio number's inbound webhook at `https://yourdomain.com/sms/inbound`. Carriers honour STOP themselves; this records it so the site stops trying.
4. Leave the Twilio variables blank to develop: texts print to the terminal instead of sending.

Members opt in on their account page: they enter a number, tick a consent box, and confirm a 6-digit code before anything is sent. The consent time and IP are stored, which is what you need if a carrier ever asks.

## Database

`DATABASE_URL` points at Postgres in production; blank uses a local SQLite file (`twelve.db`, gitignored). The Render blueprint creates the database and wires the variable in. Tables are created automatically at startup.

## How membership works

- **Subscribe:** Stripe Checkout takes the payment. On success the reader is signed in on that device.
- **Sign in on another device:** the reader enters their email and gets a link. Clicking it looks up their subscription in Stripe.
- **Cancellations:** membership is checked with Stripe again every 6 hours, so access ends automatically after a subscription lapses.
- Paywalled text is cut on the server. Non-members never receive it, even in the page source.

## Deploy

Any Python host works. On Render or Railway: connect the repo, set the build command to `pip install -r requirements.txt` and the start command to `gunicorn app:app` (a `Procfile` is included), then add the `.env` variables in the dashboard. Set `SITE_URL` to your real `https://` domain and `SECRET_KEY` to a long random string.

For sign-in emails, create a free [Resend](https://resend.com) account, verify your domain, and set `RESEND_API_KEY` and `EMAIL_FROM`.

