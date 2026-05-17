"""Integration tests for Redis connectivity and persistence health."""

from __future__ import annotations

from redis.asyncio import Redis

from src.bot.handlers import build_health_payload
from src.db.repo import RedisHealthDetails, RedisHealthError, RedisRepository
from src.monitor.health import ApplicationHealthSnapshot


async def test_redis_health_reports_connected_aof_enabled(
    redis_repo: RedisRepository,
) -> None:
    """REQ-011 / REQ-009: healthy Redis reports connectivity and AOF status."""

    health = await redis_repo.get_redis_health()
    verified = await redis_repo.verify_redis_ready()

    assert health.connected is True
    assert health.appendonly_enabled is True
    assert health.persistence_dir_writable is True
    assert verified == health


async def test_redis_health_reports_aof_disabled(
    redis_no_aof_container: object,
) -> None:
    """REQ-011: Redis health detects append-only persistence disabled."""

    redis_url = redis_no_aof_container.url
    client = Redis.from_url(redis_url, decode_responses=True)
    repo = RedisRepository(client)
    try:
        health = await repo.get_redis_health()
        assert health.connected is True
        assert health.appendonly_enabled is False

        try:
            await repo.verify_redis_ready()
        except RedisHealthError as exc:
            assert "append-only persistence is disabled" in str(exc)
        else:
            raise AssertionError("Expected verify_redis_ready() to fail.")
    finally:
        await client.aclose()


async def test_redis_health_reports_unavailable_startup() -> None:
    """REQ-011: startup health fails clearly when Redis is unavailable."""

    client = Redis.from_url(
        "redis://127.0.0.1:1/0",
        decode_responses=True,
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
    )
    repo = RedisRepository(client)
    try:
        health = await repo.get_redis_health()
        assert health.connected is False
        assert health.last_error is not None

        try:
            await repo.verify_redis_ready()
        except RedisHealthError as exc:
            assert "Redis unavailable" in str(exc)
        else:
            raise AssertionError("Expected verify_redis_ready() to fail.")
    finally:
        await client.aclose()


def test_health_payload_exposes_redis_fields() -> None:
    """REQ-011: `/health` payload includes Redis connectivity and persistence fields."""

    payload = build_health_payload(
        ApplicationHealthSnapshot(
            websocket_status="connected",
            last_tick_age_seconds=1.5,
            open_trade_count=2,
            last_error=None,
            redis=RedisHealthDetails(
                connected=True,
                appendonly_enabled=True,
                persistence_dir="/data",
                persistence_dir_writable=True,
                aof_last_write_status="ok",
                last_error=None,
            ),
        )
    )

    assert payload["websocket_status"] == "connected"
    assert payload["redis_connected"] is True
    assert payload["redis_appendonly_enabled"] is True
    assert payload["redis_persistence_dir"] == "/data"
    assert payload["redis_persistence_dir_writable"] is True
    assert payload["redis_aof_last_write_status"] == "ok"
