"""Unit tests for the pure leverage-block rule."""

from __future__ import annotations

from datetime import UTC, datetime

from src.models.signal import Severity, Signal
from src.models.trade import Direction, Regime, TradeDraft
from src.rules.context import RuleContext
from src.rules.leverage import LeverageDecision, check


def _ctx(leverage: int, *, with_signals: bool = False) -> RuleContext:
    signals = {}
    if with_signals:
        signals = {
            "macro": Signal(
                id=1,
                source="calendar",
                kind="macro_event",
                severity=Severity.HIGH,
                detected_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
                expires_at=None,
                payload_json='{"name":"CPI"}',
                summary="CPI soon.",
            )
        }
    return RuleContext(
        trade_draft=TradeDraft.model_construct(
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=leverage,
            entry_price=82000.0,
            invalidation_price=81000.0,
            max_loss_usdt=20.0,
            regime=Regime.RANGE,
            thesis="Sufficiently long thesis text.",
        ),
        recent_trades=[],
        signals=signals,
    )


def test_leverage_rule_edge_cases() -> None:
    """REQ-002: leverage edges return the expected decision states."""

    threshold = 20

    assert check(_ctx(1), threshold) == LeverageDecision.ALLOW
    assert check(_ctx(threshold - 1), threshold) == LeverageDecision.ALLOW
    assert check(_ctx(threshold), threshold) == LeverageDecision.BLOCK_NEEDS_OVERRIDE
    assert (
        check(_ctx(threshold + 1), threshold) == LeverageDecision.BLOCK_NEEDS_OVERRIDE
    )
    assert check(_ctx(125), threshold) == LeverageDecision.BLOCK_NEEDS_OVERRIDE
    assert check(_ctx(126), threshold) == LeverageDecision.REJECT_OUT_OF_RANGE
    assert check(_ctx(0), threshold) == LeverageDecision.REJECT_OUT_OF_RANGE
    assert check(_ctx(-1), threshold) == LeverageDecision.REJECT_OUT_OF_RANGE


def test_leverage_rule_ignores_signals_in_v1() -> None:
    """REQ-010: leverage decisions are unchanged when arbitrary signals are present."""

    assert check(_ctx(20, with_signals=False), 20) == check(
        _ctx(20, with_signals=True),
        20,
    )
