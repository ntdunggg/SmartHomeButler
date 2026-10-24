"""Regression contracts for rich chat messages staying inside their viewport."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
FRONTEND_HTML = ROOT / "frontend" / "home_assistant.html"


def _rule(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>[^}}]+)\}}", source)
    assert match, f"Missing CSS rule for {selector}"
    return match.group("body")


def _assert_rich_message_width_contract(source: str, chat_log_selector: str) -> None:
    chat_log = _rule(source, chat_log_selector)
    message = _rule(source, ".home-msg")
    bubble = _rule(source, ".home-msg .msg-bubble")

    assert "overflow-x: hidden" in chat_log
    assert "width: 92%" in message
    assert "min-width: 0" in message
    assert "min-width: 0" in bubble
    assert "max-width: calc(100% -" in bubble
    assert "overflow-wrap: anywhere" in bubble
    assert "max-width: 100%" not in bubble


def test_frontend_rich_chat_messages_cannot_overflow_horizontally():
    _assert_rich_message_width_contract(
        FRONTEND_HTML.read_text(encoding="utf-8"),
        ".chat-body",
    )


def test_frontend_agent_chat_serializes_requests_and_has_one_terminal_reply():
    source = FRONTEND_HTML.read_text(encoding="utf-8")

    assert "let agentMessageInFlight = false;" in source
    assert "if (agentMessageInFlight) return;" in source
    assert "agentMessageInFlight = true;" in source
    assert "agentMessageInFlight = false;" in source
    assert "body.setAttribute('aria-busy', 'true');" in source
    assert "body.removeAttribute('aria-busy');" in source
    assert "Post-command dashboard refresh error:" in source
