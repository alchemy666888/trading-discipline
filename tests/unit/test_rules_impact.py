"""Unit tests for closed-trade discipline-impact previews."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.models.trade import Direction, Regime, Trade, TradeStatus
from src.rules.impact import DisciplineImpact, discipline_impact


def test_discipline_impact_loss_to_win_changes_streak_and_cap() -> None:
    """R5.3: flipping a recent loss to a win updates streak and cap preview."""

    trades = [
        _closed_trade(
            trade_id=3,
            size_usdt=1200.0,
            realized_pnl=-30.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=1000.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=2000.0,
            realized_pnl=50.0,
            closed_offset_minutes=1,
        ),
    ]
    edited = trades[0].model_copy(update={"realized_pnl": 25.0})

    impact = discipline_impact(trades, edited, threshold=2, factor=0.5)

    assert impact == DisciplineImpact(
        streak_before=2,
        streak_after=0,
        cap_before=1000.0,
        cap_after=None,
    )


def test_discipline_impact_winner_size_edit_moves_cap() -> None:
    """R5.3: changing the most recent winner's size moves the active cap."""

    trades = [
        _closed_trade(
            trade_id=3,
            size_usdt=1200.0,
            realized_pnl=-30.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            size_usdt=1000.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=2000.0,
            realized_pnl=50.0,
            closed_offset_minutes=1,
        ),
    ]
    edited = trades[2].model_copy(update={"size_usdt": 3000.0})

    impact = discipline_impact(trades, edited, threshold=2, factor=0.5)

    assert impact.streak_before == 2
    assert impact.streak_after == 2
    assert impact.cap_before == 1000.0
    assert impact.cap_after == 1500.0


def test_discipline_impact_non_pnl_edit_leaves_state_unchanged() -> None:
    """R5.3: non-outcome edits do not move the streak or cap."""

    trades = [
        _closed_trade(
            trade_id=2,
            size_usdt=1000.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            size_usdt=2000.0,
            realized_pnl=50.0,
            closed_offset_minutes=1,
        ),
    ]
    edited = trades[0].model_copy(update={"regime": Regime.DOWNTREND})

    impact = discipline_impact(trades, edited, threshold=2, factor=0.5)

    assert impact == DisciplineImpact(
        streak_before=1,
        streak_after=1,
        cap_before=None,
        cap_after=None,
    )


def _closed_trade(
    *,
    trade_id: int,
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
