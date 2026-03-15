"""
core/config.py
==============
Pydantic v2 Settings for MailGuard.

All secrets are read from environment variables or a .env file.
Never import this module at the top of a file that is tested without
setting the required environment variables first (use conftest fixtures).
"""

from __future__ import annotations

import binascii
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Supabase ──────────────────────────────────────────────────────────────
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str

    # ── Redis (Upstash or local) ───────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── Cryptography ──────────────────────────────────────────────────────────
    # Must be exactly 64 hex characters (32 bytes → AES-256)
    ENCRYPTION_KEY: str

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET: str  # minimum 64 characters enforced below
    JWT_EXPIRY_MINUTES: int = 10

    # ── Telegram ──────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_ADMIN_UID: int

    # ── App ───────────────────────────────────────────────────────────────────
    ENV: str = "production"
    PORT: int = 3000
    ALLOWED_ORIGINS: list[str] = []

    # ─────────────────────────────────────────────────────────────────────────
    # Field-level validators
    # ─────────────────────────────────────────────────────────────────────────

    @field_validator("ENCRYPTION_KEY")
    @classmethod
    def validate_encryption_key(cls, v: str) -> str:
        """Enforce exactly 64 hex characters (32 raw bytes for AES-256)."""
        if len(v) != 64:
            raise ValueError(
                f"ENCRYPTION_KEY must be exactly 64 hex characters (got {len(v)}). "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        try:
            binascii.unhexlify(v)
        except binascii.Error as exc:
            raise ValueError(
                "ENCRYPTION_KEY contains non-hex characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            ) from exc
        return v

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        """Enforce a minimum length of 64 characters for JWT secret."""
        if len(v) < 64:
            raise ValueError(
                f"JWT_SECRET must be at least 64 characters long (got {len(v)}). "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(64))\""
            )
        return v

    # ─────────────────────────────────────────────────────────────────────────
    # Model-level validators
    # ─────────────────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def block_test_keys_in_production(self) -> Settings:
        """
        Prevent sandbox/test keys from being used in a production environment.

        Any SUPABASE_SERVICE_ROLE_KEY or JWT_SECRET that starts with the
        well-known testing prefix ``mg_test_`` must not reach production.
        """
        if self.ENV == "production":
            if self.SUPABASE_SERVICE_ROLE_KEY.startswith("mg_test_"):
                raise ValueError(
                    "SUPABASE_SERVICE_ROLE_KEY starting with 'mg_test_' is not "
                    "allowed in production (ENV=production)."
                )
            if self.JWT_SECRET.startswith("mg_test_"):
                raise ValueError(
                    "JWT_SECRET starting with 'mg_test_' is not allowed in "
                    "production (ENV=production)."
                )
        return self

    # ─────────────────────────────────────────────────────────────────────────
    # Derived helpers
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        """Return True when running in a production environment."""
        return self.ENV == "production"

    @property
    def encryption_key_bytes(self) -> bytes:
        """Return the raw 32-byte AES key decoded from the hex string."""
        return binascii.unhexlify(self.ENCRYPTION_KEY)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    The ``@lru_cache`` ensures a single Settings object for the entire
    process lifetime, avoiding repeated disk / env reads.
    """
    return Settings()


# Module-level singleton - most modules just do ``from core.config import settings``
settings: Annotated[Settings, "Cached application settings"] = get_settings()
