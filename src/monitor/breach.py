"""Pure breach-evaluation logic."""

from __future__ import annotations

from src.models.trade import Direction


def is_breach(direction: Direction, invalidation: float, tick_price: float) -> bool:
    """Return whether the current tick breaches the trade invalidation."""

    if direction == Direction.LONG:
        return tick_price <= invalidation
    return tick_price >= invalidation
