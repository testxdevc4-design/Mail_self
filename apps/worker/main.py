"""
apps/worker/main.py
===================
ARQ worker entry-point and WorkerSettings.

``python -m arq apps.worker.main.WorkerSettings``

Design decisions
----------------
* ``max_jobs=20`` prevents a single worker pod from being overloaded.
* ``job_timeout=60`` ensures a stalled email send is retried.
* ``keep_result=3600`` stores task results in Redis for one hour so the
  Telegram bot can surface failure information.
* ``retry_jobs=True`` combined with the explicit retry logic inside
  ``send_email_task`` gives us full control over back-off.
"""

from __future__ import annotations

import logging
import os

from arq.connections import RedisSettings

from apps.worker.tasks.email import send_email_task  # noqa: F401 – re-exported for ARQ
from core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)


def _redis_settings() -> RedisSettings:
    """Parse REDIS_URL into an ARQ ``RedisSettings`` instance."""
    return RedisSettings.from_dsn(settings.REDIS_URL)


class WorkerSettings:
    """
    ARQ worker configuration class.

    ARQ discovers the ``functions`` list and all other attributes automatically
    when this class is passed to ``arq.run_worker``.
    """

    # ── Queue functions ────────────────────────────────────────────────────────
    functions = [send_email_task]

    # ── Redis connection ───────────────────────────────────────────────────────
    redis_settings: RedisSettings = _redis_settings()

    # ── Concurrency & timeouts ─────────────────────────────────────────────────
    max_jobs: int = 20
    job_timeout: int = 60           # seconds per job before it's considered failed
    keep_result: int = 3600         # keep job result in Redis for 1 hour
    keep_result_forever: bool = False
    max_tries: int = 3              # maximum retry attempts per job

    # ── Queue name (must match the producer) ──────────────────────────────────
    queue_name: str = "arq:queue"

    # ── Health logging ─────────────────────────────────────────────────────────
    @staticmethod
    async def on_startup(ctx: dict) -> None:  # type: ignore[type-arg]
        """Called once when the worker process starts."""
        logger.info(
            "MailGuard ARQ worker started (ENV=%s, max_jobs=%d)",
            settings.ENV,
            WorkerSettings.max_jobs,
        )

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:  # type: ignore[type-arg]
        """Called once when the worker process stops gracefully."""
        logger.info("MailGuard ARQ worker shutting down")
