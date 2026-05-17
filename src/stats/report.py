"""Weekly summary scheduling and rendering."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.bot import formatting
from src.db.repo import RedisRepository
from src.stats.calculator import StatsResult, compute_stats

ReportSender = Callable[[str], Awaitable[None]]


class WeeklyReportScheduler:
    """Schedule and send the weekly Monday summary."""

    def __init__(
        self,
        *,
        repo: RedisRepository,
        send_message: ReportSender,
        timezone: str,
        now_fn: Callable[[], datetime] | None = None,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        self._repo = repo
        self._send_message = send_message
        self._timezone = timezone
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        self._scheduler = scheduler

    def start(self) -> AsyncIOScheduler:
        """Register and start the Monday 09:00 APScheduler job."""

        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(timezone=self._timezone)
        if not self._scheduler.get_jobs():
            self._scheduler.add_job(
                self.send_weekly_summary,
                trigger="cron",
                day_of_week="mon",
                hour=9,
                minute=0,
            )
        if not self._scheduler.running:
            self._scheduler.start()
        return self._scheduler

    async def stop(self) -> None:
        """Stop the internal scheduler when present."""

        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def send_weekly_summary(self) -> StatsResult:
        """Compute and push the trailing 7-day summary."""

        stats = await self.compute_last_7_days()
        await self._send_message(formatting.weekly_summary(stats))
        return stats

    async def compute_last_7_days(self) -> StatsResult:
        """Load data and compute trailing 7-day stats."""

        return await load_stats(self._repo, 7, now=self._now())


async def load_stats(
    repo: RedisRepository,
    window_days: int,
    *,
    now: datetime,
) -> StatsResult:
    """Load trade and breach records and compute rolling stats."""

    trades = await repo.list_all_trades()
    breaches = []
    for trade in trades:
        breaches.extend(await repo.list_breaches_for_trade(trade.id))
    return compute_stats(trades, breaches, window_days, now=now)
