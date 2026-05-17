"""Alert scheduling and escalation for invalidation breaches."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import structlog

from src.db.repo import RedisRepository
from src.models.breach import Breach
from src.models.trade import Direction, Trade

AlertSender = Callable[[str], Awaitable[None]]
AlertRenderer = Callable[[Trade, Breach, float, int, float], str]


@dataclass(frozen=True)
class AlertSequence:
    """State for one active breach-alert sequence."""

    trade: Trade
    breach: Breach
    latest_price: float
    next_due_at: datetime
    sent_count: int = 0


class AlertDispatcher:
    """Schedule and send breach alerts with the required escalation cadence."""

    def __init__(
        self,
        *,
        repo: RedisRepository,
        send_message: AlertSender,
        render_initial_alert: AlertRenderer,
        render_escalation_alert: AlertRenderer,
        first_window_seconds: int,
        first_window_duration_seconds: int,
        after_seconds: int,
        now_fn: Callable[[], datetime] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._repo = repo
        self._send_message = send_message
        self._render_initial_alert = render_initial_alert
        self._render_escalation_alert = render_escalation_alert
        self._first_window_seconds = first_window_seconds
        self._first_window_duration_seconds = first_window_duration_seconds
        self._after_seconds = after_seconds
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._sleep = sleep_func
        self._logger = structlog.get_logger(__name__)
        self._sequences: dict[int, AlertSequence] = {}
        self._closed = False

    async def trigger_breach_alert(
        self,
        trade: Trade,
        breach: Breach,
        *,
        current_price: float,
    ) -> bool:
        """Register a breach alert sequence and send the initial alert immediately."""

        if breach.id in self._sequences:
            self.update_trade_price(trade.id, current_price)
            return False

        self._sequences[breach.id] = AlertSequence(
            trade=trade,
            breach=breach,
            latest_price=current_price,
            next_due_at=self._now(),
        )
        await self.process_due_alerts()
        return True

    def update_trade_price(self, trade_id: int, current_price: float) -> None:
        """Update the latest observed price for any active breach on this trade."""

        for breach_id, sequence in list(self._sequences.items()):
            if sequence.trade.id == trade_id:
                self._sequences[breach_id] = replace(
                    sequence,
                    latest_price=current_price,
                )

    async def resolve_breach(self, breach_id: int) -> None:
        """Stop any active escalation sequence for the resolved breach."""

        self._sequences.pop(breach_id, None)

    async def process_due_alerts(self) -> None:
        """Send any breach alerts that are currently due."""

        now = self._now()
        due_sequences = sorted(
            (
                sequence
                for sequence in self._sequences.values()
                if sequence.next_due_at <= now
            ),
            key=lambda sequence: sequence.next_due_at,
        )
        for sequence in due_sequences:
            await self._send_sequence_alert(sequence, now=now)

    async def run(self) -> None:
        """Background polling loop for due alert sequences."""

        self._closed = False
        while not self._closed:
            await self.process_due_alerts()
            await self._sleep(1)

    async def stop(self) -> None:
        """Stop the background polling loop."""

        self._closed = True

    def active_breach_ids(self) -> set[int]:
        """Return active breach IDs for tests and runtime introspection."""

        return set(self._sequences)

    async def _send_sequence_alert(
        self,
        sequence: AlertSequence,
        *,
        now: datetime,
    ) -> None:
        elapsed_seconds = max(
            0,
            int((now - sequence.breach.detected_at).total_seconds()),
        )
        current_loss_usdt = self._current_loss_usdt(
            sequence.trade,
            sequence.latest_price,
        )
        if sequence.sent_count == 0:
            message = self._render_initial_alert(
                sequence.trade,
                sequence.breach,
                sequence.latest_price,
                elapsed_seconds,
                current_loss_usdt,
            )
        else:
            message = self._render_escalation_alert(
                sequence.trade,
                sequence.breach,
                sequence.latest_price,
                elapsed_seconds,
                current_loss_usdt,
            )

        sent = await self._send_with_retry(message)
        if sent:
            await self._repo.record_alert(
                sequence.breach.id,
                sent_at=now,
                escalation_level=sequence.sent_count,
                message=message,
            )

        next_interval = self._next_interval(elapsed_seconds)
        if sequence.breach.id in self._sequences:
            self._sequences[sequence.breach.id] = replace(
                sequence,
                next_due_at=now + timedelta(seconds=next_interval),
                sent_count=sequence.sent_count + 1,
            )

    def _next_interval(self, elapsed_seconds: int) -> int:
        if elapsed_seconds < self._first_window_duration_seconds:
            return self._first_window_seconds
        return self._after_seconds

    async def _send_with_retry(self, message: str) -> bool:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                await self._send_message(message)
                return True
            except Exception as exc:  # pragma: no cover - asserted via behavior
                last_error = exc
                self._logger.warning(
                    "alert_send_retry",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < 3:
                    await self._sleep(1)
        self._logger.error(
            "alert_send_failed",
            error=str(last_error) if last_error is not None else "unknown",
        )
        return False

    @staticmethod
    def _current_loss_usdt(trade: Trade, current_price: float) -> float:
        size_btc = trade.size_usdt / trade.entry_price
        direction_sign = 1.0 if trade.direction == Direction.LONG else -1.0
        pnl = (current_price - trade.entry_price) * size_btc * direction_sign
        return max(0.0, -pnl)
