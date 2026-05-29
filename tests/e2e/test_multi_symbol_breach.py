"""End-to-end monitor checks for multi-symbol breach routing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.events.bus import EventBus
from src.exchange.base import Tick
from src.models.breach import Breach
from src.models.events import Event, EventType, TickEvent
from src.models.trade import Direction, Regime, Trade, TradeStatus
from src.monitor.monitor import Monitor


class FakeRepo:
    """Minimal monitor repository with in-memory open trades and breaches."""

    def __init__(self, trades: list[Trade]) -> None:
        self.trades = trades
        self.breaches: list[Breach] = []
        self._active_trade_ids: set[int] = set()
        self._next_breach_id = 1

    async def list_open_trades(self) -> list[Trade]:
        return [
            trade
            for trade in self.trades
            if trade.status in {TradeStatus.OPEN, TradeStatus.OPEN_OVERRIDE}
        ]

    async def create_breach(
        self,
        trade_id: int,
        *,
        breach_price: float,
        detected_at: datetime,
    ) -> Breach | None:
        if trade_id in self._active_trade_ids:
            return None
        breach = Breach(
            id=self._next_breach_id,
            trade_id=trade_id,
            detected_at=detected_at,
            breach_price=breach_price,
            user_response=None,
            response_at=None,
            justification=None,
        )
        self._next_breach_id += 1
        self._active_trade_ids.add(trade_id)
        self.breaches.append(breach)
        return breach


class FakeAlerts:
    """Capture monitor alert calls."""

    def __init__(self) -> None:
        self.price_updates: list[tuple[int, float]] = []
        self.alerts: list[tuple[int, int, float]] = []

    def update_trade_price(self, trade_id: int, price: float) -> None:
        self.price_updates.append((trade_id, price))

    async def trigger_breach_alert(
        self,
        trade: Trade,
        breach: Breach,
        *,
        current_price: float,
    ) -> None:
        self.alerts.append((trade.id, breach.id, current_price))


class FakeHealth:
    """Capture tick health updates."""

    def __init__(self) -> None:
        self.recorded = 0

    def record_tick(self, *, received_at: datetime | None = None) -> None:
        self.recorded += 1


class FakeAdapter:
    """Close-only adapter surface for direct process_tick tests."""

    async def close(self) -> None:
        return None


def _trade(
    *,
    trade_id: int,
    symbol: str,
    direction: Direction,
    entry_price: float,
    invalidation_price: float,
) -> Trade:
    return Trade(
        id=trade_id,
        symbol=symbol,
        direction=direction,
        size_usdt=1000.0,
        leverage=5,
        leverage_override_reason=None,
        entry_price=entry_price,
        invalidation_price=invalidation_price,
        max_loss_usdt=25.0,
        regime=Regime.RANGE,
        thesis=f"{symbol} multi-symbol monitor fixture trade.",
        status=TradeStatus.OPEN,
        size_reduction_enforced=False,
        opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        closed_at=None,
        close_price=None,
        realized_pnl=None,
    )


@pytest.mark.asyncio
async def test_multi_symbol_breach_routes_ticks_to_matching_symbol_only() -> None:
    """R3: one symbol's tick cannot breach another symbol's open trade."""

    btc_short = _trade(
        trade_id=1,
        symbol="BTC",
        direction=Direction.SHORT,
        entry_price=90.0,
        invalidation_price=95.0,
    )
    eth_long = _trade(
        trade_id=2,
        symbol="ETH",
        direction=Direction.LONG,
        entry_price=110.0,
        invalidation_price=100.0,
    )
    repo = FakeRepo([btc_short, eth_long])
    alerts = FakeAlerts()
    health = FakeHealth()
    bus = EventBus()
    tick_events: list[TickEvent] = []

    async def record_tick(event: Event) -> None:
        assert isinstance(event, TickEvent)
        tick_events.append(event)

    bus.subscribe(EventType.TICK, record_tick)
    monitor = Monitor(
        repo=repo,  # type: ignore[arg-type]
        adapter=FakeAdapter(),  # type: ignore[arg-type]
        alerts=alerts,  # type: ignore[arg-type]
        health=health,  # type: ignore[arg-type]
        event_bus=bus,
        now_fn=lambda: datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
    )

    await monitor.process_tick(
        Tick("ETH", 99.0, datetime(2026, 5, 17, 9, 1, tzinfo=UTC))
    )
    await monitor.process_tick(
        Tick("ETH", 105.0, datetime(2026, 5, 17, 9, 2, tzinfo=UTC))
    )

    assert [(breach.trade_id, breach.breach_price) for breach in repo.breaches] == [
        (eth_long.id, 99.0)
    ]
    assert alerts.alerts == [(eth_long.id, 1, 99.0)]
    assert alerts.price_updates == [(eth_long.id, 99.0), (eth_long.id, 105.0)]
    assert health.recorded == 2
    assert [event.payload.symbol for event in tick_events] == ["ETH", "ETH"]

    await monitor.close()
