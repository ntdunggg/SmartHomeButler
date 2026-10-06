"""Event Store (spec §21, §23, §25) — lưu EMem event + link event↔turn↔entity.

V1 dùng bảng quan hệ in-memory + vector embedding nhẹ (spec §23: relational tables +
event-turn links + entity links + vector embeddings; không bắt buộc Neo4j). Đây là
nền cho EMem-G (relational expansion tuỳ chọn) ở Phase 2.
"""

from __future__ import annotations

from src.agent.schemas import MemoryEvent


class EventStore:
    """Lưu MemoryEvent + index theo actor/location (in-memory V1)."""

    def __init__(self) -> None:
        self._by_id: dict[str, MemoryEvent] = {}
        self._by_actor: dict[str, list[str]] = {}
        self._by_location: dict[str, list[str]] = {}

    def add(self, event: MemoryEvent) -> None:
        # Upsert without leaving stale/duplicate secondary-index entries.  Routine
        # instruction events are revised as the user adds constraints/corrections.
        if event.event_id in self._by_id:
            self.remove(event.event_id)
        self._by_id[event.event_id] = event
        for actor in event.actors:
            self._by_actor.setdefault(actor, []).append(event.event_id)
        for loc in event.location:
            self._by_location.setdefault(loc, []).append(event.event_id)

    def remove(self, event_id: str) -> bool:
        event = self._by_id.pop(event_id, None)
        if event is None:
            return False
        for actor in event.actors:
            ids = self._by_actor.get(actor, [])
            self._by_actor[actor] = [eid for eid in ids if eid != event_id]
            if not self._by_actor[actor]:
                self._by_actor.pop(actor, None)
        for loc in event.location:
            ids = self._by_location.get(loc, [])
            self._by_location[loc] = [eid for eid in ids if eid != event_id]
            if not self._by_location[loc]:
                self._by_location.pop(loc, None)
        return True

    def get(self, event_id: str) -> MemoryEvent | None:
        return self._by_id.get(event_id)

    def all_events(self) -> list[MemoryEvent]:
        return list(self._by_id.values())

    def by_actor(self, actor: str) -> list[MemoryEvent]:
        return [self._by_id[e] for e in self._by_actor.get(actor, []) if e in self._by_id]

    def by_location(self, location: str) -> list[MemoryEvent]:
        return [self._by_id[e] for e in self._by_location.get(location, []) if e in self._by_id]

    def turn_ids_for(self, event_id: str) -> list[str]:
        """Event → Turn links (spec §22 hierarchy) cho HiGMem turn-expansion."""
        event = self._by_id.get(event_id)
        return list(event.source_turn_ids) if event else []


_DEFAULT_STORE = EventStore()


def get_default_store() -> EventStore:
    return _DEFAULT_STORE
