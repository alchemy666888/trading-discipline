"""Unit tests for REQ-008 and REQ-011 configuration behavior."""

from __future__ import annotations

from datetime import time

import pytest
from pydantic import ValidationError

from src.config import Settings


def test_settings_require_telegram_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-008 / REQ-011: missing required Telegram env vars must fail clearly."""

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    errors = excinfo.value.errors()
    assert {error["loc"][0] for error in errors} >= {
        "telegram_bot_token",
        "telegram_chat_id",
    }


def test_settings_load_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-008 / REQ-011: valid env input loads configured defaults and overrides."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("EXCHANGE", "BYBIT")
    monkeypatch.setenv("TIMEZONE", "Asia/Hong_Kong")
    monkeypatch.setenv("HEARTBEAT_TIME_LOCAL", "07:30")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")

    settings = Settings(_env_file=None)

    assert settings.telegram_bot_token_value == "123:abc"
    assert settings.telegram_chat_id == 42
    assert settings.exchange == "bybit"
    assert settings.symbol == "BTCUSDT"
    assert settings.heartbeat_time_local == time(hour=7, minute=30)
    assert settings.timezone == "Asia/Hong_Kong"
    assert settings.redis_url == "redis://localhost:6379/1"


def test_settings_reject_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-008 / REQ-011: invalid settings must fail validation before startup."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TIMEZONE", "Mars/Olympus")
    monkeypatch.setenv("REDIS_APPENDONLY", "maybe")
    monkeypatch.setenv("SIZE_REDUCTION_FACTOR", "0")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    error_fields = {error["loc"][0] for error in excinfo.value.errors()}
    assert error_fields >= {"timezone", "redis_appendonly", "size_reduction_factor"}
