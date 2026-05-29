"""Integration tests for the weekly report scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time

from src.models.breach import Breach, BreachUserResponse
from src.models.trade import Direction, Regime, Trade, TradeStatus
from src.stats.report import WeeklyReportScheduler


class FakeReportRepo:
    """Minimal repo surface for weekly report tests."""

    def __init__(self, trades: list[Trade], breaches: dict[int, list[Breach]]) -> None:
        self._trades = trades
        self._breaches = breaches

    async def list_all_trades(self) -> list[Trade]:
        return list(self._trades)

    async def list_breaches_for_trade(self, trade_id: int) -> list[Breach]:
        return list(self._breaches.get(trade_id, []))


def _trade(now: datetime) -> Trade:
    return Trade(
        id=1,
        symbol="BTC",
        direction=Direction.LONG,
        size_usdt=1000.0,
        leverage=5,
        leverage_override_reason=None,
        entry_price=82000.0,
        invalidation_price=81000.0,
        max_loss_usdt=50.0,
        regime=Regime.UPTREND,
        thesis="Weekly report fixture trade.",
        status=TradeStatus.CLOSED,
        size_reduction_enforced=False,
        opened_at=now - timedelta(days=2),
        closed_at=now - timedelta(days=1),
        close_price=81300.0,
        realized_pnl=100.0,
    )


def _breach(now: datetime) -> Breach:
    return Breach(
        id=1,
        trade_id=1,
        detected_at=now - timedelta(days=1, hours=1),
        breach_price=80990.0,
        user_response=BreachUserResponse.CLOSED,
        response_at=now - timedelta(days=1, minutes=50),
        justification=None,
    )


@pytest.mark.asyncio
async def test_weekly_report_scheduler_targets_monday_9am_local() -> None:
    """REQ-007: the weekly summary job is scheduled for Monday 09:00 local time."""

    sent_messages: list[str] = []

    async def send_message(message: str) -> None:
        sent_messages.append(message)

    with freeze_time("2026-05-17 00:30:00+00:00"):
        now = datetime.now(tz=UTC)
        repo = FakeReportRepo([_trade(now)], {1: [_breach(now)]})
        service = WeeklyReportScheduler(
            repo=repo,  # type: ignore[arg-type]
            send_message=send_message,
            timezone="Asia/Hong_Kong",
            now_fn=lambda: datetime.now(tz=UTC),
        )
        scheduler = service.start()
        job = scheduler.get_jobs()[0]

        assert job.next_run_time is not None
        local_next_run = job.next_run_time.astimezone()
        assert local_next_run.weekday() == 0
        assert local_next_run.hour == 9
        assert local_next_run.minute == 0

        await service.send_weekly_summary()
        assert sent_messages[-1].startswith("Weekly summary:")
        await service.stop()
