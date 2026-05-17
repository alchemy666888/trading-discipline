"""Bybit exchange adapter stub reserved for a later implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator

from src.exchange.base import ExchangeAdapter, Tick


class BybitExchangeAdapter(ExchangeAdapter):
    """v1.1 stub for a future Bybit market-data adapter."""

    def stream_ticks(self) -> AsyncIterator[Tick]:
        msg = "Bybit exchange adapter is not implemented in v1."
        raise NotImplementedError(msg)

    async def healthy(self) -> bool:
        return False

    async def close(self) -> None:
        return None
