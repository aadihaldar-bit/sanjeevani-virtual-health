"""
Minimal SMTP email sender (stdlib only) for transactional one-tap links.

We deliberately avoid a paid transactional service (SendGrid/SES) — at clinic volume plain SMTP over
the org's mailbox is enough, and WhatsApp/SMS carry per-message + DLT-template costs we don't want.

Config (env, set on the VM backend/.env — keep the password OUT of git and logs):
    SMTP_HOST        e.g. smtp.gmail.com
    SMTP_PORT        587 (STARTTLS, default) or 465 (implicit TLS)
    SMTP_USER        the mailbox username (often the full address)
    SMTP_PASS        app password / SMTP password  (never logged)
    SMTP_FROM        From: address (defaults to SMTP_USER)
    SMTP_FROM_NAME   display name (default "STL City Care")

send() runs the blocking smtplib call in a thread so it never stalls the audio loop.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger("emailer")


def email_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER")))


def _send_blocking(to: str, subject: str, body: str, html: str = "") -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = (os.getenv("SMTP_USER") or "").strip()
    pwd = os.getenv("SMTP_PASS") or ""
    sender = (os.getenv("SMTP_FROM") or user).strip()
    from_name = os.getenv("SMTP_FROM_NAME", "STL City Care")

    msg = EmailMessage()
    msg["From"] = f"{from_name} <{sender}>" if from_name else sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)                       # plain-text fallback (always present)
    if html:
        msg.add_alternative(html, subtype="html")  # rich version preferred by modern clients

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
            if user and pwd:
                s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            if user and pwd:
                s.login(user, pwd)
            s.send_message(msg)


async def send(to: str, subject: str, body: str, html: str = "") -> bool:
    """Send an email; returns True on success. Never raises. Logs recipient + subject only (no body).
    Pass `html` for a rich multipart/alternative message (plain `body` stays as the fallback)."""
    if not email_configured():
        logger.warning("[email] SMTP not configured (SMTP_HOST / SMTP_FROM missing)")
        return False
    try:
        await asyncio.get_event_loop().run_in_executor(None, _send_blocking, to, subject, body, html)
        logger.info("[email] sent to %s (subject=%r)", to, subject)
        return True
    except Exception as exc:  # deliverability must never crash the call
        logger.warning("[email] send to %s failed: %s", to, exc)
        return False
