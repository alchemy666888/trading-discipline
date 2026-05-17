"""Binance public websocket adapter for BTCUSDT mark price ticks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
import websockets

from src.exchange.base import ConnectionEvent, ConnectionState, ExchangeAdapter, Tick

_BACKOFF_SEQUENCE = (1, 2, 4, 8, 16, 32, 60)


class BinanceExchangeAdapter(ExchangeAdapter):
    """Binance mark-price websocket adapter with reconnect handling."""

    def __init__(
        self,
        *,
        symbol: str = "BTCUSDT",
        websocket_url: str | None = None,
        connect: Callable[[str], Any] = websockets.connect,
        now_fn: Callable[[], datetime] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
        stale_threshold_seconds: int = 30,
    ) -> None:
        super().__init__()
        self.symbol = symbol.upper()
        self.websocket_url = (
            websocket_url
            or f"wss://fstream.binance.com/ws/{self.symbol.lower()}@markPrice@1s"
        )
        self._connect = connect
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._sleep = sleep_func
        self._stale_threshold_seconds = stale_threshold_seconds
        self._logger = structlog.get_logger(__name__)
        self._closed = False
        self._healthy = False
        self._connected = False
        self._has_connected_once = False
        self._reconnect_attempt = 0
        self._down_since: datetime | None = None
        self._current_websocket: Any | None = None

    async def stream_ticks(self) -> AsyncIterator[Tick]:
        """Yield normalized ticks, reconnecting with exponential backoff."""

        while not self._closed:
            should_reconnect = False
            try:
                async with self._connect(self.websocket_url) as websocket:
                    self._current_websocket = websocket
                    now = self._now()
                    if self._has_connected_once and self._down_since is not None:
                        gap_seconds = max(
                            0,
                            int((now - self._down_since).total_seconds()),
                        )
                        await self._publish_connection_event(
                            ConnectionEvent(
                                state=ConnectionState.RECONNECTED,
                                ts=now,
                                reconnect_attempt=self._reconnect_attempt,
                                gap_seconds=gap_seconds,
                            )
                        )
                        self._logger.info(
                            "exchange_reconnected",
                            gap_seconds=gap_seconds,
                            reconnect_attempt=self._reconnect_attempt,
                        )
                        self._down_since = None
                    else:
                        await self._publish_connection_event(
                            ConnectionEvent(
                                state=ConnectionState.CONNECTED,
                                ts=now,
                            )
                        )
                        self._logger.info("exchange_connected")

                    self._connected = True
                    self._healthy = True
                    self._has_connected_once = True
                    self._reconnect_attempt = 0

                    while not self._closed:
                        raw_message = await websocket.recv()
                        tick = self._parse_tick(raw_message)
                        if self._is_stale_tick(tick):
                            event_ts = self._now()
                            should_reconnect = True
                            self._connected = False
                            self._healthy = False
                            self._reconnect_attempt += 1
                            if self._down_since is None:
                                self._down_since = event_ts
                            await self._publish_connection_event(
                                ConnectionEvent(
                                    state=ConnectionState.STALE,
                                    ts=event_ts,
                                    reconnect_attempt=self._reconnect_attempt,
                                    reason="stale_tick",
                                )
                            )
                            self._logger.warning(
                                "exchange_stale_tick",
                                reconnect_attempt=self._reconnect_attempt,
                            )
                            await self._close_websocket(websocket)
                            break
                        yield tick
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    break
                should_reconnect = True
                self._connected = False
                self._healthy = False
                self._reconnect_attempt += 1
                event_ts = self._now()
                if self._down_since is None:
                    self._down_since = event_ts
                await self._publish_connection_event(
                    ConnectionEvent(
                        state=ConnectionState.DISCONNECTED,
                        ts=event_ts,
                        reconnect_attempt=self._reconnect_attempt,
                        reason=str(exc),
                    )
                )
                self._logger.warning(
                    "exchange_disconnected",
                    reconnect_attempt=self._reconnect_attempt,
                    error=str(exc),
                )
            finally:
                self._current_websocket = None

            if self._closed:
                break
            if should_reconnect:
                await self._sleep(self._backoff_delay(self._reconnect_attempt))
            else:
                break

    async def healthy(self) -> bool:
        """Return the adapter's current health status."""

        return self._healthy and not self._closed

    async def close(self) -> None:
        """Stop streaming and close the active websocket if present."""

        self._closed = True
        self._healthy = False
        self._connected = False
        if self._current_websocket is not None:
            await self._close_websocket(self._current_websocket)

    def _parse_tick(self, raw_message: Any) -> Tick:
        if isinstance(raw_message, bytes):
            payload = json.loads(raw_message.decode("utf-8"))
        elif isinstance(raw_message, str):
            payload = json.loads(raw_message)
        else:
            payload = raw_message

        price = float(payload["p"])
        event_time_ms = int(payload["E"])
        ts = datetime.fromtimestamp(event_time_ms / 1000, tz=UTC)
        return Tick(price=price, ts=ts)

    def _is_stale_tick(self, tick: Tick) -> bool:
        age_seconds = (self._now() - tick.ts).total_seconds()
        return age_seconds > self._stale_threshold_seconds

    def _backoff_delay(self, reconnect_attempt: int) -> int:
        if reconnect_attempt <= 0:
            return _BACKOFF_SEQUENCE[0]
        if reconnect_attempt >= len(_BACKOFF_SEQUENCE):
            return _BACKOFF_SEQUENCE[-1]
        return _BACKOFF_SEQUENCE[reconnect_attempt - 1]

    async def _close_websocket(self, websocket: Any) -> None:
        close = getattr(websocket, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result
