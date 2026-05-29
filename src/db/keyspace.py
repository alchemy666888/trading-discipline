"""Redis key builders for the trading discipline bot."""

from __future__ import annotations

from src.models.trade import TradeStatus

SCHEMA_VERSION = 1

_TRADE_STATUS_VALUES = {status.value for status in TradeStatus}


def _require_positive_int(value: int, *, field_name: str) -> int:
    if not isinstance(value, int):
        msg = f"{field_name} must be an int."
        raise TypeError(msg)
    if value <= 0:
        msg = f"{field_name} must be greater than 0."
        raise ValueError(msg)
    return value


def _require_chat_id(value: int) -> int:
    if not isinstance(value, int):
        msg = "chat_id must be an int."
        raise TypeError(msg)
    return value


def _normalize_status(status: TradeStatus | str) -> str:
    normalized = status.value if isinstance(status, TradeStatus) else status
    if normalized not in _TRADE_STATUS_VALUES:
        msg = f"Unsupported trade status: {status!r}."
        raise ValueError(msg)
    return normalized


def trade_id_sequence_key() -> str:
    return "seq:trade_id"


def breach_id_sequence_key() -> str:
    return "seq:breach_id"


def alert_id_sequence_key() -> str:
    return "seq:alert_id"


def signal_id_sequence_key() -> str:
    return "seq:signal_id"


def trade_key(trade_id: int) -> str:
    return f"trade:{_require_positive_int(trade_id, field_name='trade_id')}"


def trades_all_key() -> str:
    return "trades:all"


def trades_status_key(status: TradeStatus | str) -> str:
    return f"trades:status:{_normalize_status(status)}"


def trades_closed_key() -> str:
    return "trades:closed"


def breach_key(breach_id: int) -> str:
    return f"breach:{_require_positive_int(breach_id, field_name='breach_id')}"


def breaches_trade_key(trade_id: int) -> str:
    return f"breaches:trade:{_require_positive_int(trade_id, field_name='trade_id')}"


def breaches_unresolved_key() -> str:
    return "breaches:unresolved"


def breach_active_key(trade_id: int) -> str:
    return f"breach:active:{_require_positive_int(trade_id, field_name='trade_id')}"


def alert_key(alert_id: int) -> str:
    return f"alert:{_require_positive_int(alert_id, field_name='alert_id')}"


def alerts_breach_key(breach_id: int) -> str:
    return f"alerts:breach:{_require_positive_int(breach_id, field_name='breach_id')}"


def conversation_key(chat_id: int) -> str:
    return f"conversation:{_require_chat_id(chat_id)}"


def signal_key(signal_id: int) -> str:
    return f"signals:{_require_positive_int(signal_id, field_name='signal_id')}"


def signals_active_key() -> str:
    return "signals:active"


def hyperliquid_universe_key() -> str:
    """Return the Hyperliquid universe cache key.

    This key lives outside the trade, breach, alert, conversation, and signals
    namespaces, so it does not widen the REQ-010 intelligence write boundary.
    """

    return "hyperliquid:universe:perps"


def schema_version_key() -> str:
    return "schema:version"
