"""mailer.py — send a message over SMTP.

The daily report and the health alarms went out over an SNS topic, and SNS
email has two properties that make it the wrong carrier for mail a person is
meant to read. Its unsubscribe link needs no authentication, so any mail
provider that crawls URLs to check them can unsubscribe the address by
fetching it, which is what silently killed both subscriptions here. And it
arrives from no-reply@sns.amazonaws.com, which lands in spam, and an alarm
in a spam folder is not an alarm.

Mail sent over SMTP from the reader's own address to the reader's own
address has neither problem: nothing to confirm, nothing to unsubscribe, and
inbox placement that a person actually sees.

Everything comes from the environment, never the repository, which is public:

    WX_SMTP_HOST      default smtp.gmail.com
    WX_SMTP_PORT      default 587, STARTTLS; 465 selects implicit TLS
    WX_SMTP_USER      the account that authenticates, and the From address
    WX_SMTP_PASSWORD  an app password, not the account password
    WX_MAIL_TO        comma separated; defaults to WX_SMTP_USER
    WX_MAIL_FROM      defaults to WX_SMTP_USER

configured() is false when any required value is missing, and every caller
treats an unconfigured mailer as "do not send" rather than an error, so a
deployment without credentials behaves exactly as it did before.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

TIMEOUT_S = 20


def settings() -> dict:
    """The mail configuration as the environment gives it."""
    user = os.environ.get("WX_SMTP_USER", "").strip()
    to = os.environ.get("WX_MAIL_TO", "").strip() or user
    return {
        "host": os.environ.get("WX_SMTP_HOST", "smtp.gmail.com").strip(),
        "port": int(os.environ.get("WX_SMTP_PORT", "587") or 587),
        "user": user,
        "password": os.environ.get("WX_SMTP_PASSWORD", ""),
        "from": os.environ.get("WX_MAIL_FROM", "").strip() or user,
        "to": [a.strip() for a in to.split(",") if a.strip()],
    }


def configured(s: dict | None = None) -> bool:
    s = s or settings()
    return bool(s["host"] and s["user"] and s["password"] and s["to"])


def send(subject: str, body: str, s: dict | None = None) -> None:
    """Send one plain-text message. Raises on failure; callers decide.

    The body is plain text on purpose. A mail client shows it in a
    fixed-width font, which is what makes the report's bar chart line up,
    and there is nothing to render and nothing to load.
    """
    s = s or settings()
    if not configured(s):
        raise RuntimeError("smtp not configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s["from"]
    msg["To"] = ", ".join(s["to"])
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="weather-tools-site")
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if s["port"] == 465:
        with smtplib.SMTP_SSL(s["host"], s["port"], timeout=TIMEOUT_S, context=ctx) as smtp:
            smtp.login(s["user"], s["password"])
            smtp.send_message(msg)
        return
    with smtplib.SMTP(s["host"], s["port"], timeout=TIMEOUT_S) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
        smtp.login(s["user"], s["password"])
        smtp.send_message(msg)
