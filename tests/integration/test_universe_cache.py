"""Integration tests for the Hyperliquid universe cache."""

from __future__ import annotations

from datetime import UTC, datetime

from redis.asyncio import Redis

from src.db import keyspace
from src.db.repo import RedisRepository


async def test_universe_cache_round_trips(
    redis_repo: RedisRepository,
) -> None:
    """R2: the repository stores and loads the cached perp universe."""

    fetched_at = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)

    await redis_repo.set_universe(["BTC", "ETH", "AUDUSD"], fetched_at)

    loaded = await redis_repo.get_universe()

    assert loaded == ({"BTC", "ETH", "AUDUSD"}, fetched_at)


async def test_universe_cache_absence_returns_none(
    redis_repo: RedisRepository,
) -> None:
    """R2: a missing universe cache is represented as None."""

    assert await redis_repo.get_universe() is None


async def test_universe_cache_malformed_value_returns_none(
    redis_repo: RedisRepository,
    redis_client: Redis,
) -> None:
    """R2: malformed universe cache values degrade to a cache miss."""

    await redis_client.set(keyspace.hyperliquid_universe_key(), "{not-json")

    assert await redis_repo.get_universe() is None

    await redis_client.set(
        keyspace.hyperliquid_universe_key(),
        '{"symbols":"BTC","fetched_at":"2026-05-17T09:00:00+00:00"}',
    )

    assert await redis_repo.get_universe() is None
