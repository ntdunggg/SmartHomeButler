"""Context Resolver (spec §14, §P3) — giải thông tin thiếu theo thứ tự nguồn evidence.

Resolution order (spec §14):
    1. Explicit utterance
    2. Runtime context
    3. Requirement Ledger
    4. Recent dialogue
    5. Long-term episodic memory (events)
    6. Stable semantic/profile memory

Bất biến (spec §14): "Không được silently infer mà không lưu evidence." Mỗi lần giải
được một field, resolver TRẢ KÈM evidence trace (nguồn + confidence). Nhờ vậy Sufficiency
Gate và Clarification biết field nào đã đủ chắc, field nào còn phải hỏi (spec §P3: thử
resolve từ 6 nguồn TRƯỚC khi hỏi user).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from src.agent.schemas import MemoryEvent, RequirementLedger, RuntimeContext
from src.iot.registry import spec_for
from src.nlu.normalizer import NormalizedUtterance, analyze

# Nhãn nguồn theo đúng thứ tự ưu tiên §14 (số nhỏ = ưu tiên cao = confidence cao hơn).
SOURCE_ORDER = (
    "explicit_utterance",
    "runtime_context",
    "requirement_ledger",
    "recent_dialogue",
    "episodic_memory",
    "profile_memory",
)
_SOURCE_CONFIDENCE = {
    "explicit_utterance": 1.0,
    "runtime_context": 0.9,
    "requirement_ledger": 0.85,
    "recent_dialogue": 0.7,
    "episodic_memory": 0.6,
    "profile_memory": 0.55,
}


@dataclass(slots=True)
class ResolvedField:
    """Kết quả giải một field kèm dấu vết bằng chứng (spec §14)."""

    field: str
    value: Any
    source: str
    confidence: float
    evidence: list[dict[str, Any]] = dc_field(default_factory=list)

    def as_trace(self) -> dict[str, Any]:
        return {"resolved_field": self.field, "value": self.value, "source": self.source, "evidence": self.evidence}


def _resolved(field_name: str, value: Any, source: str, **extra: Any) -> ResolvedField:
    conf = _SOURCE_CONFIDENCE.get(source, 0.5)
    return ResolvedField(
        field=field_name,
        value=value,
        source=source,
        confidence=conf,
        evidence=[{"source": source, "confidence": conf, **extra}],
    )


def _room_from_text(text: str) -> str | None:
    """Dò tên phòng trong một câu (dùng cho recent_dialogue) qua alias registry.

    Tái dùng ĐÚNG bộ so khớp phòng của normalizer (`analyze().matched_rooms`) — MỘT
    nguồn chân lý duy nhất cho luật "alias cụ thể hơn thắng alias tổng quát mà nó chứa".
    Trước bản vá này, hàm có một vòng lặp RIÊNG chỉ trả phòng đầu tiên
    khớp theo THỨ TỰ khai báo trong dict — không so độ dài/tính cụ thể của alias, nên khi
    ROOM_ALIASES thêm một alias mới cho phòng cụ thể (vd biến thể "trẻ em"/"của bé" của
    Phòng ngủ con) mà phòng đó lại đứng SAU "Phòng ngủ" (BEDROOM) trong dict, kết quả vẫn
    có thể sai tuỳ thứ tự khai báo — một lỗi tổng quát, không chỉ riêng một cụm từ."""
    matched = analyze(text).matched_rooms
    return matched[0] if matched else None


def resolve_room(
    *,
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    ledger: RequirementLedger | None = None,
    events: list[MemoryEvent] | None = None,
) -> ResolvedField | None:
    """Giải 'room' qua 6 nguồn theo thứ tự §14. None nếu không nguồn nào trả lời."""
    # 1. Explicit utterance
    if nu.matched_rooms:
        return _resolved("room", nu.matched_rooms[0], "explicit_utterance", matched=list(nu.matched_rooms))
    # 2. Runtime context — vị trí người nói (đo được) > phòng đang tập trung
    if ctx.speaker_location and ctx.speaker_location in ctx.rooms:
        return _resolved("room", ctx.speaker_location, "runtime_context", signal="speaker_location")
    if ctx.focus_room and ctx.focus_room in ctx.rooms:
        return _resolved("room", ctx.focus_room, "runtime_context", signal="focus_room")
    # 3. Requirement Ledger — phòng đã chốt ở lượt trước
    if ledger is not None and ledger.confirmed_facts.get("room"):
        return _resolved("room", ledger.confirmed_facts["room"], "requirement_ledger")
    # 4. Recent dialogue
    for line in reversed(ctx.recent_dialogue):
        room = _room_from_text(line)
        if room:
            return _resolved("room", room, "recent_dialogue", line=line)
    # 5. Long-term episodic memory — phòng của event liên quan nhất
    for ev in events or []:
        if ev.location:
            return _resolved("room", ev.location[0], "episodic_memory", event_id=ev.event_id)
    # 6. Profile memory — phòng riêng của người nói (speaker_home_room)
    if ctx.speaker_home_room and ctx.speaker_home_room in ctx.rooms:
        return _resolved("room", ctx.speaker_home_room, "profile_memory", signal="speaker_home_room")
    return None


def resolve_device(
    *,
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    ledger: RequirementLedger | None = None,
) -> ResolvedField | None:
    """Giải 'device' (một slug) qua các nguồn. Nhiều thiết bị khớp → KHÔNG chọn bừa
    (trả None để tầng trên hỏi lại — spec §P3)."""
    # 1. Explicit utterance — đúng một thiết bị mới coi là giải được.
    if len(nu.matched_device_ids) == 1:
        slug = nu.matched_device_ids[0]
        if spec_for(slug) is not None:
            return _resolved("device", slug, "explicit_utterance")
    # 3. Requirement Ledger — thiết bị đã chốt lượt trước (nếu đúng một)
    if ledger is not None:
        devices = ledger.confirmed_facts.get("devices") or []
        if len(devices) == 1 and spec_for(devices[0]) is not None:
            return _resolved("device", devices[0], "requirement_ledger")
    return None


def resolve_missing(
    missing: list[str],
    *,
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    ledger: RequirementLedger | None = None,
    events: list[MemoryEvent] | None = None,
) -> tuple[dict[str, ResolvedField], list[str]]:
    """Thử giải mọi field còn thiếu. Trả (resolved_map, still_missing).

    still_missing = các field không nguồn nào trả lời được → đầu vào cho Clarification.
    """
    resolved: dict[str, ResolvedField] = {}
    still_missing: list[str] = []
    for fname in missing:
        if fname in ("room", "area"):
            r = resolve_room(nu=nu, ctx=ctx, ledger=ledger, events=events)
        elif fname in ("device", "device_id"):
            r = resolve_device(nu=nu, ctx=ctx, ledger=ledger)
        else:
            r = None
        if r is not None:
            resolved[fname] = r
        else:
            still_missing.append(fname)
    return resolved, still_missing
