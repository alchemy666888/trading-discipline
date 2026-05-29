"""Unit tests for Redis key builders and schema contract."""

from __future__ import annotations

import pytest

from src.db import keyspace
from src.models.trade import TradeStatus


def test_keyspace_builders_match_contract() -> None:
    """REQ-001 / REQ-005 / REQ-010 / REQ-011: key builders match the Redis contract."""

    assert keyspace.trade_id_sequence_key() == "seq:trade_id"
    assert keyspace.breach_id_sequence_key() == "seq:breach_id"
    assert keyspace.alert_id_sequence_key() == "seq:alert_id"
    assert keyspace.signal_id_sequence_key() == "seq:signal_id"
    assert keyspace.trade_key(7) == "trade:7"
    assert keyspace.trades_all_key() == "trades:all"
    assert keyspace.trades_status_key(TradeStatus.OPEN) == "trades:status:OPEN"
    assert keyspace.trades_closed_key() == "trades:closed"
    assert keyspace.breach_key(3) == "breach:3"
    assert keyspace.breaches_trade_key(7) == "breaches:trade:7"
    assert keyspace.breaches_unresolved_key() == "breaches:unresolved"
    assert keyspace.breach_active_key(7) == "breach:active:7"
    assert keyspace.alert_key(2) == "alert:2"
    assert keyspace.alerts_breach_key(3) == "alerts:breach:3"
    assert keyspace.conversation_key(42) == "conversation:42"
    assert keyspace.signal_key(9) == "signals:9"
    assert keyspace.signals_active_key() == "signals:active"
    assert keyspace.hyperliquid_universe_key() == "hyperliquid:universe:perps"
    assert keyspace.schema_version_key() == "schema:version"


def test_keyspace_rejects_invalid_ids_and_statuses() -> None:
    """REQ-011: key builders reject unsupported identifiers and statuses."""

    with pytest.raises(ValueError):
        keyspace.trade_key(0)

    with pytest.raises(ValueError):
        keyspace.breach_key(-1)

    with pytest.raises(ValueError):
        keyspace.trades_status_key("BROKEN")

    with pytest.raises(TypeError):
        keyspace.trade_key("7")  # type: ignore[arg-type]


def test_keyspace_does_not_accept_user_text_for_identifier_keys() -> None:
    """NFR-security: user-controlled text never becomes part of Redis key names."""

    suspicious = "trade:{1}\nseq:trade_id"

    with pytest.raises(TypeError):
        keyspace.trade_key(suspicious)  # type: ignore[arg-type]
