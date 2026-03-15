"""
apps/worker/tasks/email.py
==========================
ARQ task: send a transactional OTP email.

Retry strategy
--------------
ARQ retries a failing job up to ``WorkerSettings.max_tries`` times.
We implement exponential back-off within the task itself (between SMTP
attempts) and raise ``Retry`` from ARQ for the job-level retries.

On permanent failure (all retries exhausted) the task:
1. Logs the failure to the ``email_logs`` table.
2. Sends a Telegram alert to the admin so they can investigate.

Default OTP email templates
-----------------------------
If the project has no custom templates the built-in Jinja2 templates below
are used.  They are deliberately plain-text to maximise deliverability.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib
from jinja2 import Environment, StrictUndefined, Template
from telegram import Bot

from core.config import settings
from core.crypto import decrypt
from core.db import get_client

logger = logging.getLogger(__name__)

# ── Default email templates ────────────────────────────────────────────────────
_DEFAULT_SUBJECT_TMPL: str = "Your verification code: {{ otp }}"
_DEFAULT_BODY_TMPL: str = (
    "Hi,\n\n"
    "Your one-time verification code is:\n\n"
    "    {{ otp }}\n\n"
    "This code expires in {{ expiry_minutes }} minutes.\n\n"
    "If you did not request this code, please ignore this email.\n\n"
    "— MailGuard"
)

# ── SMTP provider defaults ─────────────────────────────────────────────────────
_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 587},
    "outlook": {"smtp_host": "smtp.office365.com", "smtp_port": 587},
    "zoho": {"smtp_host": "smtp.zoho.com", "smtp_port": 587},
}

# Back-off delays (seconds) between SMTP retry attempts
_RETRY_DELAYS: list[int] = [5, 15, 45]


async def send_email_task(
    ctx: dict[str, Any],
    *,
    otp_record_id: str,
    email: str,
    otp: str,
    project_id: str,
) -> dict[str, Any]:
    """
    ARQ task: fetch sender credentials, render template, send via SMTP.

    Args:
        ctx:            ARQ job context (injected by the worker).
        otp_record_id:  UUID of the ``otp_records`` row.
        email:          Plaintext recipient email address.
        otp:            Plaintext OTP code to embed in the message.
        project_id:     UUID of the owning project.

    Returns:
        A dict with ``{"status": "sent", "log_id": <uuid>}`` on success.

    Raises:
        Exception: On permanent failure after all retries (triggers ARQ retry /
                   failure logging).
    """
    supabase = await get_client()

    # ── Fetch project record ──────────────────────────────────────────────────
    project_resp = (
        await supabase.table("projects")
        .select("*")
        .eq("id", project_id)
        .limit(1)
        .execute()
    )
    if not project_resp.data:
        logger.error("Project %s not found – aborting email task", project_id)
        return {"status": "error", "reason": "project_not_found"}

    project: dict[str, Any] = project_resp.data[0]

    if not project.get("sender_email_id"):
        logger.error("Project %s has no sender configured", project_id)
        await _log_failure(supabase, project_id, None, email, "no_sender_configured")
        return {"status": "error", "reason": "no_sender_configured"}

    # ── Fetch sender email record ─────────────────────────────────────────────
    sender_resp = (
        await supabase.table("sender_emails")
        .select("*")
        .eq("id", project["sender_email_id"])
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not sender_resp.data:
        logger.error("Sender %s not found or inactive", project["sender_email_id"])
        await _log_failure(supabase, project_id, None, email, "sender_not_found")
        return {"status": "error", "reason": "sender_not_found"}

    sender: dict[str, Any] = sender_resp.data[0]

    # ── Decrypt SMTP password ─────────────────────────────────────────────────
    try:
        smtp_password: str = decrypt(sender["app_password_enc"])
    except Exception as exc:
        logger.error("Failed to decrypt SMTP password for sender %s: %s", sender["id"], exc)
        await _log_failure(supabase, project_id, sender["id"], email, f"decrypt_error: {exc}")
        return {"status": "error", "reason": "decrypt_error"}

    # ── Render email template ─────────────────────────────────────────────────
    expiry_minutes: int = project.get("otp_expiry_seconds", 600) // 60
    subject_tmpl_str: str = project.get("otp_subject_tmpl") or _DEFAULT_SUBJECT_TMPL
    body_tmpl_str: str = project.get("otp_body_tmpl") or _DEFAULT_BODY_TMPL
    raw_format: str = project.get("otp_format", "text")
    # Validate otp_format to prevent unexpected autoescape=False fallthrough
    otp_format: str = raw_format if raw_format in ("text", "html") else "text"
    if raw_format not in ("text", "html"):
        logger.warning("Unknown otp_format %r for project %s; defaulting to 'text'", raw_format, project_id)

    # Autoescape is enabled for HTML to prevent XSS; disabled for plain text
    jinja_env = Environment(autoescape=(otp_format == "html"), undefined=StrictUndefined)  # noqa: S701

    try:
        subject: str = jinja_env.from_string(subject_tmpl_str).render(otp=otp)
        body: str = jinja_env.from_string(body_tmpl_str).render(
            otp=otp,
            expiry_minutes=expiry_minutes,
            project_name=project.get("name", ""),
        )
    except Exception as exc:
        logger.error("Template rendering failed for project %s: %s", project_id, exc)
        await _log_failure(supabase, project_id, sender["id"], email, f"template_error: {exc}")
        return {"status": "error", "reason": "template_error"}

    # ── Build MIME message ────────────────────────────────────────────────────
    msg = MIMEMultipart("alternative") if otp_format == "html" else MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = (
        f"{sender.get('display_name', sender['email_address'])} <{sender['email_address']}>"
    )
    msg["To"] = email

    content_type = "html" if otp_format == "html" else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    smtp_host: str = sender["smtp_host"]
    smtp_port: int = sender["smtp_port"]

    # ── SMTP send with retry ──────────────────────────────────────────────────
    last_error: Exception | None = None
    for attempt_idx, delay in enumerate(_RETRY_DELAYS, start=1):
        try:
            await aiosmtplib.send(
                msg,
                hostname=smtp_host,
                port=smtp_port,
                username=sender["email_address"],
                password=smtp_password,
                start_tls=True,
                timeout=15,
            )
            logger.info(
                "Email sent to [REDACTED] for project %s (attempt %d)",
                project_id,
                attempt_idx,
            )
            # ── Log success ───────────────────────────────────────────────────
            await _log_success(supabase, project_id, sender["id"], email)
            # ── Update sender last_used_at ────────────────────────────────────
            await (
                supabase.table("sender_emails")
                .update({"last_used_at": datetime.now(tz=timezone.utc).isoformat()})
                .eq("id", sender["id"])
                .execute()
            )
            return {"status": "sent"}

        except Exception as exc:
            last_error = exc
            logger.warning(
                "SMTP attempt %d/%d failed for project %s: %s",
                attempt_idx,
                len(_RETRY_DELAYS),
                project_id,
                exc,
            )
            if attempt_idx < len(_RETRY_DELAYS):
                await asyncio.sleep(delay)

    # ── All attempts exhausted ────────────────────────────────────────────────
    error_msg: str = str(last_error) if last_error else "unknown"
    logger.error(
        "All SMTP attempts failed for project %s: %s", project_id, error_msg
    )
    await _log_failure(
        supabase,
        project_id,
        sender["id"],
        email,
        error_msg,
        attempt_count=len(_RETRY_DELAYS),
    )
    await _send_telegram_alert(project_id, error_msg)

    raise RuntimeError(f"Email delivery failed after {len(_RETRY_DELAYS)} attempts: {error_msg}")


async def _log_success(
    supabase: Any,
    project_id: str,
    sender_id: str,
    recipient_email: str,
) -> None:
    """Insert a success entry into email_logs."""
    from core.otp import hmac_email  # noqa: PLC0415 – avoid circular import at module level

    try:
        await supabase.table("email_logs").insert(
            {
                "project_id": project_id,
                "sender_email_id": sender_id,
                "recipient_email_hash": hmac_email(recipient_email),
                "status": "sent",
                "attempt_count": 1,
                "sent_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:
        logger.warning("Failed to write email_log (success): %s", exc)


async def _log_failure(
    supabase: Any,
    project_id: str,
    sender_id: str | None,
    recipient_email: str,
    error_message: str,
    attempt_count: int = 1,
) -> None:
    """Insert a failure entry into email_logs."""
    from core.otp import hmac_email  # noqa: PLC0415

    try:
        await supabase.table("email_logs").insert(
            {
                "project_id": project_id,
                "sender_email_id": sender_id,
                "recipient_email_hash": hmac_email(recipient_email),
                "status": "failed",
                "error_message": error_message[:1000],  # truncate to column limit
                "attempt_count": attempt_count,
            }
        ).execute()
    except Exception as exc:
        logger.warning("Failed to write email_log (failure): %s", exc)


async def _send_telegram_alert(project_id: str, error_message: str) -> None:
    """
    Send a Telegram message to the admin when email delivery fails permanently.

    Failures here are logged but not re-raised so the ARQ job can still
    record its own result cleanly.
    """
    try:
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        text = (
            f"⚠️ *MailGuard Email Failure*\n\n"
            f"Project: `{project_id}`\n"
            f"Error: `{error_message[:200]}`\n\n"
            f"Check `/logs --failed` for details."
        )
        async with bot:
            await bot.send_message(
                chat_id=settings.TELEGRAM_ADMIN_UID,
                text=text,
                parse_mode="Markdown",
            )
    except Exception as exc:
        logger.error("Failed to send Telegram alert: %s", exc)
