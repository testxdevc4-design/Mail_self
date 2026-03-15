"""
apps/bot/middleware/admin_gate.py
==================================
Admin access guard for the Telegram management bot.

Only the single ``TELEGRAM_ADMIN_UID`` configured in settings may interact
with the bot.  All other users are silently dropped – no error message is
sent back so the bot's existence remains opaque to unauthorised users.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from core.config import settings

logger = logging.getLogger(__name__)


async def check_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Verify that the incoming update originates from the configured admin UID.

    Args:
        update:  Incoming Telegram Update object.
        context: PTB application context (unused but required by filter signature).

    Returns:
        ``True``  – user is the configured admin; handler should proceed.
        ``False`` – user is not the admin; handler should abort silently.
    """
    user = update.effective_user
    if user is None:
        return False

    if user.id != settings.TELEGRAM_ADMIN_UID:
        logger.warning(
            "Unauthorised access attempt from user_id=%d username=%s",
            user.id,
            user.username or "unknown",
        )
        return False

    return True
