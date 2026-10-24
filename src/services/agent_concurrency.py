"""Per-household serialization with conversation-scoped request superseding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandLease:
    household_id: int
    conversation_id: str
    generation: int
    lock: asyncio.Lock


class CommandCoordinator:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._generation: dict[tuple[int, str], int] = {}

    def issue(self, household_id: int, conversation_id: str) -> CommandLease:
        scope = (household_id, conversation_id)
        generation = self._generation.get(scope, 0) + 1
        self._generation[scope] = generation
        lock = self._locks.setdefault(household_id, asyncio.Lock())
        return CommandLease(
            household_id=household_id,
            conversation_id=conversation_id,
            generation=generation,
            lock=lock,
        )

    def is_current(self, lease: CommandLease) -> bool:
        scope = (lease.household_id, lease.conversation_id)
        return self._generation.get(scope) == lease.generation


coordinator = CommandCoordinator()
