"""Unit tests for REQ-008 and REQ-011 configuration behavior."""

from __future__ import annotations

from datetime import time

import pytest
from pydantic import ValidationError

from src.config import Settings, load_settings, settings


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
    monkeypatch.setenv("TIMEZONE", "Asia/Hong_Kong")
    monkeypatch.setenv("HEARTBEAT_TIME_LOCAL", "07:30")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")

    settings = Settings(_env_file=None)

    assert settings.telegram_bot_token_value == "123:abc"
    assert settings.telegram_chat_id == 42
    assert settings.leverage_block_threshold == 10
    assert settings.hyperliquid_ws_url == "wss://api.hyperliquid.xyz/ws"
    assert settings.hyperliquid_info_url == "https://api.hyperliquid.xyz/info"
    assert settings.hyperliquid_universe_refresh_seconds == 300
    assert settings.hyperliquid_universe_stale_seconds == 900
    assert settings.hyperliquid_feed_stale_seconds == 30
    assert settings.hyperliquid_feed_request_timeout_seconds == 5
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
    monkeypatch.setenv("HYPERLIQUID_WS_URL", " ")
    monkeypatch.setenv("HYPERLIQUID_UNIVERSE_REFRESH_SECONDS", "0")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    error_fields = {error["loc"][0] for error in excinfo.value.errors()}
    assert error_fields >= {
        "timezone",
        "redis_appendonly",
        "size_reduction_factor",
        "hyperliquid_ws_url",
        "hyperliquid_universe_refresh_seconds",
    }


def test_settings_proxy_exposes_uppercase_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6: command-line checks can read Hyperliquid env names directly."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("HYPERLIQUID_WS_URL", "wss://example.test/ws")
    load_settings.cache_clear()

    try:
        assert settings.HYPERLIQUID_WS_URL == "wss://example.test/ws"
    finally:
        load_settings.cache_clear()
