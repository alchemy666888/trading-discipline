"""Breach models and enums."""

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


class BreachUserResponse(StrEnum):
    """Allowed responses for a breach record."""

    CLOSED = "closed"
    JUSTIFIED = "justified"
    NO_RESPONSE = "no_response"


class Breach(BaseModel):
    """Persisted invalidation-breach record."""

    model_config = ConfigDict(extra="forbid")

    id: PositiveInt
    trade_id: PositiveInt
    detected_at: AwareDatetime
    breach_price: PositiveFloat
    user_response: BreachUserResponse | None = None
    response_at: AwareDatetime | None = None
    justification: str | None = Field(default=None, min_length=5, max_length=500)

    @model_validator(mode="after")
    def validate_response_fields(self) -> Breach:
        """Keep response metadata internally consistent."""

        if self.user_response is None and self.response_at is not None:
            msg = "response_at cannot be set when user_response is absent."
            raise ValueError(msg)
        if (
            self.justification is not None
            and self.user_response != BreachUserResponse.JUSTIFIED
        ):
            msg = "justification is only valid when user_response is 'justified'."
            raise ValueError(msg)
        if (
            self.user_response
            in {
                BreachUserResponse.CLOSED,
                BreachUserResponse.JUSTIFIED,
                BreachUserResponse.NO_RESPONSE,
            }
            and self.response_at is None
        ):
            msg = "resolved breaches require response_at."
            raise ValueError(msg)
        return self
