"""Regression tests for household serialization and conversation-scoped superseding."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.services import agent_concurrency, agent_runner
from src.services.agent_concurrency import CommandCoordinator


def test_newer_request_supersedes_only_the_same_conversation() -> None:
    coordinator = CommandCoordinator()

    stale = coordinator.issue(1, "conversation-a")
    current = coordinator.issue(1, "conversation-a")

    assert coordinator.is_current(stale) is False
    assert coordinator.is_current(current) is True
    assert stale.lock is current.lock


def test_request_in_another_conversation_does_not_supersede() -> None:
    coordinator = CommandCoordinator()

    first = coordinator.issue(1, "conversation-a")
    second = coordinator.issue(1, "conversation-b")

    assert coordinator.is_current(first) is True
    assert coordinator.is_current(second) is True
    # Device execution remains serialized for the household even across conversations.
    assert first.lock is second.lock


@pytest.mark.asyncio
async def test_blank_conversation_id_is_generated_before_issuing_lease(monkeypatch) -> None:
    issued: list[tuple[int, str]] = []

    class RecordingCoordinator:
        def issue(self, household_id: int, conversation_id: str) -> SimpleNamespace:
            issued.append((household_id, conversation_id))
            return SimpleNamespace(lock=asyncio.Lock())

    async def fake_run_serialized(_session, **kwargs):
        return {"conversation_id": kwargs["conversation_id"]}

    monkeypatch.setattr(agent_concurrency, "coordinator", RecordingCoordinator())
    monkeypatch.setattr(agent_runner, "_run_command_serialized", fake_run_serialized)
    user = SimpleNamespace(household_id=7)

    first = await agent_runner.run_command(object(), user=user, message="first")
    second = await agent_runner.run_command(object(), user=user, message="second")

    assert issued == [(7, first["conversation_id"]), (7, second["conversation_id"])]
    assert first["conversation_id"]
    assert second["conversation_id"]
    assert first["conversation_id"] != second["conversation_id"]
