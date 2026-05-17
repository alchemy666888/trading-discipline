"""Exchange adapter abstractions and connection-state events."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class Tick:
    """Normalized market tick."""

    price: float
    ts: datetime


class ConnectionState(StrEnum):
    """Connection-state events emitted by exchange adapters."""

    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    STALE = "STALE"
    RECONNECTED = "RECONNECTED"


@dataclass(frozen=True)
class ConnectionEvent:
    """Adapter connection-state event payload."""

    state: ConnectionState
    ts: datetime
    reconnect_attempt: int = 0
    gap_seconds: int | None = None
    reason: str | None = None


ConnectionEventHandler = Callable[[ConnectionEvent], Awaitable[None]]


@dataclass(frozen=True)
class ConnectionSubscription:
    """Handle for unregistering a connection-state subscription."""

    _adapter: ExchangeAdapter
    _handler: ConnectionEventHandler

    def unsubscribe(self) -> None:
        """Detach the handler from the adapter publisher."""

        self._adapter.unsubscribe_connection_events(self._handler)


class ExchangeAdapter(ABC):
    """Abstract base class for market-data adapters."""

    def __init__(self) -> None:
        self._connection_handlers: list[ConnectionEventHandler] = []

    def subscribe_connection_events(
        self,
        handler: ConnectionEventHandler,
    ) -> ConnectionSubscription:
        """Subscribe to adapter connection-state events."""

        self._connection_handlers.append(handler)
        return ConnectionSubscription(_adapter=self, _handler=handler)

    def unsubscribe_connection_events(self, handler: ConnectionEventHandler) -> None:
        """Remove a connection-state handler if present."""

        try:
            self._connection_handlers.remove(handler)
        except ValueError:
            return

    async def _publish_connection_event(self, event: ConnectionEvent) -> None:
        if not self._connection_handlers:
            return
        await asyncio.gather(*(handler(event) for handler in self._connection_handlers))

    @abstractmethod
    def stream_ticks(self) -> AsyncIterator[Tick]:
        """Yield normalized ticks from the exchange feed."""

    @abstractmethod
    async def healthy(self) -> bool:
        """Return whether the adapter believes the stream is currently healthy."""

    @abstractmethod
    async def close(self) -> None:
        """Release any network resources and stop streaming."""
