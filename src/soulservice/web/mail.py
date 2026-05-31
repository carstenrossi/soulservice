"""Send magic-link emails via SMTP (Mailpit locally, no auth/TLS)."""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from soulservice.core.config import settings


async def send_magic_link(to_email: str, link: str) -> None:
    message = EmailMessage()
    message["From"] = settings.web_from_email
    message["To"] = to_email
    message["Subject"] = "Your Soulservice admin login link"
    message.set_content(
        f"Click to sign in (valid for {settings.web_magic_link_ttl_minutes} minutes):"
        f"\n\n{link}\n"
    )
    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        start_tls=False,
    )
