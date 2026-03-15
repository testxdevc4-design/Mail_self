"""
apps/bot/commands/sender.py
============================
Telegram bot commands for managing sender email accounts.

Commands
--------
/senders         - List all configured sender emails with status.
/testsender <e>  - Send a test email from the specified sender address.
/removesender    - Interactive prompt to remove a sender.
"""

from __future__ import annotations

import logging
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib
from telegram import Update
from telegram.ext import ContextTypes

from apps.bot.middleware.admin_gate import check_admin
from core.crypto import decrypt
from core.db import get_client

logger = logging.getLogger(__name__)


async def cmd_senders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /senders - List all sender emails stored in the database.

    Displays email address, provider, daily limit, verified and active status.
    """
    if not await check_admin(update, context):
        return

    supabase = await get_client()
    try:
        response = await supabase.table("sender_emails").select(
            "email_address, display_name, provider, daily_limit, "
            "is_verified, is_active, last_used_at"
        ).order("created_at").execute()
    except Exception as exc:
        logger.error("Failed to fetch senders: %s", exc)
        await update.message.reply_text("❌ Failed to fetch senders.  Check logs.")
        return

    rows: list[dict[str, Any]] = response.data
    if not rows:
        await update.message.reply_text(
            "No sender emails configured yet.\n"
            "Use the /addemail wizard to add one."
        )
        return

    lines: list[str] = ["*Sender Emails*\n"]
    for row in rows:
        verified_icon = "✅" if row["is_verified"] else "⚠️"
        active_icon = "🟢" if row["is_active"] else "🔴"
        last_used = row["last_used_at"] or "never"
        if len(str(last_used)) > 10:
            last_used = str(last_used)[:10]
        lines.append(
            f"{active_icon} {verified_icon} `{row['email_address']}`\n"
            f"  Provider: {row['provider']} | Limit: {row['daily_limit']}/day\n"
            f"  Last used: {last_used}\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_test_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /testsender <email> - Send a test email through the specified sender.

    Args passed via ``context.args[0]``: the sender's email address.
    """
    if not await check_admin(update, context):
        return

    if not context.args:
        await update.message.reply_text("Usage: /testsender <email_address>")
        return

    target_email: str = context.args[0].strip().lower()
    await update.message.reply_text(
        f"⏳ Sending test email from `{target_email}`…",
        parse_mode="Markdown",
    )

    supabase = await get_client()
    try:
        response = (
            await supabase.table("sender_emails")
            .select("*")
            .eq("email_address", target_email)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error fetching sender for test: %s", exc)
        await update.message.reply_text("❌ Database error.  Check logs.")
        return

    rows = response.data
    if not rows:
        await update.message.reply_text(
            f"❌ Sender `{target_email}` not found in the database.", parse_mode="Markdown"
        )
        return

    sender: dict[str, Any] = rows[0]

    try:
        smtp_password: str = decrypt(sender["app_password_enc"])
    except Exception as exc:
        await update.message.reply_text(f"❌ Could not decrypt sender password: {exc}")
        return

    msg = MIMEText(
        "This is a MailGuard test email.\n\nIf you received this, the sender is working correctly.",
        "plain",
        "utf-8",
    )
    msg["Subject"] = "MailGuard - Sender Test"
    msg["From"] = sender["email_address"]
    msg["To"] = sender["email_address"]  # Send test to self

    try:
        await aiosmtplib.send(
            msg,
            hostname=sender["smtp_host"],
            port=sender["smtp_port"],
            username=sender["email_address"],
            password=smtp_password,
            start_tls=True,
            timeout=15,
        )
        # Mark sender as verified
        await (
            supabase.table("sender_emails")
            .update({"is_verified": True})
            .eq("id", sender["id"])
            .execute()
        )
        await update.message.reply_text(
            f"✅ Test email sent successfully from `{target_email}`!\n"
            "Sender is now marked as verified.",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Test email failed for %s: %s", target_email, exc)
        await update.message.reply_text(
            f"❌ Test email failed:\n`{exc}`\n\nCheck your app password and SMTP settings.",
            parse_mode="Markdown",
        )


async def cmd_remove_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /removesender <email> - Remove a sender email from the database.

    This deactivates the sender rather than hard-deleting so historical
    ``email_logs`` records are not orphaned.
    """
    if not await check_admin(update, context):
        return

    if not context.args:
        await update.message.reply_text("Usage: /removesender <email_address>")
        return

    target_email: str = context.args[0].strip().lower()

    supabase = await get_client()
    try:
        # Check the sender exists
        check_resp = (
            await supabase.table("sender_emails")
            .select("id, email_address, is_active")
            .eq("email_address", target_email)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error checking sender: %s", exc)
        await update.message.reply_text("❌ Database error.  Check logs.")
        return

    if not check_resp.data:
        await update.message.reply_text(
            f"❌ Sender `{target_email}` not found.", parse_mode="Markdown"
        )
        return

    try:
        await (
            supabase.table("sender_emails")
            .update({"is_active": False})
            .eq("email_address", target_email)
            .execute()
        )
        await update.message.reply_text(
            f"✅ Sender `{target_email}` has been deactivated.\n"
            "Historical logs are preserved.",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Failed to deactivate sender %s: %s", target_email, exc)
        await update.message.reply_text("❌ Failed to remove sender.  Check logs.")
