"""Service for previewing and applying `/edit_closed` corrections."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from pydantic import ValidationError

from src.bot import formatting
from src.config import Settings
from src.db.repo import RedisRepository
from src.models.conversation import ConversationState, ConversationStep
from src.models.trade import Direction, Trade, TradeStatus
from src.rules.impact import discipline_impact
from src.rules.validation import (
    validate_direction,
    validate_entry_price,
    validate_invalidation_price,
    validate_leverage,
    validate_leverage_override_reason,
    validate_max_loss_usdt,
    validate_regime,
    validate_size_usdt,
    validate_thesis,
)

EDIT_CLOSED_EDITABLE_FIELDS = frozenset(formatting.EDIT_CLOSED_FIELDS)
PNL_FIELDS = frozenset({"direction", "size_usdt", "entry_price", "close_price"})
HIGH_LEVERAGE_THRESHOLD = 20


@dataclass(frozen=True)
class EditPrepareResult:
    """Result of preparing a closed-trade edit."""

    message: str


@dataclass(frozen=True)
class EditResolveResult:
    """Result of resolving a pending closed-trade edit."""

    message: str
    updated_trade: Trade | None = None


class ClosedTradeEditService:
    """Validate, preview, and confirm edits to already-closed trades."""

    def __init__(
        self,
        *,
        repo: RedisRepository,
        settings: Settings,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._now = now_fn or (lambda: datetime.now(tz=UTC))

    async def prepare(
        self,
        chat_id: int,
        trade_id: int,
        raw_updates: dict[str, str],
    ) -> EditPrepareResult:
        """Build and persist a pending closed-trade edit preview."""

        existing_state, _ = await self._load_active_state(chat_id)
        if existing_state is not None:
            return EditPrepareResult(message=formatting.form_already_in_progress())

        current = await self._repo.get_trade(trade_id)
        if current is None:
            return EditPrepareResult(message=formatting.edit_closed_not_found(trade_id))
        if current.status != TradeStatus.CLOSED:
            return EditPrepareResult(
                message=formatting.edit_closed_not_closed(trade_id)
            )

        invalid_field = self._first_invalid_field(raw_updates)
        if invalid_field is not None:
            return EditPrepareResult(
                message=formatting.edit_closed_invalid_field(invalid_field)
            )

        try:
            updates = self._coerce_updates(raw_updates)
            preview = self._build_preview_trade(current, updates)
            self._validate_closed_timestamps(preview)
            self._validate_high_leverage_reason(preview)
        except FieldValidationError as exc:
            return EditPrepareResult(
                message=formatting.edit_closed_validation_error(exc.field, exc.message)
            )

        recomputed_pnl: float | None = None
        pnl_override_warning = False
        if PNL_FIELDS.intersection(updates):
            recomputed_pnl = self._calculate_realized_pnl(preview)
            pnl_override_warning = self._has_manual_pnl_override(current)
            preview = self._replace_realized_pnl(preview, recomputed_pnl)

        changes = self._changed_editable_fields(current, preview)
        impact = discipline_impact(
            await self._repo.list_closed_trades(),
            preview,
            threshold=self._settings.consecutive_loss_threshold,
            factor=self._settings.size_reduction_factor,
        )
        await self._persist_pending_edit(
            chat_id=chat_id,
            trade_id=trade_id,
            updates=updates,
            recomputed_pnl=recomputed_pnl,
        )

        return EditPrepareResult(
            message=formatting.edit_closed_preview(
                changes,
                recomputed_pnl,
                impact,
                pnl_override_warning,
            )
        )

    async def resolve(
        self,
        chat_id: int,
        text: str,
    ) -> EditResolveResult | None:
        """Apply or discard a pending closed-trade edit confirmation."""

        state, expired = await self._load_active_state(chat_id)
        if (
            expired
            or state is None
            or state.state != ConversationStep.EDIT_CLOSED_CONFIRM
        ):
            return None

        payload = self._pending_payload(state)
        normalized = text.strip().lower()
        if normalized != "yes":
            await self._repo.clear_conversation_state(chat_id)
            return EditResolveResult(message=formatting.edit_closed_cancelled())

        trade_id = self._payload_trade_id(payload)
        updates = self._payload_updates(payload)
        recomputed_pnl = self._payload_recomputed_pnl(payload)
        updated_trade = await self._repo.update_closed_trade(
            trade_id,
            updates=updates,
            recomputed_pnl=recomputed_pnl,
        )
        await self._repo.clear_conversation_state(chat_id)
        if updated_trade is None:
            return EditResolveResult(
                message=formatting.edit_closed_not_closed(trade_id)
            )
        return EditResolveResult(
            message=formatting.edit_closed_applied(
                updated_trade,
                self._changed_field_names(updates),
            ),
            updated_trade=updated_trade,
        )

    def _preview_trade(self, current: Trade, updates: Mapping[str, object]) -> Trade:
        """Coerce requested values, merge them, and validate the preview trade."""

        preview = self._build_preview_trade(current, self._coerce_updates(updates))
        self._validate_closed_timestamps(preview)
        self._validate_high_leverage_reason(preview)
        return preview

    async def _load_active_state(
        self,
        chat_id: int,
    ) -> tuple[ConversationState | None, bool]:
        state = await self._repo.get_conversation_state(chat_id)
        if state is None or state.state == ConversationStep.IDLE:
            return None, False

        age_seconds = (self._now() - state.updated_at).total_seconds()
        if age_seconds <= self._settings.form_timeout_seconds:
            return state, False

        await self._repo.clear_conversation_state(chat_id)
        return None, True

    async def _persist_pending_edit(
        self,
        *,
        chat_id: int,
        trade_id: int,
        updates: dict[str, object],
        recomputed_pnl: float | None,
    ) -> None:
        state = ConversationState(
            chat_id=chat_id,
            state=ConversationStep.EDIT_CLOSED_CONFIRM,
            partial_trade_json=json.dumps(
                {
                    "trade_id": trade_id,
                    "updates": {
                        field: self._jsonable_value(value)
                        for field, value in updates.items()
                    },
                    "recomputed_pnl": recomputed_pnl,
                }
            ),
            updated_at=self._now(),
        )
        await self._repo.set_conversation_state(
            state,
            ttl_seconds=self._settings.form_timeout_seconds,
        )

    def _coerce_updates(self, raw_updates: Mapping[str, object]) -> dict[str, object]:
        updates: dict[str, object] = {}
        for field, value in raw_updates.items():
            try:
                updates[field] = self._coerce_field(field, value)
            except ValueError as exc:
                raise FieldValidationError(field, str(exc)) from exc

        leverage = updates.get("leverage")
        if isinstance(leverage, int) and leverage < HIGH_LEVERAGE_THRESHOLD:
            updates["leverage_override_reason"] = None
        return updates

    def _coerce_field(self, field: str, value: object) -> object:
        if field == "direction":
            return validate_direction(value)
        if field == "size_usdt":
            return validate_size_usdt(value)
        if field == "leverage":
            return validate_leverage(value)
        if field == "leverage_override_reason":
            return self._coerce_leverage_override_reason(value)
        if field == "entry_price":
            return validate_entry_price(value)
        if field == "invalidation_price":
            return validate_invalidation_price(value)
        if field == "max_loss_usdt":
            return validate_max_loss_usdt(value)
        if field == "regime":
            return validate_regime(value)
        if field == "thesis":
            return validate_thesis(value)
        if field in {"opened_at", "closed_at"}:
            return self._parse_timestamp(field, value)
        if field == "close_price":
            return self._validate_close_price(value)
        msg = f"Field '{field}' cannot be edited."
        raise ValueError(msg)

    @staticmethod
    def _coerce_leverage_override_reason(value: object) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        if value is None:
            return None
        return validate_leverage_override_reason(value)

    @staticmethod
    def _validate_close_price(value: object) -> float:
        try:
            return validate_entry_price(value)
        except ValueError as exc:
            msg = "close_price must be greater than 0. Example: 82500."
            raise ValueError(msg) from exc

    @staticmethod
    def _parse_timestamp(field: str, value: object) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            raw_value = value.strip()
            if not raw_value:
                raise ValueError(_timestamp_error(field))
            try:
                parsed = datetime.fromisoformat(
                    raw_value.removesuffix("Z")
                    + ("+00:00" if raw_value.endswith("Z") else "")
                )
            except ValueError as exc:
                raise ValueError(_timestamp_error(field)) from exc
        else:
            raise ValueError(_timestamp_error(field))

        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(_timestamp_error(field))
        return parsed

    def _build_preview_trade(
        self,
        current: Trade,
        updates: dict[str, object],
    ) -> Trade:
        payload = current.model_dump()
        payload.update(updates)
        try:
            return Trade.model_validate(payload)
        except ValidationError as exc:
            field, message = self._first_pydantic_error(exc)
            raise FieldValidationError(field, message) from exc

    @staticmethod
    def _validate_closed_timestamps(trade: Trade) -> None:
        if trade.closed_at is not None and trade.closed_at < trade.opened_at:
            raise FieldValidationError(
                "closed_at",
                "closed_at must be greater than or equal to opened_at.",
            )

    @staticmethod
    def _validate_high_leverage_reason(trade: Trade) -> None:
        if (
            trade.leverage >= HIGH_LEVERAGE_THRESHOLD
            and trade.leverage_override_reason is None
        ):
            raise FieldValidationError(
                "leverage_override_reason",
                "leverage_override_reason is required when leverage >= 20. "
                "Must be 10-500 characters.",
            )

    @staticmethod
    def _replace_realized_pnl(trade: Trade, realized_pnl: float) -> Trade:
        payload = trade.model_dump()
        payload["realized_pnl"] = realized_pnl
        return Trade.model_validate(payload)

    @staticmethod
    def _calculate_realized_pnl(trade: Trade) -> float:
        close_price = trade.close_price
        if close_price is None:
            msg = "close_price must be present for closed trades."
            raise ValueError(msg)
        size_btc = trade.size_usdt / trade.entry_price
        direction_sign = 1.0 if trade.direction == Direction.LONG else -1.0
        return (close_price - trade.entry_price) * size_btc * direction_sign

    def _has_manual_pnl_override(self, trade: Trade) -> bool:
        if trade.realized_pnl is None:
            return False
        formula_pnl = self._calculate_realized_pnl(trade)
        return not math.isclose(trade.realized_pnl, formula_pnl, abs_tol=1e-9)

    @staticmethod
    def _changed_editable_fields(
        current: Trade,
        preview: Trade,
    ) -> dict[str, tuple[object, object]]:
        changes: dict[str, tuple[object, object]] = {}
        for field in formatting.EDIT_CLOSED_FIELDS:
            old_value = getattr(current, field)
            new_value = getattr(preview, field)
            if old_value != new_value:
                changes[field] = (old_value, new_value)
        return changes

    @staticmethod
    def _changed_field_names(updates: dict[str, object]) -> list[str]:
        return [field for field in formatting.EDIT_CLOSED_FIELDS if field in updates]

    @staticmethod
    def _first_invalid_field(raw_updates: dict[str, str]) -> str | None:
        for field in raw_updates:
            if field not in EDIT_CLOSED_EDITABLE_FIELDS:
                return field
        return None

    @staticmethod
    def _first_pydantic_error(exc: ValidationError) -> tuple[str, str]:
        error = exc.errors()[0]
        loc = error.get("loc", ())
        field = str(loc[0]) if loc else "trade"
        message = str(error.get("msg", "Invalid trade."))
        return field, message.removeprefix("Value error, ")

    @staticmethod
    def _pending_payload(state: ConversationState) -> dict[str, object]:
        payload = json.loads(state.partial_trade_json)
        if not isinstance(payload, dict):
            msg = "Pending edit payload is invalid."
            raise ValueError(msg)
        return dict(payload)

    @staticmethod
    def _payload_trade_id(payload: dict[str, object]) -> int:
        value = payload.get("trade_id")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value)
        msg = "Pending edit trade_id is invalid."
        raise ValueError(msg)

    @staticmethod
    def _payload_updates(payload: dict[str, object]) -> dict[str, object]:
        raw_updates = payload.get("updates")
        if not isinstance(raw_updates, dict):
            msg = "Pending edit updates are invalid."
            raise ValueError(msg)
        return {str(field): value for field, value in raw_updates.items()}

    @staticmethod
    def _payload_recomputed_pnl(payload: dict[str, object]) -> float | None:
        value = payload.get("recomputed_pnl")
        if value is None:
            return None
        if isinstance(value, (float, int, str)):
            return float(value)
        msg = "Pending edit recomputed_pnl is invalid."
        raise ValueError(msg)

    @staticmethod
    def _jsonable_value(value: object) -> object:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        return value


@dataclass(frozen=True)
class FieldValidationError(Exception):
    """Field-specific validation failure for user-facing replies."""

    field: str
    message: str


def _timestamp_error(field: str) -> str:
    return (
        f"{field} must be a valid timezone-aware timestamp. "
        "Example: 2026-05-17T12:00:00+00:00."
    )
