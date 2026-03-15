"""
apps/bot/commands/keys.py
==========================
Telegram bot commands for managing API keys.

Commands
--------
/genkey <project_slug>   - Generate a new API key (plaintext shown once).
/keys <project_slug>     - List all keys for a project (prefix + status only).
/revokekey <key_prefix>  - Revoke a key by its prefix.
/testkey <key>           - Test whether a key is valid and show its project.

Security notes
--------------
* The plaintext key is **shown once** and never stored.  Only the SHA-256
  hash is persisted.
* Keys are prefixed with ``mg_live_`` (production) or ``mg_test_``
  (sandbox) so they are visually distinguishable.
* The prefix stored in the DB is the first 12 chars of the plaintext key
  (e.g. ``mg_live_xxxx``) for human-readable identification in the bot.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from apps.bot.middleware.admin_gate import check_admin
from core.db import get_client

logger = logging.getLogger(__name__)

_KEY_BODY_LENGTH: int = 32  # random bytes → 64 hex chars


def _generate_key(is_sandbox: bool) -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns:
        Tuple of (plaintext_key, key_hash, key_prefix).
    """
    prefix = "mg_test_" if is_sandbox else "mg_live_"
    body = secrets.token_hex(_KEY_BODY_LENGTH)
    plaintext = prefix + body
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    key_prefix = plaintext[:12]  # e.g. "mg_live_0abc"
    return plaintext, key_hash, key_prefix


async def cmd_gen_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /genkey <project_slug> [--sandbox]
    Generate a new API key for the specified project.

    Pass ``--sandbox`` to generate a sandbox key.
    """
    if not await check_admin(update, context):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /genkey <project_slug> [--sandbox]\n"
            "Example: /genkey myapp\n"
            "Example: /genkey myapp --sandbox"
        )
        return

    project_slug: str = context.args[0].strip().lower()
    is_sandbox: bool = "--sandbox" in (a.lower() for a in context.args[1:])

    supabase = await get_client()

    # Fetch project
    try:
        proj_resp = (
            await supabase.table("projects")
            .select("id, name, is_active")
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
            f"❌ Project `{project_slug}` not found.",
            parse_mode="Markdown",
        )
        return

    project: dict[str, Any] = proj_resp.data[0]
    if not project["is_active"]:
        await update.message.reply_text("❌ Cannot generate a key for an inactive project.")
        return

    plaintext, key_hash, key_prefix = _generate_key(is_sandbox)

    try:
        await supabase.table("api_keys").insert(
            {
                "project_id": project["id"],
                "key_hash": key_hash,
                "key_prefix": key_prefix,
                "label": f"Generated via bot ({'sandbox' if is_sandbox else 'live'})",
                "is_sandbox": is_sandbox,
                "is_active": True,
            }
        ).execute()
    except Exception as exc:
        logger.error("Failed to insert api_key: %s", exc)
        await update.message.reply_text("❌ Failed to save the key.  Check logs.")
        return

    key_type = "🔵 SANDBOX" if is_sandbox else "🟢 LIVE"
    await update.message.reply_text(
        f"✅ API key generated for *{project['name']}* ({key_type})\n\n"
        f"Copy this key - *it will NOT be shown again*:\n\n"
        f"`{plaintext}`\n\n"
        f"Key prefix: `{key_prefix}`",
        parse_mode="Markdown",
    )


async def cmd_list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /keys <project_slug> - List all API keys for a project (prefix only).
    """
    if not await check_admin(update, context):
        return

    if not context.args:
        await update.message.reply_text("Usage: /keys <project_slug>")
        return

    project_slug: str = context.args[0].strip().lower()
    supabase = await get_client()

    # Fetch project ID
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
        keys_resp = (
            await supabase.table("api_keys")
            .select("id, key_prefix, label, is_sandbox, is_active, last_used_at, created_at")
            .eq("project_id", project["id"])
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error fetching keys: %s", exc)
        await update.message.reply_text("❌ Database error.")
        return

    rows: list[dict[str, Any]] = keys_resp.data
    if not rows:
        await update.message.reply_text(
            f"No API keys found for *{project['name']}*.\n"
            "Use /genkey to create one.",
            parse_mode="Markdown",
        )
        return

    lines: list[str] = [f"*API Keys - {project['name']}*\n"]
    for row in rows:
        active_icon = "🟢" if row["is_active"] else "🔴"
        sandbox_tag = " [sandbox]" if row["is_sandbox"] else ""
        last_used = str(row["last_used_at"] or "never")[:10]
        created = str(row["created_at"])[:10]
        lines.append(
            f"{active_icon} `{row['key_prefix']}...`{sandbox_tag}\n"
            f"  Label: {row['label'] or '—'}\n"
            f"  Created: {created} | Last used: {last_used}\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_revoke_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /revokekey <key_prefix> - Revoke an API key by its prefix.
    """
    if not await check_admin(update, context):
        return

    if not context.args:
        await update.message.reply_text("Usage: /revokekey <key_prefix>")
        return

    key_prefix: str = context.args[0].strip()
    supabase = await get_client()

    try:
        check_resp = (
            await supabase.table("api_keys")
            .select("id, key_prefix, is_active")
            .eq("key_prefix", key_prefix)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error: %s", exc)
        await update.message.reply_text("❌ Database error.")
        return

    if not check_resp.data:
        await update.message.reply_text(
            f"❌ No key with prefix `{key_prefix}` found.", parse_mode="Markdown"
        )
        return

    key_row: dict[str, Any] = check_resp.data[0]
    if not key_row["is_active"]:
        await update.message.reply_text(
            f"⚠️ Key `{key_prefix}` is already revoked.", parse_mode="Markdown"
        )
        return

    try:
        await (
            supabase.table("api_keys")
            .update({"is_active": False})
            .eq("id", key_row["id"])
            .execute()
        )
        await update.message.reply_text(
            f"✅ Key `{key_prefix}` has been revoked.", parse_mode="Markdown"
        )
    except Exception as exc:
        logger.error("Failed to revoke key: %s", exc)
        await update.message.reply_text("❌ Failed to revoke key.  Check logs.")


async def cmd_test_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /testkey <key> - Validate an API key and show its project details.
    """
    if not await check_admin(update, context):
        return

    if not context.args:
        await update.message.reply_text("Usage: /testkey <api_key>")
        return

    plaintext: str = context.args[0].strip()
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    supabase = await get_client()

    try:
        key_resp = (
            await supabase.table("api_keys")
            .select("id, project_id, key_prefix, is_sandbox, is_active")
            .eq("key_hash", key_hash)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error: %s", exc)
        await update.message.reply_text("❌ Database error.")
        return

    if not key_resp.data:
        await update.message.reply_text("❌ Key not found in database.")
        return

    key_row: dict[str, Any] = key_resp.data[0]
    status = "🟢 Active" if key_row["is_active"] else "🔴 Revoked"
    sandbox = "Yes (sandbox)" if key_row["is_sandbox"] else "No (live)"

    # Fetch project name
    proj_name = "Unknown"
    try:
        proj_resp = (
            await supabase.table("projects")
            .select("name, slug")
            .eq("id", key_row["project_id"])
            .limit(1)
            .execute()
        )
        if proj_resp.data:
            proj_name = f"{proj_resp.data[0]['name']} ({proj_resp.data[0]['slug']})"
    except Exception:
        pass

    await update.message.reply_text(
        f"*Key Validation Result*\n\n"
        f"Prefix: `{key_row['key_prefix']}`\n"
        f"Status: {status}\n"
        f"Sandbox: {sandbox}\n"
        f"Project: {proj_name}",
        parse_mode="Markdown",
    )
