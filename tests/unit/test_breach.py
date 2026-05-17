"""Unit tests for the pure breach evaluator."""

from __future__ import annotations

from src.models.trade import Direction
from src.monitor.breach import is_breach


def test_breach_logic_for_long_and_short() -> None:
    """REQ-004: breach comparisons match the long/short invalidation rules."""

    assert is_breach(Direction.LONG, 81000.0, 80999.0) is True
    assert is_breach(Direction.LONG, 81000.0, 81000.0) is True
    assert is_breach(Direction.LONG, 81000.0, 81001.0) is False
    assert is_breach(Direction.SHORT, 83000.0, 83001.0) is True
    assert is_breach(Direction.SHORT, 83000.0, 83000.0) is True
    assert is_breach(Direction.SHORT, 83000.0, 82999.0) is False
