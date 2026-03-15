"""
apps/bot/wizards/new_project.py
================================
Multi-step ConversationHandler for creating a new MailGuard project.

States
------
ASK_NAME        → Project display name.
ASK_SLUG        → URL-safe slug (auto-suggested from the name).
ASK_SENDER      → Pick from existing active sender emails.
ASK_OTP_LENGTH  → OTP digit length (4 or 6).
ASK_EXPIRY      → OTP expiry in seconds.
CONFIRM         → Show summary and save.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from apps.bot.middleware.admin_gate import check_admin
from core.db import get_client

logger = logging.getLogger(__name__)

# ── Conversation state keys ────────────────────────────────────────────────────
ASK_NAME: int = 0
ASK_SLUG: int = 1
ASK_SENDER: int = 2
ASK_OTP_LENGTH: int = 3
ASK_EXPIRY: int = 4
CONFIRM: int = 5

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,98}[a-z0-9]$")


def _slugify(name: str) -> str:
    """Convert a display name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:60] or "project"


# ─────────────────────────────────────────────────────────────────────────────
# Step handlers
# ─────────────────────────────────────────────────────────────────────────────


async def start_new_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/newproject – Start the new-project wizard."""
    if not await check_admin(update, context):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "🆕 *New Project* (step 1/5)\n\n"
        "Enter a display name for this project:\n"
        "_e.g. My SaaS App_\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the project name and suggest a slug."""
    name: str = update.message.text.strip()
    if len(name) < 2 or len(name) > 100:
        await update.message.reply_text(
            "❌ Name must be between 2 and 100 characters.  Please try again."
        )
        return ASK_NAME

    context.user_data["name"] = name
    suggested_slug = _slugify(name)

    await update.message.reply_text(
        f"✅ Name: *{name}*\n\n"
        f"🆕 *Step 2/5* – Enter a URL slug for this project.\n"
        f"Suggested: `{suggested_slug}`\n\n"
        "The slug must be lowercase letters, numbers, and hyphens only.\n"
        "_Send the suggested slug or type your own._",
        parse_mode="Markdown",
    )
    context.user_data["suggested_slug"] = suggested_slug
    return ASK_SLUG


async def got_slug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate the slug."""
    raw: str = update.message.text.strip().lower()
    slug = raw if raw else context.user_data.get("suggested_slug", "")

    if not _SLUG_RE.match(slug):
        await update.message.reply_text(
            "❌ Invalid slug.  Use only lowercase letters (a-z), numbers (0-9), "
            "and hyphens (-).  Must start and end with a letter or number.\n"
            f"Suggested: `{context.user_data['suggested_slug']}`",
            parse_mode="Markdown",
        )
        return ASK_SLUG

    # Check uniqueness
    supabase = await get_client()
    try:
        check = (
            await supabase.table("projects")
            .select("id")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error checking slug: %s", exc)
        await update.message.reply_text("❌ Database error.  Try again.")
        return ASK_SLUG

    if check.data:
        await update.message.reply_text(
            f"❌ Slug `{slug}` is already taken.  Please choose a different one.",
            parse_mode="Markdown",
        )
        return ASK_SLUG

    context.user_data["slug"] = slug

    # Fetch available senders
    try:
        senders_resp = (
            await supabase.table("sender_emails")
            .select("email_address")
            .eq("is_active", True)
            .order("email_address")
            .execute()
        )
    except Exception as exc:
        logger.error("DB error fetching senders: %s", exc)
        await update.message.reply_text("❌ Database error.")
        return ConversationHandler.END

    sender_rows: list[dict[str, Any]] = senders_resp.data

    if not sender_rows:
        await update.message.reply_text(
            "⚠️ No active sender emails found.\n"
            "Add one with /addemail first, then retry /newproject."
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["available_senders"] = [r["email_address"] for r in sender_rows]
    keyboard = [[r["email_address"]] for r in sender_rows]
    keyboard.append(["(none – assign later)"])

    await update.message.reply_text(
        f"✅ Slug: `{slug}`\n\n"
        "🆕 *Step 3/5* – Select a sender email for this project:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return ASK_SENDER


async def got_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the sender selection."""
    selection: str = update.message.text.strip()

    if selection == "(none – assign later)":
        context.user_data["sender_email"] = None
    elif selection in context.user_data.get("available_senders", []):
        context.user_data["sender_email"] = selection
    else:
        await update.message.reply_text(
            "❌ Please select one of the options shown."
        )
        return ASK_SENDER

    await update.message.reply_text(
        "🆕 *Step 4/5* – OTP length\n\n"
        "How many digits should the OTP be?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["4", "6"]],
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return ASK_OTP_LENGTH


async def got_otp_length(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive OTP length."""
    raw: str = update.message.text.strip()
    if raw not in ("4", "6"):
        await update.message.reply_text("❌ Please choose 4 or 6.")
        return ASK_OTP_LENGTH

    context.user_data["otp_length"] = int(raw)

    expiry_keyboard = [["60", "120", "300"], ["600", "900", "1800"]]
    await update.message.reply_text(
        "🆕 *Step 5/5* – OTP expiry in seconds\n\n"
        "How long should each OTP be valid?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            expiry_keyboard,
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return ASK_EXPIRY


async def got_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive expiry and show confirmation."""
    raw: str = update.message.text.strip()
    try:
        expiry: int = int(raw)
    except ValueError:
        await update.message.reply_text("❌ Please enter a number of seconds.")
        return ASK_EXPIRY

    if not (30 <= expiry <= 86400):
        await update.message.reply_text("❌ Expiry must be between 30 and 86400 seconds.")
        return ASK_EXPIRY

    context.user_data["otp_expiry_seconds"] = expiry

    d = context.user_data
    sender_display = d.get("sender_email") or "— (none)"
    expiry_min = expiry // 60
    expiry_str = f"{expiry}s ({expiry_min}m)" if expiry_min >= 1 else f"{expiry}s"

    await update.message.reply_text(
        "🆕 *Confirm New Project*\n\n"
        f"Name:    {d['name']}\n"
        f"Slug:    `{d['slug']}`\n"
        f"Sender:  {sender_display}\n"
        f"OTP:     {d['otp_length']} digits, expires {expiry_str}\n\n"
        "Type *yes* to create, or *no* / /cancel to abort.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["yes", "no"]],
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return CONFIRM


async def got_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the project to the database."""
    answer: str = update.message.text.strip().lower()
    if answer != "yes":
        await update.message.reply_text(
            "Aborted.  No project was created.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    d = context.user_data
    supabase = await get_client()

    # Resolve sender email to ID
    sender_email_id: str | None = None
    if d.get("sender_email"):
        try:
            sender_resp = (
                await supabase.table("sender_emails")
                .select("id")
                .eq("email_address", d["sender_email"])
                .limit(1)
                .execute()
            )
            if sender_resp.data:
                sender_email_id = sender_resp.data[0]["id"]
        except Exception as exc:
            logger.warning("Could not resolve sender email ID: %s", exc)

    try:
        await supabase.table("projects").insert(
            {
                "name": d["name"],
                "slug": d["slug"],
                "sender_email_id": sender_email_id,
                "otp_length": d["otp_length"],
                "otp_expiry_seconds": d["otp_expiry_seconds"],
                "is_active": True,
            }
        ).execute()
    except Exception as exc:
        logger.error("Failed to create project: %s", exc)
        await update.message.reply_text(
            f"❌ Failed to create project: {exc}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ Project *{d['name']}* (`{d['slug']}`) created!\n\n"
        "Next steps:\n"
        "• Use /genkey to generate an API key.\n"
        f"• Use /assignsender {d['slug']} <email> if you skipped the sender step.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel during the wizard."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Wizard cancelled.  No project was created.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# ConversationHandler factory
# ─────────────────────────────────────────────────────────────────────────────


def build_new_project_handler() -> ConversationHandler:
    """Return the fully configured ConversationHandler for /newproject."""
    return ConversationHandler(
        entry_points=[CommandHandler("newproject", start_new_project)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
            ASK_SLUG: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_slug)],
            ASK_SENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_sender)],
            ASK_OTP_LENGTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_otp_length)],
            ASK_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_expiry)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        name="new_project_wizard",
        persistent=False,
    )
