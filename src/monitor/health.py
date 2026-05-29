"""Monitor health tracking, tiered downtime alerts, and daily heartbeat support."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from inspect import isawaitable
from pathlib import Path
from typing import TypeVar
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.db.repo import RedisHealthDetails
from src.events.bus import EventBus
from src.exchange.base import ConnectionEvent, ConnectionState
from src.models.events import (
    MonitorDownEvent,
    MonitorDownPayload,
    MonitorRecoveredEvent,
    MonitorRecoveredPayload,
)

T = TypeVar("T")

HealthSender = Callable[[str], Awaitable[None]]
OpenTradeCountProvider = Callable[[], Awaitable[int] | int]
DownAlertRenderer = Callable[[int, int, bool], str]
RecoveryAlertRenderer = Callable[[int, int | None], str]
HeartbeatRenderer = Callable[[float | None, int], str]
GapRecoveryCallback = Callable[[int], Awaitable[None] | None]


@dataclass(frozen=True)
class ApplicationHealthSnapshot:
    """Structured runtime health state for the later `/health` command."""

    websocket_status: str = "unknown"
    last_tick_age_seconds: float | None = None
    open_trade_count: int = 0
    last_error: str | None = None
    redis: RedisHealthDetails = field(default_factory=RedisHealthDetails.unknown)


@dataclass(frozen=True)
class DownState:
    """Current monitor-down state and alert schedule."""

    since: datetime
    has_open_trades: bool
    next_alert_at: datetime
    repeat_interval_seconds: int
    alert_sent: bool = False
    reconnect_attempt: int = 0
    reason: str | None = None


class MonitorHealth:
    """Manage monitor-down alerts, recovery messages, and healthy heartbeats."""

    def __init__(
        self,
        *,
        send_message: HealthSender,
        event_bus: EventBus,
        open_trade_count_provider: OpenTradeCountProvider,
        render_down_alert: DownAlertRenderer,
        render_recovery_alert: RecoveryAlertRenderer,
        render_heartbeat: HeartbeatRenderer,
        monitor_down_alert_delay_with_open_trades_seconds: int,
        monitor_down_alert_delay_no_open_trades_seconds: int,
        monitor_down_repeat_with_open_trades_seconds: int,
        monitor_down_repeat_no_open_trades_seconds: int,
        heartbeat_time_local: time,
        timezone: str,
        now_fn: Callable[[], datetime] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
        gap_recovery_callback: GapRecoveryCallback | None = None,
        heartbeat_file_path: str | None = None,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        self._send_message = send_message
        self._event_bus = event_bus
        self._open_trade_count_provider = open_trade_count_provider
        self._render_down_alert = render_down_alert
        self._render_recovery_alert = render_recovery_alert
        self._render_heartbeat = render_heartbeat
        self._delay_with_open_trades = monitor_down_alert_delay_with_open_trades_seconds
        self._delay_no_open_trades = monitor_down_alert_delay_no_open_trades_seconds
        self._repeat_with_open_trades = monitor_down_repeat_with_open_trades_seconds
        self._repeat_no_open_trades = monitor_down_repeat_no_open_trades_seconds
        self._heartbeat_time_local = heartbeat_time_local
        self._timezone = ZoneInfo(timezone)
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._sleep = sleep_func
        self._gap_recovery_callback = gap_recovery_callback
        self._heartbeat_file_path = (
            Path(heartbeat_file_path) if heartbeat_file_path else None
        )
        self._scheduler = scheduler
        self._logger = structlog.get_logger(__name__)
        self._websocket_status = "unknown"
        self._last_tick_at: datetime | None = None
        self._last_error: str | None = None
        self._down_state: DownState | None = None
        self._disconnect_history: deque[datetime] = deque()
        self._flapping_alert_next_disconnect = False
        self._last_heartbeat_date: date | None = None
        self._closed = False

    async def handle_connection_event(self, event: ConnectionEvent) -> None:
        """Update health state from exchange-adapter connection events."""

        if event.state in {ConnectionState.DISCONNECTED, ConnectionState.STALE}:
            await self._handle_down_event(event)
            return
        if event.state in {ConnectionState.CONNECTED, ConnectionState.RECONNECTED}:
            await self._handle_recovery_event(event)

    def record_tick(self, *, received_at: datetime | None = None) -> None:
        """Update the last successful tick timestamp."""

        self._last_tick_at = received_at or self._now()
        self._websocket_status = "connected"

    async def process_due_alerts(self) -> None:
        """Send any monitor-down alerts that are due right now."""

        down_state = self._down_state
        if down_state is None:
            return

        now = self._now()
        if now < down_state.next_alert_at:
            return

        duration_seconds = max(0, int((now - down_state.since).total_seconds()))
        message = self._render_down_alert(
            duration_seconds,
            down_state.reconnect_attempt,
            down_state.has_open_trades,
        )
        await self._send_with_retry(message)

        if not down_state.alert_sent:
            await self._event_bus.publish(
                MonitorDownEvent(
                    ts=now,
                    payload=MonitorDownPayload(
                        since=down_state.since,
                        has_open_trades=down_state.has_open_trades,
                    ),
                )
            )

        self._down_state = replace(
            down_state,
            alert_sent=True,
            next_alert_at=now + timedelta(seconds=down_state.repeat_interval_seconds),
        )

    async def maybe_send_daily_heartbeat(self) -> bool:
        """Send the daily healthy heartbeat if it is due and the monitor is healthy."""

        now = self._now()
        local_now = now.astimezone(self._timezone)
        if self._down_state is not None:
            return False
        if local_now.time() < self._heartbeat_time_local:
            return False
        if self._last_heartbeat_date == local_now.date():
            return False

        open_trade_count = await self._resolve(self._open_trade_count_provider())
        last_tick_age = self._last_tick_age_seconds(now)
        message = self._render_heartbeat(last_tick_age, open_trade_count)
        await self._send_with_retry(message)
        self._last_heartbeat_date = local_now.date()

        if self._heartbeat_file_path is not None:
            self._heartbeat_file_path.parent.mkdir(parents=True, exist_ok=True)
            self._heartbeat_file_path.write_text(now.isoformat(), encoding="utf-8")
        return True

    async def run(self) -> None:
        """Background polling loop for due health alerts and heartbeats."""

        self._closed = False
        while not self._closed:
            await self.process_due_alerts()
            await self._sleep(1)

    async def stop(self) -> None:
        """Stop the polling loop and shut down any internal scheduler."""

        self._closed = True
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)

    def start_scheduler(self) -> AsyncIOScheduler:
        """Create and start the APScheduler jobs used by the health subsystem."""

        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(timezone=self._timezone)
            self._scheduler.add_job(
                self.maybe_send_daily_heartbeat,
                trigger="cron",
                hour=self._heartbeat_time_local.hour,
                minute=self._heartbeat_time_local.minute,
            )
            if self._heartbeat_file_path is not None:
                self._scheduler.add_job(
                    self._write_heartbeat_file,
                    trigger="interval",
                    minutes=1,
                )
            self._scheduler.start()
        return self._scheduler

    async def build_snapshot(
        self,
        *,
        redis: RedisHealthDetails,
    ) -> ApplicationHealthSnapshot:
        """Build a structured health snapshot for `/health`."""

        open_trade_count = await self._resolve(self._open_trade_count_provider())
        return ApplicationHealthSnapshot(
            websocket_status=self._websocket_status,
            last_tick_age_seconds=self._last_tick_age_seconds(self._now()),
            open_trade_count=open_trade_count,
            last_error=self._last_error,
            redis=redis,
        )

    async def _handle_down_event(self, event: ConnectionEvent) -> None:
        self._websocket_status = (
            "stale" if event.state == ConnectionState.STALE else "disconnected"
        )
        self._last_error = event.reason

        immediate_alert = self._flapping_alert_next_disconnect
        if immediate_alert:
            self._flapping_alert_next_disconnect = False

        self._record_disconnect(event.ts)
        if len(self._disconnect_history) >= 5:
            self._flapping_alert_next_disconnect = True
            self._logger.warning(
                "monitor_flapping_detected",
                disconnect_count=len(self._disconnect_history),
            )

        if self._down_state is None:
            open_trade_count = await self._resolve(self._open_trade_count_provider())
            has_open_trades = open_trade_count > 0
            first_delay = (
                0
                if immediate_alert
                else (
                    self._delay_with_open_trades
                    if has_open_trades
                    else self._delay_no_open_trades
                )
            )
            repeat_interval = (
                self._repeat_with_open_trades
                if has_open_trades
                else self._repeat_no_open_trades
            )
            self._down_state = DownState(
                since=event.ts,
                has_open_trades=has_open_trades,
                next_alert_at=event.ts + timedelta(seconds=first_delay),
                repeat_interval_seconds=repeat_interval,
                alert_sent=False,
                reconnect_attempt=event.reconnect_attempt,
                reason=event.reason,
            )
            return

        self._down_state = replace(
            self._down_state,
            reconnect_attempt=event.reconnect_attempt,
            reason=event.reason,
        )

    async def _handle_recovery_event(self, event: ConnectionEvent) -> None:
        now = event.ts
        gap_seconds = event.gap_seconds
        if gap_seconds is None and self._down_state is not None:
            gap_seconds = max(0, int((now - self._down_state.since).total_seconds()))

        if self._down_state is not None and self._down_state.alert_sent:
            message = self._render_recovery_alert(gap_seconds or 0, gap_seconds)
            await self._send_with_retry(message)
            await self._event_bus.publish(
                MonitorRecoveredEvent(
                    ts=now,
                    payload=MonitorRecoveredPayload(gap_seconds=gap_seconds or 0),
                )
            )

        if gap_seconds is not None and gap_seconds > 60:
            await self._invoke_gap_recovery_callback(gap_seconds)

        self._down_state = None
        self._websocket_status = "connected"
        self._last_error = None

    async def _invoke_gap_recovery_callback(self, gap_seconds: int) -> None:
        if self._gap_recovery_callback is None:
            return
        result = self._gap_recovery_callback(gap_seconds)
        if asyncio.iscoroutine(result):
            await result

    async def _send_with_retry(self, message: str) -> bool:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                await self._send_message(message)
                return True
            except Exception as exc:  # pragma: no cover - behavior checked by tests
                last_error = exc
                self._logger.warning(
                    "monitor_health_send_retry",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < 3:
                    await self._sleep(1)
        self._logger.error(
            "monitor_health_send_failed",
            error=str(last_error) if last_error is not None else "unknown",
        )
        return False

    async def _write_heartbeat_file(self) -> None:
        if self._heartbeat_file_path is None:
            return
        now = self._now()
        self._heartbeat_file_path.parent.mkdir(parents=True, exist_ok=True)
        self._heartbeat_file_path.write_text(now.isoformat(), encoding="utf-8")

    def _last_tick_age_seconds(self, now: datetime) -> float | None:
        if self._last_tick_at is None:
            return None
        return max(0.0, (now - self._last_tick_at).total_seconds())

    def _record_disconnect(self, ts: datetime) -> None:
        cutoff = ts - timedelta(minutes=10)
        self._disconnect_history.append(ts)
        while self._disconnect_history and self._disconnect_history[0] < cutoff:
            self._disconnect_history.popleft()

    async def _resolve(self, value: Awaitable[T] | T) -> T:
        if isawaitable(value):
            return await value
        return value
