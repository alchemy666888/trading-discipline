"""Integration tests for Hyperliquid-aware `/health` formatting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.bot import formatting
from src.bot.handlers import build_health_payload
from src.db.repo import RedisHealthDetails
from src.monitor.health import ApplicationHealthSnapshot


def _redis_health() -> RedisHealthDetails:
    return RedisHealthDetails(
        connected=True,
        appendonly_enabled=True,
        persistence_dir="/data",
        persistence_dir_writable=True,
        aof_last_write_status="ok",
        last_error=None,
    )


def test_health_formatter_exposes_all_websocket_states() -> None:
    """R4/R7: `/health` renders Hyperliquid websocket states."""

    now = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)

    for status in ["connected", "disconnected", "stale", "unknown"]:
        payload = build_health_payload(
            ApplicationHealthSnapshot(
                websocket_status=status,
                last_tick_age_seconds=12.5,
                open_trade_count=2,
                last_error="socket closed" if status == "disconnected" else None,
                redis=_redis_health(),
            ),
            universe_fetched_at=now - timedelta(seconds=30),
            now=now,
        )
        message = formatting.health_status(payload)

        assert f"websocket: {status}" in message
        assert "last_frame_age_s: 12.5s" in message
        assert "universe_cache_age_s: 30.0s" in message
        if status == "disconnected":
            assert "last_hyperliquid_error: socket closed" in message
        else:
            assert "last_hyperliquid_error: none" in message
