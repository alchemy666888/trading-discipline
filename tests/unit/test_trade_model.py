"""Unit tests for the multi-asset trade model contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from src.db.repo import RedisRepository
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus


def _opened_at() -> datetime:
    return datetime(2026, 5, 17, 9, 0, tzinfo=UTC)


def _trade() -> Trade:
    return Trade(
        id=1,
        symbol="BTC",
        direction=Direction.LONG,
        size_usdt=5000.0,
        leverage=5,
        leverage_override_reason=None,
        entry_price=43280.0,
        invalidation_price=43100.0,
        max_loss_usdt=50.0,
        regime=Regime.UPTREND,
        thesis="Continuation trade with invalidation below the prior base.",
        status=TradeStatus.OPEN,
        size_reduction_enforced=False,
        opened_at=_opened_at(),
        closed_at=None,
        close_price=None,
        realized_pnl=None,
    )


def test_trade_symbol_round_trips_through_redis_serializer() -> None:
    """R1: persisted trade records preserve their immutable symbol field."""

    repo = RedisRepository(cast(Any, object()))
    trade = _trade()

    stored = repo._serialize_trade(trade)
    restored = repo._deserialize_trade(stored)

    assert stored["symbol"] == "BTC"
    assert restored == trade
    assert restored.symbol == "BTC"


def test_trade_requires_symbol() -> None:
    """R1: committed trades cannot be constructed without a symbol."""

    payload = _trade().model_dump()
    payload.pop("symbol")

    with pytest.raises(ValidationError):
        Trade.model_validate(payload)


def test_trade_and_draft_reject_empty_symbol() -> None:
    """R1: symbol fields require at least one character when present."""

    payload = _trade().model_dump()
    payload["symbol"] = ""

    with pytest.raises(ValidationError):
        Trade.model_validate(payload)

    with pytest.raises(ValidationError):
        TradeDraft(symbol="")


def test_trade_draft_symbol_is_optional_until_symbol_step_runs() -> None:
    """R1: drafts may exist before the form captures a symbol."""

    draft = TradeDraft()

    assert draft.symbol is None
