"""Telegram command handlers and free-text form routing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any, cast

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.bot import formatting
from src.bot.edit_closed import ClosedTradeEditService
from src.bot.forms import TradeFormService
from src.bot.whitelist import whitelisted
from src.config import Settings
from src.db.repo import RedisRepository
from src.events.bus import EventBus
from src.models.breach import Breach
from src.models.conversation import ConversationStep
from src.models.events import (
    BreachResolution,
    BreachResolvedEvent,
    BreachResolvedPayload,
    TradeClosedEvent,
    TradeClosedPayload,
    TradeOpenedEvent,
    TradeOpenedPayload,
)
from src.models.trade import Trade, TradeDraft
from src.monitor.alerts import AlertDispatcher
from src.monitor.health import ApplicationHealthSnapshot, MonitorHealth
from src.rules.context import RuleContext
from src.rules.sizing import compute_size_cap
from src.rules.sizing import consecutive_loss_count as count_consecutive_losses
from src.rules.validation import validate_justification

StatsProvider = Callable[[int], Awaitable[str]]

LOGGER = structlog.get_logger(__name__)


def build_health_payload(
    snapshot: ApplicationHealthSnapshot,
    *,
    universe_fetched_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, object | None]:
    """Expose structured health fields for the `/health` formatter."""

    reference_now = now or datetime.now(tz=UTC)
    universe_cache_age = None
    if universe_fetched_at is not None:
        fetched_at = universe_fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        universe_cache_age = max(0.0, (reference_now - fetched_at).total_seconds())

    return {
        "websocket_status": snapshot.websocket_status,
        "last_tick_age_seconds": snapshot.last_tick_age_seconds,
        "last_frame_age_s": snapshot.last_tick_age_seconds,
        "universe_cache_age_s": universe_cache_age,
        "last_hyperliquid_error": snapshot.last_error,
        "open_trade_count": snapshot.open_trade_count,
        "last_error": snapshot.last_error,
        "redis_connected": snapshot.redis.connected,
        "redis_appendonly_enabled": snapshot.redis.appendonly_enabled,
        "redis_persistence_dir": snapshot.redis.persistence_dir,
        "redis_persistence_dir_writable": snapshot.redis.persistence_dir_writable,
        "redis_aof_last_write_status": snapshot.redis.aof_last_write_status,
        "redis_last_error": snapshot.redis.last_error,
    }


def safe_handler(
    func: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Reply with the standard internal-error message on unexpected failures."""

    @wraps(func)
    async def wrapper(*args: object, **kwargs: object) -> None:
        update = _extract_update(args, kwargs)
        try:
            await func(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - exercised in integration
            LOGGER.exception(
                "telegram_handler_failed",
                handler=func.__name__,
                error=str(exc),
            )
            if update is not None:
                await _reply(update, formatting.internal_error())

    return wrapper


class TelegramHandlers:
    """Async Telegram command handlers for the single-user bot."""

    def __init__(
        self,
        *,
        settings: Settings,
        repo: RedisRepository,
        forms: TradeFormService,
        edit_closed: ClosedTradeEditService,
        alerts: AlertDispatcher,
        health: MonitorHealth,
        event_bus: EventBus,
        stats_provider: StatsProvider,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._forms = forms
        self._edit_closed = edit_closed
        self._alerts = alerts
        self._health = health
        self._event_bus = event_bus
        self._stats_provider = stats_provider
        self._now = now_fn or (lambda: datetime.now(tz=UTC))

    @whitelisted
    @safe_handler
    async def new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        result = await self._forms.start(chat_id)
        await _reply(update, result.message)

    @whitelisted
    @safe_handler
    async def text_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if message is None or not message.text:
            return

        chat_id = _chat_id(update)
        state = await self._repo.get_conversation_state(chat_id)
        if state is not None and state.state == ConversationStep.EDIT_CLOSED_CONFIRM:
            edit_result = await self._edit_closed.resolve(chat_id, message.text)
            if edit_result is not None:
                await _reply(update, edit_result.message)
                return

        result = await self._forms.handle_input(chat_id, message.text)
        if result is None:
            return

        await _reply(update, result.message)
        if result.created_trade is not None:
            await self._publish_trade_opened(result.created_trade)

    @whitelisted
    @safe_handler
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        result = await self._forms.cancel(_chat_id(update))
        await _reply(update, result.message)

    @whitelisted
    @safe_handler
    async def open(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trades = await self._repo.list_open_trades()
        if not trades:
            await _reply(update, formatting.no_open_trades())
            return
        active_breach_trade_ids: set[int] = set()
        for trade in trades:
            if await self._repo.get_open_breach(trade.id) is not None:
                active_breach_trade_ids.add(trade.id)
        await _reply(
            update,
            formatting.open_trades(
                trades,
                active_breach_trade_ids=active_breach_trade_ids,
            ),
        )

    @whitelisted
    @safe_handler
    async def closed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = list(getattr(context, "args", []))
        parsed = self._parse_closed_args(args)
        if parsed is None:
            await _reply(update, formatting.closed_usage())
            return

        trade_id, close_price = await self._resolve_trade_for_close(*parsed)
        if trade_id is None:
            await _reply(update, formatting.no_open_trade())
            return

        active_breach = await self._repo.get_open_breach(trade_id)
        closed_trade = await self._repo.close_trade(
            trade_id,
            close_price=close_price,
            closed_at=self._now(),
            breach_id=active_breach.id if active_breach is not None else None,
            response_at=self._now(),
        )
        if closed_trade is None:
            await _reply(update, formatting.trade_not_found_or_closed(trade_id))
            return

        if active_breach is not None:
            await self._alerts.resolve_breach(active_breach.id)
            await self._publish_breach_resolved(
                active_breach,
                BreachResolution.CLOSED,
            )

        streak = await self._repo.consecutive_loss_count()
        await self._publish_trade_closed(closed_trade)
        await _reply(update, formatting.close_confirmation(closed_trade, streak))

    @whitelisted
    @safe_handler
    async def justify(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        args = list(getattr(context, "args", []))
        if not args:
            await _reply(update, formatting.justify_usage())
            return

        trade_id, raw_reason = await self._resolve_trade_and_reason_for_justify(args)
        if raw_reason is None:
            await _reply(update, formatting.justify_usage())
            return
        if trade_id is None:
            await _reply(update, formatting.no_active_breach())
            return

        try:
            reason = validate_justification(raw_reason)
        except ValueError as exc:
            await _reply(
                update,
                formatting.validation_error(str(exc), formatting.justify_usage()),
            )
            return

        breach = await self._repo.get_open_breach(trade_id)
        if breach is None:
            await _reply(update, formatting.no_active_breach())
            return

        trade = await self._repo.mark_override(
            trade_id,
            breach_id=breach.id,
            justification=reason,
            response_at=self._now(),
        )
        if trade is None:
            await _reply(update, formatting.no_active_breach())
            return

        await self._alerts.resolve_breach(breach.id)
        await self._publish_breach_resolved(
            breach,
            BreachResolution.JUSTIFIED,
        )
        await _reply(update, formatting.justification_recorded(trade))

    @whitelisted
    @safe_handler
    async def streak(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        closed_trades = await self._repo.list_closed_trades()
        if not closed_trades:
            await _reply(update, formatting.no_symbol_streaks())
            return

        grouped: dict[str, list[Trade]] = {}
        for trade in closed_trades:
            grouped.setdefault(trade.symbol, []).append(trade)

        rows: list[tuple[str, int, float | None]] = []
        for symbol in sorted(grouped):
            symbol_trades = grouped[symbol]
            active_cap = compute_size_cap(
                RuleContext(
                    trade_draft=TradeDraft(symbol=symbol),
                    recent_trades=symbol_trades,
                ),
                self._settings.consecutive_loss_threshold,
                self._settings.size_reduction_factor,
            )
            rows.append(
                (
                    symbol,
                    count_consecutive_losses(symbol_trades),
                    active_cap,
                )
            )
        await _reply(update, formatting.streaks_by_symbol(rows))

    @whitelisted
    @safe_handler
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = list(getattr(context, "args", []))
        days = self._parse_stats_days(args)
        if days is None:
            await _reply(update, formatting.stats_usage())
            return
        message = await self._stats_provider(days)
        await _reply(update, message)

    @whitelisted
    @safe_handler
    async def setpnl(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        args = list(getattr(context, "args", []))
        parsed = self._parse_setpnl_args(args)
        if parsed is None:
            await _reply(update, formatting.setpnl_usage())
            return

        trade_id, pnl = parsed
        trade = await self._repo.update_trade_realized_pnl(trade_id, realized_pnl=pnl)
        if trade is None:
            await _reply(update, formatting.trade_not_found_or_closed(trade_id))
            return
        streak = await self._repo.consecutive_loss_count()
        await _reply(update, formatting.setpnl_confirmation(trade, pnl, streak))

    @whitelisted
    @safe_handler
    async def edit(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        args = list(getattr(context, "args", []))
        if len(args) < 2:
            await _reply(update, formatting.edit_usage())
            return

        # Extract trade_id from first argument
        try:
            trade_id = int(args[0])
        except ValueError:
            await _reply(update, formatting.edit_usage())
            return

        # Parse field=value pairs from remaining arguments
        updates: dict[str, str] = {}
        for arg in args[1:]:
            if "=" in arg:
                field, value = arg.split("=", 1)
                updates[field] = value

        if not updates:
            await _reply(update, formatting.edit_usage())
            return

        trade = await self._repo.update_trade(trade_id, updates)
        await _reply(update, formatting.edit_confirmation(trade, list(updates.keys())))

    @whitelisted
    @safe_handler
    async def edit_closed(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        args = list(getattr(context, "args", []))
        parsed = self._parse_edit_closed_args(args)
        if parsed is None:
            await _reply(update, formatting.edit_closed_usage())
            return

        trade_id, updates = parsed
        result = await self._edit_closed.prepare(_chat_id(update), trade_id, updates)
        await _reply(update, result.message)

    @whitelisted
    @safe_handler
    async def health(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        redis_health = await self._repo.get_redis_health()
        snapshot = await self._health.build_snapshot(redis=redis_health)
        universe = None
        get_universe = getattr(self._repo, "get_universe", None)
        if get_universe is not None:
            universe = await get_universe()
        payload = build_health_payload(
            snapshot,
            universe_fetched_at=universe[1] if universe is not None else None,
            now=self._now(),
        )
        await _reply(update, formatting.health_status(payload))

    @whitelisted
    @safe_handler
    async def signals(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await _reply(update, formatting.signals_stub())

    @whitelisted
    @safe_handler
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = list(getattr(context, "args", []))
        if not args:
            await _reply(update, formatting.help_overview())
            return
        await _reply(update, formatting.help_for(args[0]))

    @whitelisted
    @safe_handler
    async def unknown(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await _reply(update, formatting.unknown_command())

    async def _resolve_trade_for_close(
        self,
        requested_trade_id: int | None,
        close_price: float,
    ) -> tuple[int | None, float]:
        if requested_trade_id is not None:
            return requested_trade_id, close_price
        trade = await self._most_recent_open_trade()
        if trade is None:
            return None, close_price
        return trade.id, close_price

    async def _resolve_trade_and_reason_for_justify(
        self,
        args: list[str],
    ) -> tuple[int | None, str | None]:
        trade_id: int | None = None
        reason_start = 0
        try:
            if len(args) >= 2:
                trade_id = int(args[0])
                reason_start = 1
            else:
                trade_id = None
                reason_start = 0
        except ValueError:
            trade_id = None

        if trade_id is None:
            trade = await self._most_recent_open_trade_with_breach()
            if trade is None:
                return None, " ".join(args).strip()
            trade_id = trade.id
            reason_start = 0

        reason = " ".join(args[reason_start:]).strip()
        if not reason:
            return None, None
        return trade_id, reason

    async def _most_recent_open_trade(self) -> Trade | None:
        trades = await self._repo.list_open_trades()
        if not trades:
            return None
        return trades[-1]

    async def _most_recent_open_trade_with_breach(self) -> Trade | None:
        trades = await self._repo.list_open_trades()
        for trade in reversed(trades):
            breach = await self._repo.get_open_breach(trade.id)
            if breach is not None:
                return trade
        return None

    @staticmethod
    def _parse_closed_args(args: list[str]) -> tuple[int | None, float] | None:
        if len(args) == 1:
            try:
                close_price = float(args[0])
            except ValueError:
                return None
            if close_price <= 0:
                return None
            return None, close_price
        if len(args) == 2:
            try:
                trade_id = int(args[0])
                close_price = float(args[1])
            except ValueError:
                return None
            if trade_id <= 0 or close_price <= 0:
                return None
            return trade_id, close_price
        return None

    @staticmethod
    def _parse_setpnl_args(args: list[str]) -> tuple[int, float] | None:
        if len(args) != 2:
            return None
        try:
            return int(args[0]), float(args[1])
        except ValueError:
            return None

    @staticmethod
    def _parse_stats_days(args: list[str]) -> int | None:
        if not args:
            return 30
        if len(args) != 1:
            return None
        try:
            days = int(args[0])
        except ValueError:
            return None
        if days <= 0:
            return None
        return days

    @staticmethod
    def _parse_edit_closed_args(
        args: list[str],
    ) -> tuple[int, dict[str, str]] | None:
        if len(args) < 2:
            return None
        try:
            trade_id = int(args[0])
        except ValueError:
            return None
        if trade_id <= 0:
            return None

        updates: dict[str, str] = {}
        for arg in args[1:]:
            if "=" not in arg:
                return None
            field, value = arg.split("=", 1)
            if not field:
                return None
            updates[field] = value
        if not updates:
            return None
        return trade_id, updates

    async def _publish_trade_opened(self, trade: Trade) -> None:
        await self._event_bus.publish(
            TradeOpenedEvent(
                ts=trade.opened_at,
                payload=TradeOpenedPayload(
                    trade_id=trade.id,
                    snapshot=trade,
                ),
            )
        )

    async def _publish_trade_closed(self, trade: Trade) -> None:
        closed_at = trade.closed_at or self._now()
        realized_pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
        await self._event_bus.publish(
            TradeClosedEvent(
                ts=closed_at,
                payload=TradeClosedPayload(
                    trade_id=trade.id,
                    realized_pnl=realized_pnl,
                ),
            )
        )

    async def _publish_breach_resolved(
        self,
        breach: Breach,
        resolution: BreachResolution,
    ) -> None:
        await self._event_bus.publish(
            BreachResolvedEvent(
                ts=self._now(),
                payload=BreachResolvedPayload(
                    breach_id=breach.id,
                    resolution=resolution,
                ),
            )
        )


async def _reply(update: object, message: str) -> None:
    effective_message = getattr(update, "effective_message", None)
    if effective_message is None:
        return
    reply_text = getattr(effective_message, "reply_text", None)
    if callable(reply_text):
        await cast(Awaitable[Any], reply_text(message))


def _chat_id(update: Update) -> int:
    chat = update.effective_chat
    if chat is None:
        msg = "Update has no effective chat."
        raise ValueError(msg)
    return chat.id


def _extract_update(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object | None:
    if "update" in kwargs:
        return kwargs["update"]
    for candidate in args[:2]:
        if hasattr(candidate, "effective_chat"):
            return candidate
    return None
