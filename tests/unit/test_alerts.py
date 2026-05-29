"""Unit tests for alert cadence and dedup behavior."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from freezegun import freeze_time

from src.models.breach import Breach
from src.models.trade import Direction, Regime, Trade, TradeStatus
from src.monitor.alerts import AlertDispatcher


class RecordingRepo:
    """Capture alert records without touching Redis."""

    def __init__(self) -> None:
        self.records: list[tuple[int, int, str]] = []

    async def record_alert(
        self,
        breach_id: int,
        *,
        sent_at: datetime,
        escalation_level: int,
        message: str,
    ) -> None:
        self.records.append((breach_id, escalation_level, message))


def _trade() -> Trade:
    return Trade(
        id=9,
        symbol="BTC",
        direction=Direction.LONG,
        size_usdt=2000.0,
        leverage=5,
        leverage_override_reason=None,
        entry_price=82000.0,
        invalidation_price=81000.0,
        max_loss_usdt=40.0,
        regime=Regime.UPTREND,
        thesis="Alert test trade thesis text.",
        status=TradeStatus.OPEN,
        size_reduction_enforced=False,
        opened_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        closed_at=None,
        close_price=None,
        realized_pnl=None,
    )


def _breach() -> Breach:
    return Breach(
        id=4,
        trade_id=9,
        detected_at=datetime(2026, 5, 17, 9, 0, tzinfo=UTC),
        breach_price=80990.0,
        user_response=None,
        response_at=None,
        justification=None,
    )


@pytest.mark.asyncio
async def test_alert_dispatcher_cadence_and_dedup() -> None:
    """REQ-005: alert cadence escalates at 60s then 300s without duplicate sequences."""

    repo = RecordingRepo()
    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    with freeze_time("2026-05-17 09:00:00+00:00") as frozen:
        dispatcher = AlertDispatcher(
            repo=repo,  # type: ignore[arg-type]
            send_message=send_message,
            render_initial_alert=lambda trade, breach, price, elapsed, loss: (
                f"initial:{price}:{elapsed}:{loss:.2f}"
            ),
            render_escalation_alert=lambda trade, breach, price, elapsed, loss: (
                f"repeat:{price}:{elapsed}:{loss:.2f}"
            ),
            first_window_seconds=60,
            first_window_duration_seconds=300,
            after_seconds=300,
            now_fn=lambda: datetime.now(tz=UTC),
        )

        assert (
            await dispatcher.trigger_breach_alert(
                _trade(),
                _breach(),
                current_price=80990.0,
            )
            is True
        )
        assert (
            await dispatcher.trigger_breach_alert(
                _trade(),
                _breach(),
                current_price=80980.0,
            )
            is False
        )
        assert len(sent_messages) == 1

        frozen.tick(59)
        await dispatcher.process_due_alerts()
        assert len(sent_messages) == 1

        dispatcher.update_trade_price(9, 80980.0)
        frozen.tick(1)
        await dispatcher.process_due_alerts()
        assert len(sent_messages) == 2

        dispatcher.update_trade_price(9, 80970.0)
        frozen.tick(240)
        await dispatcher.process_due_alerts()
        assert len(sent_messages) == 3

        dispatcher.update_trade_price(9, 80960.0)
        frozen.tick(300)
        await dispatcher.process_due_alerts()
        assert len(sent_messages) == 4
        assert repo.records == [
            (4, 0, sent_messages[0]),
            (4, 1, sent_messages[1]),
            (4, 2, sent_messages[2]),
            (4, 3, sent_messages[3]),
        ]


@pytest.mark.asyncio
async def test_alert_dispatcher_persistent_send_failure_does_not_crash() -> None:
    """REQ-005: persistent send failures are retried and do not crash the dispatcher."""

    attempts: list[int] = []
    slept: list[float] = []

    async def send_message(message: str) -> None:
        attempts.append(1)
        raise RuntimeError("telegram down")

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    dispatcher = AlertDispatcher(
        repo=RecordingRepo(),  # type: ignore[arg-type]
        send_message=send_message,
        render_initial_alert=lambda trade, breach, price, elapsed, loss: "initial",
        render_escalation_alert=lambda trade, breach, price, elapsed, loss: "repeat",
        first_window_seconds=60,
        first_window_duration_seconds=300,
        after_seconds=300,
        sleep_func=sleep,
    )

    assert (
        await dispatcher.trigger_breach_alert(
            _trade(),
            _breach(),
            current_price=80990.0,
        )
        is True
    )

    assert len(attempts) == 3
    assert slept == [1, 1]


@pytest.mark.asyncio
async def test_alert_dispatcher_resolve_cancels_future_alerts() -> None:
    """REQ-005: resolving a breach stops future escalations."""

    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    with freeze_time("2026-05-17 09:00:00+00:00") as frozen:
        dispatcher = AlertDispatcher(
            repo=RecordingRepo(),  # type: ignore[arg-type]
            send_message=send_message,
            render_initial_alert=lambda trade, breach, price, elapsed, loss: "initial",
            render_escalation_alert=lambda trade, breach, price, elapsed, loss: (
                "repeat"
            ),
            first_window_seconds=60,
            first_window_duration_seconds=300,
            after_seconds=300,
            now_fn=lambda: datetime.now(tz=UTC),
        )

        await dispatcher.trigger_breach_alert(
            _trade(),
            _breach(),
            current_price=80990.0,
        )
        await dispatcher.resolve_breach(4)
        frozen.tick(600)
        await dispatcher.process_due_alerts()

        assert sent_messages == ["initial"]
