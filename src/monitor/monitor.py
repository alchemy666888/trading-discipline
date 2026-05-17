"""Main tick-consumption loop for open-trade breach monitoring."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import structlog

from src.db.repo import RedisRepository
from src.events.bus import EventBus
from src.exchange.base import ExchangeAdapter, Tick
from src.models.events import (
    BreachDetectedEvent,
    BreachDetectedPayload,
    TickEvent,
    TickPayload,
)
from src.monitor.alerts import AlertDispatcher
from src.monitor.breach import is_breach
from src.monitor.health import MonitorHealth


class Monitor:
    """Consume exchange ticks, detect breaches, and start alert sequences."""

    def __init__(
        self,
        *,
        repo: RedisRepository,
        adapter: ExchangeAdapter,
        alerts: AlertDispatcher,
        health: MonitorHealth,
        event_bus: EventBus,
        now_fn: Callable[[], datetime] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._repo = repo
        self._adapter = adapter
        self._alerts = alerts
        self._health = health
        self._event_bus = event_bus
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._sleep = sleep_func
        self._logger = structlog.get_logger(__name__)
        self._closed = False

    def attach_adapter_events(self) -> None:
        """Forward adapter connection-state events into the health subsystem."""

        self._adapter.subscribe_connection_events(self._health.handle_connection_event)

    async def run(self) -> None:
        """Consume ticks until the adapter stops streaming."""

        self.attach_adapter_events()
        while not self._closed:
            try:
                _ = await self._repo.list_open_trades()
                async for tick in self._adapter.stream_ticks():
                    if self._closed:
                        break
                    try:
                        await self.process_tick(tick)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._logger.exception("monitor_tick_failed", error=str(exc))
                        continue
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.exception("monitor_loop_failed", error=str(exc))
                if self._closed:
                    break
                await self._sleep(1)

    async def process_tick(self, tick: Tick) -> None:
        """Evaluate one tick against every currently open trade."""

        self._health.record_tick(received_at=self._now())
        await self._event_bus.publish(
            TickEvent(
                ts=tick.ts,
                payload=TickPayload(price=tick.price),
            )
        )
        open_trades = await self._repo.list_open_trades()
        for trade in open_trades:
            self._alerts.update_trade_price(trade.id, tick.price)
            if not is_breach(trade.direction, trade.invalidation_price, tick.price):
                continue
            breach = await self._repo.create_breach(
                trade.id,
                breach_price=tick.price,
                detected_at=tick.ts,
            )
            if breach is None:
                continue
            await self._alerts.trigger_breach_alert(
                trade,
                breach,
                current_price=tick.price,
            )
            await self._event_bus.publish(
                BreachDetectedEvent(
                    ts=tick.ts,
                    payload=BreachDetectedPayload(
                        trade_id=trade.id,
                        breach_id=breach.id,
                        price=tick.price,
                    ),
                )
            )

    async def close(self) -> None:
        """Stop processing future ticks and close the adapter."""

        self._closed = True
        await self._adapter.close()
