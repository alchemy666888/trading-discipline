"""Unit tests for the pure consecutive-loss sizing rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.models.signal import Severity, Signal
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus
from src.rules.context import RuleContext
from src.rules.sizing import compute_size_cap


def _closed_trade(
    *,
    trade_id: int,
    symbol: str = "BTC",
    size_usdt: float,
    realized_pnl: float,
    closed_offset_minutes: int,
) -> Trade:
    opened_at = datetime(2026, 5, 18, 9, 0, tzinfo=UTC) + timedelta(
        minutes=closed_offset_minutes - 1
    )
    closed_at = datetime(2026, 5, 18, 9, 0, tzinfo=UTC) + timedelta(
        minutes=closed_offset_minutes
    )
    return Trade(
        id=trade_id,
        symbol=symbol,
        direction=Direction.LONG,
        size_usdt=size_usdt,
        leverage=5,
        leverage_override_reason=None,
        entry_price=82000.0,
        invalidation_price=81000.0,
        max_loss_usdt=20.0,
        regime=Regime.RANGE,
        thesis=f"Closed trade {trade_id} thesis text.",
        status=TradeStatus.CLOSED,
        size_reduction_enforced=False,
        opened_at=opened_at,
        closed_at=closed_at,
        close_price=81000.0,
        realized_pnl=realized_pnl,
    )


def _ctx(trades: list[Trade], *, with_signals: bool = False) -> RuleContext:
    signals = {}
    if with_signals:
        signals = {
            "funding": Signal(
                id=1,
                source="funding",
                kind="funding_extreme",
                severity=Severity.CRITICAL,
                detected_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
                expires_at=None,
                payload_json='{"rate":0.01}',
                summary="Funding elevated.",
            )
        }
    return RuleContext(
        trade_draft=TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=2000.0,
            leverage=5,
            entry_price=82000.0,
            invalidation_price=81000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Upcoming trade thesis is sufficiently descriptive.",
        ),
        recent_trades=trades,
        signals=signals,
    )


def test_sizing_rule_no_history_has_no_cap() -> None:
    """REQ-003: no prior trades means no size cap is active."""

    assert compute_size_cap(_ctx([]), threshold=2, factor=0.5) is None


def test_sizing_rule_one_loss_has_no_cap() -> None:
    """REQ-003: one consecutive loss does not activate the size cap."""

    trades = [
        _closed_trade(
            trade_id=1,
            size_usdt=1000.0,
            realized_pnl=-10.0,
            closed_offset_minutes=1,
        )
    ]

    assert compute_size_cap(_ctx(trades), threshold=2, factor=0.5) is None


def test_sizing_rule_two_losses_with_recent_winner_uses_winner_size() -> None:
    """REQ-003: two losses after a winner cap size at factor * recent winning size."""

    trades = [
        _closed_trade(
            trade_id=3,
            size_usdt=1800.0,
            realized_pnl=-30.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=1600.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=2400.0,
            realized_pnl=40.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(_ctx(trades), threshold=2, factor=0.5) == 1200.0


def test_sizing_rule_three_losses_after_winner_keeps_same_reference() -> None:
    """REQ-003: a longer losing streak still uses the most recent winner."""

    trades = [
        _closed_trade(
            trade_id=4,
            size_usdt=1700.0,
            realized_pnl=-30.0,
            closed_offset_minutes=4,
        ),
        _closed_trade(
            trade_id=3,
            size_usdt=1600.0,
            realized_pnl=-25.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=1500.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=3000.0,
            realized_pnl=50.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(_ctx(trades), threshold=2, factor=0.5) == 1500.0


def test_sizing_rule_breakeven_does_not_reset_or_increment_streak() -> None:
    """REQ-003: breakeven trades neither increment nor reset the losing streak."""

    trades = [
        _closed_trade(
            trade_id=4,
            size_usdt=1400.0,
            realized_pnl=-30.0,
            closed_offset_minutes=4,
        ),
        _closed_trade(
            trade_id=3,
            size_usdt=1300.0,
            realized_pnl=0.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=1200.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=2000.0,
            realized_pnl=25.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(_ctx(trades), threshold=2, factor=0.5) == 1000.0


def test_sizing_rule_no_winners_ever_with_fewer_than_five_uses_max_size() -> None:
    """REQ-003: under five prior losses and no winners, the cap uses max size so far."""

    trades = [
        _closed_trade(
            trade_id=3,
            size_usdt=1200.0,
            realized_pnl=-30.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=2200.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=1800.0,
            realized_pnl=-10.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(_ctx(trades), threshold=2, factor=0.5) == 1100.0


def test_sizing_rule_no_winners_ever_with_five_uses_average_of_last_five() -> None:
    """REQ-003: five losses and no winners uses factor * avg(last five sizes)."""

    trades = [
        _closed_trade(
            trade_id=5,
            size_usdt=1000.0,
            realized_pnl=-50.0,
            closed_offset_minutes=5,
        ),
        _closed_trade(
            trade_id=4,
            size_usdt=2000.0,
            realized_pnl=-40.0,
            closed_offset_minutes=4,
        ),
        _closed_trade(
            trade_id=3,
            size_usdt=3000.0,
            realized_pnl=-30.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=4000.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=5000.0,
            realized_pnl=-10.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(_ctx(trades), threshold=2, factor=0.5) == 1500.0


def test_sizing_rule_mixed_history_below_threshold_has_no_cap() -> None:
    """REQ-003: mixed history below the loss threshold leaves cap inactive."""

    trades = [
        _closed_trade(
            trade_id=3,
            size_usdt=1500.0,
            realized_pnl=-30.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=1200.0,
            realized_pnl=40.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=1100.0,
            realized_pnl=-15.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(_ctx(trades), threshold=2, factor=0.5) is None


def test_sizing_rule_ignores_signals_in_v1() -> None:
    """REQ-010: size-cap decisions are unchanged when arbitrary signals are present."""

    trades = [
        _closed_trade(
            trade_id=2,
            size_usdt=1500.0,
            realized_pnl=-30.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=3000.0,
            realized_pnl=50.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(
        _ctx(trades, with_signals=False), 1, 0.5
    ) == compute_size_cap(
        _ctx(trades, with_signals=True),
        1,
        0.5,
    )
