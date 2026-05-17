"""Pure consecutive-loss sizing rule."""

from __future__ import annotations

from datetime import datetime

from src.models.trade import Trade, TradeStatus
from src.rules.context import RuleContext


def compute_size_cap(
    ctx: RuleContext,
    threshold: int,
    factor: float,
) -> float | None:
    """Compute the active size cap after a consecutive-loss streak."""

    if threshold <= 0:
        msg = "threshold must be greater than 0."
        raise ValueError(msg)
    if not 0 < factor <= 1:
        msg = "factor must be greater than 0 and less than or equal to 1."
        raise ValueError(msg)

    closed_trades = _sorted_closed_trades(ctx.recent_trades)
    if not closed_trades:
        return None

    if _consecutive_loss_count(closed_trades) < threshold:
        return None

    for trade in closed_trades:
        if trade.realized_pnl is not None and trade.realized_pnl > 0:
            return factor * trade.size_usdt

    sizes = [trade.size_usdt for trade in closed_trades]
    if not sizes:
        return None
    if len(sizes) < 5:
        return factor * max(sizes)

    last_five_sizes = sizes[:5]
    average_last_five = sum(last_five_sizes) / len(last_five_sizes)
    max_size_ever = max(sizes)
    return factor * min(average_last_five, max_size_ever)


def _consecutive_loss_count(closed_trades: list[Trade]) -> int:
    streak = 0
    for trade in closed_trades:
        if trade.realized_pnl is None:
            continue
        if trade.realized_pnl < 0:
            streak += 1
            continue
        if trade.realized_pnl > 0:
            break
    return streak


def _sorted_closed_trades(trades: list[Trade]) -> list[Trade]:
    closed_trades = [
        trade
        for trade in trades
        if trade.status == TradeStatus.CLOSED and trade.closed_at is not None
    ]
    return sorted(
        closed_trades,
        key=lambda trade: _closed_sort_key(trade.closed_at, trade.opened_at),
        reverse=True,
    )


def _closed_sort_key(closed_at: datetime | None, opened_at: datetime) -> datetime:
    return closed_at if closed_at is not None else opened_at
