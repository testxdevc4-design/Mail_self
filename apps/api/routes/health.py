"""
apps/api/routes/health.py
=========================
Liveness and readiness probes.

``GET /health``
    Returns 200 if both Supabase and Redis are reachable, 503 otherwise.
    Used by Railway, Docker health-checks, and uptime monitors.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from core.db import get_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/health",
    summary="Liveness & readiness probe",
    response_description="Service health status",
)
async def health_check(request: Request) -> JSONResponse:
    """
    Check connectivity to Supabase and Redis.

    Returns ``200 OK`` when all dependencies are reachable, ``503`` otherwise.
    The response body includes individual dependency statuses so that
    infrastructure tooling can identify which component is unhealthy.
    """
    checks: dict[str, Any] = {
        "supabase": "ok",
        "redis": "ok",
    }
    all_ok: bool = True

    # ── Supabase check ────────────────────────────────────────────────────────
    try:
        supabase = await get_client()
        # A lightweight query that exercises the connection without reading data
        await supabase.table("projects").select("id").limit(1).execute()
    except Exception as exc:
        logger.warning("Health check: Supabase unreachable - %s", exc)
        checks["supabase"] = f"error: {type(exc).__name__}"
        all_ok = False

    # ── Redis check ───────────────────────────────────────────────────────────
    try:
        redis = request.app.state.redis
        await redis.ping()
    except Exception as exc:
        logger.warning("Health check: Redis unreachable - %s", exc)
        checks["redis"] = f"error: {type(exc).__name__}"
        all_ok = False

    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if all_ok else "degraded",
            "checks": checks,
        },
    )
