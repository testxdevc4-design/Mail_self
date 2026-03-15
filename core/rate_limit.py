"""
core/rate_limit.py
==================
Redis sliding-window rate limiter.

Algorithm
---------
Uses a Redis sorted set where each member is ``<timestamp_ms>:<random>``.
The score is the Unix timestamp in seconds (float).

On each request:
1. Remove members with score < (now - window_seconds)      → ZREMRANGEBYSCORE
2. Count remaining members                                  → ZCARD
3. If count >= limit → reject (return False)
4. Otherwise add current timestamp as a new member         → ZADD
5. Set TTL on the key so it expires automatically          → EXPIRE

This is O(log N + M) per call where M is the number of expired entries
removed.  In practice windows are small so N and M are tiny.

Thread-safety: all three Redis commands are issued in a single pipeline
(no MULTI/EXEC needed because we only need eventual consistency for rate
limiting - a tiny race window is acceptable and far better than a blocking
Lua script for this use-case).
"""

from __future__ import annotations

import time
import uuid

from redis.asyncio import Redis


async def check_rate_limit(
    redis_client: Redis,
    key: str,
    limit: int,
    window_seconds: int,
) -> bool:
    """
    Sliding-window rate-limit check.

    Args:
        redis_client:   An ``redis.asyncio.Redis`` instance.
        key:            Unique key identifying the bucket (e.g.
                        ``"rl:send:<project_id>:<email_hash>"``).
        limit:          Maximum number of requests allowed in *window_seconds*.
        window_seconds: Length of the sliding window in seconds.

    Returns:
        ``True``  - request is **allowed** (counter incremented).
        ``False`` - request is **denied** (limit exceeded).

    Example::

        allowed = await check_rate_limit(redis, "rl:send:proj1:abc", 5, 3600)
        if not allowed:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    """
    now: float = time.time()
    window_start: float = now - window_seconds
    # Unique member value prevents collisions when two requests arrive within
    # the same microsecond for the same key (using 6 decimal places = microseconds
    # plus a UUID to ensure uniqueness even under extreme concurrency).
    member: str = f"{now:.6f}:{uuid.uuid4().hex}"

    pipe = redis_client.pipeline(transaction=False)

    # 1. Evict expired members (scores below the window start)
    pipe.zremrangebyscore(key, "-inf", window_start)

    # 2. Count remaining (current-window) members
    pipe.zcard(key)

    # 3. Add this request's timestamp
    pipe.zadd(key, {member: now})

    # 4. Refresh TTL so the key cleans itself up after inactivity
    pipe.expire(key, window_seconds + 10)

    results: list = await pipe.execute()

    # results[1] is the cardinality *before* this request was added
    current_count: int = results[1]

    if current_count >= limit:
        # The ZADD already ran in the pipeline; remove the member we just added
        # so it does not inflate future counts.
        await redis_client.zrem(key, member)
        return False

    return True
