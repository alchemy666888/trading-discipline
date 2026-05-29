"""Integration tests for the Redis repository lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis

from src.db.repo import RedisRepository
from src.models.breach import BreachUserResponse
from src.models.conversation import ConversationState, ConversationStep
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus


def _now() -> datetime:
    return datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


async def test_repo_full_lifecycle_open_breach_override_breach_close(
    redis_repo: RedisRepository,
) -> None:
    """REQ-001 / REQ-005 / REQ-010 / REQ-011: repo supports the full trade lifecycle."""

    draft = TradeDraft(
        symbol="BTC",
        direction=Direction.LONG,
        size_usdt=5000.0,
        leverage=20,
        leverage_override_reason="Tight stop, defined event, small size.",
        entry_price=82500.0,
        invalidation_price=81200.0,
        max_loss_usdt=160.0,
        regime=Regime.UPTREND,
        thesis="thesis with weird redis text trade:{1} seq:trade_id and newlines\nok",
        size_reduction_enforced=True,
    )

    trade = await redis_repo.create_trade(draft, opened_at=_now())
    open_trades = await redis_repo.list_open_trades()

    assert trade.status == TradeStatus.OPEN
    assert open_trades == [trade]
    assert trade.size_reduction_enforced is True
    assert trade.leverage_override_reason == draft.leverage_override_reason

    breach_1 = await redis_repo.create_breach(
        trade.id,
        breach_price=81199.0,
        detected_at=_now() + timedelta(minutes=1),
    )
    assert breach_1 is not None
    assert await redis_repo.get_open_breach(trade.id) == breach_1

    duplicate_breach = await redis_repo.create_breach(
        trade.id,
        breach_price=81190.0,
        detected_at=_now() + timedelta(minutes=2),
    )
    assert duplicate_breach is None

    alert = await redis_repo.record_alert(
        breach_1.id,
        sent_at=_now() + timedelta(minutes=1),
        escalation_level=0,
        message="Close now or justify the breach.",
    )
    assert alert.breach_id == breach_1.id

    overridden_trade = await redis_repo.mark_override(
        trade.id,
        breach_id=breach_1.id,
        justification="Price reclaim setup after first invalidation sweep.",
        response_at=_now() + timedelta(minutes=2),
    )
    assert overridden_trade is not None
    assert overridden_trade.status == TradeStatus.OPEN_OVERRIDE

    resolved_breach_1 = await redis_repo.get_breach(breach_1.id)
    assert resolved_breach_1 is not None
    assert resolved_breach_1.user_response == BreachUserResponse.JUSTIFIED
    assert resolved_breach_1.justification == (
        "Price reclaim setup after first invalidation sweep."
    )
    assert await redis_repo.get_open_breach(trade.id) is None

    breach_2 = await redis_repo.create_breach(
        trade.id,
        breach_price=81050.0,
        detected_at=_now() + timedelta(minutes=5),
    )
    assert breach_2 is not None

    closed_trade = await redis_repo.close_trade(
        trade.id,
        close_price=81000.0,
        closed_at=_now() + timedelta(minutes=6),
        breach_id=breach_2.id,
        response_at=_now() + timedelta(minutes=6),
    )
    assert closed_trade is not None
    assert closed_trade.status == TradeStatus.CLOSED
    assert closed_trade.close_price == 81000.0
    assert closed_trade.realized_pnl is not None
    assert closed_trade.realized_pnl < 0

    resolved_breach_2 = await redis_repo.get_breach(breach_2.id)
    assert resolved_breach_2 is not None
    assert resolved_breach_2.user_response == BreachUserResponse.CLOSED
    assert await redis_repo.get_open_breach(trade.id) is None
    assert await redis_repo.list_open_trades() == []
    assert await redis_repo.recent_closed_trades(1) == [closed_trade]
    assert await redis_repo.consecutive_loss_count() == 1


async def test_repo_stores_user_text_only_as_hash_values(
    redis_repo: RedisRepository,
    redis_client: Redis,
) -> None:
    """NFR-security: user text with Redis-like content stays in values, not keys."""

    suspicious_text = "seq:trade_id trade:99 breach:1\n{inject}"
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.SHORT,
            size_usdt=1200.0,
            leverage=5,
            entry_price=82500.0,
            invalidation_price=83100.0,
            max_loss_usdt=60.0,
            regime=Regime.RANGE,
            thesis=suspicious_text,
        ),
        opened_at=_now(),
    )

    stored_trade = await redis_repo.get_trade(trade.id)
    keys = {
        key.decode("utf-8") if isinstance(key, bytes) else key
        for key in await redis_client.keys("*")
    }

    assert stored_trade is not None
    assert stored_trade.thesis == suspicious_text
    assert suspicious_text not in keys
    assert all(suspicious_text not in key for key in keys)


async def test_repo_supports_conversation_state_and_empty_signal_stub_surface(
    redis_repo: RedisRepository,
) -> None:
    """REQ-001 / REQ-010: conversation state works and `signals:*` stays empty."""

    state = ConversationState(
        chat_id=42,
        state=ConversationStep.THESIS,
        partial_trade_json='{"direction":"long","size_usdt":5000}',
        updated_at=_now(),
    )
    await redis_repo.set_conversation_state(state, ttl_seconds=30)

    loaded_state = await redis_repo.get_conversation_state(42)
    assert loaded_state == state

    await redis_repo.clear_conversation_state(42)
    assert await redis_repo.get_conversation_state(42) is None
    assert await redis_repo.list_active_signals() == []


async def test_repo_resolve_breach_without_trade_transition(
    redis_repo: RedisRepository,
) -> None:
    """REQ-005: breach resolution can clear the active breach without closing."""

    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=2500.0,
            leverage=3,
            entry_price=82000.0,
            invalidation_price=81000.0,
            max_loss_usdt=80.0,
            regime=Regime.UPTREND,
            thesis="Long thesis that is definitely longer than ten characters.",
        ),
        opened_at=_now(),
    )
    breach = await redis_repo.create_breach(
        trade.id,
        breach_price=80950.0,
        detected_at=_now() + timedelta(minutes=1),
    )
    assert breach is not None

    resolved = await redis_repo.resolve_breach(
        breach.id,
        user_response=BreachUserResponse.NO_RESPONSE,
        response_at=_now() + timedelta(minutes=2),
    )
    stored_trade = await redis_repo.get_trade(trade.id)

    assert resolved is not None
    assert resolved.user_response == BreachUserResponse.NO_RESPONSE
    assert await redis_repo.get_open_breach(trade.id) is None
    assert stored_trade is not None
    assert stored_trade.status == TradeStatus.OPEN


async def test_repo_consecutive_loss_count_ignores_breakeven(
    redis_repo: RedisRepository,
) -> None:
    """REQ-003: repository loss-streak calculation ignores breakeven trades."""

    losing_trade_1 = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=2,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.RANGE,
            thesis="First loss thesis is sufficiently descriptive.",
        ),
        opened_at=_now(),
    )
    await redis_repo.close_trade(
        losing_trade_1.id,
        close_price=79000.0,
        closed_at=_now() + timedelta(minutes=1),
    )

    breakeven_trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=2,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.RANGE,
            thesis="Breakeven thesis is also sufficiently descriptive.",
        ),
        opened_at=_now() + timedelta(minutes=2),
    )
    await redis_repo.close_trade(
        breakeven_trade.id,
        close_price=80000.0,
        closed_at=_now() + timedelta(minutes=3),
    )

    losing_trade_2 = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=2,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.RANGE,
            thesis="Second loss thesis is sufficiently descriptive.",
        ),
        opened_at=_now() + timedelta(minutes=4),
    )
    await redis_repo.close_trade(
        losing_trade_2.id,
        close_price=79000.0,
        closed_at=_now() + timedelta(minutes=5),
    )

    assert await redis_repo.consecutive_loss_count() == 2

    stored_trade = await redis_repo.get_trade(losing_trade_2.id)
    assert stored_trade is not None


async def test_update_trade_nonexistent_returns_error(
    redis_repo: RedisRepository,
) -> None:
    """REQ-1.3: updating non-existent trade returns error with 'not found' message."""

    with pytest.raises(ValueError) as exc_info:
        await redis_repo.update_trade(99999, {"size_usdt": 1000.0})

    assert "not found" in str(exc_info.value).lower()


async def test_update_trade_closed_returns_error(
    redis_repo: RedisRepository,
) -> None:
    """REQ-1.5: updating closed trade returns error with 'not open' message."""

    # Create and close a trade
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Closed trade thesis is sufficiently descriptive.",
        ),
        opened_at=_now(),
    )
    await redis_repo.close_trade(
        trade.id,
        close_price=79000.0,
        closed_at=_now() + timedelta(minutes=1),
    )

    # Attempt to update closed trade should fail
    with pytest.raises(ValueError) as exc_info:
        await redis_repo.update_trade(trade.id, {"size_usdt": 2000.0})

    assert "not open" in str(exc_info.value).lower()


async def test_update_trade_only_editable_fields_modified(
    redis_repo: RedisRepository,
) -> None:
    """REQ-1.7: updating trade only modifies specified editable fields."""

    # Create a trade
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Original thesis for edit test is sufficiently descriptive.",
        ),
        opened_at=_now(),
    )

    # Update only size_usdt and regime
    updated = await redis_repo.update_trade(
        trade.id,
        {
            "size_usdt": 2000.0,
            "regime": "downtrend",
        },
    )

    # Check that only the specified fields changed
    assert updated.size_usdt == 2000.0
    assert updated.regime == Regime.DOWNTREND

    # Check that other fields preserved their values
    assert updated.direction == Direction.LONG
    assert updated.leverage == 5
    assert updated.entry_price == 80000.0
    assert updated.invalidation_price == 79000.0
    assert updated.max_loss_usdt == 20.0
    assert (
        updated.thesis == "Original thesis for edit test is sufficiently descriptive."
    )


async def test_update_trade_non_editable_fields_preserved(
    redis_repo: RedisRepository,
) -> None:
    """REQ-2.9: non-editable fields are preserved after update."""

    # Create a trade
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Thesis for non-editable fields test is descriptive.",
        ),
        opened_at=_now(),
    )

    original_id = trade.id
    original_opened_at = trade.opened_at
    original_status = trade.status
    original_size_reduction_enforced = trade.size_reduction_enforced

    # Update some editable fields
    await redis_repo.update_trade(
        trade.id,
        {
            "size_usdt": 2000.0,
            "thesis": "Updated thesis for testing non-editable fields preservation.",
        },
    )

    # Reload and verify non-editable fields unchanged
    updated = await redis_repo.get_trade(trade.id)
    assert updated is not None

    assert updated.id == original_id
    assert updated.opened_at == original_opened_at
    assert updated.status == original_status
    assert updated.size_reduction_enforced == original_size_reduction_enforced
    # These should still be None for open trade
    assert updated.closed_at is None
    assert updated.close_price is None
    assert updated.realized_pnl is None


async def test_update_trade_leverage_reduction_clears_override_reason(
    redis_repo: RedisRepository,
) -> None:
    """REQ-3.4: reducing leverage below 20 clears the override reason."""

    # Create a trade with high leverage and override reason
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=25,
            leverage_override_reason="Tight stop around news event.",
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Testing leverage reduction clears override reason.",
        ),
        opened_at=_now(),
    )

    # Verify override reason exists
    assert trade.leverage_override_reason == "Tight stop around news event."

    # Reduce leverage below 20
    updated = await redis_repo.update_trade(
        trade.id,
        {
            "leverage": 10,
        },
    )

    # Override reason should be cleared
    assert updated.leverage == 10
    assert updated.leverage_override_reason is None


async def test_update_trade_leverage_unchanged_preserves_override_reason(
    redis_repo: RedisRepository,
) -> None:
    """REQ-3.3: unchanged leverage preserves the override reason."""

    # Create a trade with high leverage and override reason
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=30,
            leverage_override_reason="Major support level with tight stop.",
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Testing leverage unchanged preserves override reason.",
        ),
        opened_at=_now(),
    )

    # Verify override reason exists
    assert trade.leverage_override_reason == "Major support level with tight stop."

    # Update something without changing leverage
    updated = await redis_repo.update_trade(
        trade.id,
        {
            "thesis": "Updated thesis text.",
        },
    )

    # Override reason should be preserved
    assert updated.leverage == 30
    assert updated.leverage_override_reason == "Major support level with tight stop."
    assert updated.thesis == "Updated thesis text."


async def test_update_trade_high_leverage_without_override_reason_fails(
    redis_repo: RedisRepository,
) -> None:
    """REQ-3.1: high leverage without an override reason fails."""

    # Create a trade with low leverage
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=10,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Testing high leverage without override reason fails.",
        ),
        opened_at=_now(),
    )

    # Attempt to increase leverage to >=20 without override reason should fail
    with pytest.raises(ValueError) as exc_info:
        await redis_repo.update_trade(
            trade.id,
            {
                "leverage": 25,
            },
        )

    assert "leverage_override_reason" in str(exc_info.value).lower()


async def test_update_trade_high_leverage_with_override_reason_succeeds(
    redis_repo: RedisRepository,
) -> None:
    """REQ-3.1: high leverage with a valid override reason succeeds."""

    # Create a trade with low leverage
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=10,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Testing high leverage with override reason succeeds.",
        ),
        opened_at=_now(),
    )

    # Increase leverage to >=20 with valid override reason
    updated = await redis_repo.update_trade(
        trade.id,
        {
            "leverage": 30,
            "leverage_override_reason": "Strong momentum with tight stop.",
        },
    )

    assert updated.leverage == 30
    assert updated.leverage_override_reason == "Strong momentum with tight stop."


async def test_update_closed_trade_only_named_fields_change(
    redis_repo: RedisRepository,
) -> None:
    """R2.9 / R3.4: closed edits preserve unspecified and non-editable fields."""

    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Closed trade update preserves unrelated fields.",
        ),
        opened_at=_now(),
    )
    closed = await redis_repo.close_trade(
        trade.id,
        close_price=80500.0,
        closed_at=_now() + timedelta(minutes=1),
    )
    assert closed is not None

    updated = await redis_repo.update_closed_trade(
        trade.id,
        updates={
            "regime": Regime.RANGE,
            "thesis": "Closed trade thesis updated after review.",
            "id": 999,
            "status": TradeStatus.OPEN,
            "realized_pnl": 5000.0,
        },
        recomputed_pnl=None,
    )

    assert updated is not None
    assert updated.id == trade.id
    assert updated.status == TradeStatus.CLOSED
    assert updated.regime == Regime.RANGE
    assert updated.thesis == "Closed trade thesis updated after review."
    assert updated.realized_pnl == closed.realized_pnl
    assert updated.size_usdt == closed.size_usdt
    assert updated.close_price == closed.close_price


async def test_update_closed_trade_recomputed_pnl_is_written(
    redis_repo: RedisRepository,
) -> None:
    """R4.1 / R4.3: repository writes the recomputed realized P&L when supplied."""

    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Closed trade recomputed pnl update fixture.",
        ),
        opened_at=_now(),
    )
    await redis_repo.close_trade(
        trade.id,
        close_price=80500.0,
        closed_at=_now() + timedelta(minutes=1),
    )

    updated = await redis_repo.update_closed_trade(
        trade.id,
        updates={"close_price": 81000.0},
        recomputed_pnl=12.5,
    )

    assert updated is not None
    assert updated.close_price == 81000.0
    assert updated.realized_pnl == 12.5


async def test_update_closed_trade_open_trade_returns_none(
    redis_repo: RedisRepository,
) -> None:
    """R5.5: closed edit aborts if the trade is no longer CLOSED."""

    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=5,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis="Open trade cannot be updated with closed update.",
        ),
        opened_at=_now(),
    )

    assert (
        await redis_repo.update_closed_trade(
            trade.id,
            updates={"regime": Regime.RANGE},
            recomputed_pnl=None,
        )
        is None
    )


async def test_update_closed_trade_reindexes_closed_order(
    redis_repo: RedisRepository,
) -> None:
    """Index integrity: closed_at edits reorder `trades:closed` consumers."""

    first = await _closed_repo_trade(redis_repo, trade_id_offset=0)
    second = await _closed_repo_trade(redis_repo, trade_id_offset=10)

    assert [trade.id for trade in await redis_repo.list_closed_trades()] == [
        second.id,
        first.id,
    ]

    updated = await redis_repo.update_closed_trade(
        first.id,
        updates={"closed_at": _now() + timedelta(minutes=30)},
        recomputed_pnl=None,
    )

    assert updated is not None
    assert [trade.id for trade in await redis_repo.list_closed_trades()] == [
        first.id,
        second.id,
    ]


async def test_update_closed_trade_reindexes_opened_order(
    redis_repo: RedisRepository,
) -> None:
    """Index integrity: opened_at edits reorder `trades:all` consumers."""

    first = await _closed_repo_trade(redis_repo, trade_id_offset=0)
    second = await _closed_repo_trade(redis_repo, trade_id_offset=10)

    assert [trade.id for trade in await redis_repo.list_all_trades()] == [
        first.id,
        second.id,
    ]

    updated = await redis_repo.update_closed_trade(
        second.id,
        updates={
            "opened_at": _now() - timedelta(minutes=30),
            "closed_at": second.closed_at,
        },
        recomputed_pnl=None,
    )

    assert updated is not None
    assert [trade.id for trade in await redis_repo.list_all_trades()] == [
        second.id,
        first.id,
    ]


async def _closed_repo_trade(
    redis_repo: RedisRepository,
    *,
    trade_id_offset: int,
) -> Trade:
    trade = await redis_repo.create_trade(
        TradeDraft(
            symbol="BTC",
            direction=Direction.LONG,
            size_usdt=1000.0 + trade_id_offset,
            leverage=5,
            entry_price=80000.0,
            invalidation_price=79000.0,
            max_loss_usdt=20.0,
            regime=Regime.UPTREND,
            thesis=f"Closed repo trade fixture {trade_id_offset}.",
        ),
        opened_at=_now() + timedelta(minutes=trade_id_offset),
    )
    closed = await redis_repo.close_trade(
        trade.id,
        close_price=80500.0,
        closed_at=_now() + timedelta(minutes=trade_id_offset + 1),
    )
    if closed is None:
        msg = "Fixture trade failed to close."
        raise AssertionError(msg)
    return closed
