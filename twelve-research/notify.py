"""Getting a live alert out: email through Resend, SMS through Twilio.

Both senders are no-ops when their keys are missing: locally the message is
printed to the terminal instead, so the whole flow can be exercised without
sending anything to anyone.
"""

import os

import requests

RESEND_URL = "https://api.resend.com/emails"
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
SMS_BODY_LIMIT = 280  # keeps a message to two segments including the footer


def site_url():
    return os.environ.get("SITE_URL", "http://localhost:8000").rstrip("/")


# ------------------------------------------------------------------- email

def send_email(to, subject, html, text_body):
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print(f"\n[email -> {to}] {subject}\n{text_body}\n", flush=True)
        return True
    try:
        r = requests.post(RESEND_URL, headers={"Authorization": f"Bearer {api_key}"},
                          json={"from": os.environ.get("EMAIL_FROM", "Twelve Research <research@twelveholdings.com>"),
                                "to": [to], "subject": subject, "html": html, "text": text_body},
                          timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[email failed -> {to}] {exc}", flush=True)
        return False


# --------------------------------------------------------------------- sms

def send_sms(to, body):
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    sender = os.environ.get("TWILIO_FROM", "")
    if not (sid and token and sender):
        print(f"\n[sms -> {to}]\n{body}\n", flush=True)
        return True
    # A Messaging Service SID (MG...) and a plain number use different fields.
    data = {"To": to, "Body": body}
    data["MessagingServiceSid" if sender.startswith("MG") else "From"] = sender
    try:
        r = requests.post(TWILIO_URL.format(sid=sid), data=data, auth=(sid, token), timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[sms failed -> {to}] {exc}", flush=True)
        return False


# ------------------------------------------------------------------ alerts

def alert_subject(alert):
    level = f" @ {alert['level']}" if alert.get("level") else ""
    return f"[{alert['ticker']}] {alert['action'].title()}{level} — Twelve Intelligence"


def alert_sms_body(alert):
    """Short enough for two segments, with the opt-out line SMS rules require."""
    level = f" @ {alert['level']}" if alert.get("level") else ""
    head = f"TWELVE {alert['action'].upper()}: {alert['ticker']}{level}. "
    link = f" {site_url()}/intelligence"
    footer = " Reply STOP to end."
    room = SMS_BODY_LIMIT - len(head) - len(link) - len(footer)
    thesis = " ".join(alert["thesis"].split())
    if len(thesis) > room:
        thesis = thesis[: max(room - 1, 0)].rstrip() + "…"
    return head + thesis + link + footer


def broadcast(alert, emails, phones, render_email):
    """Send one alert to the Twelve Intelligence list. Returns (emails, texts) sent."""
    subject = alert_subject(alert)
    sent_email = 0
    for address in emails:
        html, text_body = render_email(alert, address)
        if send_email(address, subject, html, text_body):
            sent_email += 1

    body = alert_sms_body(alert)
    sent_sms = sum(1 for number in phones if send_sms(number, body))
    return sent_email, sent_sms
