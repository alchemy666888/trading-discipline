"""Unit tests for symbol-scoped streak and size-cap behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus
from src.rules.context import RuleContext
from src.rules.impact import discipline_impact
from src.rules.sizing import compute_size_cap, consecutive_loss_count


def _closed_trade(
    *,
    trade_id: int,
    symbol: str,
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
        entry_price=100.0,
        invalidation_price=90.0,
        max_loss_usdt=20.0,
        regime=Regime.RANGE,
        thesis=f"{symbol} closed trade {trade_id} thesis text.",
        status=TradeStatus.CLOSED,
        size_reduction_enforced=False,
        opened_at=opened_at,
        closed_at=closed_at,
        close_price=90.0,
        realized_pnl=realized_pnl,
    )


def _candidate_ctx(symbol: str, trades: list[Trade]) -> RuleContext:
    return RuleContext(
        trade_draft=TradeDraft(
            symbol=symbol,
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=100.0,
            invalidation_price=90.0,
            max_loss_usdt=20.0,
            regime=Regime.RANGE,
            thesis=f"{symbol} candidate trade thesis text.",
        ),
        recent_trades=[trade for trade in trades if trade.symbol == symbol],
    )


def test_btc_loss_streak_does_not_cap_eth_candidate() -> None:
    """R5: callers scope recent trades so another symbol's streak is ignored."""

    trades = [
        _closed_trade(
            trade_id=3,
            symbol="BTC",
            size_usdt=1200.0,
            realized_pnl=-30.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            symbol="BTC",
            size_usdt=1000.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            symbol="BTC",
            size_usdt=2000.0,
            realized_pnl=50.0,
            closed_offset_minutes=1,
        ),
    ]

    assert compute_size_cap(_candidate_ctx("BTC", trades), threshold=2, factor=0.5)
    assert (
        compute_size_cap(_candidate_ctx("ETH", trades), threshold=2, factor=0.5) is None
    )


def test_btc_winner_does_not_reset_eth_streak() -> None:
    """R5: a winner on one symbol cannot reset another symbol's streak."""

    trades = [
        _closed_trade(
            trade_id=3,
            symbol="BTC",
            size_usdt=2000.0,
            realized_pnl=50.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            symbol="ETH",
            size_usdt=1200.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            symbol="ETH",
            size_usdt=1000.0,
            realized_pnl=-15.0,
            closed_offset_minutes=1,
        ),
    ]
    eth_history = [trade for trade in trades if trade.symbol == "ETH"]

    assert consecutive_loss_count(trades) == 0
    assert consecutive_loss_count(eth_history) == 2
    assert (
        compute_size_cap(_candidate_ctx("ETH", trades), threshold=2, factor=0.5)
        == 600.0
    )


def test_edit_closed_impact_preview_uses_same_symbol_subset() -> None:
    """R5: impact previews only move the edited symbol's streak and cap."""

    trades = [
        _closed_trade(
            trade_id=5,
            symbol="ETH",
            size_usdt=1000.0,
            realized_pnl=-10.0,
            closed_offset_minutes=5,
        ),
        _closed_trade(
            trade_id=4,
            symbol="BTC",
            size_usdt=1200.0,
            realized_pnl=-30.0,
            closed_offset_minutes=4,
        ),
        _closed_trade(
            trade_id=3,
            symbol="ETH",
            size_usdt=1000.0,
            realized_pnl=-10.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=2,
            symbol="BTC",
            size_usdt=1000.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=1,
            symbol="BTC",
            size_usdt=2000.0,
            realized_pnl=50.0,
            closed_offset_minutes=1,
        ),
    ]
    btc_history = [trade for trade in trades if trade.symbol == "BTC"]
    eth_history = [trade for trade in trades if trade.symbol == "ETH"]
    edited_btc = btc_history[0].model_copy(update={"realized_pnl": 25.0})

    impact = discipline_impact(btc_history, edited_btc, threshold=2, factor=0.5)

    assert impact.streak_before == 2
    assert impact.streak_after == 0
    assert impact.cap_before == 1000.0
    assert impact.cap_after is None
    assert consecutive_loss_count(eth_history) == 2
