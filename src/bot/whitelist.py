"""Chat whitelist guard for Telegram handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar, cast

import structlog

from src.config import load_settings

HandlerFn = Callable[..., Awaitable[None]]
F = TypeVar("F", bound=HandlerFn)

LOGGER = structlog.get_logger(__name__)


def whitelisted(func: F) -> F:
    """Drop updates from non-whitelisted chat IDs with a WARN log."""

    @wraps(func)
    async def wrapper(*args: object, **kwargs: object) -> None:
        update = _extract_update(args, kwargs)
        if update is None:
            return

        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id != _allowed_chat_id(args):
            LOGGER.warning("telegram_chat_not_whitelisted", chat_id=chat_id)
            return

        await func(*args, **kwargs)

    return cast(F, wrapper)


def _extract_update(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object | None:
    if "update" in kwargs:
        return kwargs["update"]
    for candidate in args[:2]:
        if hasattr(candidate, "effective_chat"):
            return candidate
    return None


def _allowed_chat_id(args: tuple[object, ...]) -> int:
    if args:
        owner = args[0]
        settings = getattr(owner, "_settings", None)
        if settings is not None:
            return int(settings.telegram_chat_id)
    return load_settings().telegram_chat_id
