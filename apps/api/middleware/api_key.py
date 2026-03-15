"""
apps/api/middleware/api_key.py
==============================
FastAPI dependency for Bearer API-key authentication.

Flow
----
1. Extract the token from ``Authorization: Bearer <token>`` header.
2. Reject ``mg_test_`` prefixed keys in production.
3. SHA-256 hash the token and look it up in the ``api_keys`` table.
4. Verify the key is active and fetch the associated project record.
5. Return a ``ValidatedKey`` data class that routes can use directly.

The plaintext key is **never** stored in the database - only its SHA-256
hex digest is persisted, so a database breach does not expose live keys.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import settings
from core.db import get_client

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)

# Module-level set to prevent garbage collection of fire-and-forget asyncio tasks.
# Tasks are added here and automatically removed via done callbacks.
_background_tasks: set[Any] = set()


@dataclass(frozen=True, slots=True)
class ValidatedKey:
    """Carries the resolved key and project records for downstream use."""

    key_id: str
    project_id: str
    project: dict[str, Any]
    is_sandbox: bool


def _hash_key(plaintext: str) -> str:
    """Return the lowercase hex SHA-256 digest of *plaintext*."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


async def get_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> ValidatedKey:
    """
    FastAPI dependency: validate the Bearer API key and return key metadata.

    Raises:
        HTTPException 401: Missing or malformed Authorization header.
        HTTPException 403: Test key used in production.
        HTTPException 401: Key not found or revoked.
        HTTPException 403: Associated project is inactive.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "MISSING_API_KEY",
                "message": "Authorization header with Bearer token is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    token: str = credentials.credentials

    # ── Production guard for test keys ────────────────────────────────────────
    if settings.is_production and token.startswith("mg_test_"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TEST_KEY_IN_PRODUCTION",
                "message": "Sandbox API keys cannot be used in production.",
            },
        )

    key_hash: str = _hash_key(token)

    supabase = await get_client()

    # ── Look up the API key ───────────────────────────────────────────────────
    try:
        key_response = (
            await supabase.table("api_keys")
            .select("id, project_id, is_sandbox, is_active, last_used_at")
            .eq("key_hash", key_hash)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("Database error during API key lookup: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DB_UNAVAILABLE",
                "message": "Service temporarily unavailable.  Please retry.",
            },
        ) from exc

    rows = key_response.data
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_API_KEY",
                "message": "The provided API key is invalid.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    key_row: dict[str, Any] = rows[0]
    if not key_row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "REVOKED_API_KEY",
                "message": "This API key has been revoked.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Fetch the associated project ──────────────────────────────────────────
    try:
        project_response = (
            await supabase.table("projects")
            .select("*")
            .eq("id", key_row["project_id"])
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("Database error during project lookup: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DB_UNAVAILABLE",
                "message": "Service temporarily unavailable.  Please retry.",
            },
        ) from exc

    project_rows = project_response.data
    if not project_rows:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "PROJECT_NOT_FOUND",
                "message": "The project associated with this key was not found.",
            },
        )

    project: dict[str, Any] = project_rows[0]
    if not project["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PROJECT_INACTIVE",
                "message": "The project associated with this key is not active.",
            },
        )

    # ── Update last_used_at asynchronously (fire-and-forget) ──────────────────
    # We intentionally do not await this to keep response latency low.
    # asyncio.create_task is preferred over ensure_future (Python 3.7+).
    import asyncio

    async def _update_last_used() -> None:
        try:
            await (
                supabase.table("api_keys")
                .update({"last_used_at": "now()"})
                .eq("id", key_row["id"])
                .execute()
            )
        except Exception:
            pass  # Non-critical; never block the request path

    task = asyncio.create_task(_update_last_used())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return ValidatedKey(
        key_id=key_row["id"],
        project_id=key_row["project_id"],
        project=project,
        is_sandbox=key_row["is_sandbox"],
    )
