"""Trade models and enums."""

from __future__ import annotations

from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    model_validator,
)


class Direction(StrEnum):
    """Supported trade directions."""

    LONG = "long"
    SHORT = "short"


class Regime(StrEnum):
    """Supported market-regime labels."""

    UPTREND = "uptrend"
    RANGE = "range"
    DOWNTREND = "downtrend"
    EVENT_RISK = "event_risk"


class TradeStatus(StrEnum):
    """Lifecycle states for committed trades."""

    OPEN = "OPEN"
    OPEN_OVERRIDE = "OPEN_OVERRIDE"
    CLOSED = "CLOSED"


class TradeDraft(BaseModel):
    """In-progress trade form data."""

    model_config = ConfigDict(extra="forbid")

    direction: Direction | None = None
    size_usdt: PositiveFloat | None = None
    leverage: int | None = Field(default=None, ge=1, le=125)
    leverage_override_reason: str | None = Field(
        default=None,
        min_length=10,
        max_length=500,
    )
    entry_price: PositiveFloat | None = None
    invalidation_price: PositiveFloat | None = None
    max_loss_usdt: PositiveFloat | None = None
    regime: Regime | None = None
    thesis: str | None = Field(default=None, min_length=10, max_length=280)
    size_reduction_enforced: bool = False

    @model_validator(mode="after")
    def validate_invalidation_side(self) -> TradeDraft:
        """Ensure invalidation sits on the correct side of entry when possible."""

        if (
            self.direction is None
            or self.entry_price is None
            or self.invalidation_price is None
        ):
            return self
        if (
            self.direction == Direction.LONG
            and self.invalidation_price >= self.entry_price
        ):
            msg = "invalidation_price must be less than entry_price for long trades."
            raise ValueError(msg)
        if (
            self.direction == Direction.SHORT
            and self.invalidation_price <= self.entry_price
        ):
            msg = (
                "invalidation_price must be greater than entry_price for short trades."
            )
            raise ValueError(msg)
        return self


class Trade(BaseModel):
    """Persisted trade record."""

    model_config = ConfigDict(extra="forbid")

    id: PositiveInt
    direction: Direction
    size_usdt: PositiveFloat
    leverage: int = Field(ge=1, le=125)
    leverage_override_reason: str | None = Field(
        default=None,
        min_length=10,
        max_length=500,
    )
    entry_price: PositiveFloat
    invalidation_price: PositiveFloat
    max_loss_usdt: PositiveFloat
    regime: Regime
    thesis: str = Field(min_length=10, max_length=280)
    status: TradeStatus
    size_reduction_enforced: bool = False
    opened_at: AwareDatetime
    closed_at: AwareDatetime | None = None
    close_price: PositiveFloat | None = None
    realized_pnl: float | None = None

    @model_validator(mode="after")
    def validate_trade_invariants(self) -> Trade:
        """Enforce cross-field trade invariants."""

        if (
            self.direction == Direction.LONG
            and self.invalidation_price >= self.entry_price
        ):
            msg = "invalidation_price must be less than entry_price for long trades."
            raise ValueError(msg)
        if (
            self.direction == Direction.SHORT
            and self.invalidation_price <= self.entry_price
        ):
            msg = (
                "invalidation_price must be greater than entry_price for short trades."
            )
            raise ValueError(msg)

        if self.status == TradeStatus.CLOSED:
            if (
                self.closed_at is None
                or self.close_price is None
                or self.realized_pnl is None
            ):
                msg = "closed trades require closed_at, close_price, and realized_pnl."
                raise ValueError(msg)
        elif (
            self.closed_at is not None
            or self.close_price is not None
            or self.realized_pnl is not None
        ):
            msg = "open trades cannot define close fields or realized_pnl."
            raise ValueError(msg)

        return self
