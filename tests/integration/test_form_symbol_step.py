"""Integration tests for the `/new` symbol step and universe fallback policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from src.bot import formatting
from src.bot.forms import TradeFormService
from src.config import Settings
from src.models.conversation import ConversationState, ConversationStep
from src.models.trade import Trade, TradeDraft, TradeStatus


@dataclass
class MutableClock:
    """Mutable wall clock for deterministic cache staleness tests."""

    current: datetime

    def now(self) -> datetime:
        return self.current


class FakeUniverseFetcher:
    """Scripted Hyperliquid universe fetcher."""

    def __init__(
        self,
        symbols: list[str] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._symbols = symbols or []
        self._error = error
        self.calls = 0

    async def fetch_universe(self) -> list[str]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._symbols


class InMemorySymbolFormRepo:
    """Minimal repo surface used by the symbol-step form tests."""

    def __init__(self) -> None:
        self.conversations: dict[int, ConversationState] = {}
        self.trades: dict[int, Trade] = {}
        self.universe: tuple[set[str], datetime] | None = None
        self._next_trade_id = 1

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

    async def list_open_trades(self) -> list[Trade]:
        trades = [
            trade
            for trade in self.trades.values()
            if trade.status in {TradeStatus.OPEN, TradeStatus.OPEN_OVERRIDE}
        ]
        return sorted(trades, key=lambda trade: trade.opened_at)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "telegram_bot_token": "token",
            "telegram_chat_id": 1,
            "form_timeout_seconds": 600,
            "leverage_block_threshold": 10,
            "consecutive_loss_threshold": 2,
            "size_reduction_factor": 0.5,
            "hyperliquid_universe_stale_seconds": 900,
        }
    )


async def _complete_trade_after_symbol(service: TradeFormService) -> Trade:
    await service.handle_input(1, "long")
    await service.handle_input(1, "5000")
    await service.handle_input(1, "5")
    await service.handle_input(1, "100")
    await service.handle_input(1, "90")
    await service.handle_input(1, "160")
    await service.handle_input(1, "uptrend")
    result = await service.handle_input(
        1,
        "Holding above the base with a clean invalidation.",
    )
    assert result is not None
    assert result.created_trade is not None
    return result.created_trade


@pytest.mark.asyncio
async def test_form_symbol_happy_path_commits_trade_with_symbol() -> None:
    """R1/R2: `/new` starts with symbol and commits the selected market."""

    repo = InMemorySymbolFormRepo()
    fetcher = FakeUniverseFetcher(["BTC", "ETH", "HYPE", "AUDUSD"])
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    service = TradeFormService(
        repo=repo,
        settings=_settings(),
        universe_fetcher=fetcher,
        now_fn=clock.now,
    )

    start = await service.start(1)
    assert start.message == formatting.prompt_symbol()
    assert repo.conversations[1].state == ConversationStep.SYMBOL

    result = await service.handle_input(1, " hype ")

    assert result is not None
    assert result.message == formatting.prompt_direction()
    assert repo.conversations[1].state == ConversationStep.DIRECTION
    assert repo.universe == ({"BTC", "ETH", "HYPE", "AUDUSD"}, clock.current)
    assert fetcher.calls == 1

    trade = await _complete_trade_after_symbol(service)

    assert trade.symbol == "HYPE"
    assert repo.conversations == {}


@pytest.mark.asyncio
async def test_form_symbol_typo_stays_on_symbol_without_trade() -> None:
    """R1/R2: unknown symbols are rejected without advancing the form."""

    repo = InMemorySymbolFormRepo()
    now = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    repo.universe = ({"BTC", "ETH"}, now)
    service = TradeFormService(
        repo=repo,
        settings=_settings(),
        universe_fetcher=FakeUniverseFetcher(["BTC", "ETH"]),
        now_fn=lambda: now,
    )

    await service.start(1)
    result = await service.handle_input(1, "notreal")

    assert result is not None
    assert result.message == formatting.symbol_unknown("NOTREAL")
    assert repo.conversations[1].state == ConversationStep.SYMBOL
    assert repo.trades == {}


@pytest.mark.asyncio
async def test_form_symbol_unavailable_without_cache_stays_on_symbol() -> None:
    """R2: no cache plus fetch failure rejects without creating a trade."""

    repo = InMemorySymbolFormRepo()
    now = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    service = TradeFormService(
        repo=repo,
        settings=_settings(),
        universe_fetcher=FakeUniverseFetcher(error=RuntimeError("down")),
        now_fn=lambda: now,
    )

    await service.start(1)
    result = await service.handle_input(1, "BTC")

    assert result is not None
    assert result.message == formatting.symbol_universe_unavailable()
    assert repo.conversations[1].state == ConversationStep.SYMBOL
    assert repo.trades == {}


@pytest.mark.asyncio
async def test_form_symbol_accepts_stale_cache_when_refresh_fails() -> None:
    """R2: refresh failure degrades to cached universe when one exists."""

    repo = InMemorySymbolFormRepo()
    now = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    repo.universe = ({"BTC", "ETH"}, now - timedelta(hours=1))
    fetcher = FakeUniverseFetcher(error=RuntimeError("down"))
    service = TradeFormService(
        repo=repo,
        settings=_settings(),
        universe_fetcher=fetcher,
        now_fn=lambda: now,
    )

    await service.start(1)
    result = await service.handle_input(1, "BTC")

    assert result is not None
    assert result.message == formatting.prompt_direction()
    assert repo.conversations[1].state == ConversationStep.DIRECTION
    assert fetcher.calls == 1
