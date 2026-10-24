"""Event Retriever (spec §26) — chấm điểm event theo liên quan tới Semantic Goal.

Score tất định, giải thích được (spec §14: evidence trace): cosine embedding + khớp
location + khớp actor + recency. Trả kèm điểm để tầng trên lọc bằng ngưỡng, và để log
"vì sao nhớ / vì sao bỏ". Memory là BẰNG CHỨNG, KHÔNG override live state (invariant 3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from src.agent.memory.embeddings import HASHING_SPACE, Embedding, cosine
from src.agent.memory.event_extractor import embed_query
from src.agent.schemas import MemoryEvent
from src.agent.text import strip_diacritics

# Hằng số suy giảm recency (ngày): event cũ hơn ~1 tuần gần như hết boost thời gian.
_RECENCY_DECAY_DAYS = 7.0

# Tokens that describe generic control syntax rather than the topic/device.  In
# hashing fallback mode they cannot be allowed to turn "bật loa" into evidence
# for "bật điều hòa".  Location tokens are removed separately per event.
_NON_TOPICAL_TOKENS = frozenset(
    {
        "bat",
        "tat",
        "mo",
        "dong",
        "tang",
        "giam",
        "dat",
        "cho",
        "giup",
        "cai",
        "thiet",
        "bi",
        "len",
        "xuong",
        "di",
        "lai",
        "mot",
        "chut",
    }
)


@dataclass(frozen=True, slots=True)
class ScoredEvent:
    event: MemoryEvent
    score: float
    reasons: tuple[str, ...]


def _tokens(text: str) -> set[str]:
    normalized = strip_diacritics(text.lower())
    return {
        token
        for token in "".join(char if char.isalnum() else " " for char in normalized).split()
        if len(token) > 1
    }


def _event_topic_text(event: MemoryEvent) -> str:
    return " ".join(
        [event.summary, *(f"{fact.subject} {fact.relation} {fact.value}" for fact in event.facts)]
    )


def _event_embedding(event: MemoryEvent) -> Embedding:
    """Vector của event kèm nhãn không gian.

    Snapshot ghi TRƯỚC khi có nhãn (`embedding_space` rỗng) đều là hashing — gán lại nhãn
    lịch sử để chúng vẫn so sánh được với nhau, thay vì bị coi là không gian lạ."""
    return Embedding(
        vector=tuple(event.embedding),
        space=event.embedding_space or HASHING_SPACE,
    )


def _recency_boost(event: MemoryEvent, now: datetime | None) -> float:
    """Boost nhỏ cho event gần đây (spec §22 hierarchy đọc event trước, recency giúp xếp hạng)."""
    if now is None or event.start_time is None:
        return 0.0
    try:
        age_days = max(0.0, (now - event.start_time).total_seconds() / 86400.0)
    except TypeError:  # so lệch tz-aware/naive → bỏ qua recency thay vì lỗi
        return 0.0
    return 0.1 * math.exp(-age_days / _RECENCY_DECAY_DAYS)


def score_events(
    events: list[MemoryEvent],
    *,
    query_text: str,
    room: str | None = None,
    actor: str | None = None,
    now: datetime | None = None,
) -> list[ScoredEvent]:
    """Chấm điểm & sắp xếp event giảm dần theo liên quan (semantic + location + actor + recency)."""
    q_embed = embed_query(query_text) if query_text else Embedding(vector=(), space="")
    scored: list[ScoredEvent] = []
    for ev in events:
        # HARD FILTER quyền riêng tư (spec §P5, QC-06): khi biết người hỏi, KHÔNG trả event của
        # người khác. Event có chủ (actors) mà không chứa `actor` → loại hẳn (không chỉ trừ điểm).
        # Event không chủ (household-shared, actors rỗng) vẫn hiển thị cho mọi người trong hộ.
        if actor and ev.actors and actor not in ev.actors:
            continue
        reasons: list[str] = []
        score = 0.0
        event_embedding = _event_embedding(ev)
        sim = cosine(q_embed, event_embedding)
        # Room, actor, and recency are ranking signals, not proof that an event is
        # about the current request.  Letting those signals create relevance on
        # their own makes an unrelated recent event in the same room leak into a
        # new topic.  The calibrated cosine already maps the embedding space's
        # unrelated baseline to zero, so require a positive topical signal first.
        if query_text:
            if q_embed.space == HASHING_SPACE and event_embedding.space == HASHING_SPACE:
                # Hashing is a deterministic degraded mode, not a semantic model.
                # A positive dot product can be only a bucket collision, so require
                # at least one real TOPICAL token match before contextual boosts may
                # apply.  A shared room or generic verb is context, not a topic.
                contextual_tokens = _tokens(" ".join(ev.location)) | _NON_TOPICAL_TOKENS
                topical_overlap = (
                    _tokens(query_text) & _tokens(_event_topic_text(ev))
                ) - contextual_tokens
                if not topical_overlap:
                    continue
            elif sim <= 0:
                continue
        if sim > 0:
            score += 0.6 * sim
            reasons.append(f"semantic_similarity={sim:.2f}")
        if room and room in ev.location:
            score += 0.3
            reasons.append(f"location={room}")
        if actor and actor in ev.actors:
            score += 0.1
            reasons.append(f"actor={actor}")
        recency = _recency_boost(ev, now)
        if recency > 0:
            score += recency
            reasons.append(f"recency={recency:.2f}")
        if score > 0:
            scored.append(ScoredEvent(event=ev, score=round(score, 4), reasons=tuple(reasons)))
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def retrieve_events(
    events: list[MemoryEvent],
    *,
    query_text: str,
    room: str | None = None,
    actor: str | None = None,
    now: datetime | None = None,
    top_k: int = 5,
    min_score: float = 0.15,
) -> list[ScoredEvent]:
    """Lọc theo ngưỡng + cắt top-k (spec §26 relevance filter)."""
    scored = score_events(events, query_text=query_text, room=room, actor=actor, now=now)
    return [s for s in scored if s.score >= min_score][:top_k]
