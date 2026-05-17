"""Pure stats calculator for rolling trade-discipline windows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.models.breach import Breach, BreachUserResponse
from src.models.trade import Direction, Regime, Trade, TradeStatus


@dataclass(frozen=True)
class StatsResult:
    """Deterministic REQ-007 metrics for a rolling window."""

    window_days: int
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    total_realized_pnl: float
    breach_count: int
    adherence_rate: float
    leverage_override_count: int
    size_reduction_enforcement_count: int
    size_reduction_compliance_rate: float
    pnl_by_regime: dict[str, float]


def compute_stats(
    trades: Sequence[Trade],
    breaches: Sequence[Breach],
    window_days: int,
    *,
    now: datetime | None = None,
) -> StatsResult:
    """Compute REQ-007 metrics over a trailing time window."""

    if window_days <= 0:
        msg = "window_days must be greater than 0."
        raise ValueError(msg)

    reference_now = now or datetime.now(tz=UTC)
    cutoff = reference_now - timedelta(days=window_days)
    closed_window_trades = [
        trade
        for trade in trades
        if trade.status == TradeStatus.CLOSED
        and trade.closed_at is not None
        and trade.closed_at >= cutoff
    ]
    breaches_in_window = [breach for breach in breaches if breach.detected_at >= cutoff]
    trade_lookup = {trade.id: trade for trade in trades}

    wins = sum(
        1
        for trade in closed_window_trades
        if trade.realized_pnl is not None and trade.realized_pnl > 0
    )
    losses = sum(
        1
        for trade in closed_window_trades
        if trade.realized_pnl is not None and trade.realized_pnl < 0
    )
    breakeven = sum(
        1
        for trade in closed_window_trades
        if trade.realized_pnl is not None and trade.realized_pnl == 0
    )
    total_trades = len(closed_window_trades)
    win_rate = 0.0 if total_trades == 0 else wins / total_trades
    total_realized_pnl = sum(
        trade.realized_pnl or 0.0 for trade in closed_window_trades
    )

    adherence_hits = 0
    for breach in breaches_in_window:
        trade = trade_lookup.get(breach.trade_id)
        if trade is None or breach.user_response != BreachUserResponse.CLOSED:
            continue
        close_price = trade.close_price
        if close_price is None:
            continue
        if _closed_at_or_better_than_invalidation(trade, close_price):
            adherence_hits += 1

    breach_count = len(breaches_in_window)
    adherence_rate = 0.0 if breach_count == 0 else adherence_hits / breach_count
    leverage_override_count = sum(
        1
        for trade in closed_window_trades
        if trade.leverage_override_reason is not None
    )
    size_reduction_enforcement_count = sum(
        1 for trade in closed_window_trades if trade.size_reduction_enforced
    )
    size_reduction_compliance_rate = _size_reduction_compliance_rate(
        closed_window_trades
    )

    pnl_by_regime = {regime.value: 0.0 for regime in Regime}
    for trade in closed_window_trades:
        pnl_by_regime[trade.regime.value] += trade.realized_pnl or 0.0

    return StatsResult(
        window_days=window_days,
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate=win_rate,
        total_realized_pnl=total_realized_pnl,
        breach_count=breach_count,
        adherence_rate=adherence_rate,
        leverage_override_count=leverage_override_count,
        size_reduction_enforcement_count=size_reduction_enforcement_count,
        size_reduction_compliance_rate=size_reduction_compliance_rate,
        pnl_by_regime=pnl_by_regime,
    )


def _closed_at_or_better_than_invalidation(trade: Trade, close_price: float) -> bool:
    if trade.direction == Direction.LONG:
        return close_price >= trade.invalidation_price
    return close_price <= trade.invalidation_price


def _size_reduction_compliance_rate(closed_window_trades: Sequence[Trade]) -> float:
    active_count = sum(
        1 for trade in closed_window_trades if trade.size_reduction_enforced
    )
    if active_count == 0:
        return 0.0
    compliant_count = sum(
        1 for trade in closed_window_trades if trade.size_reduction_enforced
    )
    return compliant_count / active_count
