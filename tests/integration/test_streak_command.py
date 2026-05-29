"""Integration tests for per-symbol `/streak` output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.bot.handlers import TelegramHandlers
from src.config import Settings
from src.events.bus import EventBus
from src.models.trade import Direction, Regime, Trade, TradeStatus


@dataclass
class FakeMessage:
    """Minimal Telegram message fake."""

    replies: list[str]

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    """Minimal Telegram update fake."""

    def __init__(self) -> None:
        self.effective_chat = SimpleNamespace(id=1)
        self.effective_message = FakeMessage(replies=[])


class FakeRepo:
    """Closed-trade repository surface for `/streak` tests."""

    def __init__(self, trades: list[Trade]) -> None:
        self._trades = trades

    async def list_closed_trades(
        self,
        limit: int | None = None,
        symbol: str | None = None,
    ) -> list[Trade]:
        trades = sorted(
            [
                trade
                for trade in self._trades
                if symbol is None or trade.symbol == symbol
            ],
            key=lambda trade: trade.closed_at or trade.opened_at,
            reverse=True,
        )
        if limit is None:
            return trades
        return trades[:limit]


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "telegram_bot_token": "token",
            "telegram_chat_id": 1,
            "consecutive_loss_threshold": 2,
            "size_reduction_factor": 0.5,
        }
    )


def _handler(repo: FakeRepo) -> TelegramHandlers:
    return TelegramHandlers(
        settings=_settings(),
        repo=repo,  # type: ignore[arg-type]
        forms=SimpleNamespace(),  # type: ignore[arg-type]
        edit_closed=SimpleNamespace(),  # type: ignore[arg-type]
        alerts=SimpleNamespace(),  # type: ignore[arg-type]
        health=SimpleNamespace(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        stats_provider=lambda _days: _noop_stats(),
        now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )


async def _noop_stats() -> str:
    return "stats"


def _closed_trade(
    *,
    trade_id: int,
    symbol: str,
    size_usdt: float,
    realized_pnl: float,
    closed_offset_minutes: int,
) -> Trade:
    opened_at = datetime(2026, 5, 17, 9, 0, tzinfo=UTC) + timedelta(
        minutes=closed_offset_minutes - 1
    )
    closed_at = datetime(2026, 5, 17, 9, 0, tzinfo=UTC) + timedelta(
        minutes=closed_offset_minutes
    )
    return Trade(
        id=trade_id,
        symbol=symbol,
        direction=Direction.LONG,
        size_usdt=size_usdt,
        leverage=5,
        leverage_override_reason=None,
        entry_price=100.0,
        invalidation_price=90.0,
        max_loss_usdt=20.0,
        regime=Regime.RANGE,
        thesis=f"{symbol} streak command fixture trade.",
        status=TradeStatus.CLOSED,
        size_reduction_enforced=False,
        opened_at=opened_at,
        closed_at=closed_at,
        close_price=90.0,
        realized_pnl=realized_pnl,
    )


@pytest.mark.asyncio
async def test_streak_command_reports_one_line_per_symbol() -> None:
    """R5: `/streak` reports symbol-local streaks and active caps."""

    trades = [
        _closed_trade(
            trade_id=1,
            symbol="BTC",
            size_usdt=5000.0,
            realized_pnl=100.0,
            closed_offset_minutes=1,
        ),
        _closed_trade(
            trade_id=2,
            symbol="BTC",
            size_usdt=1500.0,
            realized_pnl=-20.0,
            closed_offset_minutes=2,
        ),
        _closed_trade(
            trade_id=3,
            symbol="ETH",
            size_usdt=1000.0,
            realized_pnl=50.0,
            closed_offset_minutes=3,
        ),
        _closed_trade(
            trade_id=4,
            symbol="HYPE",
            size_usdt=800.0,
            realized_pnl=-10.0,
            closed_offset_minutes=4,
        ),
        _closed_trade(
            trade_id=5,
            symbol="BTC",
            size_usdt=1200.0,
            realized_pnl=-25.0,
            closed_offset_minutes=5,
        ),
    ]
    update = FakeUpdate()

    await _handler(FakeRepo(trades)).streak(update, SimpleNamespace())

    assert update.effective_message.replies == [
        "\n".join(
            [
                "BTC: streak 2, size cap 2500 USDT",
                "ETH: streak 0, no cap",
                "HYPE: streak 1, no cap",
            ]
        )
    ]


@pytest.mark.asyncio
async def test_streak_command_empty_state() -> None:
    """R5: `/streak` has a multi-symbol empty state."""

    update = FakeUpdate()

    await _handler(FakeRepo([])).streak(update, SimpleNamespace())

    assert update.effective_message.replies == ["No closed trades yet on any symbol."]
