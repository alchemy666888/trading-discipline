"""Unit tests for REQ-010 event bus behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from src.events.bus import EventBus
from src.models.events import (
    BreachDetectedEvent,
    BreachDetectedPayload,
    BreachResolution,
    BreachResolvedEvent,
    BreachResolvedPayload,
    Event,
    EventType,
    MonitorDownEvent,
    MonitorDownPayload,
    MonitorRecoveredEvent,
    MonitorRecoveredPayload,
    TickEvent,
    TickPayload,
    TradeClosedEvent,
    TradeClosedPayload,
    TradeOpenedEvent,
    TradeOpenedPayload,
)
from src.models.trade import Direction, Regime, Trade, TradeStatus


def _now() -> datetime:
    return datetime(2026, 5, 18, 9, 0, tzinfo=UTC)


def _sample_trade() -> Trade:
    return Trade(
        id=11,
        direction=Direction.LONG,
        size_usdt=2000.0,
        leverage=5,
        leverage_override_reason=None,
        entry_price=82000.0,
        invalidation_price=81000.0,
        max_loss_usdt=60.0,
        regime=Regime.UPTREND,
        thesis="Sample trade thesis for event payload coverage.",
        status=TradeStatus.OPEN,
        size_reduction_enforced=False,
        opened_at=_now(),
        closed_at=None,
        close_price=None,
        realized_pnl=None,
    )


async def test_event_bus_delivers_events_in_publish_order() -> None:
    """REQ-010: a subscriber receives every v1 event type in the published order."""

    bus = EventBus()
    received: list[EventType] = []

    async def handler(event: Event) -> None:
        received.append(event.type)

    for event_type in EventType:
        bus.subscribe(event_type, handler)

    events = [
        TickEvent(ts=_now(), payload=TickPayload(price=82010.0)),
        BreachDetectedEvent(
            ts=_now(),
            payload=BreachDetectedPayload(trade_id=11, breach_id=1, price=80990.0),
        ),
        BreachResolvedEvent(
            ts=_now(),
            payload=BreachResolvedPayload(
                breach_id=1,
                resolution=BreachResolution.CLOSED,
            ),
        ),
        TradeOpenedEvent(
            ts=_now(),
            payload=TradeOpenedPayload(trade_id=11, snapshot=_sample_trade()),
        ),
        TradeClosedEvent(
            ts=_now(),
            payload=TradeClosedPayload(trade_id=11, realized_pnl=-24.0),
        ),
        MonitorDownEvent(
            ts=_now(),
            payload=MonitorDownPayload(since=_now(), has_open_trades=True),
        ),
        MonitorRecoveredEvent(
            ts=_now(),
            payload=MonitorRecoveredPayload(gap_seconds=61),
        ),
    ]

    for event in events:
        await bus.publish(event)

    assert received == [event.type for event in events]


async def test_event_bus_isolates_failing_subscribers() -> None:
    """REQ-010: one failing subscriber does not block other subscribers."""

    bus = EventBus()
    received: list[str] = []

    async def bad_handler(event: Event) -> None:
        received.append("bad")
        raise RuntimeError("boom")

    async def good_handler(event: Event) -> None:
        received.append("good")

    bus.subscribe(EventType.TICK, bad_handler)
    bus.subscribe(EventType.TICK, good_handler)

    await bus.publish(TickEvent(ts=_now(), payload=TickPayload(price=82010.0)))

    assert received == ["bad", "good"]


async def test_event_bus_publish_without_subscribers_is_noop() -> None:
    """REQ-010: publishing with no subscribers is a no-op."""

    bus = EventBus()

    await bus.publish(TickEvent(ts=_now(), payload=TickPayload(price=82010.0)))
