"""Unit tests for centralized Telegram formatting."""

from __future__ import annotations

from datetime import UTC, datetime

from src.bot import formatting
from src.models.trade import Direction, Regime, Trade, TradeStatus
from src.rules.impact import DisciplineImpact


def _trade(
    *,
    trade_id: int = 1,
    symbol: str = "BTC",
    direction: Direction = Direction.LONG,
    status: TradeStatus = TradeStatus.OPEN,
) -> Trade:
    closed = status == TradeStatus.CLOSED
    return Trade(
        id=trade_id,
        symbol=symbol,
        direction=direction,
        size_usdt=2500.0,
        leverage=5,
        leverage_override_reason=None,
        entry_price=100.0,
        invalidation_price=90.0 if direction == Direction.LONG else 110.0,
        max_loss_usdt=50.0,
        regime=Regime.RANGE,
        thesis=f"{symbol} formatting fixture thesis text.",
        status=status,
        size_reduction_enforced=False,
        opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        closed_at=datetime(2026, 5, 17, 10, 0, tzinfo=UTC) if closed else None,
        close_price=95.0 if closed else None,
        realized_pnl=-25.0 if closed else None,
    )


def test_symbol_prompt_and_rejections_are_centralized() -> None:
    """R7: symbol copy lives in the formatting module."""

    assert formatting.prompt_symbol() == "Symbol? (e.g. BTC, ETH, HYPE, AUDUSD)"
    assert "Unknown symbol 'DOGE'" in formatting.symbol_unknown("DOGE")
    assert (
        formatting.symbol_universe_unavailable()
        == "Hyperliquid market list unavailable. Try again in a few seconds."
    )


def test_trade_formatters_include_crypto_symbol() -> None:
    """R7: trade-facing messages identify crypto symbols."""

    trade = _trade(symbol="BTC")

    assert formatting.trade_committed(trade, warn_multiple_open=False).startswith(
        "Trade #1 (BTC long) committed"
    )
    assert "#1 BTC long" in formatting.open_trades([trade])
    assert "Trade #1 (BTC long)" in formatting.edit_confirmation(trade, ["size_usdt"])
    assert "Trade #1 (BTC long)" in formatting.breach_initial_alert(
        trade,
        current_price=89.0,
        elapsed_seconds=3,
        current_loss_usdt=40.0,
    )


def test_trade_formatters_include_non_crypto_symbol() -> None:
    """R7: trade-facing messages identify non-crypto symbols."""

    trade = _trade(symbol="AUDUSD", direction=Direction.SHORT)
    closed = _trade(
        trade_id=2,
        symbol="AUDUSD",
        direction=Direction.SHORT,
        status=TradeStatus.CLOSED,
    )

    assert "Trade #1 (AUDUSD short)" in formatting.trade_committed(
        trade,
        warn_multiple_open=False,
    )
    assert "#1 AUDUSD short" in formatting.open_trades([trade])
    assert "Trade #2 (AUDUSD short) closed" in formatting.close_confirmation(
        closed,
        streak=1,
    )
    assert "Trade #2 (AUDUSD short) P&L updated" in formatting.setpnl_confirmation(
        closed,
        pnl=-15.0,
        streak=2,
    )
    assert "Trade #2 (AUDUSD short) marked OPEN_OVERRIDE" in (
        formatting.justification_recorded(closed)
    )
    assert "Trade #2 (AUDUSD short) updated" in formatting.edit_closed_applied(
        closed,
        ["close_price"],
    )
    assert "Trade #1 (AUDUSD short)" in formatting.breach_escalation_alert(
        trade,
        current_price=111.0,
        elapsed_seconds=61,
        current_loss_usdt=35.0,
    )


def test_edit_closed_preview_remains_symbol_agnostic() -> None:
    """R7: impact preview copy still renders the scoped before/after numbers."""

    impact = DisciplineImpact(
        streak_before=2,
        streak_after=0,
        cap_before=1000.0,
        cap_after=None,
    )

    message = formatting.edit_closed_preview(
        {"realized_pnl": (-25.0, 10.0)},
        recomputed_pnl=10.0,
        impact=impact,
        pnl_override_warning=False,
    )

    assert "Consecutive-loss streak: 2" in message
    assert "Active size cap: 1000 USDT" in message
