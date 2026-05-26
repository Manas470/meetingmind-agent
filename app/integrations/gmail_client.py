"""
Gmail integration — sends personalized follow-up emails to attendees.
Uses SMTP (with App Password) as the primary transport.
Falls back gracefully if not configured.
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import NamedTuple

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = structlog.get_logger(__name__)


class EmailResult(NamedTuple):
    recipient_email: str
    success: bool
    error: str = ""


class GmailClient:
    """
    Async-friendly SMTP email client.
    Actual SMTP I/O runs in a thread pool to avoid blocking the event loop.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.email_enabled:
            raise RuntimeError(
                "Email is not configured. Set SMTP_USER and SMTP_PASSWORD."
            )
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._user = settings.smtp_user
        self._password = settings.smtp_password
        self._from_name = settings.email_from_name

    # ── Public API ────────────────────────────────────────────────────────────

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        to_name: str = "",
    ) -> EmailResult:
        """Send a single email asynchronously (runs SMTP in executor)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._send_sync,
            to_email,
            to_name,
            subject,
            body,
        )

    async def send_bulk(
        self,
        recipients: list[tuple[str, str, str, str]],  # (email, name, subject, body)
    ) -> list[EmailResult]:
        """Send multiple emails concurrently."""
        tasks = [
            self.send_email(email, subject, body, name)
            for email, name, subject, body in recipients
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    # ── Private helpers ───────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def _send_sync(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        body: str,
    ) -> EmailResult:
        """Blocking SMTP send — called from executor."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self._from_name} <{self._user}>"
        msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email

        # Plain text + minimal HTML version
        plain = MIMEText(body, "plain")
        html = MIMEText(self._to_html(body), "html")
        msg.attach(plain)
        msg.attach(html)

        context = ssl.create_default_context()
        try:
            with smtplib.SMTP(self._host, self._port) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(self._user, self._password)
                server.sendmail(self._user, to_email, msg.as_string())
            logger.info("gmail.sent", to=to_email, subject=subject)
            return EmailResult(recipient_email=to_email, success=True)
        except smtplib.SMTPException as e:
            logger.error("gmail.send_failed", to=to_email, error=str(e))
            return EmailResult(recipient_email=to_email, success=False, error=str(e))

    @staticmethod
    def _to_html(plain_text: str) -> str:
        """Convert plain text to minimal HTML (preserves line breaks)."""
        escaped = plain_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html_body = escaped.replace("\n", "<br>\n")
        return f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             font-size: 14px; line-height: 1.6; color: #1a1a1a; max-width: 600px; margin: 0 auto; padding: 20px;">
{html_body}
<br><br>
<hr style="border: none; border-top: 1px solid #eee;">
<p style="font-size: 12px; color: #888;">
  Sent by MeetingMind — autonomous meeting intelligence.
</p>
</body>
</html>"""
