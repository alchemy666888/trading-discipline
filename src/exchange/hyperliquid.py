"""Hyperliquid public websocket and info/meta adapter."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
import websockets

from src.exchange.base import ConnectionEvent, ConnectionState, ExchangeAdapter, Tick

_BACKOFF_SEQUENCE = (1, 2, 4, 8, 16, 32, 60)
_DEFAULT_WS_URL = "wss://api.hyperliquid.xyz/ws"
_DEFAULT_INFO_URL = "https://api.hyperliquid.xyz/info"

HttpClientFactory = Callable[..., AbstractAsyncContextManager[Any]]


class HyperliquidExchangeAdapter(ExchangeAdapter):
    """Hyperliquid allMids adapter with reconnect and universe fetching."""

    def __init__(
        self,
        settings: Any | None = None,
        *,
        websocket_url: str | None = None,
        info_url: str | None = None,
        connect: Callable[[str], Any] = websockets.connect,
        http_client_factory: HttpClientFactory = httpx.AsyncClient,
        now_fn: Callable[[], datetime] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
        stale_threshold_seconds: float | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.websocket_url = str(
            websocket_url
            if websocket_url is not None
            else getattr(settings, "hyperliquid_ws_url", _DEFAULT_WS_URL)
        )
        self.info_url = str(
            info_url
            if info_url is not None
            else getattr(settings, "hyperliquid_info_url", _DEFAULT_INFO_URL)
        )
        self._connect = connect
        self._http_client_factory = http_client_factory
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._sleep = sleep_func
        raw_stale_threshold = (
            stale_threshold_seconds
            if stale_threshold_seconds is not None
            else getattr(settings, "hyperliquid_feed_stale_seconds", 30)
        )
        raw_request_timeout = (
            request_timeout_seconds
            if request_timeout_seconds is not None
            else getattr(settings, "hyperliquid_feed_request_timeout_seconds", 5)
        )
        self._stale_threshold_seconds = float(raw_stale_threshold)
        self._request_timeout_seconds = float(raw_request_timeout)
        self._logger = structlog.get_logger(__name__)
        self._closed = False
        self._connected = False
        self._has_connected_once = False
        self._reconnect_attempt = 0
        self._down_since: datetime | None = None
        self._last_frame_at: datetime | None = None
        self._current_websocket: Any | None = None

    async def stream_ticks(self) -> AsyncIterator[Tick]:
        """Yield one normalized tick per symbol from the shared allMids feed."""

        while not self._closed:
            should_reconnect = False
            try:
                async with self._connect(self.websocket_url) as websocket:
                    self._current_websocket = websocket
                    await self._send_subscription(websocket)
                    await self._publish_connected()

                    while not self._closed:
                        try:
                            raw_message = await asyncio.wait_for(
                                websocket.recv(),
                                timeout=self._stale_threshold_seconds,
                            )
                        except TimeoutError:
                            should_reconnect = True
                            await self._mark_stale(websocket)
                            break

                        received_at = self._now()
                        for tick in self._parse_message(raw_message, received_at):
                            yield tick
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    break
                should_reconnect = True
                await self._mark_disconnected(exc)
            finally:
                self._current_websocket = None

            if self._closed:
                break
            if should_reconnect:
                await self._sleep(self._backoff_delay(self._reconnect_attempt))
            else:
                break

    async def healthy(self) -> bool:
        """Return true iff a recent allMids frame has arrived."""

        if self._closed or self._last_frame_at is None:
            return False
        age_seconds = (self._now() - self._last_frame_at).total_seconds()
        return age_seconds <= self._stale_threshold_seconds

    async def close(self) -> None:
        """Stop streaming and close the active websocket if present."""

        self._closed = True
        self._connected = False
        if self._current_websocket is not None:
            await self._close_websocket(self._current_websocket)

    async def fetch_universe(self) -> list[str]:
        """Fetch Hyperliquid perpetual universe names from info/meta."""

        async with self._http_client_factory(
            timeout=self._request_timeout_seconds,
        ) as client:
            response = await client.post(
                self.info_url,
                json={"type": "meta"},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict):
            msg = "Hyperliquid meta response must be an object."
            raise ValueError(msg)
        universe = data.get("universe")
        if not isinstance(universe, list):
            msg = "Hyperliquid meta response missing universe list."
            raise ValueError(msg)

        symbols: list[str] = []
        for entry in universe:
            if not isinstance(entry, dict):
                continue
            if entry.get("isDelisted") is True:
                continue
            name = entry.get("name")
            if isinstance(name, str) and name:
                symbols.append(name)
        return symbols

    async def _send_subscription(self, websocket: Any) -> None:
        message = json.dumps(
            {
                "method": "subscribe",
                "subscription": {"type": "allMids"},
            }
        )
        result = websocket.send(message)
        if asyncio.iscoroutine(result):
            await result

    async def _publish_connected(self) -> None:
        now = self._now()
        if self._has_connected_once and self._down_since is not None:
            gap_seconds = max(0, int((now - self._down_since).total_seconds()))
            await self._publish_connection_event(
                ConnectionEvent(
                    state=ConnectionState.RECONNECTED,
                    ts=now,
                    reconnect_attempt=self._reconnect_attempt,
                    gap_seconds=gap_seconds,
                )
            )
            self._logger.info(
                "hyperliquid_reconnected",
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
            self._logger.info("hyperliquid_connected")

        self._connected = True
        self._has_connected_once = True
        self._reconnect_attempt = 0

    async def _mark_stale(self, websocket: Any) -> None:
        event_ts = self._now()
        self._connected = False
        self._reconnect_attempt += 1
        if self._down_since is None:
            self._down_since = event_ts
        await self._publish_connection_event(
            ConnectionEvent(
                state=ConnectionState.STALE,
                ts=event_ts,
                reconnect_attempt=self._reconnect_attempt,
                reason="allMids_timeout",
            )
        )
        self._logger.warning(
            "hyperliquid_stale",
            reconnect_attempt=self._reconnect_attempt,
        )
        await self._close_websocket(websocket)

    async def _mark_disconnected(self, exc: Exception) -> None:
        self._connected = False
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
            "hyperliquid_disconnected",
            reconnect_attempt=self._reconnect_attempt,
            error=str(exc),
        )

    def _parse_message(self, raw_message: Any, received_at: datetime) -> list[Tick]:
        try:
            payload = self._decode_message(raw_message)
        except (TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._logger.warning("hyperliquid_message_malformed", error=str(exc))
            return []

        if not isinstance(payload, dict):
            self._logger.warning("hyperliquid_message_malformed")
            return []

        channel = payload.get("channel")
        if channel == "subscriptionResponse":
            self._logger.info("hyperliquid_subscription_ack")
            return []
        if channel != "allMids":
            return []

        data = payload.get("data")
        mids = data.get("mids") if isinstance(data, dict) else None
        if not isinstance(mids, dict):
            self._logger.warning("hyperliquid_all_mids_malformed")
            return []

        self._last_frame_at = received_at
        ticks: list[Tick] = []
        for raw_symbol, raw_price in mids.items():
            if not isinstance(raw_symbol, str):
                self._logger.warning("hyperliquid_mid_malformed", symbol=raw_symbol)
                continue
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                self._logger.warning(
                    "hyperliquid_mid_malformed",
                    symbol=raw_symbol,
                    price=raw_price,
                )
                continue
            if not math.isfinite(price):
                self._logger.warning(
                    "hyperliquid_mid_malformed",
                    symbol=raw_symbol,
                    price=raw_price,
                )
                continue
            ticks.append(Tick(raw_symbol, price, received_at))
        return ticks

    @staticmethod
    def _decode_message(raw_message: Any) -> Any:
        if isinstance(raw_message, bytes):
            return json.loads(raw_message.decode("utf-8"))
        if isinstance(raw_message, str):
            return json.loads(raw_message)
        return raw_message

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
