"""Unit tests for the closed-trade edit service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from src.bot.edit_closed import ClosedTradeEditService, FieldValidationError
from src.config import Settings
from src.models.conversation import ConversationState, ConversationStep
from src.models.trade import Direction, Regime, Trade, TradeStatus


@dataclass
class MutableClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


class InMemoryEditClosedRepo:
    def __init__(self) -> None:
        self.conversations: dict[int, ConversationState] = {}
        self.trades: dict[int, Trade] = {}
        self.update_calls: list[tuple[int, dict[str, object], float | None]] = []

    async def get_trade(self, trade_id: int) -> Trade | None:
        return self.trades.get(trade_id)

    async def list_closed_trades(self, limit: int | None = None) -> list[Trade]:
        trades = [
            trade
            for trade in self.trades.values()
            if trade.status == TradeStatus.CLOSED
        ]
        trades.sort(key=lambda trade: trade.closed_at or trade.opened_at, reverse=True)
        if limit is None:
            return trades
        return trades[:limit]

    async def get_conversation_state(self, chat_id: int) -> ConversationState | None:
        return self.conversations.get(chat_id)

    async def set_conversation_state(
        self,
        state: ConversationState,
        *,
        ttl_seconds: int | None = None,
    ) -> ConversationState:
        self.conversations[chat_id := state.chat_id] = state
        return self.conversations[chat_id]

    async def clear_conversation_state(self, chat_id: int) -> None:
        self.conversations.pop(chat_id, None)

    async def update_closed_trade(
        self,
        trade_id: int,
        *,
        updates: dict[str, object],
        recomputed_pnl: float | None,
    ) -> Trade | None:
        self.update_calls.append((trade_id, updates, recomputed_pnl))
        trade = self.trades.get(trade_id)
        if trade is None or trade.status != TradeStatus.CLOSED:
            return None
        payload = trade.model_dump()
        payload.update(updates)
        if recomputed_pnl is not None:
            payload["realized_pnl"] = recomputed_pnl
        updated = Trade.model_validate(payload)
        self.trades[trade_id] = updated
        return updated


def test_preview_preserves_unspecified_fields_and_clears_low_leverage_reason() -> None:
    """R2.9 / R3.3: preview preserves unspecified fields and clears low leverage."""

    service = _service(InMemoryEditClosedRepo())
    current = _closed_trade(
        leverage=25,
        leverage_override_reason="Defined event with very tight invalidation.",
    )

    preview = service._preview_trade(current, {"leverage": "10"})

    assert preview.leverage == 10
    assert preview.leverage_override_reason is None
    assert preview.direction == current.direction
    assert preview.size_usdt == current.size_usdt
    assert preview.realized_pnl == current.realized_pnl


@pytest.mark.asyncio
async def test_prepare_rejects_non_editable_field_without_state() -> None:
    """R2.2: non-editable fields are rejected before a pending edit is stored."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade()
    service = _service(repo)

    result = await service.prepare(1, 1, {"realized_pnl": "12"})

    assert "realized_pnl" in result.message
    assert repo.conversations == {}


@pytest.mark.asyncio
async def test_prepare_reports_missing_trade_without_state() -> None:
    """R1.4: a missing trade id returns not-found and stores no pending edit."""

    repo = InMemoryEditClosedRepo()
    service = _service(repo)

    result = await service.prepare(1, 404, {"regime": "range"})

    assert result.message == "Trade 404 not found."
    assert repo.conversations == {}


@pytest.mark.asyncio
async def test_prepare_rejects_open_trade_with_edit_suggestion() -> None:
    """R1.5: non-closed trades are rejected with a `/edit` suggestion."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade().model_copy(
        update={
            "status": TradeStatus.OPEN,
            "closed_at": None,
            "close_price": None,
            "realized_pnl": None,
        }
    )
    service = _service(repo)

    result = await service.prepare(1, 1, {"regime": "range"})

    assert result.message == "Trade 1 is not closed. Use /edit for open trades."
    assert repo.conversations == {}


@pytest.mark.parametrize(
    ("updates", "expect_recomputed"),
    [
        ({"size_usdt": "1500"}, True),
        ({"entry_price": "80500"}, True),
        ({"close_price": "81000"}, True),
        ({"regime": "downtrend"}, False),
    ],
)
@pytest.mark.asyncio
async def test_prepare_recomputes_pnl_only_for_pnl_fields(
    updates: dict[str, str],
    expect_recomputed: bool,
) -> None:
    """R4.1 / R4.2: recomputation fires only for P&L-determining fields."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade()
    service = _service(repo)

    await service.prepare(1, 1, updates)
    payload = json.loads(repo.conversations[1].partial_trade_json)

    assert (payload["recomputed_pnl"] is not None) is expect_recomputed


@pytest.mark.asyncio
async def test_prepare_recomputes_pnl_for_direction_edit() -> None:
    """R4.1: direction edits trigger realized-P&L recomputation."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade(
        direction=Direction.SHORT,
        invalidation_price=81000.0,
        close_price=79000.0,
        realized_pnl=12.5,
    )
    service = _service(repo)

    await service.prepare(
        1,
        1,
        {"direction": "long", "invalidation_price": "79000"},
    )
    payload = json.loads(repo.conversations[1].partial_trade_json)

    assert payload["recomputed_pnl"] is not None


@pytest.mark.asyncio
async def test_prepare_warns_when_recompute_overwrites_manual_pnl() -> None:
    """R4.3: a prior manual P&L override is flagged in the preview."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade(realized_pnl=99.0)
    service = _service(repo)

    result = await service.prepare(1, 1, {"close_price": "81000"})

    assert "overwrites a prior /setpnl" in result.message
    assert "Use /setpnl" in result.message


@pytest.mark.parametrize(
    ("trade_kwargs", "updates", "expected"),
    [
        ({}, {"invalidation_price": "81000"}, "invalidation_price"),
        (
            {"direction": Direction.SHORT, "invalidation_price": 81000.0},
            {"invalidation_price": "79000"},
            "invalidation_price",
        ),
        ({}, {"leverage": "20"}, "leverage_override_reason"),
        (
            {},
            {"closed_at": "2026-05-17T11:59:00+00:00"},
            "closed_at",
        ),
    ],
)
@pytest.mark.asyncio
async def test_prepare_rejects_closed_trade_invariants(
    trade_kwargs: dict[str, object],
    updates: dict[str, str],
    expected: str,
) -> None:
    """R3: invalid closed-trade previews are rejected before state is stored."""

    repo = InMemoryEditClosedRepo()
    trade = _closed_trade(**trade_kwargs)
    repo.trades[trade.id] = trade
    service = _service(repo)

    result = await service.prepare(1, trade.id, updates)

    assert expected in result.message
    assert repo.conversations == {}


@pytest.mark.asyncio
async def test_prepare_refuses_when_conversation_slot_is_active() -> None:
    """R5.4: an active `/new` form blocks preparing a closed-trade edit."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade()
    clock = MutableClock(datetime(2026, 5, 17, 12, 0, tzinfo=UTC))
    repo.conversations[1] = ConversationState(
        chat_id=1,
        state=ConversationStep.DIRECTION,
        partial_trade_json="{}",
        updated_at=clock.now(),
    )
    service = _service(repo, clock=clock)

    result = await service.prepare(1, 1, {"regime": "range"})

    assert result.message == "Form already in progress, /cancel first."


@pytest.mark.asyncio
async def test_resolve_decline_clears_pending_edit_without_write() -> None:
    """R5.6: a non-yes reply discards the pending edit."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade()
    service = _service(repo)

    await service.prepare(1, 1, {"regime": "downtrend"})
    result = await service.resolve(1, "no")

    assert result is not None
    assert result.message == "Closed-trade edit cancelled."
    assert repo.conversations == {}
    assert repo.update_calls == []


@pytest.mark.asyncio
async def test_resolve_yes_applies_pending_edit_and_clears_state() -> None:
    """R5.5: yes applies the atomic closed-trade update."""

    repo = InMemoryEditClosedRepo()
    repo.trades[1] = _closed_trade()
    service = _service(repo)

    await service.prepare(1, 1, {"close_price": "81000"})
    result = await service.resolve(1, "yes")

    assert result is not None
    assert result.updated_trade is not None
    assert result.updated_trade.close_price == 81000.0
    assert result.updated_trade.realized_pnl is not None
    assert repo.conversations == {}
    assert repo.update_calls


def test_preview_rejects_naive_timestamp() -> None:
    """R2.8: timestamp edits must be timezone-aware."""

    service = _service(InMemoryEditClosedRepo())
    with pytest.raises(FieldValidationError, match="opened_at"):
        service._preview_trade(
            _closed_trade(),
            {"opened_at": "2026-05-17T12:00:00"},
        )


def _service(
    repo: InMemoryEditClosedRepo,
    *,
    clock: MutableClock | None = None,
) -> ClosedTradeEditService:
    clock = clock or MutableClock(datetime(2026, 5, 17, 12, 0, tzinfo=UTC))
    return ClosedTradeEditService(
        repo=repo,  # type: ignore[arg-type]
        settings=_settings(),
        now_fn=clock.now,
    )


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
    *,
    direction: Direction = Direction.LONG,
    leverage: int = 5,
    leverage_override_reason: str | None = None,
    invalidation_price: float = 79000.0,
    close_price: float = 80500.0,
    realized_pnl: float = 6.25,
) -> Trade:
    opened_at = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    return Trade(
        id=1,
        direction=direction,
        size_usdt=1000.0,
        leverage=leverage,
        leverage_override_reason=leverage_override_reason,
        entry_price=80000.0,
        invalidation_price=invalidation_price,
        max_loss_usdt=20.0,
        regime=Regime.UPTREND,
        thesis="Closed trade edit service fixture.",
        status=TradeStatus.CLOSED,
        size_reduction_enforced=False,
        opened_at=opened_at,
        closed_at=opened_at + timedelta(minutes=5),
        close_price=close_price,
        realized_pnl=realized_pnl,
    )
