"""Unit tests for the Binance websocket adapter."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.exchange.base import ConnectionEvent, ConnectionState, Tick
from src.exchange.binance import BinanceExchangeAdapter


@dataclass
class MutableClock:
    """Mutable clock and fake sleeper for deterministic async tests."""

    current: datetime
    sleeps: list[float]

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class FakeWebSocket:
    """Scripted websocket object with async context-manager support."""

    def __init__(
        self,
        messages: list[Any],
        *,
        terminal_error: Exception | None = None,
    ) -> None:
        self._messages = deque(messages)
        self._terminal_error = terminal_error or RuntimeError("stream ended")
        self.closed = False

    async def recv(self) -> Any:
        if self._messages:
            return self._messages.popleft()
        raise self._terminal_error

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> FakeWebSocket:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConnector:
    """Return scripted websocket sessions in sequence."""

    def __init__(self, sessions: list[FakeWebSocket]) -> None:
        self._sessions = deque(sessions)
        self.calls: list[str] = []

    def __call__(self, url: str) -> FakeWebSocket:
        self.calls.append(url)
        if not self._sessions:
            raise RuntimeError("no more websocket sessions")
        return self._sessions.popleft()


def _mark_price_message(price: float, ts: datetime) -> str:
    return json.dumps(
        {
            "e": "markPriceUpdate",
            "E": int(ts.timestamp() * 1000),
            "p": str(price),
        }
    )


@pytest.mark.asyncio
async def test_binance_adapter_disconnects_and_reconnects() -> None:
    """REQ-004: disconnects trigger backoff and a later RECONNECTED event."""

    clock = MutableClock(
        current=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        sleeps=[],
    )
    first_tick_time = clock.current
    second_tick_time = clock.current + timedelta(seconds=1)
    connector = FakeConnector(
        [
            FakeWebSocket(
                [_mark_price_message(82000.0, first_tick_time)],
                terminal_error=RuntimeError("socket closed"),
            ),
            FakeWebSocket(
                [_mark_price_message(82010.0, second_tick_time)],
                terminal_error=asyncio.CancelledError(),
            ),
        ]
    )

    adapter = BinanceExchangeAdapter(
        connect=connector,
        now_fn=clock.now,
        sleep_func=clock.sleep,
    )
    events: list[ConnectionEvent] = []

    async def record(event: ConnectionEvent) -> None:
        events.append(event)

    adapter.subscribe_connection_events(record)

    stream = adapter.stream_ticks()
    first_tick = await anext(stream)
    second_tick = await anext(stream)
    await adapter.close()

    assert first_tick == Tick(price=82000.0, ts=first_tick_time)
    assert second_tick == Tick(price=82010.0, ts=second_tick_time)
    assert [event.state for event in events] == [
        ConnectionState.CONNECTED,
        ConnectionState.DISCONNECTED,
        ConnectionState.RECONNECTED,
    ]
    assert events[1].reconnect_attempt == 1
    assert events[2].gap_seconds == 1
    assert clock.sleeps == [1]


@pytest.mark.asyncio
async def test_binance_adapter_drops_stale_ticks_and_reconnects() -> None:
    """REQ-004: stale ticks are discarded and treated as reconnect-worthy outages."""

    current = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    clock = MutableClock(current=current, sleeps=[])
    stale_tick_time = current - timedelta(seconds=31)
    fresh_tick_time = current + timedelta(seconds=1)
    connector = FakeConnector(
        [
            FakeWebSocket([_mark_price_message(82000.0, stale_tick_time)]),
            FakeWebSocket(
                [_mark_price_message(82010.0, fresh_tick_time)],
                terminal_error=asyncio.CancelledError(),
            ),
        ]
    )

    adapter = BinanceExchangeAdapter(
        connect=connector,
        now_fn=clock.now,
        sleep_func=clock.sleep,
    )
    events: list[ConnectionEvent] = []

    async def record(event: ConnectionEvent) -> None:
        events.append(event)

    adapter.subscribe_connection_events(record)

    stream = adapter.stream_ticks()
    tick = await anext(stream)
    await adapter.close()

    assert tick == Tick(price=82010.0, ts=fresh_tick_time)
    assert [event.state for event in events] == [
        ConnectionState.CONNECTED,
        ConnectionState.STALE,
        ConnectionState.RECONNECTED,
    ]
    assert events[1].reconnect_attempt == 1
    assert clock.sleeps == [1]


@pytest.mark.network
@pytest.mark.asyncio
async def test_binance_adapter_real_endpoint_smoke() -> None:
    """REQ-004: the live Binance endpoint yields at least one normalized tick."""

    adapter = BinanceExchangeAdapter()
    stream = adapter.stream_ticks()
    try:
        async with asyncio.timeout(10):
            tick = await anext(stream)
    finally:
        await adapter.close()

    assert tick.price > 0
    assert tick.ts.tzinfo is not None
