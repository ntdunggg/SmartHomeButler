"""Memory update path (spec §25, §54) — ghi turn + trích event sau khi thực thi.

Độc lập với RL update (spec §54). Chỉ ghi event từ hành động ĐÃ thực thi thật (evidence),
không từ kế hoạch đề xuất. `event_type` là metadata retrieval, không phải routine taxonomy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.memory.event_extractor import extract_event, extract_feedback_event
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.schemas import ExecutionResult, MemoryEvent, TurnRecord


def record_turn(turn_store: TurnStore, *, turn_id: str, conversation_id: str, text: str, speaker: str = "user") -> None:
    turn_store.add(
        TurnRecord(turn_id=turn_id, conversation_id=conversation_id, speaker=speaker, text=text, timestamp=datetime.now(UTC))
    )


def commit_event(
    event_store: EventStore,
    *,
    event_id: str,
    conversation_id: str,
    actors: list[str],
    execution: ExecutionResult,
    summary: str = "",
    source_turn_ids: list[str] | None = None,
    learn_preferences: bool = True,
) -> MemoryEvent | None:
    """Trích + lưu một event từ kết quả thực thi (spec §25). None nếu không có fact.

    `learn_preferences=False` (lượt bị bác/sửa) → giữ event hoạt động nhưng KHÔNG học giá
    trị đã execute thành preferred (event feedback riêng ghi giá trị đúng).
    """
    event = extract_event(
        event_id=event_id,
        conversation_id=conversation_id,
        actors=actors,
        execution=execution,
        summary=summary,
        source_turn_ids=source_turn_ids,
        start_time=datetime.now(UTC),
        learn_preferences=learn_preferences,
    )
    if event is not None:
        event_store.add(event)
    return event


def commit_feedback_event(
    event_store: EventStore,
    *,
    event_id: str,
    conversation_id: str,
    actors: list[str],
    kind: str,
    execution: ExecutionResult | None = None,
    dimension: str | None = None,
    corrected_value: int | float | None = None,
    subject: str | None = None,
    chosen_value: int | float | None = None,
    source_turn_ids: list[str] | None = None,
    location: list[str] | None = None,
    summary: str = "",
) -> MemoryEvent | None:
    """Trích + lưu một event feedback CÓ NGHĨA (accepted/rejected/correction) (spec §21, §34).

    Đây là mảnh còn thiếu của event-based memory: sự kiện tương tác đáng nhớ được lưu dưới
    dạng event có cấu trúc thay vì tan vào raw turn. None nếu feedback không đáng lưu.
    """
    event = extract_feedback_event(
        event_id=event_id,
        conversation_id=conversation_id,
        actors=actors,
        kind=kind,
        execution=execution,
        dimension=dimension,
        corrected_value=corrected_value,
        subject=subject,
        chosen_value=chosen_value,
        location=location,
        source_turn_ids=source_turn_ids,
        start_time=datetime.now(UTC),
        summary_override=summary,
    )
    if event is not None:
        event_store.add(event)
    return event
