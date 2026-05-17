"""Asyncio event bus for internal application events."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from src.models.events import Event, EventType

EventHandler = Callable[[Event], Awaitable[None]]


@dataclass(frozen=True)
class Subscription:
    """Handle for removing a bus subscription."""

    event_type: EventType
    _bus: EventBus
    _handler: EventHandler

    def unsubscribe(self) -> None:
        """Remove the subscribed handler from the bus."""

        self._bus.unsubscribe(self.event_type, self._handler)


class EventBus:
    """Simple in-process asyncio pub/sub bus."""

    def __init__(self) -> None:
        self._handlers: defaultdict[EventType, list[EventHandler]] = defaultdict(list)
        self._logger = structlog.get_logger(__name__)

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ) -> Subscription:
        """Register a handler for a specific event type."""

        self._handlers[event_type].append(handler)
        return Subscription(event_type=event_type, _bus=self, _handler=handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a registered handler if it exists."""

        handlers = self._handlers.get(event_type)
        if handlers is None:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return
        if not handlers:
            self._handlers.pop(event_type, None)

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers without propagating handler failures."""

        handlers = list(self._handlers.get(event.type, []))
        if not handlers:
            return

        await asyncio.gather(
            *(self._dispatch(handler, event) for handler in handlers),
        )

    async def _dispatch(self, handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except Exception as exc:  # pragma: no cover - exercised by tests
            self._logger.exception(
                "event_handler_failed",
                event_type=event.type.value,
                handler=getattr(handler, "__name__", repr(handler)),
                error=str(exc),
            )
