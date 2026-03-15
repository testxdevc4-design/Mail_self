"""
apps/bot/commands/project.py
=============================
Telegram bot commands for managing projects.

Commands
--------
/projects             - List all projects with configuration summary.
/assignsender         - Assign a sender email to a project.
/setotp               - Update OTP configuration for a project.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from apps.bot.middleware.admin_gate import check_admin
from core.db import get_client

logger = logging.getLogger(__name__)


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /projects - List all projects with their OTP and sender configuration.
    """
    if not await check_admin(update, context):
        return

    supabase = await get_client()
    try:
        response = await supabase.table("projects").select(
            "id, name, slug, otp_length, otp_expiry_seconds, otp_max_attempts, "
            "rate_limit_per_hour, is_active, sender_email_id"
        ).order("created_at").execute()
    except Exception as exc:
        logger.error("Failed to fetch projects: %s", exc)
        await update.message.reply_text("❌ Failed to fetch projects.  Check logs.")
        return

    rows: list[dict[str, Any]] = response.data
    if not rows:
        await update.message.reply_text(
            "No projects found.\nUse /newproject to create one."
        )
        return

    lines: list[str] = ["*Projects*\n"]
    for row in rows:
        status_icon = "🟢" if row["is_active"] else "🔴"
        has_sender = "✅" if row["sender_email_id"] else "⚠️ no sender"
        expiry_min = row["otp_expiry_seconds"] // 60
        lines.append(
            f"{status_icon} *{row['name']}* (`{row['slug']}`)\n"
            f"  OTP: {row['otp_length']} digits, expires {expiry_min}m, "
            f"max {row['otp_max_attempts']} attempts\n"
            f"  Rate: {row['rate_limit_per_hour']}/hr | Sender: {has_sender}\n"
            f"  ID: `{row['id']}`\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_assign_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /assignsender <project_slug> <sender_email> - Assign a sender to a project.
    """
    if not await check_admin(update, context):
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /assignsender <project_slug> <sender_email>\n"
            "Example: /assignsender myapp noreply@example.com"
        )
        return

    project_slug: str = context.args[0].strip().lower()
    sender_email: str = context.args[1].strip().lower()

    supabase = await get_client()

    # Fetch project
    try:
        proj_resp = (
            await supabase.table("projects")
            .select("id, name")
            .eq("slug", project_slug)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error fetching project: %s", exc)
        await update.message.reply_text("❌ Database error.  Check logs.")
        return

    if not proj_resp.data:
        await update.message.reply_text(
            f"❌ Project `{project_slug}` not found.",
            parse_mode="Markdown",
        )
        return

    project: dict[str, Any] = proj_resp.data[0]

    # Fetch sender
    try:
        sender_resp = (
            await supabase.table("sender_emails")
            .select("id, email_address, is_active")
            .eq("email_address", sender_email)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error fetching sender: %s", exc)
        await update.message.reply_text("❌ Database error.  Check logs.")
        return

    if not sender_resp.data:
        await update.message.reply_text(
            f"❌ Sender `{sender_email}` not found.\nUse /addemail to add it first.",
            parse_mode="Markdown",
        )
        return

    sender: dict[str, Any] = sender_resp.data[0]
    if not sender["is_active"]:
        await update.message.reply_text(
            f"⚠️ Sender `{sender_email}` is not active.  Please reactivate it first.",
            parse_mode="Markdown",
        )
        return

    # Assign
    try:
        await (
            supabase.table("projects")
            .update({"sender_email_id": sender["id"]})
            .eq("id", project["id"])
            .execute()
        )
        await update.message.reply_text(
            f"✅ Sender `{sender_email}` assigned to project *{project['name']}*.",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Failed to assign sender to project: %s", exc)
        await update.message.reply_text("❌ Failed to assign sender.  Check logs.")


async def cmd_set_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setotp <project_slug> <length> <expiry_seconds> <max_attempts>
    - Update OTP settings for a project.

    Example: /setotp myapp 6 600 5
    """
    if not await check_admin(update, context):
        return

    if not context.args or len(context.args) < 4:
        await update.message.reply_text(
            "Usage: /setotp <project_slug> <length> <expiry_seconds> <max_attempts>\n"
            "Example: /setotp myapp 6 600 5"
        )
        return

    project_slug: str = context.args[0].strip().lower()
    try:
        otp_length: int = int(context.args[1])
        expiry_seconds: int = int(context.args[2])
        max_attempts: int = int(context.args[3])
    except ValueError:
        await update.message.reply_text(
            "❌ length, expiry_seconds, and max_attempts must all be integers."
        )
        return

    if otp_length not in (4, 6, 8):
        await update.message.reply_text("❌ OTP length must be 4, 6, or 8.")
        return
    if not (60 <= expiry_seconds <= 86400):
        await update.message.reply_text("❌ expiry_seconds must be between 60 and 86400.")
        return
    if not (1 <= max_attempts <= 10):
        await update.message.reply_text("❌ max_attempts must be between 1 and 10.")
        return

    supabase = await get_client()
    try:
        proj_resp = (
            await supabase.table("projects")
            .select("id, name")
            .eq("slug", project_slug)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error: %s", exc)
        await update.message.reply_text("❌ Database error.")
        return

    if not proj_resp.data:
        await update.message.reply_text(
            f"❌ Project `{project_slug}` not found.",
            parse_mode="Markdown",
        )
        return

    project: dict[str, Any] = proj_resp.data[0]

    try:
        await (
            supabase.table("projects")
            .update(
                {
                    "otp_length": otp_length,
                    "otp_expiry_seconds": expiry_seconds,
                    "otp_max_attempts": max_attempts,
                }
            )
            .eq("id", project["id"])
            .execute()
        )
        await update.message.reply_text(
            f"✅ OTP config updated for *{project['name']}*:\n"
            f"  Length: {otp_length} digits\n"
            f"  Expiry: {expiry_seconds}s ({expiry_seconds // 60}m)\n"
            f"  Max attempts: {max_attempts}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("Failed to update OTP config: %s", exc)
        await update.message.reply_text("❌ Failed to update OTP config.  Check logs.")
