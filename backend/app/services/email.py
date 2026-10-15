"""Optional email delivery. Without SMTP_URL configured nothing is sent (and the UI says so)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import urlparse

from ..core.config import get_settings

log = logging.getLogger("app.email")


def configured() -> bool:
    s = get_settings()
    return bool(s.smtp_url and s.email_from)


def send(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if not configured():
        log.info("email not configured; message not sent", extra={"extra_fields": {"subject": subject}})
        return False
    u = urlparse(s.smtp_url)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.email_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(u.hostname, u.port or 587, timeout=15) as smtp:
            smtp.starttls()
            if u.username:
                smtp.login(u.username, u.password or "")
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        log.exception("email delivery failed")
        return False


def send_password_reset(to: str, raw_token: str) -> bool:
    link = f"{get_settings().frontend_url}/reset-password?token={raw_token}"
    return send(to, "Reset your password",
                f"Use this link within 30 minutes to reset your password:\n\n{link}\n\n"
                "If you did not request this, you can ignore this email.")
