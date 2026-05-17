"""Pure rule-context dataclass."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from src.models.signal import Signal
from src.models.trade import Trade, TradeDraft


@dataclass(frozen=True)
class RuleContext:
    """Context passed to every rule function in v1 and v2."""

    trade_draft: TradeDraft
    recent_trades: list[Trade]
    signals: Mapping[str, Signal] = field(default_factory=dict)
