"""Unit tests for deterministic stats calculation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.models.breach import Breach, BreachUserResponse
from src.models.trade import Direction, Regime, Trade, TradeStatus
from src.stats.calculator import StatsResult, compute_stats


def _closed_trade(
    trade_id: int,
    *,
    direction: Direction,
    regime: Regime,
    opened_at: datetime,
    closed_at: datetime,
    size_usdt: float,
    invalidation_price: float,
    close_price: float,
    realized_pnl: float,
    leverage_override_reason: str | None = None,
    size_reduction_enforced: bool = False,
) -> Trade:
    return Trade(
        id=trade_id,
        direction=direction,
        size_usdt=size_usdt,
        leverage=5,
        leverage_override_reason=leverage_override_reason,
        entry_price=82000.0,
        invalidation_price=invalidation_price,
        max_loss_usdt=50.0,
        regime=regime,
        thesis=f"Trade {trade_id} fixture thesis.",
        status=TradeStatus.CLOSED,
        size_reduction_enforced=size_reduction_enforced,
        opened_at=opened_at,
        closed_at=closed_at,
        close_price=close_price,
        realized_pnl=realized_pnl,
    )


def _breach(
    breach_id: int,
    *,
    trade_id: int,
    detected_at: datetime,
    price: float,
    user_response: BreachUserResponse | None,
) -> Breach:
    return Breach(
        id=breach_id,
        trade_id=trade_id,
        detected_at=detected_at,
        breach_price=price,
        user_response=user_response,
        response_at=detected_at + timedelta(minutes=1) if user_response else None,
        justification=(
            "Still valid after retest."
            if user_response == BreachUserResponse.JUSTIFIED
            else None
        ),
    )


def test_compute_stats_is_deterministic_and_groups_regime_pnl() -> None:
    """REQ-007: stats are deterministic and compute regime-level P&L correctly."""

    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    trades = [
        _closed_trade(
            1,
            direction=Direction.LONG,
            regime=Regime.UPTREND,
            opened_at=now - timedelta(days=3, hours=2),
            closed_at=now - timedelta(days=3),
            size_usdt=1000.0,
            invalidation_price=81000.0,
            close_price=81300.0,
            realized_pnl=100.0,
        ),
        _closed_trade(
            2,
            direction=Direction.LONG,
            regime=Regime.RANGE,
            opened_at=now - timedelta(days=2, hours=2),
            closed_at=now - timedelta(days=2),
            size_usdt=800.0,
            invalidation_price=81000.0,
            close_price=81000.0,
            realized_pnl=-50.0,
            leverage_override_reason="Defined event with tight stop.",
            size_reduction_enforced=True,
        ),
        _closed_trade(
            3,
            direction=Direction.SHORT,
            regime=Regime.DOWNTREND,
            opened_at=now - timedelta(days=1, hours=2),
            closed_at=now - timedelta(days=1),
            size_usdt=700.0,
            invalidation_price=83000.0,
            close_price=83000.0,
            realized_pnl=0.0,
            size_reduction_enforced=True,
        ),
    ]
    breaches = [
        _breach(
            1,
            trade_id=2,
            detected_at=now - timedelta(days=2, hours=1),
            price=80990.0,
            user_response=BreachUserResponse.CLOSED,
        ),
        _breach(
            2,
            trade_id=1,
            detected_at=now - timedelta(days=3, hours=1),
            price=80980.0,
            user_response=BreachUserResponse.JUSTIFIED,
        ),
        _breach(
            3,
            trade_id=3,
            detected_at=now - timedelta(days=1, hours=1),
            price=83010.0,
            user_response=None,
        ),
    ]

    first = compute_stats(trades, breaches, 7, now=now)
    second = compute_stats(trades, breaches, 7, now=now)

    assert first == second
    assert isinstance(first, StatsResult)
    assert first.total_trades == 3
    assert first.wins == 1
    assert first.losses == 1
    assert first.breakeven == 1
    assert first.win_rate == 1 / 3
    assert first.total_realized_pnl == 50.0
    assert first.breach_count == 3
    assert first.adherence_rate == 1 / 3
    assert first.leverage_override_count == 1
    assert first.size_reduction_enforcement_count == 2
    assert first.size_reduction_compliance_rate == 1.0
    assert first.pnl_by_regime == {
        "uptrend": 100.0,
        "range": -50.0,
        "downtrend": 0.0,
        "event_risk": 0.0,
    }


def test_compute_stats_rejects_invalid_window_days() -> None:
    """REQ-007: stats window must be a positive rolling-day count."""

    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    try:
        compute_stats([], [], 0, now=now)
    except ValueError as exc:
        assert "window_days" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError for window_days=0")
