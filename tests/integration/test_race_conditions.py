"""Integration tests for race-safe breach and close transitions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from src.db.repo import RedisRepository
from src.models.trade import Direction, Regime, TradeDraft, TradeStatus


@pytest.mark.asyncio
async def test_close_and_breach_race_keeps_state_consistent(
    redis_repo: RedisRepository,
) -> None:
    """TASK-023: concurrent close/breach transitions leave no inconsistent state."""

    trade = await redis_repo.create_trade(
        TradeDraft(
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=82000.0,
            invalidation_price=81000.0,
            max_loss_usdt=50.0,
            regime=Regime.RANGE,
            thesis="Race-condition fixture trade.",
        ),
        opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )

    close_task = asyncio.create_task(
        redis_repo.close_trade(
            trade.id,
            close_price=80990.0,
            closed_at=datetime(2026, 5, 17, 9, 1, tzinfo=UTC),
        )
    )
    breach_task = asyncio.create_task(
        redis_repo.create_breach(
            trade.id,
            breach_price=80990.0,
            detected_at=datetime(2026, 5, 17, 9, 1, tzinfo=UTC),
        )
    )
    closed_trade, breach = await asyncio.gather(close_task, breach_task)

    refreshed_trade = await redis_repo.get_trade(trade.id)
    open_breach = await redis_repo.get_open_breach(trade.id)

    assert refreshed_trade is not None
    if refreshed_trade.status == TradeStatus.CLOSED:
        assert open_breach is None
    if breach is not None and closed_trade is not None:
        resolved_breach = await redis_repo.get_breach(breach.id)
        assert resolved_breach is not None
        assert resolved_breach.user_response is not None
