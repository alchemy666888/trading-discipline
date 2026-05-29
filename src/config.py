"""Application settings loaded from environment variables and .env."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the trading discipline bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    telegram_bot_token: SecretStr
    telegram_chat_id: int
    leverage_block_threshold: int = 10
    consecutive_loss_threshold: int = 2
    size_reduction_factor: float = 0.5
    hyperliquid_ws_url: str = "wss://api.hyperliquid.xyz/ws"
    hyperliquid_info_url: str = "https://api.hyperliquid.xyz/info"
    hyperliquid_universe_refresh_seconds: int = 300
    hyperliquid_universe_stale_seconds: int = 900
    hyperliquid_feed_stale_seconds: int = 30
    hyperliquid_feed_request_timeout_seconds: int = 5
    form_timeout_seconds: int = 600
    alert_interval_first_window_seconds: int = 60
    alert_interval_first_window_duration_seconds: int = 300
    alert_interval_after_seconds: int = 300
    monitor_down_alert_delay_with_open_trades_seconds: int = 10
    monitor_down_alert_delay_no_open_trades_seconds: int = 60
    monitor_down_repeat_with_open_trades_seconds: int = 60
    monitor_down_repeat_no_open_trades_seconds: int = 300
    heartbeat_time_local: time = time(hour=9, minute=0)
    heartbeat_file_path: str | None = None
    timezone: str = "UTC"
    redis_url: str = "redis://redis:6379/0"
    redis_data_dir: str = "./data/redis"
    redis_appendonly: str = "yes"
    compose_project_name: str = "btc-discipline-bot"

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_telegram_bot_token(cls, value: SecretStr) -> SecretStr:
        """Ensure the token is present and non-empty."""

        if not value.get_secret_value().strip():
            msg = "TELEGRAM_BOT_TOKEN must not be empty."
            raise ValueError(msg)
        return value

    @field_validator(
        "leverage_block_threshold",
        "consecutive_loss_threshold",
        "hyperliquid_universe_refresh_seconds",
        "hyperliquid_universe_stale_seconds",
        "hyperliquid_feed_stale_seconds",
        "hyperliquid_feed_request_timeout_seconds",
        "form_timeout_seconds",
        "alert_interval_first_window_seconds",
        "alert_interval_first_window_duration_seconds",
        "alert_interval_after_seconds",
        "monitor_down_alert_delay_with_open_trades_seconds",
        "monitor_down_alert_delay_no_open_trades_seconds",
        "monitor_down_repeat_with_open_trades_seconds",
        "monitor_down_repeat_no_open_trades_seconds",
    )
    @classmethod
    def validate_positive_ints(cls, value: int, info: ValidationInfo) -> int:
        """Ensure timing and threshold integers stay positive."""

        if value <= 0:
            msg = f"{info.field_name} must be greater than 0."
            raise ValueError(msg)
        return value

    @field_validator("leverage_block_threshold")
    @classmethod
    def validate_leverage_threshold(cls, value: int) -> int:
        """Ensure the leverage threshold fits the supported leverage range."""

        if value > 125:
            msg = "LEVERAGE_BLOCK_THRESHOLD must be less than or equal to 125."
            raise ValueError(msg)
        return value

    @field_validator("size_reduction_factor")
    @classmethod
    def validate_size_reduction_factor(cls, value: float) -> float:
        """Ensure the reduction factor is a usable fraction."""

        if not 0 < value <= 1:
            msg = (
                "SIZE_REDUCTION_FACTOR must be greater than 0 and less than or "
                "equal to 1."
            )
            raise ValueError(msg)
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Validate the configured IANA timezone string."""

        normalized = value.strip()
        if not normalized:
            msg = "TIMEZONE must not be empty."
            raise ValueError(msg)
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            msg = f"TIMEZONE {normalized!r} is not a valid IANA timezone."
            raise ValueError(msg) from exc
        return normalized

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        """Require a redis or rediss connection URL."""

        parsed = urlparse(value)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.netloc:
            msg = "REDIS_URL must be a redis:// or rediss:// URL."
            raise ValueError(msg)
        return value

    @field_validator(
        "hyperliquid_ws_url",
        "hyperliquid_info_url",
        "redis_data_dir",
        "compose_project_name",
    )
    @classmethod
    def validate_non_empty_strings(cls, value: str, info: ValidationInfo) -> str:
        """Ensure non-empty string settings."""

        normalized = value.strip()
        if not normalized:
            msg = f"{info.field_name} must not be empty."
            raise ValueError(msg)
        return normalized

    @field_validator("redis_appendonly")
    @classmethod
    def validate_redis_appendonly(cls, value: str) -> str:
        """Restrict Redis append-only mode to explicit yes/no values."""

        normalized = value.strip().lower()
        if normalized not in {"yes", "no"}:
            msg = "REDIS_APPENDONLY must be either 'yes' or 'no'."
            raise ValueError(msg)
        return normalized

    @property
    def timezone_info(self) -> ZoneInfo:
        """Return the configured timezone as a ZoneInfo instance."""

        return ZoneInfo(self.timezone)

    @property
    def telegram_bot_token_value(self) -> str:
        """Expose the Telegram token to runtime code without leaking it in reprs."""

        return self.telegram_bot_token.get_secret_value()


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Construct and cache application settings."""

    return Settings()


class _SettingsProxy:
    """Lazily expose configured settings for small command-line checks."""

    def __getattr__(self, name: str) -> Any:
        loaded = load_settings()
        if name.isupper():
            return getattr(loaded, name.lower())
        return getattr(loaded, name)


settings: _SettingsProxy = _SettingsProxy()
