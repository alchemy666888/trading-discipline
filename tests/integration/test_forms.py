"""Integration tests for the `/new` form state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from src.bot.forms import TradeFormService
from src.config import Settings
from src.models.conversation import ConversationState, ConversationStep
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus


@dataclass
class MutableClock:
    """Mutable wall clock for deterministic timeout tests."""

    current: datetime

    def now(self) -> datetime:
        return self.current


class InMemoryFormRepo:
    """Minimal repo surface used by the form service tests."""

    def __init__(self) -> None:
        self.conversations: dict[int, ConversationState] = {}
        self.trades: dict[int, Trade] = {}
        self.universe: tuple[set[str], datetime] | None = (
            {"BTC", "ETH", "HYPE", "AUDUSD"},
            datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        )
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
            "leverage_block_threshold": 20,
            "consecutive_loss_threshold": 2,
            "size_reduction_factor": 0.5,
        }
    )


def _closed_trade(
    trade_id: int,
    *,
    opened_at: datetime,
    closed_at: datetime,
    size_usdt: float,
    realized_pnl: float,
) -> Trade:
    close_price = 81000.0 if realized_pnl < 0 else 83000.0
    return Trade(
        id=trade_id,
        symbol="BTC",
        direction=Direction.LONG,
        size_usdt=size_usdt,
        leverage=5,
        leverage_override_reason=None,
        entry_price=82000.0,
        invalidation_price=81000.0,
        max_loss_usdt=50.0,
        regime=Regime.RANGE,
        thesis=f"Closed trade {trade_id} fixture.",
        status=TradeStatus.CLOSED,
        size_reduction_enforced=False,
        opened_at=opened_at,
        closed_at=closed_at,
        close_price=close_price,
        realized_pnl=realized_pnl,
    )


@pytest.mark.asyncio
async def test_form_happy_path_creates_trade_and_clears_state() -> None:
    """REQ-001: the thesis step commits the trade immediately and clears form state."""

    repo = InMemoryFormRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    service = TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now)

    start = await service.start(1)
    assert start.message == "Symbol? (e.g. BTC, ETH, HYPE, AUDUSD)"
    assert repo.conversations[1].state == ConversationStep.SYMBOL

    await service.handle_input(1, "BTC")
    assert repo.conversations[1].state == ConversationStep.DIRECTION
    await service.handle_input(1, "long")
    assert repo.conversations[1].state == ConversationStep.SIZE
    await service.handle_input(1, "5000")
    assert repo.conversations[1].state == ConversationStep.LEVERAGE
    await service.handle_input(1, "10")
    await service.handle_input(1, "82500")
    await service.handle_input(1, "81200")
    await service.handle_input(1, "160")
    await service.handle_input(1, "uptrend")
    result = await service.handle_input(
        1,
        "Holding above 82K with strong ETF inflows.",
    )

    assert result is not None
    assert result.created_trade is not None
    assert result.created_trade.status == TradeStatus.OPEN
    assert repo.conversations == {}
    assert len(repo.trades) == 1


@pytest.mark.asyncio
async def test_form_high_leverage_override_reason_is_persisted() -> None:
    """REQ-002: leverage at the threshold requires and stores an override reason."""

    repo = InMemoryFormRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    service = TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now)

    await service.start(1)
    await service.handle_input(1, "BTC")
    await service.handle_input(1, "short")
    await service.handle_input(1, "1000")
    result = await service.handle_input(1, "20")
    assert result is not None
    assert "Warning:" in result.message
    assert repo.conversations[1].state == ConversationStep.LEV_OVERRIDE

    await service.handle_input(1, "Defined event with tight stop.")
    await service.handle_input(1, "82500")
    await service.handle_input(1, "83800")
    await service.handle_input(1, "75")
    await service.handle_input(1, "event_risk")
    final = await service.handle_input(
        1,
        "Event-risk short with defined invalidation.",
    )

    assert final is not None
    assert final.created_trade is not None
    assert final.created_trade.leverage_override_reason == (
        "Defined event with tight stop."
    )


@pytest.mark.asyncio
async def test_form_cancel_during_override_returns_to_idle_without_trade() -> None:
    """REQ-002: /cancel during the override step exits the whole form to IDLE."""

    repo = InMemoryFormRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    service = TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now)

    await service.start(1)
    await service.handle_input(1, "BTC")
    await service.handle_input(1, "long")
    await service.handle_input(1, "900")
    await service.handle_input(1, "20")
    result = await service.cancel(1)

    assert result.message == "Form cancelled."
    assert repo.conversations == {}
    assert repo.trades == {}


@pytest.mark.asyncio
async def test_form_timeout_drops_state_without_creating_trade() -> None:
    """REQ-001: an abandoned form expires after FORM_TIMEOUT_SECONDS with no trade."""

    repo = InMemoryFormRepo()
    clock = MutableClock(current=datetime(2026, 5, 17, 9, 0, tzinfo=UTC))
    service = TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now)

    await service.start(1)
    clock.current += timedelta(seconds=601)
    result = await service.handle_input(1, "BTC")

    assert result is not None
    assert "Form expired after 600 seconds" in result.message
    assert repo.conversations == {}
    assert repo.trades == {}


@pytest.mark.asyncio
async def test_form_rejects_size_above_active_cap() -> None:
    """REQ-003: size input above the active loss-streak cap is rejected."""

    repo = InMemoryFormRepo()
    now = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    repo.trades[1] = _closed_trade(
        1,
        opened_at=now - timedelta(days=3),
        closed_at=now - timedelta(days=3, minutes=-5),
        size_usdt=1000.0,
        realized_pnl=-50.0,
    )
    repo.trades[2] = _closed_trade(
        2,
        opened_at=now - timedelta(days=2),
        closed_at=now - timedelta(days=2, minutes=-5),
        size_usdt=800.0,
        realized_pnl=-25.0,
    )
    clock = MutableClock(current=now)
    service = TradeFormService(repo=repo, settings=_settings(), now_fn=clock.now)

    await service.start(1)
    await service.handle_input(1, "BTC")
    await service.handle_input(1, "long")
    result = await service.handle_input(1, "600")

    assert result is not None
    assert "active size cap" in result.message
    assert repo.conversations[1].state == ConversationStep.SIZE
