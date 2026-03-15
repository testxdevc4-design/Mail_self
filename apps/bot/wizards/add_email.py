"""
apps/bot/wizards/add_email.py
==============================
Multi-step ConversationHandler for adding a new sender email.

States
------
ASK_EMAIL      → Prompt for the sender's email address.
ASK_PASSWORD   → Prompt for the SMTP app-password (deleted immediately).
ASK_PROVIDER   → Ask which SMTP provider (gmail/outlook/zoho/other).
ASK_SMTP_HOST  → (only for 'other') ask custom SMTP host + port.
CONFIRM        → Show summary and ask to confirm before saving.

On confirmation:
1. Encrypt the password with AES-256-GCM.
2. Save to ``sender_emails``.
3. Send a test email to verify the credentials.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import aiosmtplib
from email.mime.text import MIMEText
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from apps.bot.middleware.admin_gate import check_admin
from core.crypto import encrypt
from core.db import get_client

logger = logging.getLogger(__name__)

# ── Conversation state keys ────────────────────────────────────────────────────
ASK_EMAIL: int = 0
ASK_PASSWORD: int = 1
ASK_PROVIDER: int = 2
ASK_SMTP_HOST: int = 3
CONFIRM: int = 4

# ── SMTP provider defaults ─────────────────────────────────────────────────────
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 587},
    "outlook": {"smtp_host": "smtp.office365.com", "smtp_port": 587},
    "zoho": {"smtp_host": "smtp.zoho.com", "smtp_port": 587},
}

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


# ─────────────────────────────────────────────────────────────────────────────
# Step handlers
# ─────────────────────────────────────────────────────────────────────────────


async def start_add_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/addemail – Begin the add-email wizard."""
    if not await check_admin(update, context):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "📧 *Add Sender Email* (step 1/4)\n\n"
        "Please enter the email address you want to use as a sender:\n"
        "_e.g. noreply@yourdomain.com_\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_EMAIL


async def got_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate the email address."""
    email: str = update.message.text.strip().lower()

    if not _EMAIL_RE.match(email):
        await update.message.reply_text(
            "❌ That doesn't look like a valid email address.\nPlease try again or send /cancel."
        )
        return ASK_EMAIL

    context.user_data["email"] = email
    await update.message.reply_text(
        f"✅ Email: `{email}`\n\n"
        "📧 *Step 2/4* – Enter the SMTP **app password** for this address.\n"
        "⚠️ _Your message will be deleted immediately after processing._\n\n"
        "For Gmail, generate an app password at: myaccount.google.com → Security → App passwords",
        parse_mode="Markdown",
    )
    return ASK_PASSWORD


async def got_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the app password, immediately delete the message, store encrypted."""
    password: str = update.message.text.strip()

    # Immediately delete the message for security
    try:
        await update.message.delete()
    except Exception:
        pass  # Bot may not have delete permission in all chat types

    if len(password) < 6:
        await update.message.reply_text(
            "❌ Password seems too short (< 6 characters).\nPlease try again or send /cancel."
        )
        return ASK_PASSWORD

    context.user_data["password"] = password
    context.user_data["display_name"] = ""

    provider_keyboard = [["gmail", "outlook"], ["zoho", "other"]]
    await update.message.reply_text(
        "✅ Password received and secured.\n\n"
        "📧 *Step 3/4* – Which SMTP provider does this address use?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            provider_keyboard,
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return ASK_PROVIDER


async def got_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the provider choice."""
    provider: str = update.message.text.strip().lower()

    if provider not in ("gmail", "outlook", "zoho", "other"):
        await update.message.reply_text(
            "❌ Please choose one of: gmail, outlook, zoho, other"
        )
        return ASK_PROVIDER

    context.user_data["provider"] = provider

    if provider == "other":
        await update.message.reply_text(
            "📧 *Step 3b/4* – Enter the SMTP host and port separated by a space:\n"
            "_e.g. smtp.fastmail.com 587_",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ASK_SMTP_HOST

    # Use provider defaults
    defaults = PROVIDER_DEFAULTS[provider]
    context.user_data["smtp_host"] = defaults["smtp_host"]
    context.user_data["smtp_port"] = defaults["smtp_port"]
    return await _show_confirm(update, context)


async def got_smtp_host(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive custom SMTP host and port."""
    parts = update.message.text.strip().split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Please enter host and port separated by a space.\n"
            "Example: smtp.fastmail.com 587"
        )
        return ASK_SMTP_HOST

    host: str = parts[0].strip()
    try:
        port: int = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Port must be a number (e.g. 587).")
        return ASK_SMTP_HOST

    if not (1 <= port <= 65535):
        await update.message.reply_text("❌ Port must be between 1 and 65535.")
        return ASK_SMTP_HOST

    context.user_data["smtp_host"] = host
    context.user_data["smtp_port"] = port
    return await _show_confirm(update, context)


async def _show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show a summary and ask for confirmation before saving."""
    d = context.user_data
    await update.message.reply_text(
        "📧 *Step 4/4 – Confirm*\n\n"
        f"Email: `{d['email']}`\n"
        f"Provider: {d['provider']}\n"
        f"SMTP: `{d['smtp_host']}:{d['smtp_port']}`\n\n"
        "Type *yes* to save, or *no* / /cancel to abort.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["yes", "no"]],
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return CONFIRM


async def got_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the sender email to the database and run a test send."""
    answer: str = update.message.text.strip().lower()

    if answer != "yes":
        await update.message.reply_text(
            "Aborted.  Sender email was NOT saved.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    d = context.user_data
    await update.message.reply_text(
        "⏳ Saving sender and sending test email…",
        reply_markup=ReplyKeyboardRemove(),
    )

    # Encrypt password
    try:
        encrypted_password: str = encrypt(d["password"])
    except Exception as exc:
        logger.error("Failed to encrypt password: %s", exc)
        await update.message.reply_text(f"❌ Encryption failed: {exc}")
        return ConversationHandler.END

    supabase = await get_client()
    try:
        await supabase.table("sender_emails").insert(
            {
                "email_address": d["email"],
                "display_name": d.get("display_name") or d["email"],
                "provider": d["provider"],
                "smtp_host": d["smtp_host"],
                "smtp_port": d["smtp_port"],
                "app_password_enc": encrypted_password,
                "is_verified": False,
                "is_active": True,
            }
        ).execute()
    except Exception as exc:
        logger.error("Failed to save sender_email: %s", exc)
        await update.message.reply_text(
            f"❌ Failed to save sender: {exc}\n"
            "The email address may already be registered."
        )
        return ConversationHandler.END

    # Send test email
    msg = MIMEText(
        "This is a MailGuard test email.\n\nYour sender has been configured successfully!",
        "plain",
        "utf-8",
    )
    msg["Subject"] = "MailGuard – Sender Verified ✅"
    msg["From"] = d["email"]
    msg["To"] = d["email"]

    try:
        await aiosmtplib.send(
            msg,
            hostname=d["smtp_host"],
            port=d["smtp_port"],
            username=d["email"],
            password=d["password"],
            start_tls=True,
            timeout=15,
        )
        # Mark as verified
        await (
            supabase.table("sender_emails")
            .update({"is_verified": True})
            .eq("email_address", d["email"])
            .execute()
        )
        await update.message.reply_text(
            f"✅ Sender `{d['email']}` saved and verified!\n"
            "A test email was sent to confirm the credentials.",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.warning("Test email failed for %s: %s", d["email"], exc)
        await update.message.reply_text(
            f"⚠️ Sender saved but test email failed:\n`{exc}`\n\n"
            "Check your app password.  Use /testsender to retry.",
            parse_mode="Markdown",
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel during the wizard."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Wizard cancelled.  No changes were made.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# ConversationHandler factory
# ─────────────────────────────────────────────────────────────────────────────


def build_add_email_handler() -> ConversationHandler:
    """Return the fully configured ConversationHandler for /addemail."""
    return ConversationHandler(
        entry_points=[CommandHandler("addemail", start_add_email)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_email)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_password)],
            ASK_PROVIDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_provider)],
            ASK_SMTP_HOST: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_smtp_host)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        name="add_email_wizard",
        persistent=False,
    )
