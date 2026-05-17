"""Unit tests for monitor health timing and tiered alerts."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from freezegun import freeze_time

from src.events.bus import EventBus
from src.exchange.base import ConnectionEvent, ConnectionState
from src.monitor.health import MonitorHealth


def _event(
    state: ConnectionState,
    ts: datetime,
    *,
    gap_seconds: int | None = None,
) -> ConnectionEvent:
    return ConnectionEvent(
        state=state,
        ts=ts,
        reconnect_attempt=1,
        gap_seconds=gap_seconds,
        reason=state.value.lower(),
    )


def _build_health(
    *,
    sent_messages: list[str],
    open_trade_count: int,
    gap_rechecks: list[int] | None = None,
) -> MonitorHealth:
    async def send_message(message: str) -> None:
        sent_messages.append(message)

    async def recheck(gap_seconds: int) -> None:
        if gap_rechecks is not None:
            gap_rechecks.append(gap_seconds)

    return MonitorHealth(
        send_message=send_message,
        event_bus=EventBus(),
        open_trade_count_provider=lambda: open_trade_count,
        render_down_alert=lambda duration, attempts, has_open: (
            f"down:{duration}:{attempts}:{int(has_open)}"
        ),
        render_recovery_alert=lambda duration, gap: f"recovery:{duration}:{gap}",
        render_heartbeat=lambda age, open_trades: f"heartbeat:{age}:{open_trades}",
        monitor_down_alert_delay_with_open_trades_seconds=10,
        monitor_down_alert_delay_no_open_trades_seconds=60,
        monitor_down_repeat_with_open_trades_seconds=60,
        monitor_down_repeat_no_open_trades_seconds=300,
        heartbeat_time_local=time(hour=9, minute=0),
        timezone="Asia/Hong_Kong",
        now_fn=lambda: datetime.now(tz=UTC),
        gap_recovery_callback=recheck,
    )


@pytest.mark.asyncio
async def test_monitor_health_open_trade_alerts_then_recovers() -> None:
    """REQ-009: 15s open-trade disconnect alerts at 10s, then recovers."""

    messages: list[str] = []
    with freeze_time("2026-05-17 00:00:00+00:00") as frozen:
        health = _build_health(sent_messages=messages, open_trade_count=1)
        start = datetime.now(tz=UTC)
        await health.handle_connection_event(
            _event(ConnectionState.DISCONNECTED, start)
        )
        await health.process_due_alerts()
        assert messages == []

        frozen.tick(10)
        await health.process_due_alerts()
        assert messages == ["down:10:1:1"]

        frozen.tick(5)
        end = datetime.now(tz=UTC)
        await health.handle_connection_event(
            _event(ConnectionState.RECONNECTED, end, gap_seconds=15)
        )
        assert messages[-1] == "recovery:15:15"


@pytest.mark.asyncio
async def test_monitor_health_8s_disconnect_with_open_trade_stays_silent() -> None:
    """REQ-009: a brief 8s disconnect with open trades does not alert."""

    messages: list[str] = []
    with freeze_time("2026-05-17 00:00:00+00:00") as frozen:
        health = _build_health(sent_messages=messages, open_trade_count=1)
        start = datetime.now(tz=UTC)
        await health.handle_connection_event(
            _event(ConnectionState.DISCONNECTED, start)
        )
        frozen.tick(8)
        await health.process_due_alerts()
        await health.handle_connection_event(
            _event(ConnectionState.RECONNECTED, datetime.now(tz=UTC), gap_seconds=8)
        )

        assert messages == []


@pytest.mark.asyncio
async def test_monitor_health_no_open_trades_uses_long_delay() -> None:
    """REQ-009: without open trades, the first down alert waits 60s."""

    messages: list[str] = []
    with freeze_time("2026-05-17 00:00:00+00:00") as frozen:
        health = _build_health(sent_messages=messages, open_trade_count=0)
        start = datetime.now(tz=UTC)
        await health.handle_connection_event(
            _event(ConnectionState.DISCONNECTED, start)
        )
        frozen.tick(59)
        await health.process_due_alerts()
        assert messages == []

        frozen.tick(1)
        await health.process_due_alerts()
        assert messages == ["down:60:1:0"]

        frozen.tick(10)
        await health.handle_connection_event(
            _event(ConnectionState.RECONNECTED, datetime.now(tz=UTC), gap_seconds=70)
        )
        assert messages[-1] == "recovery:70:70"


@pytest.mark.asyncio
async def test_monitor_health_flapping_alerts_on_sixth_disconnect() -> None:
    """REQ-009: after five disconnects in 10 minutes, the sixth alerts immediately."""

    messages: list[str] = []
    with freeze_time("2026-05-17 00:00:00+00:00") as frozen:
        health = _build_health(sent_messages=messages, open_trade_count=1)
        for _ in range(5):
            start = datetime.now(tz=UTC)
            await health.handle_connection_event(
                _event(ConnectionState.DISCONNECTED, start)
            )
            frozen.tick(8)
            await health.handle_connection_event(
                _event(ConnectionState.RECONNECTED, datetime.now(tz=UTC), gap_seconds=8)
            )
            frozen.tick(60)

        sixth_start = datetime.now(tz=UTC)
        await health.handle_connection_event(
            _event(ConnectionState.DISCONNECTED, sixth_start)
        )
        await health.process_due_alerts()

        assert messages[-1] == "down:0:1:1"


@pytest.mark.asyncio
async def test_monitor_health_heartbeat_when_healthy_only() -> None:
    """REQ-009: heartbeat fires on time when healthy and stays suppressed when down."""

    messages: list[str] = []
    with freeze_time("2026-05-17 01:00:00+00:00"):
        health = _build_health(sent_messages=messages, open_trade_count=2)
        health.record_tick(received_at=datetime.now(tz=UTC) - timedelta(seconds=3))

        assert await health.maybe_send_daily_heartbeat() is True
        assert messages == ["heartbeat:3.0:2"]

    with freeze_time("2026-05-18 01:00:00+00:00"):
        messages.clear()
        health = _build_health(sent_messages=messages, open_trade_count=2)
        await health.handle_connection_event(
            _event(ConnectionState.DISCONNECTED, datetime.now(tz=UTC))
        )
        assert await health.maybe_send_daily_heartbeat() is False
        assert messages == []


@pytest.mark.asyncio
async def test_monitor_health_gap_recovery_requests_recheck() -> None:
    """REQ-009: reconnect after a >60s gap requests a re-evaluation callback."""

    messages: list[str] = []
    gap_rechecks: list[int] = []
    with freeze_time("2026-05-17 00:00:00+00:00") as frozen:
        health = _build_health(
            sent_messages=messages,
            open_trade_count=0,
            gap_rechecks=gap_rechecks,
        )
        await health.handle_connection_event(
            _event(ConnectionState.DISCONNECTED, datetime.now(tz=UTC))
        )
        frozen.tick(70)
        await health.handle_connection_event(
            _event(ConnectionState.RECONNECTED, datetime.now(tz=UTC), gap_seconds=70)
        )

        assert gap_rechecks == [70]
