"""Centralized user-facing Telegram copy."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from src.models.trade import Direction, Trade, TradeStatus
from src.rules.impact import DisciplineImpact
from src.stats.calculator import StatsResult


def prompt_direction() -> str:
    return "Direction? (long/short)"


def prompt_size_usdt() -> str:
    return "Size in USDT notional?"


def prompt_leverage() -> str:
    return "Leverage?"


def prompt_entry_price() -> str:
    return "Entry price?"


def prompt_invalidation(direction: Direction) -> str:
    if direction == Direction.LONG:
        return "Invalidation price? (must be < entry for long)"
    return "Invalidation price? (must be > entry for short)"


def prompt_max_loss_usdt() -> str:
    return "Max loss in USDT?"


def prompt_regime() -> str:
    return "Regime? (uptrend / range / downtrend / event_risk)"


def prompt_thesis() -> str:
    return "One-line thesis (10-280 chars)?"


def leverage_block_warning(threshold: int) -> str:
    return (
        f"Warning: You are about to use ≥{threshold}X leverage. This is the "
        "leverage level that historically destroyed your account. Type a "
        "one-line reason to proceed, or /cancel to lower it."
    )


def validation_error(error_message: str, prompt: str) -> str:
    return f"{error_message}\n{prompt}"


def form_already_in_progress() -> str:
    return "Form already in progress, /cancel first."


def form_cancelled() -> str:
    return "Form cancelled."


def no_form_in_progress() -> str:
    return "No form in progress."


def form_expired(timeout_seconds: int) -> str:
    return (
        "Form expired after "
        f"{timeout_seconds} seconds of inactivity. Use /new to start again."
    )


def size_cap_exceeded(active_cap: float) -> str:
    cap = format(active_cap, ".10g")
    return f"size_usdt exceeds the active size cap of {cap}. Example: {cap}."


def trade_committed(trade: Trade, *, warn_multiple_open: bool) -> str:
    lines = [
        f"Trade #{trade.id} committed and monitored.",
        (
            "Invalidation: "
            f"{_format_price(trade.invalidation_price)}. Max loss: "
            f"{_format_amount(trade.max_loss_usdt)} USDT. /open to view."
        ),
    ]
    if warn_multiple_open:
        lines.append("Warning: You now have multiple open trades.")
    return "\n".join(lines)


def no_open_trades() -> str:
    return "No open trades. Use /new to commit one."


def open_trades(
    trades: list[Trade],
    *,
    active_breach_trade_ids: set[int] | None = None,
) -> str:
    active_breach_trade_ids = active_breach_trade_ids or set()
    lines = ["Open trades:"]
    for trade in trades:
        suffixes: list[str] = []
        if trade.status == TradeStatus.OPEN_OVERRIDE:
            suffixes.append("OPEN_OVERRIDE")
        if trade.id in active_breach_trade_ids:
            suffixes.append("active breach")
        status_suffix = f" [{', '.join(suffixes)}]" if suffixes else ""
        lines.append(
            f"#{trade.id} {trade.direction.value} "
            f"{_format_amount(trade.size_usdt)} USDT @ "
            f"{_format_price(trade.entry_price)} invalidation "
            f"{_format_price(trade.invalidation_price)}{status_suffix}"
        )
    return "\n".join(lines)


def no_open_trade() -> str:
    return "No open trade."


def trade_not_found_or_closed(trade_id: int) -> str:
    return f"Trade {trade_id} not found or already closed."


def no_active_breach() -> str:
    return "No active breach to justify."


def close_confirmation(trade: Trade, streak: int) -> str:
    close_price = trade.close_price if trade.close_price is not None else 0.0
    realized_pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    return (
        f"Trade #{trade.id} closed at {_format_price(close_price)}. "
        f"Realized P&L: {_format_signed_amount(realized_pnl)} USDT. "
        f"Streak: {streak}."
    )


def justification_recorded(trade_id: int) -> str:
    return (
        f"Trade #{trade_id} marked OPEN_OVERRIDE. Justification recorded. "
        "Monitoring resumed."
    )


def streak_status(streak: int, active_cap: float | None) -> str:
    if active_cap is None:
        return f"Current losing streak: {streak}. No active size cap."
    return (
        f"Current losing streak: {streak}. "
        f"Active size cap: {_format_amount(active_cap)} USDT."
    )


def stats_usage() -> str:
    return "Usage: /stats [days]"


def stats_empty(days: int) -> str:
    return f"No closed trades in the last {days} days."


def stats_summary(days: int, body: str) -> str:
    return f"Stats ({days}d):\n{body}"


def format_stats(stats: StatsResult) -> str:
    """Render `/stats` output from computed REQ-007 metrics."""

    regime_lines = ", ".join(
        f"{regime}={_format_signed_amount(pnl)}"
        for regime, pnl in stats.pnl_by_regime.items()
    )
    body = "\n".join(
        [
            (
                f"Trades: total={stats.total_trades}, wins={stats.wins}, "
                f"losses={stats.losses}, breakeven={stats.breakeven}"
            ),
            (
                f"Win rate: {stats.win_rate:.1%}. Realized P&L: "
                f"{_format_signed_amount(stats.total_realized_pnl)} USDT."
            ),
            (
                f"Breaches: {stats.breach_count}. Adherence rate: "
                f"{stats.adherence_rate:.1%}."
            ),
            f"Leverage overrides: {stats.leverage_override_count}.",
            (
                "Size reduction: enforced="
                f"{stats.size_reduction_enforcement_count}, compliance="
                f"{stats.size_reduction_compliance_rate:.1%}."
            ),
            f"P&L by regime: {regime_lines}.",
        ]
    )
    return stats_summary(stats.window_days, body)


def weekly_summary(stats: StatsResult) -> str:
    """Render the weekly-report message."""

    return "Weekly summary:\n" + format_stats(stats).split("\n", 1)[1]


def setpnl_usage() -> str:
    return "Usage: /setpnl <trade_id> <pnl>"


def setpnl_confirmation(trade_id: int, pnl: float, streak: int) -> str:
    return (
        f"Trade #{trade_id} P&L updated to {_format_signed_amount(pnl)} USDT. "
        f"Streak: {streak}."
    )


def closed_usage() -> str:
    return "Usage: /closed <price> or /closed <id> <price>"


def justify_usage() -> str:
    return "Usage: /justify <trade_id> <reason> or /justify <reason>"


EDITABLE_FIELDS = (
    "direction",
    "size_usdt",
    "leverage",
    "leverage_override_reason",
    "entry_price",
    "invalidation_price",
    "max_loss_usdt",
    "regime",
    "thesis",
)

EDIT_CLOSED_FIELDS = (
    "direction",
    "size_usdt",
    "leverage",
    "leverage_override_reason",
    "entry_price",
    "invalidation_price",
    "max_loss_usdt",
    "regime",
    "thesis",
    "opened_at",
    "closed_at",
    "close_price",
)


def edit_usage() -> str:
    fields = ", ".join(EDITABLE_FIELDS)
    return (
        "Usage: /edit <trade_id> <field1>=<value1> [<field2>=<value2> ...]\n"
        f"Editable fields: {fields}"
    )


def edit_invalid_format() -> str:
    return edit_usage()


def edit_invalid_field(field: str) -> str:
    fields = ", ".join(EDITABLE_FIELDS)
    return f"Field '{field}' cannot be edited. Editable fields: {fields}"


def edit_trade_not_found(trade_id: int) -> str:
    return f"Trade {trade_id} not found"


def edit_trade_closed(trade_id: int) -> str:
    return f"Trade {trade_id} is closed, only open trades can be edited"


def edit_validation_error(field: str, message: str) -> str:
    return f"{field}: {message}"


def edit_high_leverage_no_reason() -> str:
    return "Leverage >= 20 requires justification"


def edit_confirmation(trade: Trade, updated_fields: list[str]) -> str:
    fields = ", ".join(updated_fields)
    return "\n".join(
        [
            f"Trade #{trade.id} updated: {fields}.",
            (
                f"{trade.direction.value} {_format_amount(trade.size_usdt)} USDT "
                f"{trade.leverage}x @ {_format_price(trade.entry_price)}"
            ),
            (
                f"Invalidation: {_format_price(trade.invalidation_price)}. "
                f"Max loss: {_format_amount(trade.max_loss_usdt)} USDT."
            ),
            f"Regime: {trade.regime.value}. Thesis: {trade.thesis}",
        ]
    )


def edit_closed_usage() -> str:
    fields = ", ".join(EDIT_CLOSED_FIELDS)
    return (
        "Usage: /edit_closed <trade_id> <field1>=<value1> "
        "[<field2>=<value2> ...]\n"
        f"Editable fields: {fields}"
    )


def edit_closed_not_found(trade_id: int) -> str:
    return f"Trade {trade_id} not found."


def edit_closed_not_closed(trade_id: int) -> str:
    return f"Trade {trade_id} is not closed. Use /edit for open trades."


def edit_closed_invalid_field(field: str) -> str:
    fields = ", ".join(EDIT_CLOSED_FIELDS)
    return f"Field '{field}' cannot be edited. Editable fields: {fields}"


def edit_closed_validation_error(field: str, message: str) -> str:
    return f"{field}: {message}"


def edit_closed_preview(
    changes: dict[str, tuple[object, object]],
    recomputed_pnl: float | None,
    impact: DisciplineImpact,
    pnl_override_warning: bool,
) -> str:
    lines = ["Preview closed-trade edit:"]
    for field, (old_value, new_value) in changes.items():
        lines.append(
            f"{field}: {_format_field_value(old_value)} "
            f"→ {_format_field_value(new_value)}"
        )
    if recomputed_pnl is not None:
        lines.append(
            "Recomputed realized P&L: " f"{_format_signed_amount(recomputed_pnl)} USDT."
        )
    lines.extend(
        [
            (
                "Consecutive-loss streak: "
                f"{impact.streak_before} → {impact.streak_after}."
            ),
            (
                "Active size cap: "
                f"{_format_optional_cap(impact.cap_before)} → "
                f"{_format_optional_cap(impact.cap_after)}."
            ),
        ]
    )
    if pnl_override_warning:
        lines.append(
            "This overwrites a prior /setpnl manual P&L override. "
            "Use /setpnl after confirming if you want a manual P&L value."
        )
    lines.append("Reply yes to apply, or no to cancel.")
    return "\n".join(lines)


def edit_closed_applied(trade: Trade, changed_fields: list[str]) -> str:
    fields = ", ".join(changed_fields)
    close_price = trade.close_price if trade.close_price is not None else 0.0
    realized_pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    return "\n".join(
        [
            f"Trade #{trade.id} updated: {fields}.",
            (
                f"{trade.direction.value} {_format_amount(trade.size_usdt)} USDT "
                f"{trade.leverage}x @ {_format_price(trade.entry_price)}"
            ),
            (
                f"Closed at {_format_price(close_price)}. Realized P&L: "
                f"{_format_signed_amount(realized_pnl)} USDT."
            ),
            f"Regime: {trade.regime.value}. Thesis: {trade.thesis}",
        ]
    )


def edit_closed_cancelled() -> str:
    return "Closed-trade edit cancelled."


def health_status(payload: dict[str, object | None]) -> str:
    lines = [
        f"Websocket: {payload['websocket_status']}",
        f"Last tick age: {_format_optional_seconds(payload['last_tick_age_seconds'])}",
        f"Open trades: {payload['open_trade_count']}",
        f"Last error: {payload['last_error'] or 'none'}",
        f"Redis connected: {payload['redis_connected']}",
        f"Redis AOF enabled: {payload['redis_appendonly_enabled']}",
        f"Redis persistence dir: {payload['redis_persistence_dir'] or 'unknown'}",
        ("Redis persistence writable: " f"{payload['redis_persistence_dir_writable']}"),
        (
            "Redis AOF last write status: "
            f"{payload['redis_aof_last_write_status'] or 'unknown'}"
        ),
        f"Redis last error: {payload['redis_last_error'] or 'none'}",
    ]
    return "\n".join(lines)


def signals_stub() -> str:
    return "Intelligence layer not configured. v2 feature — see REQ-010."


def help_overview() -> str:
    return "\n".join(
        [
            "Commands:",
            "/new",
            "/closed <price> or /closed <id> <price>",
            "/justify <trade_id> <reason> or /justify <reason>",
            "/cancel",
            "/open",
            "/streak",
            "/stats [days]",
            "/setpnl <trade_id> <pnl>",
            "/edit <trade_id> field=value [...]",
            "/edit_closed <trade_id> field=value [...]",
            "/health",
            "/signals",
            "/help [cmd]",
        ]
    )


def help_for(command: str) -> str:
    normalized = command.strip().lstrip("/").lower()
    help_map = {
        "new": "/new: Start the pre-trade commitment form.",
        "closed": "/closed <price> or /closed <id> <price>: Close an open trade.",
        "justify": (
            "/justify <trade_id> <reason> or /justify <reason>: "
            "Resolve an active breach and resume monitoring."
        ),
        "cancel": "/cancel: Cancel the in-progress form only.",
        "open": "/open: List trades in OPEN or OPEN_OVERRIDE.",
        "streak": "/streak: Show the consecutive-loss streak and active size cap.",
        "stats": "/stats [days]: Show rolling adherence and P&L stats.",
        "setpnl": "/setpnl <trade_id> <pnl>: Override realized P&L for a closed trade.",
        "edit": "/edit <trade_id> field=value [...]: Edit an open trade.",
        "edit_closed": (
            "/edit_closed <trade_id> field=value [...]: Edit a closed trade "
            "after preview confirmation. Editable fields: "
            f"{', '.join(EDIT_CLOSED_FIELDS)}."
        ),
        "health": "/health: Show websocket and Redis health status.",
        "signals": "/signals: Show the v1 intelligence stub response.",
        "help": "/help [cmd]: Show the command list or help for one command.",
    }
    return help_map.get(normalized, "Unknown command. Use /help.")


def unknown_command() -> str:
    return "Unknown command. Use /help."


def internal_error() -> str:
    return "Internal error, try again."


def breach_initial_alert(
    trade: Trade,
    current_price: float,
    elapsed_seconds: int,
    current_loss_usdt: float,
) -> str:
    return (
        f"Alert: Trade #{trade.id} invalidation breached at "
        f"{_format_price(current_price)}. Elapsed: {elapsed_seconds}s. "
        f"Estimated loss: {_format_amount(current_loss_usdt)} USDT. "
        f"Reply /closed {trade.id} <price> or /justify {trade.id} <reason>."
    )


def breach_escalation_alert(
    trade: Trade,
    current_price: float,
    elapsed_seconds: int,
    current_loss_usdt: float,
) -> str:
    return (
        f"Alert: Trade #{trade.id} breach still unresolved at "
        f"{_format_price(current_price)} after {elapsed_seconds}s. "
        f"Estimated loss: {_format_amount(current_loss_usdt)} USDT. "
        f"Reply /closed {trade.id} <price> or /justify {trade.id} <reason>."
    )


def monitor_down_alert(
    duration_seconds: int,
    reconnect_attempt: int,
    has_open_trades: bool,
) -> str:
    exposure = "with open trades" if has_open_trades else "with no open trades"
    return (
        "Warning: Price monitor is down "
        f"for {duration_seconds}s ({exposure}). "
        f"Reconnect attempt: {reconnect_attempt}."
    )


def monitor_recovery_alert(duration_seconds: int, gap_seconds: int | None) -> str:
    gap_text = "none" if gap_seconds is None else str(gap_seconds)
    return (
        f"Recovery: Price monitor reconnected after {duration_seconds}s. "
        f"Coverage gap: {gap_text}s."
    )


def heartbeat_alert(last_tick_age_seconds: float | None, open_trade_count: int) -> str:
    return (
        "Heartbeat: Monitor healthy. "
        f"Last tick age: {_format_optional_seconds(last_tick_age_seconds)}. "
        f"Open trades: {open_trade_count}."
    )


def _format_price(value: float) -> str:
    return _trimmed_number(value)


def _format_amount(value: float) -> str:
    return _trimmed_number(value)


def _format_signed_amount(value: float) -> str:
    formatted = _trimmed_number(abs(value))
    if value > 0:
        return f"+{formatted}"
    if value < 0:
        return f"-{formatted}"
    return formatted


def _format_optional_seconds(value: object | None) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}s"
    return str(value)


def _format_optional_cap(value: float | None) -> str:
    if value is None:
        return "none"
    return f"{_format_amount(value)} USDT"


def _format_field_value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return _trimmed_number(value)
    return str(value)


def _trimmed_number(value: float) -> str:
    return format(value, ".10g")
