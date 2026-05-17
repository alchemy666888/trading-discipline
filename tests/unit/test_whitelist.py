"""Unit tests for the Telegram chat whitelist decorator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.bot import whitelist as whitelist_module
from src.bot.whitelist import whitelisted


class RecordingLogger:
    """Capture warn events from the whitelist guard."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))


class FakeMessage:
    """Minimal Telegram message fake."""

    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    """Minimal Telegram update fake."""

    def __init__(self, chat_id: int) -> None:
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage()


class DummyHandler:
    """Bound-method target for the whitelist decorator."""

    def __init__(self, allowed_chat_id: int) -> None:
        self._settings = SimpleNamespace(telegram_chat_id=allowed_chat_id)
        self.called = False

    @whitelisted
    async def handle(self, update: FakeUpdate) -> None:
        self.called = True
        await update.effective_message.reply_text("ok")


@pytest.mark.asyncio
async def test_whitelist_allows_configured_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFR-security: the whitelisted chat ID is allowed through."""

    logger = RecordingLogger()
    monkeypatch.setattr(whitelist_module, "LOGGER", logger)
    handler = DummyHandler(allowed_chat_id=42)
    update = FakeUpdate(chat_id=42)

    await handler.handle(update)

    assert handler.called is True
    assert update.effective_message.replies == ["ok"]
    assert logger.events == []


@pytest.mark.asyncio
async def test_whitelist_ignores_non_whitelisted_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFR-security / BR-4: non-whitelisted chats get no reply and log a WARN."""

    logger = RecordingLogger()
    monkeypatch.setattr(whitelist_module, "LOGGER", logger)
    handler = DummyHandler(allowed_chat_id=42)
    update = FakeUpdate(chat_id=99)

    await handler.handle(update)

    assert handler.called is False
    assert update.effective_message.replies == []
    assert logger.events == [("telegram_chat_not_whitelisted", {"chat_id": 99})]
