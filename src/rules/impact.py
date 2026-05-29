"""Pure discipline-impact preview helpers."""

from __future__ import annotations

from dataclasses import dataclass

from src.models.trade import Trade, TradeDraft
from src.rules.context import RuleContext
from src.rules.sizing import compute_size_cap, consecutive_loss_count


@dataclass(frozen=True)
class DisciplineImpact:
    """Before/after loss-streak and size-cap preview."""

    streak_before: int
    streak_after: int
    cap_before: float | None
    cap_after: float | None


def discipline_impact(
    closed_trades: list[Trade],
    edited: Trade,
    *,
    threshold: int = 2,
    factor: float = 0.5,
) -> DisciplineImpact:
    """Compute the discipline impact over a caller-scoped closed-trade list."""

    before = list(closed_trades)
    after = [edited if trade.id == edited.id else trade for trade in closed_trades]
    if all(trade.id != edited.id for trade in after):
        after.append(edited)

    return DisciplineImpact(
        streak_before=consecutive_loss_count(before),
        streak_after=consecutive_loss_count(after),
        cap_before=_size_cap(before, threshold=threshold, factor=factor),
        cap_after=_size_cap(after, threshold=threshold, factor=factor),
    )


def _size_cap(
    trades: list[Trade],
    *,
    threshold: int,
    factor: float,
) -> float | None:
    return compute_size_cap(
        RuleContext(trade_draft=TradeDraft(), recent_trades=trades),
        threshold,
        factor,
    )
