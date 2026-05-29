"""Unit tests for Hyperliquid symbol validation."""

from __future__ import annotations

import pytest

from src.rules.validation import validate_symbol


def test_validate_symbol_accepts_canonical_names() -> None:
    """R1/R2: canonical universe symbols are accepted unchanged."""

    universe = {"BTC", "ETH", "HYPE", "AUDUSD"}

    assert validate_symbol("BTC", universe) == "BTC"
    assert validate_symbol("AUDUSD", universe) == "AUDUSD"


def test_validate_symbol_trims_and_uppercases_before_exact_match() -> None:
    """R1/R2: user input is normalized before exact universe membership."""

    universe = {"BTC", "ETH", "HYPE", "AUDUSD"}

    assert validate_symbol("  btc ", universe) == "BTC"


@pytest.mark.parametrize("value", ["DOGE", "", "   ", 123, None])
def test_validate_symbol_rejects_unknown_empty_or_non_string(value: object) -> None:
    """R1/R2: invalid symbols raise a field-named validation error."""

    with pytest.raises(ValueError, match="symbol must be"):
        validate_symbol(value, {"BTC", "ETH"})
