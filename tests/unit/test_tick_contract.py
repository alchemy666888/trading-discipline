"""Unit tests for the normalized exchange tick contract."""

from __future__ import annotations

from datetime import UTC, datetime

from src.exchange.base import Tick
from src.models.trade import Direction
from src.monitor.breach import is_breach


def test_tick_carries_symbol_through_breach_evaluation() -> None:
    """R3: ticks carry the symbol alongside the price used for breach checks."""

    ts = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    tick = Tick("BTC", 43250.5, ts)

    breached = is_breach(Direction.LONG, 43251.0, tick.price)

    assert tick.symbol == "BTC"
    assert tick.price == 43250.5
    assert tick.ts == ts
    assert breached is True
