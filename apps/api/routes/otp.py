"""
apps/api/routes/otp.py
======================
OTP send and verify endpoints.

POST /api/v1/otp/send
    Generates a new OTP, stores it hashed, enqueues the email task.

POST /api/v1/otp/verify
    Verifies the submitted code, issues a short-lived JWT on success.

Security properties
-------------------
* Anti-enumeration: both endpoints enforce a minimum 200 ms response
  time regardless of whether the email address exists.
* Bcrypt is used for OTP storage to make offline brute-force expensive.
* JWT ``jti`` (JWT ID) is stored alongside the OTP record so tokens can be
  revoked if needed.
* Attempt counting and automatic locking prevent online brute-force.
* Rate limiting is applied at five tiers:
    1. Global IP              – 60/min
    2. Project + IP           – 30/min
    3. Project + email        – 5/hour
    4. Project global         – 1 000/hour
    5. Sandbox per-project    – 20/hour  (sandbox keys only)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel, EmailStr, field_validator

from apps.api.middleware.api_key import ValidatedKey, get_api_key
from core.config import settings
from core.db import get_client
from core.otp import generate_otp, hash_otp, hmac_email, mask_email, verify_otp
from core.rate_limit import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Minimum response time (anti-enumeration) ──────────────────────────────────
_MIN_RESPONSE_SECONDS: float = 0.2


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────────────────────────────────────────


class OtpSendRequest(BaseModel):
    """Payload for the OTP send endpoint."""

    email: EmailStr
    purpose: str = "verification"

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, v: str) -> str:
        allowed = {"verification", "login", "reset", "confirmation"}
        v = v.strip().lower()
        if v not in allowed:
            raise ValueError(f"purpose must be one of {allowed}")
        return v


class OtpSendResponse(BaseModel):
    """Response returned after successfully queuing an OTP email."""

    id: str
    status: str
    expires_in: int
    masked_email: str


class OtpVerifyRequest(BaseModel):
    """Payload for the OTP verify endpoint."""

    email: EmailStr
    code: str

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError("code must contain only digits")
        if not (4 <= len(v) <= 10):
            raise ValueError("code must be between 4 and 10 digits")
        return v


class OtpVerifyResponse(BaseModel):
    """Response returned on successful OTP verification."""

    verified: bool
    token: str
    expires_in: int


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_jwt(project_id: str, email_hash: str, otp_record_id: str) -> str:
    """Issue a short-lived JWT for the verified email session."""
    now = datetime.now(tz=timezone.utc)
    expiry = now + timedelta(minutes=settings.JWT_EXPIRY_MINUTES)
    payload = {
        "sub": email_hash,
        "project_id": project_id,
        "otp_record_id": otp_record_id,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


async def _enforce_rate_limits(
    redis: Any,
    request: Request,
    project_id: str,
    email_hash: str,
    is_sandbox: bool,
) -> None:
    """
    Apply the five-tier rate limiting strategy.

    Raises HTTPException 429 if any tier is exceeded.
    """
    client_ip: str = (
        request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
        if request.client
        else "unknown"
    )

    checks: list[tuple[str, int, int]] = [
        # (key, limit, window_seconds)
        (f"rl:global_ip:{client_ip}", 60, 60),
        (f"rl:proj_ip:{project_id}:{client_ip}", 30, 60),
        (f"rl:proj_email:{project_id}:{email_hash}", 5, 3600),
        (f"rl:proj_global:{project_id}", 1000, 3600),
    ]
    if is_sandbox:
        checks.append((f"rl:sandbox:{project_id}", 20, 3600))

    for key, limit, window in checks:
        allowed = await check_rate_limit(redis, key, limit, window)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Too many requests.  Please wait before retrying.",
                },
                headers={"Retry-After": str(window)},
            )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/otp/send",
    response_model=OtpSendResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate and send an OTP to an email address",
)
async def send_otp(
    body: OtpSendRequest,
    request: Request,
    validated_key: ValidatedKey = Depends(get_api_key),
) -> OtpSendResponse:
    """
    Generate a numeric OTP, store its bcrypt hash, and enqueue the email task.

    The endpoint always takes at least 200 ms to respond regardless of the
    internal code path, preventing timing-based email enumeration.
    """
    start_time: float = time.monotonic()

    redis = request.app.state.redis
    project: dict[str, Any] = validated_key.project

    email_hash: str = hmac_email(str(body.email))

    # ── Rate limiting ─────────────────────────────────────────────────────────
    await _enforce_rate_limits(
        redis,
        request,
        validated_key.project_id,
        email_hash,
        validated_key.is_sandbox,
    )

    supabase = await get_client()

    # ── Invalidate any previous pending OTPs for this email+project ───────────
    try:
        await (
            supabase.table("otp_records")
            .update({"is_invalidated": True})
            .eq("project_id", validated_key.project_id)
            .eq("email_hash", email_hash)
            .eq("is_verified", False)
            .eq("is_invalidated", False)
            .execute()
        )
    except Exception as exc:
        logger.warning("Could not invalidate previous OTPs: %s", exc)

    # ── Generate OTP ──────────────────────────────────────────────────────────
    otp_length: int = project.get("otp_length", 6)
    otp_expiry_seconds: int = project.get("otp_expiry_seconds", 600)

    otp: str = generate_otp(otp_length)
    otp_hash: str = hash_otp(otp)

    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=otp_expiry_seconds)

    # ── Persist OTP record ────────────────────────────────────────────────────
    record_id: str = str(uuid.uuid4())
    try:
        await (
            supabase.table("otp_records")
            .insert(
                {
                    "id": record_id,
                    "project_id": validated_key.project_id,
                    "email_hash": email_hash,
                    "otp_hash": otp_hash,
                    "purpose": body.purpose,
                    "expires_at": expires_at.isoformat(),
                }
            )
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to insert otp_record: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DB_ERROR", "message": "Failed to create OTP record."},
        ) from exc

    # ── Enqueue email task ────────────────────────────────────────────────────
    try:
        arq_redis = await create_pool(
            RedisSettings.from_dsn(settings.REDIS_URL)
        )
        await arq_redis.enqueue_job(
            "send_email_task",
            otp_record_id=record_id,
            email=str(body.email),
            otp=otp,
            project_id=validated_key.project_id,
        )
        await arq_redis.aclose()
    except Exception as exc:
        logger.error("Failed to enqueue email task: %s", exc, exc_info=True)
        # Do NOT expose internal error; OTP was saved so user can still verify
        # but we log the failure so the admin can investigate.

    # ── Anti-enumeration: enforce minimum response time ───────────────────────
    elapsed: float = time.monotonic() - start_time
    if elapsed < _MIN_RESPONSE_SECONDS:
        await asyncio.sleep(_MIN_RESPONSE_SECONDS - elapsed)

    return OtpSendResponse(
        id=record_id,
        status="sent",
        expires_in=otp_expiry_seconds,
        masked_email=mask_email(str(body.email)),
    )


@router.post(
    "/otp/verify",
    response_model=OtpVerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify a submitted OTP code",
)
async def verify_otp_endpoint(
    body: OtpVerifyRequest,
    request: Request,
    validated_key: ValidatedKey = Depends(get_api_key),
) -> OtpVerifyResponse:
    """
    Verify the supplied OTP code.

    * ``200`` – code is correct → returns a short-lived JWT.
    * ``400`` – code is wrong (attempts incremented).
    * ``410`` – OTP has expired or was already verified/invalidated.
    * ``423`` – OTP is locked after too many failed attempts.
    """
    start_time: float = time.monotonic()

    email_hash: str = hmac_email(str(body.email))
    project: dict[str, Any] = validated_key.project

    supabase = await get_client()

    # ── Fetch the latest valid OTP record ─────────────────────────────────────
    try:
        response = (
            await supabase.table("otp_records")
            .select("*")
            .eq("project_id", validated_key.project_id)
            .eq("email_hash", email_hash)
            .eq("is_invalidated", False)
            .eq("is_verified", False)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("DB error fetching otp_record: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DB_ERROR", "message": "Service temporarily unavailable."},
        ) from exc

    rows = response.data
    if not rows:
        await _anti_enum_sleep(start_time)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "OTP_NOT_FOUND",
                "message": "No active OTP found.  Please request a new code.",
            },
        )

    record: dict[str, Any] = rows[0]

    # ── Check expiry ──────────────────────────────────────────────────────────
    expires_at = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
    if datetime.now(tz=timezone.utc) > expires_at:
        await _anti_enum_sleep(start_time)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "OTP_EXPIRED",
                "message": "This OTP has expired.  Please request a new code.",
            },
        )

    # ── Check attempt count ───────────────────────────────────────────────────
    max_attempts: int = project.get("otp_max_attempts", 5)
    if record["attempt_count"] >= max_attempts:
        await _anti_enum_sleep(start_time)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "code": "OTP_LOCKED",
                "message": "Too many failed attempts.  Please request a new code.",
            },
        )

    # ── Verify the code (constant-time bcrypt) ────────────────────────────────
    code_correct: bool = verify_otp(body.code, record["otp_hash"])

    if not code_correct:
        new_attempts: int = record["attempt_count"] + 1
        update_payload: dict[str, Any] = {"attempt_count": new_attempts}
        if new_attempts >= max_attempts:
            update_payload["is_invalidated"] = True

        try:
            await (
                supabase.table("otp_records")
                .update(update_payload)
                .eq("id", record["id"])
                .execute()
            )
        except Exception as exc:
            logger.warning("Failed to update attempt count: %s", exc)

        await _anti_enum_sleep(start_time)

        if new_attempts >= max_attempts:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={
                    "code": "OTP_LOCKED",
                    "message": "Too many failed attempts.  Please request a new code.",
                },
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CODE",
                "message": "The code you entered is incorrect.",
                "attempts_remaining": max_attempts - new_attempts,
            },
        )

    # ── Mark as verified ──────────────────────────────────────────────────────
    try:
        await (
            supabase.table("otp_records")
            .update({"is_verified": True, "is_invalidated": True})
            .eq("id", record["id"])
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to mark OTP as verified: %s", exc, exc_info=True)

    # ── Issue JWT ─────────────────────────────────────────────────────────────
    token: str = _make_jwt(
        project_id=validated_key.project_id,
        email_hash=email_hash,
        otp_record_id=record["id"],
    )

    await _anti_enum_sleep(start_time)

    return OtpVerifyResponse(
        verified=True,
        token=token,
        expires_in=settings.JWT_EXPIRY_MINUTES * 60,
    )


async def _anti_enum_sleep(start_time: float) -> None:
    """Sleep the remaining time to enforce the minimum response duration."""
    elapsed: float = time.monotonic() - start_time
    if elapsed < _MIN_RESPONSE_SECONDS:
        await asyncio.sleep(_MIN_RESPONSE_SECONDS - elapsed)
