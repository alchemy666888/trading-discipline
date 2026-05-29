"""Unit tests for REQ-001, REQ-005, and REQ-010 model contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from src.models.alert import Alert
from src.models.breach import Breach, BreachUserResponse
from src.models.conversation import ConversationState, ConversationStep
from src.models.events import Event, EventType, TickEvent, TickPayload
from src.models.signal import Severity, Signal
from src.models.trade import Direction, Regime, Trade, TradeDraft, TradeStatus
from src.rules.context import RuleContext


def _now() -> datetime:
    return datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def test_trade_round_trip_json_serialization() -> None:
    """REQ-001: trade models round-trip through JSON without losing schema fidelity."""

    trade = Trade(
        id=1,
        symbol="BTC",
        direction=Direction.LONG,
        size_usdt=5000.0,
        leverage=10,
        leverage_override_reason=None,
        entry_price=82500.0,
        invalidation_price=81200.0,
        max_loss_usdt=160.0,
        regime=Regime.UPTREND,
        thesis="Holding above 82K with ETF support and continuation setup.",
        status=TradeStatus.OPEN,
        size_reduction_enforced=False,
        opened_at=_now(),
        closed_at=None,
        close_price=None,
        realized_pnl=None,
    )

    restored = Trade.model_validate_json(trade.model_dump_json())

    assert restored == trade


def test_record_models_round_trip_json_serialization() -> None:
    """REQ-005 / REQ-010: non-trade models round-trip through JSON."""

    breach = Breach(
        id=7,
        trade_id=1,
        detected_at=_now(),
        breach_price=81199.0,
        user_response=BreachUserResponse.JUSTIFIED,
        response_at=_now(),
        justification="Price sweep into reclaim attempt.",
    )
    alert = Alert(
        id=9,
        breach_id=7,
        sent_at=_now(),
        escalation_level=1,
        message="Close now or justify the breach.",
    )
    conversation = ConversationState(
        chat_id=42,
        state=ConversationStep.LEVERAGE,
        partial_trade_json='{"direction":"long","size_usdt":5000}',
        updated_at=_now(),
    )
    signal = Signal(
        id=3,
        source="calendar",
        kind="macro_event",
        severity=Severity.CRITICAL,
        detected_at=_now(),
        expires_at=_now() + timedelta(minutes=15),
        payload_json='{"name":"FOMC"}',
        summary="FOMC begins in 15 minutes.",
    )
    event_adapter = TypeAdapter(Event)
    event = TickEvent(ts=_now(), payload=TickPayload(symbol="BTC", price=82510.5))

    assert Breach.model_validate_json(breach.model_dump_json()) == breach
    assert Alert.model_validate_json(alert.model_dump_json()) == alert
    assert (
        ConversationState.model_validate_json(conversation.model_dump_json())
        == conversation
    )
    assert Signal.model_validate_json(signal.model_dump_json()) == signal
    restored_event = event_adapter.validate_json(event_adapter.dump_json(event))
    assert restored_event == event
    assert restored_event.type == EventType.TICK


def test_invalid_values_raise_validation_errors() -> None:
    """REQ-001 / REQ-005 / REQ-010: invalid values fail fast with ValidationError."""

    with pytest.raises(ValidationError):
        Trade(
            id=1,
            symbol="BTC",
            direction=Direction.SHORT,
            size_usdt=1000.0,
            leverage=5,
            leverage_override_reason=None,
            entry_price=82500.0,
            invalidation_price=82000.0,
            max_loss_usdt=50.0,
            regime=Regime.RANGE,
            thesis="This thesis is long enough.",
            status=TradeStatus.OPEN,
            size_reduction_enforced=False,
            opened_at=_now(),
            closed_at=None,
            close_price=None,
            realized_pnl=None,
        )

    with pytest.raises(ValidationError):
        Breach(
            id=2,
            trade_id=1,
            detected_at=_now(),
            breach_price=82000.0,
            user_response=BreachUserResponse.CLOSED,
            response_at=None,
            justification=None,
        )

    with pytest.raises(ValidationError):
        ConversationState(
            chat_id=42,
            state=ConversationStep.SIZE,
            partial_trade_json="{not-json}",
            updated_at=_now(),
        )

    with pytest.raises(ValidationError):
        Signal(
            id=4,
            source="calendar",
            kind="macro_event",
            severity=Severity.HIGH,
            detected_at=_now(),
            expires_at=_now(),
            payload_json="{}",
            summary="Signal summary.",
        )


def test_rule_context_defaults_signals_to_empty_mapping() -> None:
    """REQ-010: RuleContext defaults signals to an empty mapping in v1."""

    ctx = RuleContext(
        trade_draft=TradeDraft(direction=Direction.LONG),
        recent_trades=[],
    )

    assert ctx.signals == {}
