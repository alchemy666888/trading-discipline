"""Pure leverage-block rule."""

from __future__ import annotations

from enum import StrEnum

from src.rules.context import RuleContext


class LeverageDecision(StrEnum):
    """Possible leverage-rule decisions."""

    ALLOW = "ALLOW"
    BLOCK_NEEDS_OVERRIDE = "BLOCK_NEEDS_OVERRIDE"
    REJECT_OUT_OF_RANGE = "REJECT_OUT_OF_RANGE"


def check(ctx: RuleContext, threshold: int) -> LeverageDecision:
    """Evaluate the configured leverage threshold without any I/O."""

    if threshold <= 0:
        msg = "threshold must be greater than 0."
        raise ValueError(msg)

    leverage = ctx.trade_draft.leverage
    if leverage is None or leverage <= 0 or leverage > 125:
        return LeverageDecision.REJECT_OUT_OF_RANGE
    if leverage >= threshold:
        return LeverageDecision.BLOCK_NEEDS_OVERRIDE
    return LeverageDecision.ALLOW
