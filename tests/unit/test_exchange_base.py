"""Unit tests for the exchange adapter ABC."""

from __future__ import annotations

import pytest

from src.exchange.base import ExchangeAdapter


def test_exchange_adapter_requires_abstract_methods() -> None:
    """REQ-004: abstract exchange adapters cannot be instantiated without methods."""

    class IncompleteAdapter(ExchangeAdapter):
        pass

    with pytest.raises(TypeError):
        IncompleteAdapter()
