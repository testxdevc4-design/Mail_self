"""
tests/conftest.py
=================
Shared pytest fixtures.

All tests that import from ``core.config`` need the required env vars set
before the module is imported.  We patch them here so no real credentials
are required during CI.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Set required environment variables BEFORE any core module is imported ──────
# These must be set at collection time, hence the module-level os.environ calls.

_TEST_ENV = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test",
    "REDIS_URL": "redis://localhost:6379",
    "ENCRYPTION_KEY": "a" * 64,  # 64 hex chars = 32 bytes
    "JWT_SECRET": "b" * 64,      # 64-char minimum
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFtesttoken",
    "TELEGRAM_ADMIN_UID": "123456789",
    "ENV": "development",
    "PORT": "3000",
}

for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_supabase():
    """Return a fully mocked Supabase async client."""
    client = MagicMock()
    # Make table() return a chainable mock where execute() is an AsyncMock
    table_mock = MagicMock()
    execute_mock = AsyncMock(return_value=MagicMock(data=[]))

    # Chain: .table().select().eq().limit().execute()
    chain = MagicMock()
    chain.execute = execute_mock
    chain.eq = MagicMock(return_value=chain)
    chain.limit = MagicMock(return_value=chain)
    chain.order = MagicMock(return_value=chain)
    chain.gte = MagicMock(return_value=chain)
    chain.update = MagicMock(return_value=chain)
    chain.insert = MagicMock(return_value=chain)

    table_mock.select = MagicMock(return_value=chain)
    table_mock.insert = MagicMock(return_value=chain)
    table_mock.update = MagicMock(return_value=chain)
    client.table = MagicMock(return_value=table_mock)
    return client


@pytest.fixture()
def mock_redis():
    """Return a mocked async Redis client."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.zadd = AsyncMock(return_value=1)
    redis.zremrangebyscore = AsyncMock(return_value=0)
    redis.zcard = AsyncMock(return_value=0)
    redis.zrem = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    # Pipeline mock
    pipe = AsyncMock()
    pipe.zremrangebyscore = AsyncMock()
    pipe.zcard = AsyncMock()
    pipe.zadd = AsyncMock()
    pipe.expire = AsyncMock()
    pipe.execute = AsyncMock(return_value=[0, 0, 1, True])
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


@pytest.fixture()
def anyio_backend():
    """Use asyncio as the anyio backend for async tests."""
    return "asyncio"
