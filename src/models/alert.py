"""Alert models."""

from __future__ import annotations

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
)


class Alert(BaseModel):
    """Persisted alert record linked to a breach."""

    model_config = ConfigDict(extra="forbid")

    id: PositiveInt
    breach_id: PositiveInt
    sent_at: AwareDatetime
    escalation_level: NonNegativeInt
    message: str = Field(min_length=1, max_length=4096)
