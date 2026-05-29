"""Integration tests for the main monitor loop."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, time, timedelta

import pytest

from src.db.repo import RedisRepository
from src.events.bus import EventBus
from src.exchange.base import ConnectionEvent, ConnectionState, ExchangeAdapter, Tick
from src.models.events import Event, EventType
from src.models.trade import Direction, Regime, TradeDraft
from src.monitor.alerts import AlertDispatcher
from src.monitor.health import MonitorHealth
from src.monitor.monitor import Monitor


class FakeAdapter(ExchangeAdapter):
    """Scripted adapter that interleaves connection events and ticks."""

    def __init__(self, script: list[ConnectionEvent | Tick]) -> None:
        super().__init__()
        self._script = deque(script)
        self._closed = False

    async def stream_ticks(self):
        while self._script and not self._closed:
            item = self._script.popleft()
            if isinstance(item, ConnectionEvent):
                await self._publish_connection_event(item)
                continue
            yield item

    async def healthy(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True


class RecordingRepo:
    """Capture alert records without affecting the Redis repo under test."""

    def __init__(self) -> None:
        self.records: list[int] = []

    async def record_alert(
        self,
        breach_id: int,
        *,
        sent_at: datetime,
        escalation_level: int,
        message: str,
    ) -> None:
        self.records.append(breach_id)


@pytest.mark.asyncio
async def test_monitor_loop_creates_single_breach_and_publishes_events(
    redis_repo: RedisRepository,
) -> None:
    """REQ-004/005/010: monitor loop dedups breaches and publishes events."""

    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=2000.0,
            leverage=5,
            entry_price=82000.0,
            invalidation_price=81000.0,
            max_loss_usdt=40.0,
            regime=Regime.UPTREND,
            thesis="Open trade for monitor loop integration testing.",
        ),
        opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )
    bus = EventBus()
    received: list[EventType] = []

    async def record_event(event: Event) -> None:
        received.append(event.type)

    bus.subscribe(EventType.TICK, record_event)
    bus.subscribe(EventType.BREACH_DETECTED, record_event)
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    alerts = AlertDispatcher(
        repo=RecordingRepo(),  # type: ignore[arg-type]
        send_message=send_message,
        render_initial_alert=lambda trade, breach, price, elapsed, loss: "initial",
        render_escalation_alert=lambda trade, breach, price, elapsed, loss: "repeat",
        first_window_seconds=60,
        first_window_duration_seconds=300,
        after_seconds=300,
        now_fn=lambda: datetime.now(tz=UTC),
    )
    health = MonitorHealth(
        send_message=send_message,
        event_bus=bus,
        open_trade_count_provider=lambda: len(sent_messages) + 1,
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
    adapter = FakeAdapter(
        [
            Tick("BTC", 81500.0, datetime(2026, 5, 17, 9, 1, tzinfo=UTC)),
            Tick("BTC", 80990.0, datetime(2026, 5, 17, 9, 2, tzinfo=UTC)),
            Tick("BTC", 80980.0, datetime(2026, 5, 17, 9, 3, tzinfo=UTC)),
        ]
    )
    monitor = Monitor(
        repo=redis_repo,
        adapter=adapter,
        alerts=alerts,
        health=health,
        event_bus=bus,
    )

    await monitor.run()

    breach = await redis_repo.get_open_breach(trade.id)
    assert breach is not None
    assert len(sent_messages) == 1
    assert received == [
        EventType.TICK,
        EventType.TICK,
        EventType.BREACH_DETECTED,
        EventType.TICK,
    ]


@pytest.mark.asyncio
async def test_monitor_gap_through_breach_on_first_post_reconnect_tick(
    redis_repo: RedisRepository,
) -> None:
    """REQ-004/009: first post-reconnect tick after a gap can breach."""

    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.SHORT,
            size_usdt=1500.0,
            leverage=4,
            entry_price=82000.0,
            invalidation_price=83000.0,
            max_loss_usdt=35.0,
            regime=Regime.RANGE,
            thesis="Short trade for reconnect gap breach testing.",
        ),
        opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )
    bus = EventBus()
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    alerts = AlertDispatcher(
        repo=RecordingRepo(),  # type: ignore[arg-type]
        send_message=send_message,
        render_initial_alert=lambda trade, breach, price, elapsed, loss: "initial",
        render_escalation_alert=lambda trade, breach, price, elapsed, loss: "repeat",
        first_window_seconds=60,
        first_window_duration_seconds=300,
        after_seconds=300,
        now_fn=lambda: datetime.now(tz=UTC),
    )
    rechecks: list[int] = []

    async def recheck(gap_seconds: int) -> None:
        rechecks.append(gap_seconds)

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
        gap_recovery_callback=recheck,
    )
    start = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    adapter = FakeAdapter(
        [
            ConnectionEvent(
                state=ConnectionState.DISCONNECTED,
                ts=start,
                reconnect_attempt=1,
                reason="disconnect",
            ),
            ConnectionEvent(
                state=ConnectionState.RECONNECTED,
                ts=start + timedelta(seconds=70),
                reconnect_attempt=1,
                gap_seconds=70,
                reason=None,
            ),
            Tick("BTC", 83010.0, start + timedelta(seconds=70)),
        ]
    )
    monitor = Monitor(
        repo=redis_repo,
        adapter=adapter,
        alerts=alerts,
        health=health,
        event_bus=bus,
    )

    await monitor.run()

    breach = await redis_repo.get_open_breach(trade.id)
    assert breach is not None
    assert breach.trade_id == trade.id
    assert rechecks == [70]
    assert sent_messages[-1] == "initial"
