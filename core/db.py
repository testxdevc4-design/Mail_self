"""
core/db.py
==========
Supabase client singleton.

Uses the *service-role* key which bypasses Row Level Security - this is
intentional because all business logic enforces its own authorisation.

Import pattern::

    from core.db import supabase
"""

from __future__ import annotations

from supabase import AsyncClient, acreate_client

from core.config import settings

# Module-level reference - populated lazily via ``get_client()``
_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    """
    Return the module-level Supabase AsyncClient, creating it on first call.

    All three services (api, worker, bot) call this function; the ``global``
    assignment means only one client exists per process.
    """
    global _client  # noqa: PLW0603 - intentional module-level singleton
    if _client is None:
        _client = await acreate_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,
        )
    return _client


async def close_client() -> None:
    """Close the Supabase client connection (call on application shutdown)."""
    global _client  # noqa: PLW0603
    if _client is not None:
        await _client.aclose()
        _client = None
