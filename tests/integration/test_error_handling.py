"""Integration tests for top-level error handling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, time
from types import SimpleNamespace

import pytest

from src.bot.edit_closed import ClosedTradeEditService
from src.bot.forms import TradeFormService
from src.bot.handlers import TelegramHandlers
from src.config import Settings
from src.events.bus import EventBus
from src.exchange.base import ExchangeAdapter, Tick
from src.monitor.health import MonitorHealth
from src.monitor.monitor import Monitor


class FakeMessage:
    """Minimal Telegram message fake."""

    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    """Minimal Telegram update fake."""

    def __init__(self, chat_id: int) -> None:
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage()


class FakeContext:
    """Minimal Telegram context fake."""

    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args or []


class MinimalRepo:
    """Minimal repo surface for safe-handler tests."""

    async def get_conversation_state(self, chat_id: int):
        return None

    async def set_conversation_state(self, state, *, ttl_seconds=None):
        return state

    async def clear_conversation_state(self, chat_id: int) -> None:
        return None

    async def list_closed_trades(self, limit=None):
        return []

    async def list_open_trades(self):
        return []

    async def get_redis_health(self):
        raise RuntimeError("redis health failure")


class FakeExchangeAdapter(ExchangeAdapter):
    """Finite adapter used to prove the monitor loop survives per-tick failures."""

    def __init__(self, ticks: list[Tick]) -> None:
        super().__init__()
        self._ticks = list(ticks)
        self._closed = False

    async def stream_ticks(self) -> AsyncIterator[Tick]:
        for tick in self._ticks:
            if self._closed:
                break
            yield tick

    async def healthy(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True


class MinimalMonitorRepo:
    """Minimal repo surface for monitor run-loop tests."""

    async def list_open_trades(self):
        return []


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
async def test_handler_exception_replies_with_internal_error_and_keeps_running() -> (
    None
):
    """TASK-022: handler exceptions reply cleanly and do not break later commands."""

    forms = TradeFormService(
        repo=MinimalRepo(),  # type: ignore[arg-type]
        settings=_settings(),
        now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )

    async def failing_stats_provider(days: int) -> str:
        raise RuntimeError("boom")

    handlers = TelegramHandlers(
        settings=_settings(),
        repo=MinimalRepo(),  # type: ignore[arg-type]
        forms=forms,
        edit_closed=ClosedTradeEditService(
            repo=MinimalRepo(),  # type: ignore[arg-type]
            settings=_settings(),
            now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        ),
        alerts=SimpleNamespace(),  # type: ignore[arg-type]
        health=SimpleNamespace(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        stats_provider=failing_stats_provider,
        now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )

    failing_update = FakeUpdate(1)
    await handlers.stats(failing_update, FakeContext(args=["30"]))
    assert failing_update.effective_message.replies == ["Internal error, try again."]

    healthy_update = FakeUpdate(1)
    await handlers.help(healthy_update, FakeContext())
    assert healthy_update.effective_message.replies[-1].startswith("Commands:")


@pytest.mark.asyncio
async def test_monitor_loop_logs_tick_failure_and_continues() -> None:
    """TASK-022: tick-processing failures are logged and later ticks continue."""

    tick_one = Tick(price=82000.0, ts=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    tick_two = Tick(price=82100.0, ts=datetime(2026, 5, 17, 9, 1, tzinfo=UTC))
    monitor = Monitor(
        repo=MinimalMonitorRepo(),  # type: ignore[arg-type]
        adapter=FakeExchangeAdapter([tick_one, tick_two]),
        alerts=SimpleNamespace(update_trade_price=lambda *_args, **_kwargs: None),  # type: ignore[arg-type]
        health=MonitorHealth(
            send_message=lambda _message: _noop(),  # type: ignore[arg-type]
            event_bus=EventBus(),
            open_trade_count_provider=lambda: 0,
            render_down_alert=lambda *_args: "down",
            render_recovery_alert=lambda *_args: "recovery",
            render_heartbeat=lambda *_args: "heartbeat",
            monitor_down_alert_delay_with_open_trades_seconds=10,
            monitor_down_alert_delay_no_open_trades_seconds=60,
            monitor_down_repeat_with_open_trades_seconds=60,
            monitor_down_repeat_no_open_trades_seconds=300,
            heartbeat_time_local=time(hour=9, minute=0),
            timezone="UTC",
            now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        ),
        event_bus=EventBus(),
        now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )
    calls: list[float] = []

    async def flaky_process_tick(tick: Tick) -> None:
        calls.append(tick.price)
        if len(calls) == 1:
            raise RuntimeError("tick failed")

    monitor.process_tick = flaky_process_tick  # type: ignore[method-assign]

    await monitor.run()

    assert calls == [82000.0, 82100.0]


async def _noop() -> None:
    return None
