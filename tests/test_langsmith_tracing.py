"""LangSmith setup stays opt-in and never leaks credentials to logs."""

from __future__ import annotations

import logging
import os

from src.config import Settings
from src.main import log_langsmith_runtime_config
from src.observability import langsmith


def test_disabled_tracing_does_not_configure_environment(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    settings = Settings(_env_file=None, langsmith_tracing=False, langsmith_api_key="key")

    assert langsmith.configure_langsmith(settings) is False
    assert "LANGSMITH_TRACING" not in os.environ


def test_enabled_tracing_configures_privacy_safe_environment(monkeypatch) -> None:
    keys = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_HIDE_INPUTS", "LANGSMITH_HIDE_OUTPUTS")
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="langsmith-test-key",
        langsmith_project="test-project",
        langsmith_hide_inputs=True,
        langsmith_hide_outputs=True,
    )

    try:
        assert langsmith.configure_langsmith(settings) is True
        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGSMITH_PROJECT"] == "test-project"
        assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
        assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_enabled_tracing_wraps_openai_client(monkeypatch) -> None:
    keys = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_HIDE_INPUTS", "LANGSMITH_HIDE_OUTPUTS")
    previous = {key: os.environ.get(key) for key in keys}
    wrapped: list[object] = []
    client = object()
    monkeypatch.setattr(langsmith, "_wrap_openai", lambda value: wrapped.append(value) or "wrapped-client")
    settings = Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key="langsmith-test-key")

    try:
        assert langsmith.wrap_openai_client(client, settings) == "wrapped-client"
        assert wrapped == [client]
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_startup_log_reports_state_without_api_key(caplog) -> None:
    settings = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="must-not-appear-in-logs",
        langsmith_project="trace-project",
    )

    with caplog.at_level(logging.INFO, logger="src.main"):
        log_langsmith_runtime_config(settings)

    assert "enabled=True" in caplog.text
    assert "project=trace-project" in caplog.text
    assert "must-not-appear-in-logs" not in caplog.text
