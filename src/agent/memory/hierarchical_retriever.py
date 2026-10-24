"""HiGMem Hierarchical Retriever (spec §22, §26) — Event → Turn read path.

Read path (spec §22):
    Semantic Goal → Event Retrieval → relevance filter → need more evidence?
        NO  → dùng event summary
        YES → Turn expansion (đọc raw turn từ TurnStore)
    → Memory Evidence

Event summary đọc TRƯỚC; raw turns chỉ expand khi cần (spec §22, §P4) — tiết kiệm
context và giữ planner đọc trạng thái đã chưng cất thay vì raw chat.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.agent.memory.event_retriever import ScoredEvent, retrieve_events
from src.agent.memory.event_store import EventStore
from src.agent.memory.graph_memory import MemoryGraph
from src.agent.memory.turn_store import TurnStore
from src.agent.schemas import MemoryEvent, TurnRecord

DEFAULT_MEMORY_PROMPT_CHAR_BUDGET = 6_000
DEFAULT_MAX_EXPANDED_TURNS = 6
DEFAULT_MAX_GRAPH_EVENTS = 3


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _event_payload(event: MemoryEvent) -> dict[str, Any]:
    """Serialize an event without discarding its evidence provenance."""
    return {
        "event_id": event.event_id,
        "summary": event.summary,
        "location": event.location,
        "actors": event.actors,
        "start_time": _timestamp(event.start_time),
        "facts": [fact.model_dump(mode="json") for fact in event.facts],
        "source_turn_ids": event.source_turn_ids,
    }


def _turn_payload(turn: TurnRecord) -> dict[str, Any]:
    return {
        "turn_id": turn.turn_id,
        "conversation_id": turn.conversation_id,
        "speaker": turn.speaker,
        "timestamp": _timestamp(turn.timestamp),
        "text": turn.text,
    }


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


@dataclass(slots=True)
class MemoryEvidence:
    """Bằng chứng ký ức trả cho tầng suy luận (spec §26 output)."""

    events: list[MemoryEvent] = field(default_factory=list)
    expanded_turns: list[TurnRecord] = field(default_factory=list)
    graph_events: list[MemoryEvent] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def to_prompt_payload(self, *, max_chars: int = DEFAULT_MEMORY_PROMPT_CHAR_BUDGET) -> dict[str, Any]:
        """Build a bounded, inspectable prompt payload.

        Event summaries are added first, followed by only the raw turns selected by
        hierarchical expansion, then graph-neighbor evidence.  Every record keeps
        source identifiers and timestamps so a downstream model or audit can tell
        where a claim came from.  The hard character budget is deterministic and
        tokenizer-independent; callers can choose a tighter provider-specific limit.
        """
        if max_chars <= 0:
            return {"events": [], "expanded_turns": [], "graph_events": [], "truncated": True}

        payload: dict[str, Any] = {
            "events": [],
            "expanded_turns": [],
            "graph_events": [],
            "truncated": False,
        }

        def add(section: str, item: dict[str, Any]) -> bool:
            payload[section].append(item)
            if _serialized_size(payload) <= max_chars:
                return True
            payload[section].pop()
            payload["truncated"] = True
            return False

        for event in self.events:
            if not add("events", _event_payload(event)):
                break
        for turn in self.expanded_turns:
            if not add("expanded_turns", _turn_payload(turn)):
                break
        for event in self.graph_events:
            if not add("graph_events", _event_payload(event)):
                break
        return payload


def retrieve_memory(
    *,
    event_store: EventStore,
    turn_store: TurnStore,
    query_text: str,
    room: str | None = None,
    actor: str | None = None,
    conversation_id: str | None = None,
    now: datetime | None = None,
    graph: MemoryGraph | None = None,
    top_k: int = 5,
    expand_threshold: float = 0.45,
    min_score: float = 0.15,
    max_expanded_turns: int = DEFAULT_MAX_EXPANDED_TURNS,
    max_graph_events: int = DEFAULT_MAX_GRAPH_EVENTS,
) -> MemoryEvidence:
    """Truy hồi ký ức phân tầng (spec §22, §26).

    `expand_threshold`: event điểm CAO nhưng thiếu fact → cần thêm bằng chứng → expand
    turn. Event điểm cao đã đủ fact thì KHÔNG expand (tiết kiệm, đọc summary là đủ).
    `graph` (tuỳ chọn, EMem-G §23): lan sang event LIÊN QUAN qua entity chung — Optional
    Graph Expansion (spec §26).
    """
    events = event_store.all_events()
    if conversation_id is not None:
        # Context resolution may use episodic evidence only when its raw-turn
        # provenance belongs to the active conversation. Actor filtering alone is
        # insufficient: the same user can have an unrelated conversation in the
        # same household, and an event from that session must not silently supply
        # the room for a new underspecified command. Events without turn provenance
        # cannot prove conversation membership, so the safe result is to exclude
        # them from this scoped read.
        events = [
            event
            for event in events
            if any(
                turn.conversation_id == conversation_id
                for turn in turn_store.get_many(event.source_turn_ids)
            )
        ]
    scored: list[ScoredEvent] = retrieve_events(
        events,
        query_text=query_text,
        room=room,
        actor=actor,
        now=now,
        top_k=top_k,
        min_score=min_score,
    )
    evidence = MemoryEvidence()
    seen_turn_ids: set[str] = set()
    for s in scored:
        evidence.events.append(s.event)
        evidence.trace.append(f"event {s.event.event_id} score={s.score} ({', '.join(s.reasons)})")
        # Cần thêm bằng chứng? (điểm cao nhưng event nghèo fact) → expand turn.
        needs_more = s.score >= expand_threshold and len(s.event.facts) == 0
        if needs_more and s.event.source_turn_ids:
            turns = turn_store.get_many(s.event.source_turn_ids)
            available = max(0, max_expanded_turns - len(evidence.expanded_turns))
            selected = [turn for turn in turns if turn.turn_id not in seen_turn_ids][:available]
            evidence.expanded_turns.extend(selected)
            seen_turn_ids.update(turn.turn_id for turn in selected)
            evidence.trace.append(
                f"expanded {len(selected)}/{len(turns)} turns for {s.event.event_id}"
            )
            if len(evidence.expanded_turns) >= max_expanded_turns:
                evidence.trace.append(f"turn_expansion_budget={max_expanded_turns}")

    # Optional Graph Expansion (spec §26, EMem-G §23): event liên quan qua entity chung
    # mà vector bỏ sót (vd cùng phòng/thiết bị) — bổ sung làm bằng chứng phụ, KHÔNG chèn
    # vào tập chính (giữ độ chính xác retrieval).
    if graph is not None and evidence.events:
        seed_ids = [e.event_id for e in evidence.events]
        for extra_id in sorted(graph.expand(seed_ids, hops=1))[:max_graph_events]:
            ev = event_store.get(extra_id)
            if ev is not None:
                evidence.graph_events.append(ev)
        if evidence.graph_events:
            evidence.trace.append(f"graph_expansion={[e.event_id for e in evidence.graph_events]}")
    return evidence
