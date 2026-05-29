"""Integration tests for Telegram command handlers with a mocked client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.bot.edit_closed import ClosedTradeEditService
from src.bot.forms import TradeFormService
from src.bot.handlers import TelegramHandlers
from src.config import Settings
from src.db.repo import RedisHealthDetails
from src.events.bus import EventBus
from src.models.breach import Breach, BreachUserResponse
from src.models.conversation import ConversationState, ConversationStep
from src.models.events import Event, EventType
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus
from src.monitor.health import ApplicationHealthSnapshot


@dataclass
class MutableClock:
    """Deterministic clock for handler tests."""

    current: datetime

    def now(self) -> datetime:
        return self.current


class FakeMessage:
    """Minimal Telegram message fake."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    """Minimal Telegram update fake."""

    def __init__(self, chat_id: int, *, text: str = "") -> None:
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage(text=text)


class FakeContext:
    """Minimal Telegram context fake."""

    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []


class RecordingAlerts:
    """Capture resolved breach IDs from the handler layer."""

    def __init__(self) -> None:
        self.resolved_breach_ids: list[int] = []

    async def resolve_breach(self, breach_id: int) -> None:
        self.resolved_breach_ids.append(breach_id)


class FakeHealth:
    """Return a stable `/health` snapshot."""

    async def build_snapshot(
        self,
        *,
        redis: RedisHealthDetails,
    ) -> ApplicationHealthSnapshot:
        return ApplicationHealthSnapshot(
            websocket_status="connected",
            last_tick_age_seconds=1.5,
            open_trade_count=2,
            last_error=None,
            redis=redis,
        )


class InMemoryHandlerRepo:
    """Minimal repo surface for command-handler tests."""

    def __init__(self) -> None:
        self.conversations: dict[int, ConversationState] = {}
        self.trades: dict[int, Trade] = {}
        self.breaches: dict[int, Breach] = {}
        self.active_breaches: dict[int, int] = {}
        self.universe: tuple[set[str], datetime] | None = (
            {"BTC", "ETH", "HYPE", "AUDUSD"},
            datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        )
        self._next_trade_id = 1
        self._next_breach_id = 1

    async def get_conversation_state(self, chat_id: int) -> ConversationState | None:
        return self.conversations.get(chat_id)

    async def set_conversation_state(
        self,
        state: ConversationState,
        *,
        ttl_seconds: int | None = None,
    ) -> ConversationState:
        self.conversations[state.chat_id] = state
        return state

    async def clear_conversation_state(self, chat_id: int) -> None:
        self.conversations.pop(chat_id, None)

    async def get_universe(self) -> tuple[set[str], datetime] | None:
        return self.universe

    async def set_universe(self, symbols: list[str], fetched_at: datetime) -> None:
        self.universe = (set(symbols), fetched_at)

    async def create_trade(
        self,
        draft: TradeDraft,
        *,
        opened_at: datetime,
        status: TradeStatus = TradeStatus.OPEN,
        size_reduction_enforced: bool | None = None,
    ) -> Trade:
        trade = Trade(
            id=self._next_trade_id,
            symbol=draft.symbol,
            direction=draft.direction,
            size_usdt=draft.size_usdt,
            leverage=draft.leverage,
            leverage_override_reason=draft.leverage_override_reason,
            entry_price=draft.entry_price,
            invalidation_price=draft.invalidation_price,
            max_loss_usdt=draft.max_loss_usdt,
            regime=draft.regime,
            thesis=draft.thesis,
            status=status,
            size_reduction_enforced=(
                draft.size_reduction_enforced
                if size_reduction_enforced is None
                else size_reduction_enforced
            ),
            opened_at=opened_at,
            closed_at=None,
            close_price=None,
            realized_pnl=None,
        )
        self.trades[trade.id] = trade
        self._next_trade_id += 1
        return trade

    async def get_trade(self, trade_id: int) -> Trade | None:
        return self.trades.get(trade_id)

    async def list_open_trades(self) -> list[Trade]:
        trades = [
            trade
            for trade in self.trades.values()
            if trade.status in {TradeStatus.OPEN, TradeStatus.OPEN_OVERRIDE}
        ]
        return sorted(trades, key=lambda trade: trade.opened_at)

    async def close_trade(
        self,
        trade_id: int,
        *,
        close_price: float,
        closed_at: datetime,
        breach_id: int | None = None,
        response_at: datetime | None = None,
    ) -> Trade | None:
        trade = self.trades.get(trade_id)
        if trade is None or trade.status == TradeStatus.CLOSED:
            return None
        realized_pnl = _calculate_realized_pnl(trade, close_price)
        closed_trade = trade.model_copy(
            update={
                "status": TradeStatus.CLOSED,
                "closed_at": closed_at,
                "close_price": close_price,
                "realized_pnl": realized_pnl,
            }
        )
        self.trades[trade_id] = closed_trade
        if breach_id is not None:
            breach = self.breaches[breach_id].model_copy(
                update={
                    "user_response": BreachUserResponse.CLOSED,
                    "response_at": response_at,
                }
            )
            self.breaches[breach_id] = breach
            self.active_breaches.pop(trade_id, None)
        return closed_trade

    async def mark_override(
        self,
        trade_id: int,
        *,
        breach_id: int,
        justification: str,
        response_at: datetime,
    ) -> Trade | None:
        trade = self.trades.get(trade_id)
        if trade is None or breach_id not in self.breaches:
            return None
        updated_trade = trade.model_copy(update={"status": TradeStatus.OPEN_OVERRIDE})
        self.trades[trade_id] = updated_trade
        breach = self.breaches[breach_id].model_copy(
            update={
                "user_response": BreachUserResponse.JUSTIFIED,
                "response_at": response_at,
                "justification": justification,
            }
        )
        self.breaches[breach_id] = breach
        self.active_breaches.pop(trade_id, None)
        return updated_trade

    async def get_open_breach(self, trade_id: int) -> Breach | None:
        breach_id = self.active_breaches.get(trade_id)
        if breach_id is None:
            return None
        return self.breaches.get(breach_id)

    async def list_closed_trades(
        self,
        limit: int | None = None,
        symbol: str | None = None,
    ) -> list[Trade]:
        trades = [
            trade
            for trade in self.trades.values()
            if trade.status == TradeStatus.CLOSED
            and (symbol is None or trade.symbol == symbol)
        ]
        trades.sort(key=lambda trade: trade.closed_at or trade.opened_at, reverse=True)
        if limit is None:
            return trades
        return trades[:limit]

    async def consecutive_loss_count(self) -> int:
        trades = await self.list_closed_trades()
        streak = 0
        for trade in trades:
            if trade.realized_pnl is None:
                continue
            if trade.realized_pnl < 0:
                streak += 1
                continue
            if trade.realized_pnl > 0:
                break
        return streak

    async def update_trade_realized_pnl(
        self,
        trade_id: int,
        *,
        realized_pnl: float,
    ) -> Trade | None:
        trade = self.trades.get(trade_id)
        if trade is None or trade.status != TradeStatus.CLOSED:
            return None
        updated_trade = trade.model_copy(update={"realized_pnl": realized_pnl})
        self.trades[trade_id] = updated_trade
        return updated_trade

    async def update_closed_trade(
        self,
        trade_id: int,
        *,
        updates: dict[str, object],
        recomputed_pnl: float | None,
    ) -> Trade | None:
        trade = self.trades.get(trade_id)
        if trade is None or trade.status != TradeStatus.CLOSED:
            return None
        payload = trade.model_dump()
        payload.update(updates)
        if recomputed_pnl is not None:
            payload["realized_pnl"] = recomputed_pnl
        updated_trade = Trade.model_validate(payload)
        self.trades[trade_id] = updated_trade
        return updated_trade

    async def get_redis_health(self) -> RedisHealthDetails:
        return RedisHealthDetails(
            connected=True,
            appendonly_enabled=True,
            persistence_dir="/data",
            persistence_dir_writable=True,
            aof_last_write_status="ok",
            last_error=None,
        )

    def create_breach_fixture(
        self,
        *,
        trade_id: int,
        detected_at: datetime,
        breach_price: float,
    ) -> Breach:
        breach = Breach(
            id=self._next_breach_id,
            trade_id=trade_id,
            detected_at=detected_at,
            breach_price=breach_price,
            user_response=None,
            response_at=None,
            justification=None,
        )
        self.breaches[breach.id] = breach
        self.active_breaches[trade_id] = breach.id
        self._next_breach_id += 1
        return breach


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "telegram_bot_token": "token",
            "telegram_chat_id": 1,
            "form_timeout_seconds": 600,
            "leverage_block_threshold": 20,
            "consecutive_loss_threshold": 2,
            "size_reduction_factor": 0.5,
        }
    )


def _draft(
    *,
    direction: Direction = Direction.LONG,
    size_usdt: float = 1000.0,
    leverage: int = 5,
    invalidation_price: float = 81000.0,
    thesis: str = "Disciplined test trade thesis.",
) -> TradeDraft:
    entry_price = 82000.0 if direction == Direction.LONG else 82000.0
    return TradeDraft(
        symbol="BTC",
        direction=direction,
        size_usdt=size_usdt,
        leverage=leverage,
        entry_price=entry_price,
        invalidation_price=invalidation_price,
        max_loss_usdt=50.0,
        regime=Regime.RANGE,
        thesis=thesis,
        leverage_override_reason=None,
        size_reduction_enforced=False,
    )


async def _stats_provider(days: int) -> str:
    return f"Stats ({days}d): total trades 3"


@pytest.mark.asyncio
async def test_handlers_new_text_cancel_and_trade_opened_event() -> None:
    """REQ-001 / REQ-006: `/new` commits after thesis and publishes trade_opened."""

    repo = InMemoryHandlerRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    bus = EventBus()
    received: list[EventType] = []

    async def record_event(event: Event) -> None:
        received.append(event.type)

    bus.subscribe(EventType.TRADE_OPENED, record_event)
    forms = TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now)
    handlers = TelegramHandlers(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        forms=forms,
        edit_closed=ClosedTradeEditService(
            repo=repo,  # type: ignore[arg-type]
            settings=_settings(),
            now_fn=clock.now,
        ),
        alerts=RecordingAlerts(),  # type: ignore[arg-type]
        health=FakeHealth(),  # type: ignore[arg-type]
        event_bus=bus,
        stats_provider=_stats_provider,
        now_fn=clock.now,
    )

    update = FakeUpdate(1)
    await handlers.new(update, FakeContext())
    assert update.effective_message.replies[-1] == (
        "Symbol? (e.g. BTC, ETH, HYPE, AUDUSD)"
    )

    await handlers.new(update, FakeContext())
    assert (
        update.effective_message.replies[-1]
        == "Form already in progress, /cancel first."
    )

    for text in [
        "BTC",
        "long",
        "5000",
        "10",
        "82500",
        "81200",
        "160",
        "uptrend",
        "Holding above 82K with strong ETF inflows.",
    ]:
        message_update = FakeUpdate(1, text=text)
        await handlers.text_message(message_update, FakeContext())

    assert received == [EventType.TRADE_OPENED]
    assert len(repo.trades) == 1

    cancel_update = FakeUpdate(1)
    await handlers.cancel(cancel_update, FakeContext())
    assert cancel_update.effective_message.replies == ["No form in progress."]


@pytest.mark.asyncio
async def test_handlers_closed_and_justify_publish_events() -> None:
    """REQ-005 / REQ-006: `/closed` and `/justify` resolve breaches and publish."""

    repo = InMemoryHandlerRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    trade = await repo.create_trade(_draft(), opened_at=clock.now())
    breach = repo.create_breach_fixture(
        trade_id=trade.id,
        detected_at=clock.now(),
        breach_price=80990.0,
    )
    bus = EventBus()
    received: list[EventType] = []

    async def record_event(event: Event) -> None:
        received.append(event.type)

    bus.subscribe(EventType.TRADE_CLOSED, record_event)
    bus.subscribe(EventType.BREACH_RESOLVED, record_event)
    alerts = RecordingAlerts()
    forms = TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now)
    handlers = TelegramHandlers(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        forms=forms,
        edit_closed=ClosedTradeEditService(
            repo=repo,  # type: ignore[arg-type]
            settings=_settings(),
            now_fn=clock.now,
        ),
        alerts=alerts,  # type: ignore[arg-type]
        health=FakeHealth(),  # type: ignore[arg-type]
        event_bus=bus,
        stats_provider=_stats_provider,
        now_fn=clock.now,
    )

    closed_update = FakeUpdate(1)
    await handlers.closed(closed_update, FakeContext(args=["81050"]))

    assert (
        "Trade #1 (BTC long) closed at 81050."
        in closed_update.effective_message.replies[-1]
    )
    assert alerts.resolved_breach_ids == [breach.id]
    assert received == [EventType.BREACH_RESOLVED, EventType.TRADE_CLOSED]

    trade_two = await repo.create_trade(
        _draft(direction=Direction.SHORT, invalidation_price=83000.0),
        opened_at=clock.now(),
    )
    repo.create_breach_fixture(
        trade_id=trade_two.id,
        detected_at=clock.now(),
        breach_price=83010.0,
    )
    justify_update = FakeUpdate(1)
    await handlers.justify(
        justify_update,
        FakeContext(args=[str(trade_two.id), "Still", "valid", "on", "retest"]),
    )

    assert justify_update.effective_message.replies[-1].startswith(
        "Trade #2 (BTC short) marked OPEN_OVERRIDE."
    )
    assert repo.trades[trade_two.id].status == TradeStatus.OPEN_OVERRIDE


@pytest.mark.asyncio
async def test_handlers_open_streak_stats_and_setpnl_commands() -> None:
    """REQ-003 / REQ-006: summary commands return expected mocked responses."""

    repo = InMemoryHandlerRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    open_trade = await repo.create_trade(_draft(size_usdt=900.0), opened_at=clock.now())
    losing_trade = await repo.create_trade(
        _draft(size_usdt=1000.0, thesis="Closed losing trade one."),
        opened_at=clock.now(),
    )
    await repo.close_trade(
        losing_trade.id,
        close_price=81000.0,
        closed_at=clock.now(),
    )
    winning_trade = await repo.create_trade(
        _draft(size_usdt=1200.0, thesis="Closed winning trade two."),
        opened_at=clock.now(),
    )
    await repo.close_trade(
        winning_trade.id,
        close_price=83000.0,
        closed_at=clock.now(),
    )

    handlers = TelegramHandlers(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        forms=TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now),
        edit_closed=ClosedTradeEditService(
            repo=repo,  # type: ignore[arg-type]
            settings=_settings(),
            now_fn=clock.now,
        ),
        alerts=RecordingAlerts(),  # type: ignore[arg-type]
        health=FakeHealth(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        stats_provider=_stats_provider,
        now_fn=clock.now,
    )

    open_update = FakeUpdate(1)
    repo.create_breach_fixture(
        trade_id=open_trade.id,
        detected_at=clock.now(),
        breach_price=80990.0,
    )
    await handlers.open(open_update, FakeContext())
    assert "Open trades:" in open_update.effective_message.replies[-1]
    assert f"#{open_trade.id}" in open_update.effective_message.replies[-1]
    assert "active breach" in open_update.effective_message.replies[-1]

    streak_update = FakeUpdate(1)
    await handlers.streak(streak_update, FakeContext())
    assert streak_update.effective_message.replies[-1].startswith("BTC: streak")

    stats_update = FakeUpdate(1)
    await handlers.stats(stats_update, FakeContext(args=["14"]))
    assert stats_update.effective_message.replies == ["Stats (14d): total trades 3"]

    stats_fail_update = FakeUpdate(1)
    await handlers.stats(stats_fail_update, FakeContext(args=["zero"]))
    assert stats_fail_update.effective_message.replies == ["Usage: /stats [days]"]

    setpnl_update = FakeUpdate(1)
    await handlers.setpnl(
        setpnl_update,
        FakeContext(args=[str(losing_trade.id), "-42.5"]),
    )
    assert (
        "Trade #2 (BTC long) P&L updated to -42.5 USDT."
        in setpnl_update.effective_message.replies[-1]
    )

    setpnl_fail_update = FakeUpdate(1)
    await handlers.setpnl(setpnl_fail_update, FakeContext(args=["oops"]))
    assert setpnl_fail_update.effective_message.replies == [
        "Usage: /setpnl <trade_id> <pnl>"
    ]


@pytest.mark.asyncio
async def test_handlers_edit_closed_happy_path_previews_and_applies() -> None:
    """R5.1-R5.5: `/edit_closed` previews impact, then yes persists the edit."""

    repo = InMemoryHandlerRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    target = await repo.create_trade(
        _draft(size_usdt=1000.0, thesis="Closed loss to revise."),
        opened_at=clock.now(),
    )
    closed = await repo.close_trade(
        target.id,
        close_price=81000.0,
        closed_at=clock.now(),
    )
    assert closed is not None
    handlers = _handlers(repo, clock)

    preview_update = FakeUpdate(1)
    await handlers.edit_closed(
        preview_update,
        FakeContext(args=[str(target.id), "close_price=83000"]),
    )

    preview = preview_update.effective_message.replies[-1]
    assert "Preview closed-trade edit:" in preview
    assert "close_price: 81000 → 83000" in preview
    assert "Recomputed realized P&L:" in preview
    assert "Consecutive-loss streak:" in preview
    assert "Active size cap:" in preview
    assert "Reply yes to apply, or no to cancel." in preview
    assert repo.trades[target.id].close_price == 81000.0

    confirm_update = FakeUpdate(1, text="yes")
    await handlers.text_message(confirm_update, FakeContext())

    assert repo.trades[target.id].close_price == 83000.0
    assert repo.trades[target.id].realized_pnl is not None
    assert repo.trades[target.id].realized_pnl > 0
    assert (
        "Trade #1 (BTC long) updated: close_price."
        in confirm_update.effective_message.replies[-1]
    )
    assert repo.conversations == {}


@pytest.mark.asyncio
async def test_handlers_edit_closed_decline_and_not_closed_paths() -> None:
    """R1.5 / R5.6: decline leaves history unchanged; open trades redirect."""

    repo = InMemoryHandlerRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    closed_target = await repo.create_trade(
        _draft(size_usdt=1000.0, thesis="Closed trade decline fixture."),
        opened_at=clock.now(),
    )
    closed = await repo.close_trade(
        closed_target.id,
        close_price=81000.0,
        closed_at=clock.now(),
    )
    assert closed is not None
    open_trade = await repo.create_trade(
        _draft(size_usdt=900.0, thesis="Open trade edit redirect fixture."),
        opened_at=clock.now(),
    )
    handlers = _handlers(repo, clock)

    preview_update = FakeUpdate(1)
    await handlers.edit_closed(
        preview_update,
        FakeContext(args=[str(closed_target.id), "close_price=83000"]),
    )
    decline_update = FakeUpdate(1, text="no")
    await handlers.text_message(decline_update, FakeContext())

    assert decline_update.effective_message.replies == ["Closed-trade edit cancelled."]
    assert repo.trades[closed_target.id].close_price == 81000.0
    assert repo.conversations == {}

    not_closed_update = FakeUpdate(1)
    await handlers.edit_closed(
        not_closed_update,
        FakeContext(args=[str(open_trade.id), "regime=range"]),
    )

    assert not_closed_update.effective_message.replies == [
        f"Trade {open_trade.id} is not closed. Use /edit for open trades."
    ]


@pytest.mark.asyncio
async def test_handlers_health_signals_help_unknown_and_empty_open() -> None:
    """REQ-006 / REQ-010: utility commands and empty states return centralized copy."""

    repo = InMemoryHandlerRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    handlers = TelegramHandlers(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        forms=TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now),
        edit_closed=ClosedTradeEditService(
            repo=repo,  # type: ignore[arg-type]
            settings=_settings(),
            now_fn=clock.now,
        ),
        alerts=RecordingAlerts(),  # type: ignore[arg-type]
        health=FakeHealth(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        stats_provider=_stats_provider,
        now_fn=clock.now,
    )

    open_update = FakeUpdate(1)
    await handlers.open(open_update, FakeContext())
    assert open_update.effective_message.replies == [
        "No open trades. Use /new to commit one."
    ]

    health_update = FakeUpdate(1)
    await handlers.health(health_update, FakeContext())
    assert "websocket: connected" in health_update.effective_message.replies[-1]
    assert "Redis AOF enabled: True" in health_update.effective_message.replies[-1]

    signals_update = FakeUpdate(1)
    await handlers.signals(signals_update, FakeContext())
    assert signals_update.effective_message.replies == [
        "Intelligence layer not configured. v2 feature — see REQ-010."
    ]

    help_update = FakeUpdate(1)
    await handlers.help(help_update, FakeContext())
    assert help_update.effective_message.replies[-1].startswith("Commands:")
    assert (
        "/edit <trade_id> field=value [...]"
        in help_update.effective_message.replies[-1]
    )
    assert (
        "/edit_closed <trade_id> field=value [...]"
        in help_update.effective_message.replies[-1]
    )

    help_one_update = FakeUpdate(1)
    await handlers.help(help_one_update, FakeContext(args=["closed"]))
    assert help_one_update.effective_message.replies == [
        "/closed <price> or /closed <id> <price>: Close an open trade."
    ]

    help_edit_update = FakeUpdate(1)
    await handlers.help(help_edit_update, FakeContext(args=["edit"]))
    assert help_edit_update.effective_message.replies == [
        "/edit <trade_id> field=value [...]: Edit an open trade."
    ]

    help_edit_closed_update = FakeUpdate(1)
    await handlers.help(help_edit_closed_update, FakeContext(args=["edit_closed"]))
    assert "/edit_closed <trade_id> field=value [...]" in (
        help_edit_closed_update.effective_message.replies[-1]
    )
    assert "Editable fields:" in help_edit_closed_update.effective_message.replies[-1]

    edit_closed_usage_update = FakeUpdate(1)
    await handlers.edit_closed(edit_closed_usage_update, FakeContext(args=[]))
    assert edit_closed_usage_update.effective_message.replies == [
        "Usage: /edit_closed <trade_id> <field1>=<value1> "
        "[<field2>=<value2> ...]\n"
        "Editable fields: direction, size_usdt, leverage, "
        "leverage_override_reason, entry_price, invalidation_price, "
        "max_loss_usdt, regime, thesis, opened_at, closed_at, close_price"
    ]

    non_whitelisted_update = FakeUpdate(999)
    await handlers.edit_closed(
        non_whitelisted_update,
        FakeContext(args=["1", "regime=range"]),
    )
    assert non_whitelisted_update.effective_message.replies == []

    unknown_update = FakeUpdate(1)
    await handlers.unknown(unknown_update, FakeContext())
    assert unknown_update.effective_message.replies == ["Unknown command. Use /help."]

    repo.conversations[1] = ConversationState(
        chat_id=1,
        state=ConversationStep.EDIT_CLOSED_CONFIRM,
        partial_trade_json='{"trade_id": 1, "updates": {}, "recomputed_pnl": null}',
        updated_at=clock.now(),
    )
    cancel_update = FakeUpdate(1)
    await handlers.cancel(cancel_update, FakeContext())
    assert cancel_update.effective_message.replies == ["Form cancelled."]
    assert repo.conversations == {}


def _calculate_realized_pnl(trade: Trade, close_price: float) -> float:
    size_btc = trade.size_usdt / trade.entry_price
    direction_sign = 1.0 if trade.direction == Direction.LONG else -1.0
    return (close_price - trade.entry_price) * size_btc * direction_sign


def _handlers(
    repo: InMemoryHandlerRepo,
    clock: MutableClock,
    *,
    event_bus: EventBus | None = None,
) -> TelegramHandlers:
    return TelegramHandlers(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        forms=TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now),
        edit_closed=ClosedTradeEditService(
            repo=repo,  # type: ignore[arg-type]
            settings=_settings(),
            now_fn=clock.now,
        ),
        alerts=RecordingAlerts(),  # type: ignore[arg-type]
        health=FakeHealth(),  # type: ignore[arg-type]
        event_bus=event_bus or EventBus(),
        stats_provider=_stats_provider,
        now_fn=clock.now,
    )
