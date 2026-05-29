"""Conversational form state machine for `/new`."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

import structlog

from src.bot import formatting
from src.config import Settings
from src.db.repo import RedisRepository
from src.models.conversation import ConversationState, ConversationStep
from src.models.trade import Trade, TradeDraft
from src.rules.context import RuleContext
from src.rules.leverage import LeverageDecision
from src.rules.leverage import check as check_leverage
from src.rules.sizing import compute_size_cap
from src.rules.validation import (
    validate_direction,
    validate_entry_price,
    validate_invalidation_price,
    validate_invalidation_side,
    validate_leverage,
    validate_leverage_override_reason,
    validate_max_loss_usdt,
    validate_regime,
    validate_size_usdt,
    validate_symbol,
    validate_thesis,
)

T = TypeVar("T")


@dataclass(frozen=True)
class FormResult:
    """Result of processing one form action or input."""

    message: str
    created_trade: Trade | None = None


class UniverseFetcher(Protocol):
    """Adapter surface needed by the symbol step."""

    async def fetch_universe(self) -> list[str]:
        """Fetch canonical Hyperliquid perpetual symbols."""


class UniverseUnavailableError(RuntimeError):
    """Raised when no market universe can be used for validation."""


class TradeFormService:
    """Persisted `/new` workflow backed by Redis conversation state."""

    def __init__(
        self,
        *,
        repo: RedisRepository,
        settings: Settings,
        universe_fetcher: UniverseFetcher | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._universe_fetcher = universe_fetcher
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._logger = structlog.get_logger(__name__)

    async def start(self, chat_id: int) -> FormResult:
        """Begin the `/new` flow unless another form is already active."""

        existing_state, _ = await self._load_active_state(chat_id)
        if existing_state is not None:
            return FormResult(message=formatting.form_already_in_progress())

        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.SYMBOL,
            draft=TradeDraft(),
        )
        return FormResult(message=formatting.prompt_symbol())

    async def cancel(self, chat_id: int) -> FormResult:
        """Abort the current form, if any."""

        existing_state = await self._repo.get_conversation_state(chat_id)
        if existing_state is None or existing_state.state == ConversationStep.IDLE:
            return FormResult(message=formatting.no_form_in_progress())
        await self._repo.clear_conversation_state(chat_id)
        return FormResult(message=formatting.form_cancelled())

    async def handle_input(self, chat_id: int, text: str) -> FormResult | None:
        """Apply one free-text reply to the active form."""

        state, expired = await self._load_active_state(chat_id)
        if expired:
            return FormResult(message=formatting.form_expired(self._timeout_seconds))
        if state is None:
            return None

        draft = self._draft_from_state(state)

        if state.state == ConversationStep.SYMBOL:
            return await self._handle_symbol(chat_id, draft, text)
        if state.state == ConversationStep.DIRECTION:
            return await self._handle_direction(chat_id, draft, text)
        if state.state == ConversationStep.SIZE:
            return await self._handle_size(chat_id, draft, text)
        if state.state == ConversationStep.LEVERAGE:
            return await self._handle_leverage(chat_id, draft, text)
        if state.state == ConversationStep.LEV_OVERRIDE:
            return await self._handle_override(chat_id, draft, text)
        if state.state == ConversationStep.ENTRY:
            return await self._handle_entry(chat_id, draft, text)
        if state.state == ConversationStep.INVALIDATION:
            return await self._handle_invalidation(chat_id, draft, text)
        if state.state == ConversationStep.MAX_LOSS:
            return await self._handle_max_loss(chat_id, draft, text)
        if state.state == ConversationStep.REGIME:
            return await self._handle_regime(chat_id, draft, text)
        if state.state == ConversationStep.THESIS:
            return await self._handle_thesis(chat_id, draft, text)

        await self._repo.clear_conversation_state(chat_id)
        return FormResult(message=formatting.form_expired(self._timeout_seconds))

    @property
    def _timeout_seconds(self) -> int:
        return self._settings.form_timeout_seconds

    async def _handle_symbol(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        normalized_input = text.strip().upper()
        try:
            universe = await self._universe_for_validation()
        except UniverseUnavailableError:
            return FormResult(message=formatting.symbol_universe_unavailable())

        try:
            draft.symbol = validate_symbol(text, universe)
        except ValueError:
            self._logger.info("symbol_rejected", symbol=normalized_input)
            return FormResult(message=formatting.symbol_unknown(normalized_input))

        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.DIRECTION,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_direction())

    async def _handle_direction(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            draft.direction = validate_direction(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_direction(),
                )
            )
        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.SIZE,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_size_usdt())

    async def _handle_size(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            size_usdt = validate_size_usdt(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_size_usdt(),
                )
            )

        recent_trades = await self._repo.list_closed_trades(
            symbol=self._require(draft.symbol),
        )
        cap = compute_size_cap(
            RuleContext(trade_draft=draft, recent_trades=recent_trades),
            self._settings.consecutive_loss_threshold,
            self._settings.size_reduction_factor,
        )
        if cap is not None and size_usdt > cap:
            return FormResult(
                message=formatting.validation_error(
                    formatting.size_cap_exceeded(cap),
                    formatting.prompt_size_usdt(),
                )
            )

        draft.size_usdt = size_usdt
        draft.size_reduction_enforced = cap is not None
        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.LEVERAGE,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_leverage())

    async def _handle_leverage(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            draft.leverage = validate_leverage(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_leverage(),
                )
            )

        decision = check_leverage(
            RuleContext(trade_draft=draft, recent_trades=[]),
            self._settings.leverage_block_threshold,
        )
        if decision == LeverageDecision.BLOCK_NEEDS_OVERRIDE:
            await self._persist_state(
                chat_id=chat_id,
                step=ConversationStep.LEV_OVERRIDE,
                draft=draft,
            )
            return FormResult(
                message=formatting.leverage_block_warning(
                    self._settings.leverage_block_threshold
                )
            )

        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.ENTRY,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_entry_price())

    async def _handle_override(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            draft.leverage_override_reason = validate_leverage_override_reason(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.leverage_block_warning(
                        self._settings.leverage_block_threshold
                    ),
                )
            )
        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.ENTRY,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_entry_price())

    async def _handle_entry(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            draft.entry_price = validate_entry_price(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_entry_price(),
                )
            )
        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.INVALIDATION,
            draft=draft,
        )
        return FormResult(
            message=formatting.prompt_invalidation(self._require(draft.direction))
        )

    async def _handle_invalidation(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            invalidation_price = validate_invalidation_price(text)
            validate_invalidation_side(
                direction=self._require(draft.direction),
                entry_price=self._require(draft.entry_price),
                invalidation_price=invalidation_price,
            )
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_invalidation(self._require(draft.direction)),
                )
            )
        draft.invalidation_price = invalidation_price
        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.MAX_LOSS,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_max_loss_usdt())

    async def _handle_max_loss(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            draft.max_loss_usdt = validate_max_loss_usdt(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_max_loss_usdt(),
                )
            )
        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.REGIME,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_regime())

    async def _handle_regime(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            draft.regime = validate_regime(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_regime(),
                )
            )
        await self._persist_state(
            chat_id=chat_id,
            step=ConversationStep.THESIS,
            draft=draft,
        )
        return FormResult(message=formatting.prompt_thesis())

    async def _handle_thesis(
        self,
        chat_id: int,
        draft: TradeDraft,
        text: str,
    ) -> FormResult:
        try:
            draft.thesis = validate_thesis(text)
        except ValueError as exc:
            return FormResult(
                message=formatting.validation_error(
                    str(exc),
                    formatting.prompt_thesis(),
                )
            )

        trade = await self._repo.create_trade(
            draft,
            opened_at=self._now(),
        )
        await self._repo.clear_conversation_state(chat_id)
        open_trades = await self._repo.list_open_trades()
        return FormResult(
            message=formatting.trade_committed(
                trade,
                warn_multiple_open=len(open_trades) > 1,
            ),
            created_trade=trade,
        )

    async def _load_active_state(
        self,
        chat_id: int,
    ) -> tuple[ConversationState | None, bool]:
        state = await self._repo.get_conversation_state(chat_id)
        if state is None or state.state == ConversationStep.IDLE:
            return None, False
        age_seconds = (self._now() - state.updated_at).total_seconds()
        if age_seconds <= self._timeout_seconds:
            return state, False

        await self._repo.clear_conversation_state(chat_id)
        self._logger.info(
            "form_abandoned",
            chat_id=chat_id,
            age_seconds=age_seconds,
        )
        return None, True

    async def _universe_for_validation(self) -> set[str]:
        cached = await self._repo.get_universe()
        if cached is not None:
            cached_symbols, fetched_at = cached
            if self._is_universe_fresh(fetched_at):
                return cached_symbols

        try:
            refreshed_symbols = await self._refresh_universe()
        except Exception as exc:
            if cached is not None:
                self._logger.warning("universe_refresh_failed", error=str(exc))
                return cached[0]
            self._logger.warning("universe_refresh_failed", error=str(exc))
            raise UniverseUnavailableError from exc
        return set(refreshed_symbols)

    async def _refresh_universe(self) -> list[str]:
        if self._universe_fetcher is None:
            msg = "universe fetcher is not configured."
            raise UniverseUnavailableError(msg)
        symbols = await self._universe_fetcher.fetch_universe()
        fetched_at = self._now()
        await self._repo.set_universe(symbols, fetched_at)
        self._logger.info(
            "universe_refreshed",
            symbol_count=len(symbols),
            fetched_at=fetched_at.isoformat(),
        )
        return symbols

    def _is_universe_fresh(self, fetched_at: datetime) -> bool:
        reference = fetched_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        age_seconds = (self._now() - reference).total_seconds()
        return age_seconds <= self._settings.hyperliquid_universe_stale_seconds

    async def _persist_state(
        self,
        *,
        chat_id: int,
        step: ConversationStep,
        draft: TradeDraft,
    ) -> None:
        state = ConversationState(
            chat_id=chat_id,
            state=step,
            partial_trade_json=json.dumps(
                draft.model_dump(mode="json", exclude_none=True)
            ),
            updated_at=self._now(),
        )
        await self._repo.set_conversation_state(
            state,
            ttl_seconds=self._timeout_seconds,
        )

    @staticmethod
    def _draft_from_state(state: ConversationState) -> TradeDraft:
        return TradeDraft.model_validate_json(state.partial_trade_json)

    @staticmethod
    def _require(value: T | None) -> T:
        if value is None:
            msg = "Form state is incomplete."
            raise ValueError(msg)
        return value
