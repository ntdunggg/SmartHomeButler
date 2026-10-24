"""EMem-G relational memory (spec §23) — mở rộng quan hệ tuỳ chọn quanh Event.

Concept (spec §23):

    User → Event → { Room, Activity, Device, Constraint, Preference }

V1 dùng bảng quan hệ in-memory + entity links (spec §23: "relational tables + event-turn
links + entity links + vector embeddings", KHÔNG bắt buộc Neo4j). Dùng cho "Optional
Graph Expansion" trong read path (spec §26): từ event khớp theo vector, lan sang event
LIÊN QUAN qua entity chung (cùng phòng/thiết bị/actor) mà vector có thể bỏ sót.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from src.agent.schemas import MemoryEvent


@dataclass(frozen=True, slots=True)
class Entity:
    """Một node entity điển hình hoá (typed) để nối các event."""

    kind: str  # actor | room | device | activity | preference
    value: str

    def key(self) -> str:
        return f"{self.kind}:{self.value}"


# Loại entity dùng để LAN QUAN HỆ mặc định: entity TOPICAL CỤ THỂ (phòng + thiết bị).
# CỐ TÌNH bỏ 'actor', 'activity' và 'preference' generic — một user / một loại hoạt động /
# một loại preference ("preferred_brightness") nối MỌI event, khiến expansion vô nghĩa.
RELATIONAL_KINDS = frozenset({"room", "device"})


def entities_of(event: MemoryEvent) -> set[Entity]:
    """Trích các entity từ một event (spec §23 relations)."""
    ents: set[Entity] = set()
    for actor in event.actors:
        ents.add(Entity("actor", actor))
    for room in event.location:
        ents.add(Entity("room", room))
    if event.event_type:
        ents.add(Entity("activity", event.event_type))
    for fact in event.facts:
        ents.add(Entity("device", fact.subject))
        ents.add(Entity("preference", fact.relation))
    return ents


class MemoryGraph:
    """Chỉ mục quan hệ entity ↔ event (in-memory V1)."""

    def __init__(self) -> None:
        self._entity_to_events: dict[str, set[str]] = {}
        self._event_to_entities: dict[str, set[Entity]] = {}

    def add_event(self, event: MemoryEvent) -> None:
        ents = entities_of(event)
        self._event_to_entities[event.event_id] = ents
        for e in ents:
            self._entity_to_events.setdefault(e.key(), set()).add(event.event_id)

    def neighbors(self, event_id: str, *, kinds: frozenset[str] = RELATIONAL_KINDS) -> set[str]:
        """Các event chia sẻ ≥1 entity TOPICAL với event này (mặc định room/device/preference)."""
        out: set[str] = set()
        for e in self._event_to_entities.get(event_id, set()):
            if e.kind not in kinds:
                continue
            out |= self._entity_to_events.get(e.key(), set())
        out.discard(event_id)
        return out

    def expand(self, seed_ids: list[str], *, hops: int = 1, kinds: frozenset[str] = RELATIONAL_KINDS) -> set[str]:
        """Lan BFS `hops` bước từ tập seed qua entity chung (spec §26 graph expansion)."""
        seen: set[str] = set(seed_ids)
        frontier: deque[tuple[str, int]] = deque((sid, 0) for sid in seed_ids)
        found: set[str] = set()
        while frontier:
            eid, depth = frontier.popleft()
            if depth >= hops:
                continue
            for nb in self.neighbors(eid, kinds=kinds):
                if nb not in seen:
                    seen.add(nb)
                    found.add(nb)
                    frontier.append((nb, depth + 1))
        return found

    def entities_for(self, event_id: str) -> set[Entity]:
        return set(self._event_to_entities.get(event_id, set()))
