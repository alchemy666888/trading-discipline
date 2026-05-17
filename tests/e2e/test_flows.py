"""End-to-end scenarios using a fake Telegram client and scripted tick feeder."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app import build_runtime
from src.config import Settings
from src.db.repo import RedisHealthDetails
from src.exchange.base import ConnectionEvent, ConnectionState, ExchangeAdapter, Tick
from src.models.breach import Breach, BreachUserResponse
from src.models.conversation import ConversationState
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus


@dataclass
class MutableClock:
    """Deterministic wall clock for E2E scenarios."""

    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


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


class ScriptedAdapter(ExchangeAdapter):
    """Fake adapter that yields scripted ticks and connection events."""

    def __init__(self, script: list[ConnectionEvent | Tick]) -> None:
        super().__init__()
        self._script = deque(script)
        self._closed = False

    async def stream_ticks(self):
        while self._script and not self._closed:
            item = self._script.popleft()
            if isinstance(item, ConnectionEvent):
                await self._publish_connection_event(item)
                continue
            yield item

    async def healthy(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True


class InMemoryE2ERepo:
    """In-memory repo with the runtime surface needed by the E2E stack."""

    def __init__(self) -> None:
        self.conversations: dict[int, ConversationState] = {}
        self.trades: dict[int, Trade] = {}
        self.breaches: dict[int, Breach] = {}
        self.active_breaches: dict[int, int] = {}
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

    async def list_open_trades(self) -> list[Trade]:
        trades = [
            trade
            for trade in self.trades.values()
            if trade.status in {TradeStatus.OPEN, TradeStatus.OPEN_OVERRIDE}
        ]
        return sorted(trades, key=lambda trade: trade.opened_at)

    async def list_closed_trades(self, limit: int | None = None) -> list[Trade]:
        trades = [
            trade
            for trade in self.trades.values()
            if trade.status == TradeStatus.CLOSED
        ]
        trades.sort(key=lambda trade: trade.closed_at or trade.opened_at, reverse=True)
        return trades if limit is None else trades[:limit]

    async def list_all_trades(self) -> list[Trade]:
        return sorted(self.trades.values(), key=lambda trade: trade.opened_at)

    async def get_trade(self, trade_id: int) -> Trade | None:
        return self.trades.get(trade_id)

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
        realized_pnl = _realized_pnl(trade, close_price)
        updated_trade = trade.model_copy(
            update={
                "status": TradeStatus.CLOSED,
                "closed_at": closed_at,
                "close_price": close_price,
                "realized_pnl": realized_pnl,
            }
        )
        self.trades[trade_id] = updated_trade
        active_breach_id = breach_id or self.active_breaches.get(trade_id)
        if active_breach_id is not None and active_breach_id in self.breaches:
            self.breaches[active_breach_id] = self.breaches[
                active_breach_id
            ].model_copy(
                update={
                    "user_response": BreachUserResponse.CLOSED,
                    "response_at": response_at or closed_at,
                }
            )
            self.active_breaches.pop(trade_id, None)
        return updated_trade

    async def create_breach(
        self,
        trade_id: int,
        *,
        breach_price: float,
        detected_at: datetime,
    ) -> Breach | None:
        trade = self.trades.get(trade_id)
        if trade is None or trade.status == TradeStatus.CLOSED:
            return None
        if trade_id in self.active_breaches:
            return None
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

    async def get_open_breach(self, trade_id: int) -> Breach | None:
        breach_id = self.active_breaches.get(trade_id)
        if breach_id is None:
            return None
        return self.breaches.get(breach_id)

    async def get_breach(self, breach_id: int) -> Breach | None:
        return self.breaches.get(breach_id)

    async def list_breaches_for_trade(self, trade_id: int) -> list[Breach]:
        breaches = [
            breach for breach in self.breaches.values() if breach.trade_id == trade_id
        ]
        return sorted(breaches, key=lambda breach: breach.detected_at)

    async def list_unresolved_breaches(self) -> list[Breach]:
        return [
            self.breaches[breach_id]
            for breach_id in self.active_breaches.values()
            if self.breaches[breach_id].user_response is None
        ]

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
        self.breaches[breach_id] = self.breaches[breach_id].model_copy(
            update={
                "user_response": BreachUserResponse.JUSTIFIED,
                "response_at": response_at,
                "justification": justification,
            }
        )
        self.active_breaches.pop(trade_id, None)
        return updated_trade

    async def recent_closed_trades(self, n: int) -> list[Trade]:
        return (await self.list_closed_trades())[:n]

    async def consecutive_loss_count(self) -> int:
        streak = 0
        for trade in await self.list_closed_trades():
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

    async def record_alert(
        self,
        breach_id: int,
        *,
        sent_at: datetime,
        escalation_level: int,
        message: str,
    ) -> None:
        return None

    async def get_redis_health(self) -> RedisHealthDetails:
        return RedisHealthDetails(
            connected=True,
            appendonly_enabled=True,
            persistence_dir="/data",
            persistence_dir_writable=True,
            aof_last_write_status="ok",
            last_error=None,
        )


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "telegram_bot_token": "token",
            "telegram_chat_id": 1,
            "timezone": "UTC",
            "form_timeout_seconds": 600,
            "leverage_block_threshold": 20,
            "consecutive_loss_threshold": 2,
            "size_reduction_factor": 0.5,
            "heartbeat_time_local": "09:00",
        }
    )


def _build_runtime(
    *,
    clock: MutableClock,
    repo: InMemoryE2ERepo,
    adapter: ExchangeAdapter | None = None,
) -> tuple[object, list[str]]:
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    runtime = build_runtime(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        application=None,
        exchange_adapter=adapter or ScriptedAdapter([]),
        send_message=send_message,
        now_fn=clock.now,
        redis_client=None,
    )
    return runtime, sent_messages


async def _open_trade(runtime: object) -> Trade:
    handlers = runtime.handlers
    start_update = FakeUpdate(1)
    await handlers.new(start_update, FakeContext())
    for text in [
        "long",
        "1000",
        "10",
        "82500",
        "81200",
        "160",
        "uptrend",
        "Holding above 82K with continuation potential.",
    ]:
        await handlers.text_message(FakeUpdate(1, text=text), FakeContext())
    trades = await runtime.repo.list_open_trades()
    return trades[-1]


@pytest.mark.asyncio
async def test_happy_path_open_favorable_close_profit() -> None:
    """E2E: open a trade, see a favorable tick, and close at a profit."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)
    trade = await _open_trade(runtime)

    await runtime.monitor.process_tick(
        Tick(price=83000.0, ts=datetime(2026, 5, 17, 9, 1, tzinfo=UTC))
    )
    assert sent_messages == []

    close_update = FakeUpdate(1)
    await runtime.handlers.closed(close_update, FakeContext(args=["83000"]))

    closed_trade = await repo.get_trade(trade.id)
    assert closed_trade is not None
    assert closed_trade.status == TradeStatus.CLOSED
    assert closed_trade.realized_pnl is not None and closed_trade.realized_pnl > 0


@pytest.mark.asyncio
async def test_breach_then_closed_flow() -> None:
    """E2E: breach alert fires and `/closed` resolves it."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)
    trade = await _open_trade(runtime)

    await runtime.monitor.process_tick(
        Tick(price=81190.0, ts=datetime(2026, 5, 17, 9, 1, tzinfo=UTC))
    )
    assert sent_messages and "Trade #1" in sent_messages[0]

    close_update = FakeUpdate(1)
    await runtime.handlers.closed(
        close_update,
        FakeContext(args=[str(trade.id), "81200"]),
    )

    assert repo.active_breaches == {}
    assert "Trade #1 closed" in close_update.effective_message.replies[-1]


@pytest.mark.asyncio
async def test_breach_then_justify_then_second_breach_rearms() -> None:
    """E2E: `/justify` resolves the first breach and a later breach fires again."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)
    trade = await _open_trade(runtime)

    await runtime.monitor.process_tick(
        Tick(price=81190.0, ts=datetime(2026, 5, 17, 9, 1, tzinfo=UTC))
    )
    justify_update = FakeUpdate(1)
    await runtime.handlers.justify(
        justify_update,
        FakeContext(args=[str(trade.id), "Still", "valid", "after", "retest"]),
    )
    assert repo.active_breaches == {}

    await runtime.monitor.process_tick(
        Tick(price=81300.0, ts=datetime(2026, 5, 17, 9, 2, tzinfo=UTC))
    )
    await runtime.monitor.process_tick(
        Tick(price=81180.0, ts=datetime(2026, 5, 17, 9, 3, tzinfo=UTC))
    )

    assert len(sent_messages) >= 2
    assert repo.trades[trade.id].status == TradeStatus.OPEN_OVERRIDE


@pytest.mark.asyncio
async def test_breach_no_response_escalates() -> None:
    """E2E: unresolved breaches escalate on the configured cadence."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)
    await _open_trade(runtime)

    await runtime.monitor.process_tick(
        Tick(price=81190.0, ts=datetime(2026, 5, 17, 9, 1, tzinfo=UTC))
    )
    initial_count = len(sent_messages)

    clock.advance(60)
    await runtime.alerts.process_due_alerts()
    clock.advance(240)
    await runtime.alerts.process_due_alerts()

    assert len(sent_messages) >= initial_count + 2


@pytest.mark.asyncio
async def test_disconnect_during_breach_reconnect_alerts_continue() -> None:
    """E2E: reconnecting after a breach does not stop breach escalation."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)
    await _open_trade(runtime)

    await runtime.monitor.process_tick(
        Tick(price=81190.0, ts=datetime(2026, 5, 17, 9, 1, tzinfo=UTC))
    )
    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.DISCONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            reason="disconnect",
        )
    )
    clock.advance(10)
    await runtime.health.process_due_alerts()
    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.RECONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            gap_seconds=10,
            reason=None,
        )
    )
    clock.advance(60)
    await runtime.alerts.process_due_alerts()

    assert any("Price monitor is down" in message for message in sent_messages)
    assert any("breach still unresolved" in message for message in sent_messages)


@pytest.mark.asyncio
async def test_ws_down_15s_with_open_trade_alerts_then_recovers() -> None:
    """E2E: 15s downtime with open trades sends one down alert and one recovery."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)
    await _open_trade(runtime)

    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.DISCONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            reason="disconnect",
        )
    )
    clock.advance(10)
    await runtime.health.process_due_alerts()
    clock.advance(5)
    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.RECONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            gap_seconds=15,
            reason=None,
        )
    )

    assert any("Price monitor is down" in message for message in sent_messages)
    assert any("Recovery:" in message for message in sent_messages)


@pytest.mark.asyncio
async def test_ws_down_8s_with_open_trade_stays_silent() -> None:
    """E2E: brief 8s downtime with open trades is debounced."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)
    await _open_trade(runtime)

    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.DISCONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            reason="disconnect",
        )
    )
    clock.advance(8)
    await runtime.health.process_due_alerts()
    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.RECONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            gap_seconds=8,
            reason=None,
        )
    )

    assert sent_messages == []


@pytest.mark.asyncio
async def test_ws_down_70s_without_open_trade_alerts_then_recovers() -> None:
    """E2E: 70s downtime without open trades uses the longer alert threshold."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)

    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.DISCONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            reason="disconnect",
        )
    )
    clock.advance(60)
    await runtime.health.process_due_alerts()
    clock.advance(10)
    await runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.RECONNECTED,
            ts=clock.now(),
            reconnect_attempt=1,
            gap_seconds=70,
            reason=None,
        )
    )

    assert any("Price monitor is down" in message for message in sent_messages)
    assert any("Recovery:" in message for message in sent_messages)


@pytest.mark.asyncio
async def test_gap_over_60s_reconnect_rechecks_and_breaches() -> None:
    """E2E: first post-reconnect tick after a >60s gap can create the breach."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    trade = await repo.create_trade(
        TradeDraft(
            direction=Direction.SHORT,
            size_usdt=1000.0,
            leverage=5,
            entry_price=82000.0,
            invalidation_price=83000.0,
            max_loss_usdt=50.0,
            regime=Regime.RANGE,
            thesis="Gap-through short trade.",
        ),
        opened_at=clock.now(),
    )
    adapter = ScriptedAdapter(
        [
            ConnectionEvent(
                state=ConnectionState.DISCONNECTED,
                ts=clock.now(),
                reconnect_attempt=1,
                reason="disconnect",
            ),
            ConnectionEvent(
                state=ConnectionState.RECONNECTED,
                ts=clock.now() + timedelta(seconds=70),
                reconnect_attempt=1,
                gap_seconds=70,
                reason=None,
            ),
            Tick(price=83010.0, ts=clock.now() + timedelta(seconds=70)),
        ]
    )
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo, adapter=adapter)

    await runtime.monitor.run()

    assert await repo.get_open_breach(trade.id) is not None
    assert sent_messages


@pytest.mark.asyncio
async def test_daily_heartbeat_fires_when_healthy_and_suppresses_when_unhealthy() -> (
    None
):
    """E2E: daily heartbeat fires when healthy and stays suppressed when unhealthy."""

    healthy_clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    healthy_repo = InMemoryE2ERepo()
    healthy_runtime, healthy_messages = _build_runtime(
        clock=healthy_clock,
        repo=healthy_repo,
    )
    healthy_runtime.health.record_tick(
        received_at=healthy_clock.now() - timedelta(seconds=3)
    )
    assert await healthy_runtime.health.maybe_send_daily_heartbeat() is True
    assert any("Heartbeat:" in message for message in healthy_messages)

    unhealthy_clock = MutableClock(current=datetime(2026, 5, 18, 9, 0, tzinfo=UTC))
    unhealthy_repo = InMemoryE2ERepo()
    unhealthy_runtime, unhealthy_messages = _build_runtime(
        clock=unhealthy_clock,
        repo=unhealthy_repo,
    )
    await unhealthy_runtime.health.handle_connection_event(
        ConnectionEvent(
            state=ConnectionState.DISCONNECTED,
            ts=unhealthy_clock.now(),
            reconnect_attempt=1,
            reason="disconnect",
        )
    )
    assert await unhealthy_runtime.health.maybe_send_daily_heartbeat() is False
    assert unhealthy_messages == []


@pytest.mark.asyncio
async def test_restart_with_open_trade_and_unresolved_breach_rearms_alerts() -> None:
    """E2E: restart re-arms unresolved breach alerting for open trades."""

    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    repo = InMemoryE2ERepo()
    trade = await repo.create_trade(
        TradeDraft(
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=82000.0,
            invalidation_price=81000.0,
            max_loss_usdt=50.0,
            regime=Regime.RANGE,
            thesis="Restart-open-breach fixture.",
        ),
        opened_at=clock.now(),
    )
    await repo.create_breach(
        trade.id,
        breach_price=80990.0,
        detected_at=clock.now(),
    )
    runtime, sent_messages = _build_runtime(clock=clock, repo=repo)

    await runtime.resume_state()

    assert runtime.alerts.active_breach_ids()
    assert any("Trade #1" in message for message in sent_messages)


def _realized_pnl(trade: Trade, close_price: float) -> float:
    size_btc = trade.size_usdt / trade.entry_price
    direction_sign = 1.0 if trade.direction == Direction.LONG else -1.0
    return (close_price - trade.entry_price) * size_btc * direction_sign
