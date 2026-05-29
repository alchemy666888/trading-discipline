"""Tests for the Hyperliquid public market-data adapter."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from src.exchange.base import ConnectionEvent, ConnectionState, Tick
from src.exchange.hyperliquid import HyperliquidExchangeAdapter


@dataclass
class MutableClock:
    """Mutable clock and fake sleeper for deterministic reconnect tests."""

    current: datetime
    sleeps: list[float]

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class NoMessage:
    """Sentinel that makes FakeWebSocket.recv wait until cancelled."""


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
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> Any:
        if self._messages:
            message = self._messages.popleft()
            if isinstance(message, NoMessage):
                await asyncio.Event().wait()
            if isinstance(message, Exception):
                raise message
            return message
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
        self.sessions = deque(sessions)
        self.calls: list[str] = []

    def __call__(self, url: str) -> FakeWebSocket:
        self.calls.append(url)
        if not self.sessions:
            raise RuntimeError("no more websocket sessions")
        return self.sessions.popleft()


class RecordingLogger:
    """Capture structlog-style events from the adapter."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.records.append(("info", event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.records.append(("warning", event, kwargs))


class FakeResponse:
    """Minimal httpx response double."""

    def __init__(
        self,
        payload: Any = None,
        *,
        status_code: int = 200,
        json_error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._json_error = json_error
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        request = httpx.Request("POST", "https://api.hyperliquid.xyz/info")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("bad status", request=request, response=response)

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeHttpClient:
    """Minimal async httpx client double."""

    def __init__(
        self,
        response: FakeResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._response = response or FakeResponse({})
        self._error = error
        self.posts: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, str],
        headers: dict[str, str],
    ) -> FakeResponse:
        self.posts.append({"url": url, "json": json, "headers": headers})
        if self._error is not None:
            raise self._error
        return self._response

    async def __aenter__(self) -> FakeHttpClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeHttpClientFactory:
    """Capture AsyncClient construction kwargs."""

    def __init__(self, client: FakeHttpClient) -> None:
        self.client = client
        self.kwargs: dict[str, Any] | None = None

    def __call__(self, **kwargs: Any) -> FakeHttpClient:
        self.kwargs = kwargs
        return self.client


def _all_mids(mids: dict[str, str]) -> str:
    return json.dumps({"channel": "allMids", "data": {"mids": mids}})


def _subscription_ack() -> str:
    return json.dumps(
        {
            "channel": "subscriptionResponse",
            "data": {"subscription": {"type": "allMids"}},
        }
    )


def _decoded_subscription(websocket: FakeWebSocket) -> dict[str, Any]:
    assert websocket.sent
    return json.loads(websocket.sent[0])


@pytest.mark.asyncio
async def test_stream_ticks_yields_one_tick_per_symbol_with_receive_timestamp() -> None:
    """R3: one allMids frame yields one receive-time tick per symbol."""

    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    clock = MutableClock(current=now, sleeps=[])
    websocket = FakeWebSocket(
        [
            _subscription_ack(),
            _all_mids({"BTC": "43250.5", "ETH": "3100", "AUDUSD": "0.664"}),
        ]
    )
    connector = FakeConnector([websocket])
    adapter = HyperliquidExchangeAdapter(
        websocket_url="wss://example.test/ws",
        connect=connector,
        now_fn=clock.now,
        sleep_func=clock.sleep,
        stale_threshold_seconds=30,
    )
    logger = RecordingLogger()
    adapter._logger = logger  # type: ignore[attr-defined]

    stream = adapter.stream_ticks()
    ticks = [await anext(stream), await anext(stream), await anext(stream)]

    assert ticks == [
        Tick("BTC", 43250.5, now),
        Tick("ETH", 3100.0, now),
        Tick("AUDUSD", 0.664, now),
    ]
    assert connector.calls == ["wss://example.test/ws"]
    assert _decoded_subscription(websocket) == {
        "method": "subscribe",
        "subscription": {"type": "allMids"},
    }
    assert ("info", "hyperliquid_subscription_ack", {}) in logger.records
    assert await adapter.healthy() is True

    clock.current = now + timedelta(seconds=31)
    assert await adapter.healthy() is False
    await adapter.close()


@pytest.mark.asyncio
async def test_stream_ticks_omits_symbols_missing_from_later_frames() -> None:
    """R3: a frame missing a symbol simply emits no tick for that symbol."""

    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    websocket = FakeWebSocket(
        [
            _all_mids({"BTC": "43250.5", "ETH": "3100"}),
            _all_mids({"ETH": "3101"}),
        ]
    )
    adapter = HyperliquidExchangeAdapter(
        connect=FakeConnector([websocket]),
        now_fn=lambda: now,
    )

    stream = adapter.stream_ticks()
    ticks = [await anext(stream), await anext(stream), await anext(stream)]
    await adapter.close()

    assert [tick.symbol for tick in ticks] == ["BTC", "ETH", "ETH"]
    assert [tick.price for tick in ticks] == [43250.5, 3100.0, 3101.0]


@pytest.mark.asyncio
async def test_stream_ticks_skips_malformed_mid_and_keeps_other_symbols() -> None:
    """R3: malformed mid strings are skipped without poisoning the frame."""

    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    websocket = FakeWebSocket([_all_mids({"BTC": "not-a-number", "ETH": "3100"})])
    adapter = HyperliquidExchangeAdapter(
        connect=FakeConnector([websocket]),
        now_fn=lambda: now,
    )
    logger = RecordingLogger()
    adapter._logger = logger  # type: ignore[attr-defined]

    stream = adapter.stream_ticks()
    tick = await anext(stream)
    await adapter.close()

    assert tick == Tick("ETH", 3100.0, now)
    assert any(
        level == "warning"
        and event == "hyperliquid_mid_malformed"
        and fields["symbol"] == "BTC"
        for level, event, fields in logger.records
    )


@pytest.mark.asyncio
async def test_stream_ticks_disconnect_reconnects_with_backoff_and_resubscribe() -> (
    None
):
    """R4: disconnects trigger backoff, resubscribe, and RECONNECTED."""

    clock = MutableClock(
        current=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        sleeps=[],
    )
    first_session = FakeWebSocket(
        [_all_mids({"BTC": "43250.5"})],
        terminal_error=RuntimeError("socket closed"),
    )
    second_session = FakeWebSocket([_all_mids({"ETH": "3100"})])
    adapter = HyperliquidExchangeAdapter(
        connect=FakeConnector([first_session, second_session]),
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

    assert first_tick.symbol == "BTC"
    assert second_tick.symbol == "ETH"
    assert [event.state for event in events] == [
        ConnectionState.CONNECTED,
        ConnectionState.DISCONNECTED,
        ConnectionState.RECONNECTED,
    ]
    assert events[1].reconnect_attempt == 1
    assert events[2].gap_seconds == 1
    assert clock.sleeps == [1]
    assert _decoded_subscription(first_session)["subscription"] == {"type": "allMids"}
    assert _decoded_subscription(second_session)["subscription"] == {"type": "allMids"}


@pytest.mark.asyncio
async def test_stream_ticks_stale_timeout_reconnects() -> None:
    """R4: no allMids frame within the stale threshold forces reconnect."""

    clock = MutableClock(
        current=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        sleeps=[],
    )
    first_session = FakeWebSocket([NoMessage()])
    second_session = FakeWebSocket([_all_mids({"BTC": "43250.5"})])
    adapter = HyperliquidExchangeAdapter(
        connect=FakeConnector([first_session, second_session]),
        now_fn=clock.now,
        sleep_func=clock.sleep,
        stale_threshold_seconds=0.01,
    )
    events: list[ConnectionEvent] = []

    async def record(event: ConnectionEvent) -> None:
        events.append(event)

    adapter.subscribe_connection_events(record)

    stream = adapter.stream_ticks()
    tick = await anext(stream)
    await adapter.close()

    assert tick.symbol == "BTC"
    assert [event.state for event in events] == [
        ConnectionState.CONNECTED,
        ConnectionState.STALE,
        ConnectionState.RECONNECTED,
    ]
    assert events[1].reason == "allMids_timeout"
    assert first_session.closed is True
    assert clock.sleeps == [1]


@pytest.mark.asyncio
async def test_fetch_universe_returns_active_canonical_names() -> None:
    """R2: info/meta parsing returns active perp names and tolerates extras."""

    response = FakeResponse(
        {
            "universe": [
                {"name": "BTC", "maxLeverage": 50},
                {"name": "ETH", "extra": "ignored"},
                {"name": "OLD", "isDelisted": True},
                {"not_name": "ignored"},
                "malformed",
            ]
        }
    )
    client = FakeHttpClient(response)
    factory = FakeHttpClientFactory(client)
    adapter = HyperliquidExchangeAdapter(
        info_url="https://example.test/info",
        http_client_factory=factory,
        request_timeout_seconds=5,
    )

    symbols = await adapter.fetch_universe()

    assert symbols == ["BTC", "ETH"]
    assert factory.kwargs == {"timeout": 5}
    assert client.posts == [
        {
            "url": "https://example.test/info",
            "json": {"type": "meta"},
            "headers": {"Content-Type": "application/json"},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client",
    [
        FakeHttpClient(FakeResponse({"error": "bad"}, status_code=500)),
        FakeHttpClient(error=httpx.TimeoutException("timeout")),
        FakeHttpClient(FakeResponse(json_error=ValueError("bad json"))),
        FakeHttpClient(FakeResponse({"not_universe": []})),
    ],
)
async def test_fetch_universe_raises_on_http_timeout_or_malformed_json(
    client: FakeHttpClient,
) -> None:
    """R2: info/meta fetch failures are surfaced to the cache layer."""

    adapter = HyperliquidExchangeAdapter(
        http_client_factory=FakeHttpClientFactory(client),
    )

    with pytest.raises((httpx.HTTPError, ValueError)):
        await adapter.fetch_universe()
