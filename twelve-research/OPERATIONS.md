# How Twelve Research actually runs

A map of where everything lives and who does what. Written for the day you've
forgotten how this works.

## The four places your business data lives

Nothing is in one system. Each piece sits where it's safest:

| What | Where it lives | How you look at it |
|---|---|---|
| Accounts (name, email, when they joined) | Your database | Render dashboard → the database → Query |
| Who is paying, which tier, card details | **Stripe only** | dashboard.stripe.com → Customers |
| Phone numbers and their consent records | Your database | Render dashboard |
| Portfolio positions, live alerts, cached prices | Your database | Render dashboard |
| Research reports | Markdown files in the git repo | Your Mac, and GitHub |
| A record of every email sent | Resend | resend.com → Logs |
| A record of every text sent | Twilio | console.twilio.com → Monitor → Messages |

Card numbers never touch your server. Stripe holds them; your site only ever
asks Stripe "is this person paid up, and on which plan?"

## What happens when someone signs up

```
Visitor fills in name + email at /signup
        |
        v
Row created in your database          <- this is the whole "account"
        |
        v
Site asks Resend to send a sign-in link
        |
        v
They click the link -> signed in, and stay signed in for 30 days
```

That's it. No password is created, because the system doesn't have passwords.
The link *is* the proof they own the address.

If instead they pay first, the same thing happens from the other direction:
Stripe Checkout takes the payment, tells the site who they are, and the account
is created with the name Stripe collected.

## What the site sends on its own

Exactly three things, all automatic. You are never in the loop:

1. **Sign-in links** — whenever anyone asks to sign in.
2. **Live alerts** — email and SMS to Twelve Intelligence members, the moment
   you hit Publish on the Desk with the notify box ticked.
3. **Phone confirmation codes** — when a member sets up alert texts.

Stripe separately handles everything about money, if you switch it on in
**Stripe → Settings → Customer emails**: receipts, renewal notices, failed-card
chase-ups, and cancellation confirmations. Turn those on before you launch, or
your members get charged every month with no receipt.

## What you do by hand

- **Write research.** `python3 new.py "Title"`, write it, `./publish "message"`.
- **Post live entries.** The Desk, from your phone. One button.
- **Keep the Twelve book honest.** Close positions at the price you actually
  got out at, so the track record stays real.
- **Refresh the chart data** every month or so: `.venv/bin/python prepare_data.py`.

You do **not** manually email anybody. If you ever find yourself copying
addresses out of Stripe to send something, stop — that's a feature to build, not
a chore to do.

## The site is in pre-launch mode

`COMING_SOON=true` in `.env` means the public sees a holding page: the wordmark,
one paragraph, an email box, and the Epoch AI charts. Everything else — research,
the live feed, membership, sign-up — redirects back to it.

Signed in as the admin address you see the real site as normal, so you can keep
working behind the holding page. Addresses from the email box land in the same
accounts table as everyone else.

To open the site: set `COMING_SOON=false` and restart (or change it in Render's
environment, which restarts the service itself).

## The gap you should know about

**Publishing a research report notifies nobody.** It appears on the site, and
that's all. Members have to come and look.

This is fine on day one, when your readers arrive from Seeking Alpha and
Substack. It stops being fine once people are paying $10 a month for reports
they don't know exist. The fix is a "new report published" email to members,
which is a small job — the sending machinery already exists for alerts.

## When something looks broken

| Symptom | Where to look first |
|---|---|
| "I never got my sign-in link" | Resend → Logs. If it's not there, the site never sent it; if it's there and bounced, it's their mail server. |
| "I didn't get the text" | Twilio → Monitor → Messages. Check their number is confirmed and they haven't replied STOP. |
| Someone paid but has no access | Stripe → Customers. Check the subscription is active and on the right price. |
| The site is down or erroring | Render → your service → Logs. |
| Charts look stale | Run `prepare_data.py`. The date under the section header tells you how old they are. |

## A sensible rhythm

- **Daily, when something sets up:** post the entry from the Desk. Close
  positions in the book when you exit.
- **Weekly:** publish research. Check Stripe for new members and cancellations.
- **Monthly:** refresh the Epoch chart data. Skim Resend and Twilio logs for
  failures you didn't notice.
