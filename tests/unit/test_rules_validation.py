"""Unit tests for pure field validators."""

from __future__ import annotations

import pytest

from src.models.trade import Direction, Regime
from src.rules import validation


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("long", Direction.LONG),
        ("short", Direction.SHORT),
    ],
)
def test_validate_direction_accepts_valid_values(
    raw_value: str,
    expected: Direction,
) -> None:
    """REQ-001: direction validation accepts the allowed enum values."""

    assert validation.validate_direction(raw_value) == expected


@pytest.mark.parametrize("raw_value", ["", "LONG", "flat"])
def test_validate_direction_rejects_invalid_values(raw_value: str) -> None:
    """REQ-001: direction validation rejects empty or unsupported values."""

    with pytest.raises(ValueError, match="direction must"):
        validation.validate_direction(raw_value)


def test_validate_positive_numeric_fields() -> None:
    """REQ-001: numeric validators accept positive values and numeric strings."""

    assert validation.validate_size_usdt("5000") == 5000.0
    assert validation.validate_entry_price(82500) == 82500.0
    assert validation.validate_invalidation_price("81200") == 81200.0
    assert validation.validate_max_loss_usdt(160) == 160.0


@pytest.mark.parametrize(
    ("validator", "value", "field_name"),
    [
        (validation.validate_size_usdt, "0", "size_usdt"),
        (validation.validate_entry_price, -1, "entry_price"),
        (validation.validate_invalidation_price, "abc", "invalidation_price"),
        (validation.validate_max_loss_usdt, False, "max_loss_usdt"),
    ],
)
def test_validate_positive_numeric_fields_reject_invalid_values(
    validator: object,
    value: object,
    field_name: str,
) -> None:
    """REQ-001: numeric validators reject non-positive and non-numeric input."""

    with pytest.raises(ValueError, match=field_name):
        validator(value)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1", 1),
        (20, 20),
        ("125", 125),
    ],
)
def test_validate_leverage_accepts_valid_range(
    raw_value: object,
    expected: int,
) -> None:
    """REQ-001 / REQ-002: leverage validation accepts integers inside 1..125."""

    assert validation.validate_leverage(raw_value) == expected


@pytest.mark.parametrize("raw_value", ["0", "-1", "126", "10.5", "abc", True])
def test_validate_leverage_rejects_invalid_values(raw_value: object) -> None:
    """REQ-001 / REQ-002: leverage validation rejects invalid input."""

    with pytest.raises(ValueError, match="leverage"):
        validation.validate_leverage(raw_value)


def test_validate_invalidation_side_for_long_and_short() -> None:
    """REQ-001: invalidation-side checks enforce long and short comparisons."""

    validation.validate_invalidation_side(
        direction=Direction.LONG,
        entry_price=82500.0,
        invalidation_price=81200.0,
    )
    validation.validate_invalidation_side(
        direction=Direction.SHORT,
        entry_price=82500.0,
        invalidation_price=83800.0,
    )

    with pytest.raises(ValueError, match="less than entry_price"):
        validation.validate_invalidation_side(
            direction=Direction.LONG,
            entry_price=82500.0,
            invalidation_price=82500.0,
        )

    with pytest.raises(ValueError, match="greater than entry_price"):
        validation.validate_invalidation_side(
            direction=Direction.SHORT,
            entry_price=82500.0,
            invalidation_price=82000.0,
        )


def test_validate_regime_accepts_expected_values() -> None:
    """REQ-001: regime validation accepts only the declared regime labels."""

    assert validation.validate_regime("uptrend") == Regime.UPTREND
    assert validation.validate_regime("range") == Regime.RANGE
    assert validation.validate_regime("downtrend") == Regime.DOWNTREND
    assert validation.validate_regime("event_risk") == Regime.EVENT_RISK


@pytest.mark.parametrize("raw_value", ["", "UPTREND", "sideways"])
def test_validate_regime_rejects_invalid_values(raw_value: str) -> None:
    """REQ-001: regime validation rejects invalid labels with a clear message."""

    with pytest.raises(ValueError, match="regime must be one of"):
        validation.validate_regime(raw_value)


def test_validate_thesis_accepts_length_bounds() -> None:
    """REQ-001: thesis validation accepts values within the 10..280 character bounds."""

    assert validation.validate_thesis("x" * 10) == "x" * 10
    assert validation.validate_thesis("x" * 280) == "x" * 280


@pytest.mark.parametrize("raw_value", ["", "short", "x" * 281])
def test_validate_thesis_rejects_invalid_lengths(raw_value: str) -> None:
    """REQ-001: thesis validation rejects empty, too-short, and too-long values."""

    with pytest.raises(ValueError, match="thesis"):
        validation.validate_thesis(raw_value)


def test_validate_leverage_override_reason_accepts_valid_text() -> None:
    """REQ-002: leverage override reasons accept 10..500 characters."""

    assert (
        validation.validate_leverage_override_reason(
            "Tight stop around a defined event."
        )
        == "Tight stop around a defined event."
    )


@pytest.mark.parametrize("raw_value", ["short", "", 123])
def test_validate_leverage_override_reason_rejects_invalid_text(
    raw_value: object,
) -> None:
    """REQ-002: leverage override reasons reject short or non-string input."""

    with pytest.raises(ValueError, match="leverage_override_reason"):
        validation.validate_leverage_override_reason(raw_value)


def test_validate_justification_accepts_valid_text() -> None:
    """REQ-005: breach justification accepts 5..500 characters."""

    assert validation.validate_justification("Still valid on retest.") == (
        "Still valid on retest."
    )


@pytest.mark.parametrize("raw_value", ["no", "", 123])
def test_validate_justification_rejects_invalid_text(raw_value: object) -> None:
    """REQ-005: breach justification rejects short or non-string input."""

    with pytest.raises(ValueError, match="justification"):
        validation.validate_justification(raw_value)
