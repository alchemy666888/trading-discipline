"""Integration coverage for monitor health plus event-bus publishing."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from freezegun import freeze_time

from src.events.bus import EventBus
from src.exchange.base import ConnectionEvent, ConnectionState
from src.models.events import EventType
from src.monitor.health import MonitorHealth


@pytest.mark.asyncio
async def test_monitor_health_publishes_down_and_recovered_events() -> None:
    """REQ-009/010: health alerts publish down and recovered events."""

    bus = EventBus()
    received: list[EventType] = []

    async def record(event: object) -> None:
        received.append(event.type)  # type: ignore[attr-defined]

    bus.subscribe(EventType.MONITOR_DOWN, record)
    bus.subscribe(EventType.MONITOR_RECOVERED, record)
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    health = MonitorHealth(
        send_message=send_message,
        event_bus=bus,
        open_trade_count_provider=lambda: 1,
        render_down_alert=lambda duration, attempts, has_open: "down",
        render_recovery_alert=lambda duration, gap: "recovery",
        render_heartbeat=lambda age, open_trades: "heartbeat",
        monitor_down_alert_delay_with_open_trades_seconds=10,
        monitor_down_alert_delay_no_open_trades_seconds=60,
        monitor_down_repeat_with_open_trades_seconds=60,
        monitor_down_repeat_no_open_trades_seconds=300,
        heartbeat_time_local=time(hour=9, minute=0),
        timezone="UTC",
        now_fn=lambda: datetime.now(tz=UTC),
    )

    with freeze_time("2026-05-17 00:00:00+00:00") as frozen:
        await health.handle_connection_event(
            ConnectionEvent(
                state=ConnectionState.DISCONNECTED,
                ts=datetime.now(tz=UTC),
                reconnect_attempt=1,
                reason="disconnect",
            )
        )
        frozen.tick(10)
        await health.process_due_alerts()
        await health.handle_connection_event(
            ConnectionEvent(
                state=ConnectionState.RECONNECTED,
                ts=datetime.now(tz=UTC),
                reconnect_attempt=1,
                gap_seconds=10,
                reason=None,
            )
        )

    assert sent_messages == ["down", "recovery"]
    assert received == [EventType.MONITOR_DOWN, EventType.MONITOR_RECOVERED]
