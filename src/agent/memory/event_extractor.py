"""Event Extractor (spec §21, §25) — chưng cất hội thoại thành EMem event.

KHÔNG lưu long-term memory chỉ bằng raw chat chunk (spec §21): trích một event có
actors/location/facts/summary. V1 trích TẤT ĐỊNH từ hành động đã thực thi + desired
state (không cần LLM) — đủ cho preference recall. `event_type` là metadata tự do,
KHÔNG phải closed routine taxonomy (spec §21, §P8).
"""

from __future__ import annotations

from datetime import datetime

from src.agent.memory.embeddings import Embedding, get_provider
from src.agent.schemas import ExecutionResult, MemoryEvent, MemoryFact
from src.iot.registry import spec_for


def _embed(text: str) -> Embedding:
    """Nhúng qua provider dùng chung (Text-Embedding-3 khi có API, hashing khi offline).

    Provider tự suy giảm về hashing nếu dịch vụ nhúng hỏng, nên hàm này luôn trả một
    vector dùng được — nhưng vector đó mang NHÃN không gian để retriever chỉ so sánh
    những vector thực sự cùng hệ toạ độ."""
    return get_provider().embed(text)


def _relation_for_capability(capability: str) -> str:
    """Ánh xạ capability → relation ổn định cho profile learning (spec §21 facts)."""
    return {
        "brightness": "preferred_brightness",
        "temperature": "preferred_temperature",
        "fan_speed": "preferred_fan_speed",
        "volume": "preferred_volume",
        "position": "preferred_position",
    }.get(capability, f"set_{capability}")


def extract_event(
    *,
    event_id: str,
    conversation_id: str,
    actors: list[str],
    execution: ExecutionResult,
    summary: str = "",
    location: list[str] | None = None,
    source_turn_ids: list[str] | None = None,
    start_time: datetime | None = None,
    learn_preferences: bool = True,
) -> MemoryEvent | None:
    """Trích một MemoryEvent từ kết quả thực thi thật (spec §25 write path).

    Chỉ lấy action THÀNH CÔNG (evidence thật). Không có fact nào → trả None (không tạo
    event rỗng làm nhiễu retrieval).

    `learn_preferences=False` khi lượt này bị user BÁC BỎ / SỬA (feedback reject/correction):
    giá trị ĐÃ execute không còn là bằng chứng sở thích (§24) — nếu vẫn trích `preferred_*`
    ta sẽ học nhầm đúng con số user vừa từ chối. Khi đó vẫn giữ event (summary/location) làm
    dấu vết hoạt động, nhưng KHÔNG sinh fact preference; event feedback riêng ghi giá trị đúng.
    """
    facts: list[MemoryFact] = []
    rooms: set[str] = set(location or [])

    for act in execution.actions:
        if act.status not in ("SUCCESS", "NO_OP"):
            continue
        spec = spec_for(act.device_id)
        if spec and spec.room:
            rooms.add(spec.room)
        # Fact preference: mỗi tham số số học trong requested → (device, preferred_x, value).
        if not learn_preferences:
            continue
        for cap, value in (act.requested or {}).items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                facts.append(
                    MemoryFact(subject=act.device_id, relation=_relation_for_capability(cap), value=value)
                )

    if not facts and not summary:
        return None

    embed_text = " ".join([summary, *(f"{f.subject} {f.relation} {f.value}" for f in facts)])
    embedded = _embed(embed_text or " ".join(sorted(rooms)))
    return MemoryEvent(
        event_id=event_id,
        event_type="household_activity",
        actors=list(actors),
        location=sorted(rooms),
        start_time=start_time,
        summary=summary,
        facts=facts,
        source_turn_ids=list(source_turn_ids or []),
        embedding=list(embedded.vector),
        embedding_space=embedded.space,
    )


# Ánh xạ FeedbackKind (value chuỗi) → event_type ký ức. `event_type` là METADATA
# retrieval tự do (spec §21, §P8), KHÔNG phải closed routine taxonomy — chỉ đánh dấu
# NGUỒN GỐC/ý nghĩa của event để lượt sau truy hồi được "user đã sửa/bác ở đây".
_FEEDBACK_EVENT_TYPE = {
    "explicit_accept": "plan_accepted",
    "explicit_reject": "plan_rejected",
    "slight_adjustment": "preference_correction",
    "preference_stated": "preference_stated",
}


def _dimension_subject_value(execution: ExecutionResult, dimension: str) -> tuple[str | None, float | None]:
    """Tìm (device_id, giá trị số) đã EXECUTE cho một dimension trong plan (để neo fact đúng thiết bị)."""
    for act in execution.actions:
        if act.status not in ("SUCCESS", "NO_OP"):
            continue
        value = (act.requested or {}).get(dimension)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return act.device_id, float(value)
    return None, None


def _device_supporting(execution: ExecutionResult, dimension: str) -> str | None:
    """Thiết bị ĐÃ tác động (SUCCESS/NO_OP) HỖ TRỢ dimension — fallback neo khi plan không mang
    tham số số (vd bật đèn không kèm brightness) nhưng user vẫn sửa/bác chiều đó."""
    for act in execution.actions:
        if act.status not in ("SUCCESS", "NO_OP"):
            continue
        spec = spec_for(act.device_id)
        if spec is not None and any(c.value == dimension for c in spec.capabilities):
            return act.device_id
    return None


def extract_feedback_event(
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
    location: list[str] | None = None,
    source_turn_ids: list[str] | None = None,
    start_time: datetime | None = None,
    summary_override: str = "",
) -> MemoryEvent | None:
    """Trích một MemoryEvent CÓ NGHĨA từ tín hiệu feedback có cấu trúc (spec §21, §25, §34).

    Đây là phần còn thiếu của event-based memory (§10/§11 yêu cầu): thay vì chỉ lưu
    `household_activity`, ta biểu diễn các sự kiện tương tác ĐÁNG NHỚ — plan được CHẤP NHẬN,
    plan bị BÁC BỎ, một SỬA đổi/sở thích được nêu — dưới dạng event có facts đúng chiều:

    - ``plan_accepted``        → fact ``preferred_<dim> = giá trị đã execute`` (bằng chứng dương).
    - ``plan_rejected``        → fact ``rejected_<dim> = giá trị đã execute`` (KHÔNG học thành
      preferred; relation ``rejected_*`` để consolidation preference bỏ qua — không học nhầm).
    - ``preference_correction``→ fact ``preferred_<dim> = corrected_value`` (giá trị user muốn).

    KHÔNG dựng event từ so khớp chuỗi câu — chỉ từ FeedbackSignal đã diễn giải (§34). Trả None
    nếu feedback không mang thông tin đáng lưu (no_correction) hoặc không neo được thiết bị.
    """
    event_type = _FEEDBACK_EVENT_TYPE.get(kind)
    if event_type is None:
        return None

    facts: list[MemoryFact] = []
    rooms: set[str] = set(location or [])
    summary = ""

    if dimension:
        # Ưu tiên subject/value do caller neo từ selected_plan (planner biết capability+giá trị);
        # nếu không có, dò từ execution (chỉ khớp khi executor mang tham số số — offline thường không).
        executed = chosen_value
        if subject is None or executed is None:
            exec_subject, exec_value = _dimension_subject_value(execution, dimension) if execution is not None else (None, None)
            subject = subject or exec_subject
            if executed is None:
                executed = exec_value
        # Fallback cuối: neo vào thiết bị đã tác động có hỗ trợ dimension (plan không set số).
        if subject is None and execution is not None:
            subject = _device_supporting(execution, dimension)
        if executed is not None:
            executed = float(executed)
        if subject is not None:
            spec = spec_for(subject)
            if spec and spec.room:
                rooms.add(spec.room)
            base_relation = _relation_for_capability(dimension)  # preferred_<dim>
            if event_type == "plan_rejected" and executed is not None:
                facts.append(MemoryFact(subject=subject, relation=f"rejected_{dimension}", value=executed))
                summary = f"Bác giá trị {dimension}={executed:g} ({spec.name if spec else subject})"
            elif event_type == "preference_correction" and corrected_value is not None:
                facts.append(MemoryFact(subject=subject, relation=base_relation, value=float(corrected_value)))
                summary = f"Sửa {dimension} → {float(corrected_value):g} ({spec.name if spec else subject})"
            elif event_type in {"plan_accepted", "preference_stated"} and executed is not None:
                facts.append(MemoryFact(subject=subject, relation=base_relation, value=executed))
                verb = "Ghi nhận" if event_type == "preference_stated" else "Chấp nhận"
                summary = f"{verb} {dimension}={executed:g} ({spec.name if spec else subject})"

    if summary_override:
        summary = summary_override
    if not summary:
        # Feedback không neo được (dimension/thiết bị) — vẫn ghi event thô để có dấu vết chấp
        # nhận/bác bỏ ở phòng liên quan, nhưng không sinh fact số (tránh học nhầm).
        label = {
            "plan_accepted": "Chấp nhận",
            "plan_rejected": "Bác bỏ",
            "preference_correction": "Sửa",
            "preference_stated": "Nêu sở thích cho",
        }[event_type]
        summary = f"{label} kế hoạch lượt vừa rồi"

    embed_text = " ".join([summary, *(f"{f.subject} {f.relation} {f.value}" for f in facts)])
    embedded = _embed(embed_text)
    return MemoryEvent(
        event_id=event_id,
        event_type=event_type,
        actors=list(actors),
        location=sorted(rooms),
        start_time=start_time,
        summary=summary,
        facts=facts,
        source_turn_ids=list(source_turn_ids or []),
        embedding=list(embedded.vector),
        embedding_space=embedded.space,
    )


def embed_query(text: str) -> Embedding:
    """Nhúng câu truy vấn CÙNG provider với event embedding (cho retriever).

    Trả `Embedding` (vector + nhãn không gian), không phải list trần: retriever phải biết
    hệ toạ độ để không so một truy vấn Text-Embedding-3 với ký ức cũ nhúng bằng hashing."""
    return _embed(text)
