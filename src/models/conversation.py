"""Conversation-state models."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator


class ConversationStep(StrEnum):
    """Conversation states for the `/new` flow."""

    IDLE = "IDLE"
    DIRECTION = "DIRECTION"
    SIZE = "SIZE"
    LEVERAGE = "LEVERAGE"
    LEV_OVERRIDE = "LEV_OVERRIDE"
    ENTRY = "ENTRY"
    INVALIDATION = "INVALIDATION"
    MAX_LOSS = "MAX_LOSS"
    REGIME = "REGIME"
    THESIS = "THESIS"
    CONFIRM = "CONFIRM"


class ConversationState(BaseModel):
    """Persisted per-chat form state."""

    model_config = ConfigDict(extra="forbid")

    chat_id: int
    state: ConversationStep
    partial_trade_json: str = "{}"
    updated_at: AwareDatetime

    @field_validator("partial_trade_json")
    @classmethod
    def validate_partial_trade_json(cls, value: str) -> str:
        """Ensure the stored form blob is valid JSON."""

        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            msg = "partial_trade_json must contain valid JSON."
            raise ValueError(msg) from exc
        return value
