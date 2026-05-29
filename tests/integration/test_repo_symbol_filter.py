"""Integration tests for symbol-scoped closed-trade repository queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.db.repo import RedisRepository
from src.models.trade import Direction, Regime, Trade, TradeDraft


def _base_time() -> datetime:
    return datetime(2026, 5, 17, 9, 0, tzinfo=UTC)


async def _close_trade(
    repo: RedisRepository,
    *,
    symbol: str,
    minutes: int,
    close_price: float,
) -> Trade:
    opened_at = _base_time() + timedelta(minutes=minutes)
    trade = await repo.create_trade(
        TradeDraft(
            symbol=symbol,
            direction=Direction.LONG,
            size_usdt=1000.0,
            leverage=2,
            entry_price=100.0,
            invalidation_price=90.0,
            max_loss_usdt=20.0,
            regime=Regime.RANGE,
            thesis=f"{symbol} repository symbol filter integration trade.",
        ),
        opened_at=opened_at,
    )
    closed = await repo.close_trade(
        trade.id,
        close_price=close_price,
        closed_at=opened_at + timedelta(seconds=30),
    )
    assert closed is not None
    return closed


async def test_list_closed_trades_filters_by_symbol(
    redis_repo: RedisRepository,
) -> None:
    """R5: closed-trade history can be isolated per canonical symbol."""

    btc_win = await _close_trade(
        redis_repo,
        symbol="BTC",
        minutes=1,
        close_price=110.0,
    )
    eth_loss_1 = await _close_trade(
        redis_repo,
        symbol="ETH",
        minutes=2,
        close_price=90.0,
    )
    btc_loss_1 = await _close_trade(
        redis_repo,
        symbol="BTC",
        minutes=3,
        close_price=90.0,
    )
    eth_loss_2 = await _close_trade(
        redis_repo,
        symbol="ETH",
        minutes=4,
        close_price=90.0,
    )
    btc_loss_2 = await _close_trade(
        redis_repo,
        symbol="BTC",
        minutes=5,
        close_price=90.0,
    )

    assert [trade.id for trade in await redis_repo.list_closed_trades()] == [
        btc_loss_2.id,
        eth_loss_2.id,
        btc_loss_1.id,
        eth_loss_1.id,
        btc_win.id,
    ]
    assert [
        trade.id for trade in await redis_repo.list_closed_trades(symbol="BTC")
    ] == [
        btc_loss_2.id,
        btc_loss_1.id,
        btc_win.id,
    ]
    assert [
        trade.id for trade in await redis_repo.list_closed_trades(symbol="ETH")
    ] == [
        eth_loss_2.id,
        eth_loss_1.id,
    ]
    assert await redis_repo.list_closed_trades(symbol="btc") == []
    assert [
        trade.id for trade in await redis_repo.list_closed_trades(limit=1, symbol="BTC")
    ] == [btc_loss_2.id]


async def test_consecutive_loss_count_filters_by_symbol(
    redis_repo: RedisRepository,
) -> None:
    """R5: interleaved symbol histories have isolated loss streaks."""

    await _close_trade(redis_repo, symbol="BTC", minutes=1, close_price=110.0)
    await _close_trade(redis_repo, symbol="ETH", minutes=2, close_price=90.0)
    await _close_trade(redis_repo, symbol="BTC", minutes=3, close_price=90.0)
    await _close_trade(redis_repo, symbol="ETH", minutes=4, close_price=90.0)
    await _close_trade(redis_repo, symbol="BTC", minutes=5, close_price=90.0)

    assert await redis_repo.consecutive_loss_count() == 4
    assert await redis_repo.consecutive_loss_count(symbol="BTC") == 2
    assert await redis_repo.consecutive_loss_count(symbol="ETH") == 2
    assert await redis_repo.consecutive_loss_count(symbol="btc") == 0
