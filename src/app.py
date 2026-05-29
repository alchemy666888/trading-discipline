"""Application entrypoint and component wiring."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.bot import formatting
from src.bot.edit_closed import ClosedTradeEditService
from src.bot.forms import TradeFormService
from src.bot.handlers import TelegramHandlers
from src.config import Settings, load_settings
from src.db.repo import RedisRepository
from src.events.bus import EventBus
from src.exchange import ExchangeAdapter, HyperliquidExchangeAdapter
from src.monitor.alerts import AlertDispatcher
from src.monitor.health import MonitorHealth
from src.monitor.monitor import Monitor
from src.stats.report import WeeklyReportScheduler, load_stats

TelegramApplication = Application[Any, Any, Any, Any, Any, Any]
TelegramSender = Callable[[str], Awaitable[None]]

LOGGER = structlog.get_logger(__name__)


@dataclass
class AppRuntime:
    """Assembled runtime and lifecycle hooks for the bot."""

    settings: Settings
    redis_client: Redis | None
    repo: RedisRepository
    event_bus: EventBus
    exchange_adapter: ExchangeAdapter
    alerts: AlertDispatcher
    health: MonitorHealth
    monitor: Monitor
    forms: TradeFormService
    edit_closed: ClosedTradeEditService
    handlers: TelegramHandlers
    weekly_reporter: WeeklyReportScheduler
    application: TelegramApplication | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def resume_state(self) -> None:
        """Re-arm unresolved breach alerts on startup."""

        open_trades = await self.repo.list_open_trades()
        trade_lookup = {trade.id: trade for trade in open_trades}
        for breach in await self.repo.list_unresolved_breaches():
            trade = trade_lookup.get(breach.trade_id)
            if trade is None:
                continue
            await self.alerts.trigger_breach_alert(
                trade,
                breach,
                current_price=breach.breach_price,
            )

    async def start(self) -> None:
        """Start schedulers, Telegram polling, and background loops."""

        await self.resume_state()
        self.health.start_scheduler()
        self.weekly_reporter.start()
        await self._start_application()
        self._tasks = [
            asyncio.create_task(self.alerts.run(), name="alerts"),
            asyncio.create_task(self.health.run(), name="health"),
            asyncio.create_task(self.monitor.run(), name="monitor"),
            asyncio.create_task(
                self._run_universe_refresh_loop(),
                name="universe-refresh",
            ),
        ]

    async def wait(self) -> None:
        """Wait for background tasks to complete."""

        if not self._tasks:
            return
        await asyncio.gather(*self._tasks)

    async def run(self) -> None:
        """Run the full application until cancelled."""

        await self.start()
        try:
            await self.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop background tasks, schedulers, and external resources."""

        await self.alerts.stop()
        await self.health.stop()
        await self.weekly_reporter.stop()
        await self.monitor.close()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._stop_application()
        if self.redis_client is not None:
            await self.redis_client.aclose()

    async def _run_universe_refresh_loop(self) -> None:
        fetch_universe = getattr(self.exchange_adapter, "fetch_universe", None)
        if fetch_universe is None:
            return

        while True:
            try:
                symbols = await fetch_universe()
                fetched_at = datetime.now(tz=UTC)
                await self.repo.set_universe(symbols, fetched_at)
                LOGGER.info(
                    "universe_refreshed",
                    symbol_count=len(symbols),
                    fetched_at=fetched_at.isoformat(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("universe_refresh_failed", error=str(exc))
            await asyncio.sleep(self.settings.hyperliquid_universe_refresh_seconds)

    async def _start_application(self) -> None:
        if self.application is None:
            return
        await self.application.initialize()
        await self.application.start()
        updater = getattr(self.application, "updater", None)
        if updater is not None:
            await updater.start_polling()

    async def _stop_application(self) -> None:
        if self.application is None:
            return
        updater = getattr(self.application, "updater", None)
        if updater is not None:
            await updater.stop()
        await self.application.stop()
        await self.application.shutdown()


async def create_runtime(
    *,
    settings: Settings,
    redis_client: Redis,
    application: TelegramApplication | None = None,
    exchange_adapter: ExchangeAdapter | None = None,
    send_message: TelegramSender | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> AppRuntime:
    """Create a fully wired runtime from config and a Redis client."""

    repo = RedisRepository(redis_client)
    await repo.apply_migrations()
    await repo.verify_redis_ready()

    application_instance = application or build_application(settings)
    sender = send_message or build_sender(
        application_instance,
        settings.telegram_chat_id,
    )
    runtime = build_runtime(
        settings=settings,
        repo=repo,
        application=application_instance,
        exchange_adapter=exchange_adapter,
        send_message=sender,
        now_fn=now_fn,
        redis_client=redis_client,
    )
    return runtime


def build_runtime(
    *,
    settings: Settings,
    repo: RedisRepository,
    application: TelegramApplication | None,
    exchange_adapter: ExchangeAdapter | None,
    send_message: TelegramSender,
    now_fn: Callable[[], datetime] | None = None,
    redis_client: Redis | None = None,
) -> AppRuntime:
    """Wire runtime components from already-constructed dependencies."""

    reference_now = now_fn or (lambda: datetime.now(tz=UTC))
    event_bus = EventBus()
    exchange = exchange_adapter or build_exchange_adapter(settings)
    alerts = AlertDispatcher(
        repo=repo,
        send_message=send_message,
        render_initial_alert=lambda trade, breach, price, elapsed, loss: (
            formatting.breach_initial_alert(
                trade,
                price,
                elapsed,
                loss,
            )
        ),
        render_escalation_alert=lambda trade, breach, price, elapsed, loss: (
            formatting.breach_escalation_alert(
                trade,
                price,
                elapsed,
                loss,
            )
        ),
        first_window_seconds=settings.alert_interval_first_window_seconds,
        first_window_duration_seconds=settings.alert_interval_first_window_duration_seconds,
        after_seconds=settings.alert_interval_after_seconds,
        now_fn=reference_now,
    )
    health = MonitorHealth(
        send_message=send_message,
        event_bus=event_bus,
        open_trade_count_provider=lambda: _open_trade_count(repo),
        render_down_alert=formatting.monitor_down_alert,
        render_recovery_alert=formatting.monitor_recovery_alert,
        render_heartbeat=formatting.heartbeat_alert,
        monitor_down_alert_delay_with_open_trades_seconds=(
            settings.monitor_down_alert_delay_with_open_trades_seconds
        ),
        monitor_down_alert_delay_no_open_trades_seconds=(
            settings.monitor_down_alert_delay_no_open_trades_seconds
        ),
        monitor_down_repeat_with_open_trades_seconds=(
            settings.monitor_down_repeat_with_open_trades_seconds
        ),
        monitor_down_repeat_no_open_trades_seconds=(
            settings.monitor_down_repeat_no_open_trades_seconds
        ),
        heartbeat_time_local=settings.heartbeat_time_local,
        timezone=settings.timezone,
        now_fn=reference_now,
        heartbeat_file_path=settings.heartbeat_file_path,
    )
    monitor = Monitor(
        repo=repo,
        adapter=exchange,
        alerts=alerts,
        health=health,
        event_bus=event_bus,
        now_fn=reference_now,
    )
    forms = TradeFormService(
        repo=repo,
        settings=settings,
        universe_fetcher=exchange,  # type: ignore[arg-type]
        now_fn=reference_now,
    )
    edit_closed = ClosedTradeEditService(
        repo=repo,
        settings=settings,
        now_fn=reference_now,
    )
    handlers = TelegramHandlers(
        settings=settings,
        repo=repo,
        forms=forms,
        edit_closed=edit_closed,
        alerts=alerts,
        health=health,
        event_bus=event_bus,
        stats_provider=_build_stats_provider(repo, reference_now),
        now_fn=reference_now,
    )
    weekly_reporter = WeeklyReportScheduler(
        repo=repo,
        send_message=send_message,
        timezone=settings.timezone,
        now_fn=reference_now,
    )
    if application is not None:
        wire_handlers(application, handlers)

    return AppRuntime(
        settings=settings,
        redis_client=redis_client,
        repo=repo,
        event_bus=event_bus,
        exchange_adapter=exchange,
        alerts=alerts,
        health=health,
        monitor=monitor,
        forms=forms,
        edit_closed=edit_closed,
        handlers=handlers,
        weekly_reporter=weekly_reporter,
        application=application,
    )


def build_application(settings: Settings) -> TelegramApplication:
    """Build the PTB application."""

    return ApplicationBuilder().token(settings.telegram_bot_token_value).build()


def build_sender(application: TelegramApplication, chat_id: int) -> TelegramSender:
    """Wrap Telegram bot sending behind a simple async callback."""

    async def send_message(message: str) -> None:
        await application.bot.send_message(chat_id=chat_id, text=message)

    return send_message


def build_exchange_adapter(settings: Settings) -> ExchangeAdapter:
    """Construct the Hyperliquid exchange adapter."""

    return HyperliquidExchangeAdapter(settings)


def wire_handlers(application: Any, handlers: TelegramHandlers) -> None:
    """Register command and text handlers on the PTB application."""

    application.add_handler(CommandHandler("new", cast(Any, handlers.new)))
    application.add_handler(CommandHandler("edit", cast(Any, handlers.edit)))
    application.add_handler(
        CommandHandler("edit_closed", cast(Any, handlers.edit_closed))
    )
    application.add_handler(CommandHandler("closed", cast(Any, handlers.closed)))
    application.add_handler(CommandHandler("justify", cast(Any, handlers.justify)))
    application.add_handler(CommandHandler("cancel", cast(Any, handlers.cancel)))
    application.add_handler(CommandHandler("open", cast(Any, handlers.open)))
    application.add_handler(CommandHandler("streak", cast(Any, handlers.streak)))
    application.add_handler(CommandHandler("stats", cast(Any, handlers.stats)))
    application.add_handler(CommandHandler("setpnl", cast(Any, handlers.setpnl)))
    application.add_handler(CommandHandler("health", cast(Any, handlers.health)))
    application.add_handler(CommandHandler("signals", cast(Any, handlers.signals)))
    application.add_handler(CommandHandler("help", cast(Any, handlers.help)))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            cast(Any, handlers.text_message),
        )
    )
    application.add_handler(
        MessageHandler(filters.COMMAND, cast(Any, handlers.unknown))
    )


def _build_stats_provider(
    repo: RedisRepository,
    now_fn: Callable[[], datetime],
) -> Callable[[int], Awaitable[str]]:
    async def provide(days: int) -> str:
        stats = await load_stats(repo, days, now=now_fn())
        if stats.total_trades == 0:
            return formatting.stats_empty(days)
        return formatting.format_stats(stats)

    return provide


async def _open_trade_count(repo: RedisRepository) -> int:
    return len(await repo.list_open_trades())


async def run() -> None:
    """Load config, connect dependencies, and run the bot."""

    settings = load_settings()
    structlog.configure()
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    runtime = await create_runtime(
        settings=settings,
        redis_client=redis_client,
    )
    LOGGER.info("app_starting")
    try:
        await runtime.run()
    finally:
        LOGGER.info("app_stopped")


def main() -> None:
    """Synchronous entrypoint for `python -m src.app`."""

    asyncio.run(run())


if __name__ == "__main__":
    main()
