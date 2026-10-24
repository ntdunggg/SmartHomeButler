"""Regression tests for the .env -> Settings -> runtime LLM client path."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from src.config import Settings, get_settings
from src.main import log_llm_runtime_config
from src.nlu.model_client import build_nlu_model_client


class _CapturingCompletions:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        message = SimpleNamespace(
            content=json.dumps({"utterance_type": "SOCIAL_UTTERANCE", "intent": "confirm"})
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model=kwargs["model"],
            id="runtime-model-test",
        )


def test_model_name_env_reaches_runtime_api_request(monkeypatch) -> None:
    """Changing MODEL_NAME changes both the built client and the actual SDK request."""
    selected_model = "runtime-model-from-env"
    monkeypatch.setenv("MODEL_NAME", selected_model)
    # A legacy second source must not override MODEL_NAME.
    monkeypatch.setenv("NLU_MODEL", "legacy-model-must-not-win")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-secret")
    monkeypatch.setenv("LLM_DISABLED", "false")
    get_settings.cache_clear()

    try:
        settings = get_settings()
        client = build_nlu_model_client()
        assert settings.model_name == selected_model
        assert client is not None
        assert client.model == selected_model

        completions = _CapturingCompletions()
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        client.understand(utterance="xin chào", context={})

        assert completions.requests[0]["model"] == selected_model
    finally:
        get_settings.cache_clear()


def test_startup_log_contains_model_and_provider_but_not_key(caplog) -> None:
    runtime_settings = Settings(
        _env_file=None,
        model_name="startup-model-from-config",
        llm_provider="test-provider",
        openai_api_key="must-not-appear-in-logs",
        llm_disabled=False,
    )

    with caplog.at_level(logging.INFO, logger="src.main"):
        log_llm_runtime_config(runtime_settings)

    assert "provider=test-provider" in caplog.text
    assert "model=startup-model-from-config" in caplog.text
    assert "must-not-appear-in-logs" not in caplog.text
