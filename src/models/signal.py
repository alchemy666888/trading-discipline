"""Signal models reserved for the v2 intelligence layer."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    model_validator,
)


class Severity(StrEnum):
    """Reserved signal severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Signal(BaseModel):
    """Reserved v2 intelligence signal record."""

    model_config = ConfigDict(extra="forbid")

    id: PositiveInt
    source: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    severity: Severity
    detected_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    payload_json: str = "{}"
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_signal_fields(self) -> Signal:
        """Validate temporal ordering and payload encoding."""

        if self.expires_at is not None and self.expires_at <= self.detected_at:
            msg = "expires_at must be later than detected_at."
            raise ValueError(msg)
        try:
            json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            msg = "payload_json must contain valid JSON."
            raise ValueError(msg) from exc
        return self
