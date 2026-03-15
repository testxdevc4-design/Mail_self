"""
apps/api/main.py
================
FastAPI application factory and lifespan manager.

Startup order
-------------
1. Connect to Redis (module-level singleton in core.rate_limit)
2. Verify Supabase reachability
3. Mount middleware (CORS → secure headers → request-id)
4. Register routers

Shutdown order
--------------
1. Close Redis connection
2. Close Supabase client
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from secure import Secure

from apps.api.routes import health, otp
from core.config import settings
from core.db import close_client, get_client

logger = logging.getLogger(__name__)

# ── Module-level Redis client shared across the api process ───────────────────
redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return the module-level Redis client (must be initialised first)."""
    if redis_client is None:
        raise RuntimeError("Redis client has not been initialised")
    return redis_client


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and graceful shutdown for the FastAPI process."""
    global redis_client  # noqa: PLW0603

    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("MailGuard API starting up (ENV=%s)", settings.ENV)

    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    await redis_client.ping()
    logger.info("Redis connected")

    await get_client()
    logger.info("Supabase client initialised")

    # Store redis_client in app.state so routes can access it via request.app.state
    app.state.redis = redis_client

    yield  # ── Application running ──────────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("MailGuard API shutting down")
    if redis_client:
        await redis_client.aclose()
        logger.info("Redis connection closed")
    await close_client()
    logger.info("Supabase connection closed")


# ── Application factory ───────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Construct and return the configured FastAPI instance."""

    _app = FastAPI(
        title="MailGuard OTP API",
        description="Self-hosted OTP send/verify service.",
        version="1.0.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    origins: list[str] = (
        settings.ALLOWED_ORIGINS if settings.ALLOWED_ORIGINS else ["*"]
    )
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # ── Secure HTTP headers ───────────────────────────────────────────────────
    # secure==0.3.0 uses Secure() directly; .headers() returns a dict
    secure_headers = Secure()

    @_app.middleware("http")
    async def add_secure_headers(request: Request, call_next: Any) -> Response:  # type: ignore[misc]
        response: Response = await call_next(request)
        for header_name, header_value in secure_headers.headers().items():
            response.headers[header_name] = header_value
        return response

    # ── Request-ID middleware ─────────────────────────────────────────────────
    @_app.middleware("http")
    async def add_request_id(request: Request, call_next: Any) -> Response:  # type: ignore[misc]
        request_id: str = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # ── Routers ───────────────────────────────────────────────────────────────
    _app.include_router(health.router, tags=["health"])
    _app.include_router(otp.router, prefix="/api/v1", tags=["otp"])

    return _app


# ── Module-level app instance (used by uvicorn) ────────────────────────────────
app: FastAPI = create_app()
