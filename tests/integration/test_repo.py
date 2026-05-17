"""Integration tests for the Redis repository lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from src.db.repo import RedisRepository
from src.models.breach import BreachUserResponse
from src.models.conversation import ConversationState, ConversationStep
from src.models.trade import Direction, Regime, TradeDraft, TradeStatus


def _now() -> datetime:
    return datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


async def test_repo_full_lifecycle_open_breach_override_breach_close(
    redis_repo: RedisRepository,
) -> None:
    """REQ-001 / REQ-005 / REQ-010 / REQ-011: repo supports the full trade lifecycle."""

    draft = TradeDraft(
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
