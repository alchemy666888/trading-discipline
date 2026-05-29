"""Internal event-bus payload models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
)

from src.models.trade import Trade


class EventType(StrEnum):
    """Supported v1 event types."""

    TICK = "tick"
    BREACH_DETECTED = "breach_detected"
    BREACH_RESOLVED = "breach_resolved"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    MONITOR_DOWN = "monitor_down"
    MONITOR_RECOVERED = "monitor_recovered"


class BreachResolution(StrEnum):
    """Ways an open breach can be resolved."""

    CLOSED = "closed"
    JUSTIFIED = "justified"


class EventPayload(BaseModel):
    """Base payload model."""

    model_config = ConfigDict(extra="forbid")


class TickPayload(EventPayload):
    """Payload for price ticks."""

    symbol: str
    price: PositiveFloat


class BreachDetectedPayload(EventPayload):
    """Payload for a detected breach."""

    trade_id: PositiveInt
    breach_id: PositiveInt
    price: PositiveFloat


class BreachResolvedPayload(EventPayload):
    """Payload for a resolved breach."""

    breach_id: PositiveInt
    resolution: BreachResolution


class TradeOpenedPayload(EventPayload):
    """Payload for an opened trade event."""

    trade_id: PositiveInt
    snapshot: Trade


class TradeClosedPayload(EventPayload):
    """Payload for a closed trade event."""

    trade_id: PositiveInt
    realized_pnl: float


class MonitorDownPayload(EventPayload):
    """Payload for a monitor-down event."""

    since: AwareDatetime
    has_open_trades: bool


class MonitorRecoveredPayload(EventPayload):
    """Payload for a monitor-recovered event."""

    gap_seconds: NonNegativeInt


class BaseEvent(BaseModel):
    """Base event envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts: AwareDatetime


class TickEvent(BaseEvent):
    """Price-tick event."""

    type: Literal[EventType.TICK] = EventType.TICK
    payload: TickPayload


class BreachDetectedEvent(BaseEvent):
    """Breach-detected event."""

    type: Literal[EventType.BREACH_DETECTED] = EventType.BREACH_DETECTED
    payload: BreachDetectedPayload


class BreachResolvedEvent(BaseEvent):
    """Breach-resolved event."""

    type: Literal[EventType.BREACH_RESOLVED] = EventType.BREACH_RESOLVED
    payload: BreachResolvedPayload


class TradeOpenedEvent(BaseEvent):
    """Trade-opened event."""

    type: Literal[EventType.TRADE_OPENED] = EventType.TRADE_OPENED
    payload: TradeOpenedPayload


class TradeClosedEvent(BaseEvent):
    """Trade-closed event."""

    type: Literal[EventType.TRADE_CLOSED] = EventType.TRADE_CLOSED
    payload: TradeClosedPayload


class MonitorDownEvent(BaseEvent):
    """Monitor-down event."""

    type: Literal[EventType.MONITOR_DOWN] = EventType.MONITOR_DOWN
    payload: MonitorDownPayload


class MonitorRecoveredEvent(BaseEvent):
    """Monitor-recovered event."""

    type: Literal[EventType.MONITOR_RECOVERED] = EventType.MONITOR_RECOVERED
    payload: MonitorRecoveredPayload


Event = Annotated[
    TickEvent
    | BreachDetectedEvent
    | BreachResolvedEvent
    | TradeOpenedEvent
    | TradeClosedEvent
    | MonitorDownEvent
    | MonitorRecoveredEvent,
    Field(discriminator="type"),
]
