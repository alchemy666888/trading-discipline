"""Integration tests for application wiring and restart resilience."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.app import build_runtime, create_runtime
from src.config import Settings
from src.db.keyspace import schema_version_key
from src.exchange.base import ExchangeAdapter, Tick
from src.models.breach import Breach
from src.models.events import Event, EventType, TradeOpenedEvent, TradeOpenedPayload
from src.models.trade import Direction, Regime, Trade, TradeStatus


class FakeUpdater:
    """Minimal PTB updater fake."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start_polling(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class FakeApplication:
    """Minimal PTB application fake."""

    def __init__(self) -> None:
        self.handlers: list[object] = []
        self.initialized = False
        self.started = False
        self.stopped = False
        self.shutdown_called = False
        self.updater = FakeUpdater()

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    async def initialize(self) -> None:
        self.initialized = True

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def shutdown(self) -> None:
        self.shutdown_called = True


class FakeExchangeAdapter(ExchangeAdapter):
    """Finite fake adapter for runtime startup tests."""

    def __init__(self, ticks: list[Tick] | None = None) -> None:
        super().__init__()
        self._ticks = list(ticks or [])
        self.closed = False

    async def stream_ticks(self):
        for tick in self._ticks:
            if self.closed:
                break
            yield tick

    async def healthy(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True


class FakeMessage:
    """Minimal message fake for handler invocation."""

    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    """Minimal update fake for handler invocation."""

    def __init__(self, chat_id: int) -> None:
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage()


class FakeContext:
    """Minimal context fake."""

    args: list[str] = []


@dataclass
class FakeRepo:
    """Minimal repo surface for restart-resilience runtime tests."""

    trade: Trade
    breach: Breach

    async def list_open_trades(self) -> list[Trade]:
        return [self.trade]

    async def list_unresolved_breaches(self) -> list[Breach]:
        return [self.breach]

    async def list_all_trades(self) -> list[Trade]:
        return [self.trade]

    async def list_breaches_for_trade(self, trade_id: int) -> list[Breach]:
        return [self.breach] if trade_id == self.trade.id else []

    async def record_alert(
        self,
        breach_id: int,
        *,
        sent_at: datetime,
        escalation_level: int,
        message: str,
    ) -> None:
        return None

    async def get_redis_health(self):  # pragma: no cover - not used in this test
        raise NotImplementedError


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
        }
    )


@pytest.mark.asyncio
async def test_create_runtime_applies_migrations_and_starts_components(
    redis_client,
) -> None:
    """TASK-020: startup wires the app, health handler, and event bus together."""

    fake_app = FakeApplication()
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    runtime = await create_runtime(
        settings=_settings(),
        redis_client=redis_client,
        application=fake_app,  # type: ignore[arg-type]
        exchange_adapter=FakeExchangeAdapter(),
        send_message=send_message,
        now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )

    assert await redis_client.get(schema_version_key()) == "1"
    assert len(fake_app.handlers) >= 13

    received: list[EventType] = []

    async def record(event: Event) -> None:
        received.append(event.type)

    runtime.event_bus.subscribe(EventType.TRADE_OPENED, record)
    await runtime.event_bus.publish(
        TradeOpenedEvent(
            ts=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
            payload=TradeOpenedPayload(
                trade_id=1,
                snapshot=Trade(
                    id=1,
                    direction=Direction.LONG,
                    size_usdt=1000.0,
                    leverage=5,
                    leverage_override_reason=None,
                    entry_price=82000.0,
                    invalidation_price=81000.0,
                    max_loss_usdt=50.0,
                    regime=Regime.UPTREND,
                    thesis="Runtime startup fixture trade.",
                    status=TradeStatus.OPEN,
                    size_reduction_enforced=False,
                    opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
                    closed_at=None,
                    close_price=None,
                    realized_pnl=None,
                ),
            ),
        )
    )
    assert received == [EventType.TRADE_OPENED]

    health_update = FakeUpdate(1)
    await runtime.handlers.health(health_update, FakeContext())
    assert "Websocket:" in health_update.effective_message.replies[-1]
    assert "Redis connected: True" in health_update.effective_message.replies[-1]

    await runtime.start()
    await asyncio.sleep(0)
    assert fake_app.initialized is True
    assert fake_app.started is True
    assert fake_app.updater.started is True
    await runtime.stop()
    assert fake_app.stopped is True
    assert fake_app.shutdown_called is True


@pytest.mark.asyncio
async def test_runtime_resume_state_rearms_unresolved_breaches() -> None:
    """TASK-021: startup re-arms unresolved breaches from level 0."""

    trade = Trade(
        id=7,
        direction=Direction.LONG,
        size_usdt=1000.0,
        leverage=5,
        leverage_override_reason=None,
        entry_price=82000.0,
        invalidation_price=81000.0,
        max_loss_usdt=50.0,
        regime=Regime.RANGE,
        thesis="Restart resilience fixture trade.",
        status=TradeStatus.OPEN,
        size_reduction_enforced=False,
        opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        closed_at=None,
        close_price=None,
        realized_pnl=None,
    )
    breach = Breach(
        id=4,
        trade_id=trade.id,
        detected_at=datetime(2026, 5, 17, 9, 5, tzinfo=UTC),
        breach_price=80990.0,
        user_response=None,
        response_at=None,
        justification=None,
    )
    repo = FakeRepo(trade=trade, breach=breach)
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    runtime = build_runtime(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        application=None,
        exchange_adapter=FakeExchangeAdapter(),
        send_message=send_message,
        now_fn=lambda: datetime(2026, 5, 17, 9, 10, tzinfo=UTC),
        redis_client=None,
    )

    await runtime.resume_state()

    assert runtime.alerts.active_breach_ids() == {breach.id}
    assert sent_messages
    assert "Trade #7" in sent_messages[0]
