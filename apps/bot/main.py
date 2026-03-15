"""
apps/bot/main.py
================
Telegram bot entry-point for the MailGuard admin interface.

All management of sender emails, projects, API keys, and logs is done
through this bot.  Only the configured ``TELEGRAM_ADMIN_UID`` may interact
with it.

Start the bot:
    python apps/bot/main.py

Commands registered
-------------------
/start          - Show help message.
/help           - Show the full command reference.
/senders        - List sender emails.
/addemail       - Add sender email (wizard).
/testsender     - Test a sender email.
/removesender   - Deactivate a sender email.
/projects       - List projects.
/newproject     - Create a project (wizard).
/assignsender   - Assign sender to project.
/setotp         - Update OTP config.
/genkey         - Generate an API key.
/keys           - List API keys for project.
/revokekey      - Revoke an API key.
/testkey        - Validate an API key.
/logs           - View email delivery logs.
/cancel         - Cancel current wizard.
"""

from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from apps.bot.commands.keys import (
    cmd_gen_key,
    cmd_list_keys,
    cmd_revoke_key,
    cmd_test_key,
)
from apps.bot.commands.logs import cmd_logs
from apps.bot.commands.project import cmd_assign_sender, cmd_projects, cmd_set_otp
from apps.bot.commands.sender import cmd_remove_sender, cmd_senders, cmd_test_sender
from apps.bot.middleware.admin_gate import check_admin
from apps.bot.wizards.add_email import build_add_email_handler
from apps.bot.wizards.new_project import build_new_project_handler
from core.config import settings

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_HELP_TEXT = """
*MailGuard Admin Bot*

*Sender Management*
/senders - List all senders
/addemail - Add a sender (wizard)
/testsender <email> - Test a sender
/removesender <email> - Deactivate a sender

*Project Management*
/projects - List all projects
/newproject - Create a project (wizard)
/assignsender <slug> <email> - Assign sender
/setotp <slug> <len> <expiry> <max\\_attempts> - Update OTP config

*API Key Management*
/genkey <slug> [--sandbox] - Generate API key
/keys <slug> - List keys for a project
/revokekey <prefix> - Revoke a key
/testkey <key> - Validate a key

*Logs*
/logs - Recent logs
/logs <slug> - Logs for a project
/logs --failed - Failed deliveries
/logs --today - Today's logs
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start - show a welcome message."""
    if not await check_admin(update, context):
        return
    await update.message.reply_text(
        "👋 Welcome to *MailGuard Admin Bot*!\n\n"
        "Send /help to see all available commands.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help - show the command reference."""
    if not await check_admin(update, context):
        return
    await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all unhandled exceptions raised by handlers."""
    logger.error("Unhandled exception in bot handler", exc_info=context.error)


def build_application() -> Application:  # type: ignore[type-arg]
    """Construct the PTB Application with all handlers registered."""
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # ── Wizard handlers (must come before plain CommandHandlers) ──────────────
    app.add_handler(build_add_email_handler())
    app.add_handler(build_new_project_handler())

    # ── Simple command handlers ───────────────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Sender
    app.add_handler(CommandHandler("senders", cmd_senders))
    app.add_handler(CommandHandler("testsender", cmd_test_sender))
    app.add_handler(CommandHandler("removesender", cmd_remove_sender))

    # Project
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("assignsender", cmd_assign_sender))
    app.add_handler(CommandHandler("setotp", cmd_set_otp))

    # Keys
    app.add_handler(CommandHandler("genkey", cmd_gen_key))
    app.add_handler(CommandHandler("keys", cmd_list_keys))
    app.add_handler(CommandHandler("revokekey", cmd_revoke_key))
    app.add_handler(CommandHandler("testkey", cmd_test_key))

    # Logs
    app.add_handler(CommandHandler("logs", cmd_logs))

    # ── Error handler ─────────────────────────────────────────────────────────
    app.add_error_handler(error_handler)

    return app


async def _set_bot_commands(app: Application) -> None:  # type: ignore[type-arg]
    """Register the command list in Telegram so it appears in the menu."""
    commands = [
        BotCommand("start", "Show welcome message"),
        BotCommand("help", "Show all commands"),
        BotCommand("senders", "List sender emails"),
        BotCommand("addemail", "Add a sender email"),
        BotCommand("testsender", "Test a sender email"),
        BotCommand("removesender", "Deactivate a sender"),
        BotCommand("projects", "List projects"),
        BotCommand("newproject", "Create a new project"),
        BotCommand("assignsender", "Assign sender to project"),
        BotCommand("setotp", "Update OTP config"),
        BotCommand("genkey", "Generate an API key"),
        BotCommand("keys", "List API keys"),
        BotCommand("revokekey", "Revoke a key"),
        BotCommand("testkey", "Validate a key"),
        BotCommand("logs", "View delivery logs"),
        BotCommand("cancel", "Cancel current wizard"),
    ]
    await app.bot.set_my_commands(commands)


def main() -> None:
    """Start the Telegram bot in polling mode."""
    logger.info("MailGuard bot starting (ENV=%s)", settings.ENV)
    app = build_application()

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
