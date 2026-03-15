"""
apps/bot/commands/logs.py
==========================
Telegram bot commands for viewing email delivery logs.

Commands
--------
/logs                   – Show the 10 most recent logs across all projects.
/logs <slug>            – Show the 10 most recent logs for a project.
/logs --failed          – Show the 10 most recent failed deliveries.
/logs --today           – Show all logs from today (UTC).

Usage is parsed from ``context.args`` so all four forms are handled by a
single command handler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from apps.bot.middleware.admin_gate import check_admin
from core.db import get_client

logger = logging.getLogger(__name__)

_MAX_ROWS: int = 10
_STATUS_ICON: dict[str, str] = {
    "sent": "✅",
    "failed": "❌",
    "pending": "⏳",
    "retrying": "🔄",
}


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /logs [<project_slug> | --failed | --today]

    Delegates to the appropriate query based on the first argument.
    """
    if not await check_admin(update, context):
        return

    args: list[str] = context.args or []
    supabase = await get_client()

    query = (
        supabase.table("email_logs")
        .select(
            "id, project_id, status, error_message, attempt_count, "
            "sent_at, created_at"
        )
    )

    title_suffix: str = "Recent Logs"

    if args and args[0] == "--failed":
        query = query.eq("status", "failed")
        title_suffix = "Failed Deliveries"

    elif args and args[0] == "--today":
        today_start = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        query = query.gte("created_at", today_start)
        title_suffix = "Today's Logs"

    elif args and not args[0].startswith("--"):
        project_slug: str = args[0].strip().lower()
        # Resolve project slug → id
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
            await update.message.reply_text("❌ Database error.")
            return

        if not proj_resp.data:
            await update.message.reply_text(
                f"❌ Project `{project_slug}` not found.", parse_mode="Markdown"
            )
            return

        project: dict[str, Any] = proj_resp.data[0]
        query = query.eq("project_id", project["id"])
        title_suffix = f"Logs – {project['name']}"

    try:
        response = (
            await query
            .order("created_at", desc=True)
            .limit(_MAX_ROWS)
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to fetch email_logs: %s", exc)
        await update.message.reply_text("❌ Failed to fetch logs.  Check logs.")
        return

    rows: list[dict[str, Any]] = response.data
    if not rows:
        await update.message.reply_text(
            f"📭 No logs found for: *{title_suffix}*", parse_mode="Markdown"
        )
        return

    lines: list[str] = [f"*{title_suffix}* (showing {len(rows)})\n"]
    for row in rows:
        icon = _STATUS_ICON.get(row["status"], "❓")
        created = str(row["created_at"])[:16].replace("T", " ")
        error_snippet = ""
        if row["status"] == "failed" and row.get("error_message"):
            error_snippet = f"\n    ⚠️ {row['error_message'][:80]}"
        lines.append(
            f"{icon} `{created}` – {row['status']} "
            f"(attempts: {row['attempt_count']})"
            f"{error_snippet}"
        )

    # Stats footer
    total_count = len(rows)
    sent_count = sum(1 for r in rows if r["status"] == "sent")
    failed_count = sum(1 for r in rows if r["status"] == "failed")
    lines.append(
        f"\n📊 Showing {total_count} | ✅ {sent_count} sent | ❌ {failed_count} failed"
    )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
