"""Adapter mỏng: HTTP chat request ↔ pipeline multi-agent v2.

Chỉ làm việc nối, KHÔNG execute thiết bị. Output luôn là CandidatePlan (proposal,
`requires_policy_validation=True`) hoặc Clarification hoặc controlled error. Catalog
thiết bị lấy từ Registry của Backend, KHÔNG nhận từ request. Model client được dựng từ
settings và truyền vào pipeline; API key không đi vào state hay response.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from src.iot.registry import ROOMS
from src.nlu.model_client import build_nlu_model_client
from src.services import pipeline_bridge
from src.services.context import speaker_home_room_for
from src.services.location_resolver import build_signals, resolve_location


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------
class NluDialogueTurn(BaseModel):
    role: str = "user"
    content: str


class NluRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(..., min_length=1)
    session_id: str = ""
    speaker_id: str = "user_01"
    # Tín hiệu vị trí THÔ (không phải phòng chọn thủ công): loa/mic nào nghe thấy câu nói.
    current_room: str | None = None
    # Giả lập cảm biến hiện diện theo phòng (bảng mô phỏng tín hiệu ở frontend). Nếu rỗng,
    # resolver lấy presence thật từ live_sensors.
    presence_rooms: list[str] = Field(default_factory=list)
    focus_room: str | None = None
    last_device_id: str | None = None
    recent_dialogue: list[NluDialogueTurn] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Response (contract ổn định cho Frontend — không trả raw pipeline state)
# ---------------------------------------------------------------------------
class SemanticGoalOut(BaseModel):
    utterance_type: str
    primary_intent: str
    confidence: float
    # Mục tiêu ở mức capability (goal_description + desired_outcomes). Lộ ra để tầng ghi nhớ
    # lưu lại CÁCH HIỂU tái dùng được cho routine — KHÔNG phải hành động cụ thể.
    goal_description: str = ""
    desired_outcomes: list[dict[str, Any]] = Field(default_factory=list)


class CandidateActionOut(BaseModel):
    device_id: str
    capability: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason_vi: str = ""
    risk_level: str = "normal"


class CandidatePlanOut(BaseModel):
    intent: str
    actions: list[CandidateActionOut] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    requires_policy_validation: bool = True
    requires_confirmation: bool = False


class ClarificationOut(BaseModel):
    reason: str
    question: str
    options: list[str] = Field(default_factory=list)
    expected_answer_type: str = "text"


class NluMetadata(BaseModel):
    route: str = "deterministic"
    model: str | None = None
    latency_ms: float = 0.0
    token_usage: int | None = None
    # Số mẩu ký ức thật sự được đưa vào suy luận lượt này (0 = agent không nhớ gì liên
    # quan). Lộ ra để debug được: ký ức sai thì thấy ngay ở đây thay vì đoán mò.
    memories_used: int = 0
    # Nhãn thói quen đã học được TÁI DÙNG cho lượt này (None nếu agent tự hiểu từ đầu). Lộ ra
    # để giải thích được vì sao agent hành động "ngay" mà không cần hỏi lại như tình huống mới.
    reused_routine: str | None = None


class NluErrorOut(BaseModel):
    code: str
    retryable: bool = True


class LocationOut(BaseModel):
    """Ước lượng vị trí người nói do Location Resolver suy ra (hiển thị/ debug ở UI)."""

    resolved: bool = False  # True nếu đủ tin cậy để dùng mà không cần hỏi
    room: str | None = None
    confidence: float = 0.0
    source: str = ""
    evidence: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)


class NluResponse(BaseModel):
    type: str  # candidate_plan | clarification | controlled_error
    reply: str
    trace_id: str
    semantic_goal: SemanticGoalOut | None = None
    candidate_plan: CandidatePlanOut | None = None
    clarification: ClarificationOut | None = None
    requires_clarification: bool = False
    execution_status: str = "not_executed"
    metadata: NluMetadata = Field(default_factory=NluMetadata)
    location: LocationOut | None = None
    error: NluErrorOut | None = None
    session_id: str = ""
    last_device_id: str | None = None
    # Id của EpisodicMemory vừa ghi cho lượt này — client trả lại khi người dùng
    # đồng ý/từ chối để hệ thống cập nhật bằng chứng đúng lượt đó.
    episode_id: int | None = None


def _plan_reply(plan: Any, *, reused_routine: str | None = None) -> str:
    n = len(plan.actions)
    if n == 0:
        return plan.explanation_vi or "Mình đã hiểu, nhưng chưa có hành động cụ thể nào để đề xuất."
    prefix = "Mình dùng lại thói quen đã học của bạn — " if reused_routine else "Mình "
    return f"{prefix}đã tạo kế hoạch đề xuất gồm {n} bước (chưa thực thi, chờ kiểm duyệt an toàn)."


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _map_response(
    result: pipeline_bridge.ReasoningResult,
    *,
    trace_id: str,
    session_id: str,
    latency_ms: float,
    client: Any,
    memories_used: int = 0,
    reused_routine: str | None = None,
) -> NluResponse:
    goal = result.semantic_goal
    plan = result.candidate_plan
    clar = result.clarification

    calls = list(getattr(client, "calls", []) or []) if client is not None else []
    route = "llm" if calls else "deterministic"
    meta = NluMetadata(
        route=route,
        model=(calls[-1].model if calls else None),
        latency_ms=round(latency_ms, 3),
        token_usage=(sum(c.total_tokens or 0 for c in calls) or None),
        memories_used=memories_used,
        reused_routine=reused_routine,
    )

    # Xác nhận huỷ (dialogue manager) — không hỏi gì thêm, không action.
    if result.outcome == "cancelled":
        return NluResponse(
            type="clarification",
            reply=result.reply or "Đã huỷ.",
            trace_id=trace_id,
            requires_clarification=False,
            metadata=meta,
            session_id=session_id,
        )
    semantic_goal = (
        SemanticGoalOut(
            utterance_type=_enum_value(goal.utterance_type),
            primary_intent=goal.intent,
            confidence=round(goal.confidence, 3),
            goal_description=getattr(goal, "goal_description", "") or "",
            # Chỉ mục tiêu OPEN-ENDED mới có desired_outcomes; lệnh tường minh (action_hint)
            # để rỗng — nó đã tất định, không cần học lại thành routine.
            desired_outcomes=(
                [o.model_dump(mode="json") for o in goal.desired_outcomes]
                if getattr(goal, "action_hint", None) is None
                else []
            ),
        )
        if goal is not None
        else None
    )

    # Model được thử nhưng transport/provider hỏng → controlled error, KHÔNG đoán.
    diagnostics = result.diagnostics or {}
    model_failed_without_record = client is not None and not calls and result.outcome == "clarification"
    if (diagnostics.get("provider_errors") or model_failed_without_record) and result.candidate_plan is None:
        return NluResponse(
            type="controlled_error",
            reply="Hiện tại mình chưa phân tích được câu này. Bạn hãy nói cụ thể hơn giúp mình nhé.",
            trace_id=trace_id,
            requires_clarification=True,
            metadata=meta,
            error=NluErrorOut(code="NLU_MODEL_UNAVAILABLE", retryable=True),
            session_id=session_id,
        )

    if result.outcome == "candidate_plan" and plan is not None:
        last_device = plan.actions[0].device_id if plan.actions else None
        plan_reply = _plan_reply(plan, reused_routine=reused_routine)
        if result.reply:
            plan_reply = f"{plan_reply} {result.reply}"
        return NluResponse(
            type="candidate_plan",
            reply=plan_reply,
            trace_id=trace_id,
            semantic_goal=semantic_goal,
            candidate_plan=CandidatePlanOut(
                intent=plan.goal_summary,
                actions=[
                    CandidateActionOut(
                        device_id=a.device_id,
                        capability=_enum_value(a.capability),
                        action=_enum_value(a.action),
                        params=dict(a.params),
                        reason_vi=a.reason_vi,
                        risk_level=_enum_value(a.risk_level),
                    )
                    for a in plan.actions
                ],
                assumptions=list(plan.assumptions),
                missing_information=list(plan.missing_information),
                confidence=round(goal.confidence if goal is not None else 0.0, 3),
                requires_policy_validation=plan.requires_policy_validation,
                requires_confirmation=plan.requires_confirmation,
            ),
            requires_clarification=False,
            metadata=meta,
            session_id=session_id,
            last_device_id=last_device,
        )

    # clarification / no_goal
    question = clar.question_vi if clar is not None else (result.reply or "Bạn nói rõ hơn một chút được không?")
    return NluResponse(
        type="clarification",
        reply=question,
        trace_id=trace_id,
        semantic_goal=semantic_goal,
        clarification=ClarificationOut(
            reason=getattr(clar, "reason", "no_goal") if clar else "no_goal",
            question=question,
            options=list(getattr(clar, "options", [])) if clar else [],
            expected_answer_type=getattr(clar, "expected_answer_type", "text") if clar else "text",
        ),
        requires_clarification=True,
        metadata=meta,
        session_id=session_id,
    )


async def analyze(
    req: NluRequest,
    *,
    live_device_states: dict[str, dict] | None = None,
    live_sensors: list[dict] | None = None,
    household_id: int | None = None,
    user_id: int | str | None = None,
    role: str = "owner",
    speaker_home_room: str | None = None,
    speaker_private_room: str | None = None,
    session: Any = None,
) -> NluResponse:
    """Chạy pipeline multi-agent v2 cho một câu chat, không execute.

    `current_room` = vị trí VẬT LÝ người nói (→ speaker_location, OBSERVATION), TÁCH khỏi
    `focus_room` = phòng suy từ hội thoại (ASSUMPTION). `live_*` là snapshot thật do route
    bơm vào (route có DB session; gateway thì không)."""
    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    session_id = req.session_id or uuid.uuid4().hex[:16]
    client = build_nlu_model_client()  # None khi LLM_DISABLED/không có key → chỉ fast path
    recent = [t.content for t in req.recent_dialogue][-8:]
    now = datetime.now(UTC)

    # --- Location Resolver: tự suy phòng người nói từ tín hiệu, KHÔNG bắt chọn thủ công.
    # Presence: ưu tiên bảng giả lập từ request; nếu rỗng thì lấy từ cảm biến thật.
    presence = list(req.presence_rooms) or _presence_from_sensors(live_sensors)
    estimate = resolve_location(
        build_signals(capture_room=req.current_room, presence_rooms=presence, valid_rooms=set(ROOMS))
    )

    started = datetime.now(UTC)
    fallback_room = speaker_home_room_for(role)
    effective_home_room = speaker_home_room or fallback_room
    effective_private_room = speaker_private_room or effective_home_room
    reason_kwargs = {
        "message": req.message,
        "conversation_id": session_id,
        "role": role,
        "user_id": str(user_id) if user_id is not None else req.speaker_id,
        "household_id": household_id,
        "session": session,
        "now": now,
        "focus_room": req.focus_room,
        # Chỉ đưa vị trí vào context khi ĐỦ TIN CẬY; nếu None → pipeline hỏi lại.
        "speaker_location": estimate.room,
        # Danh tính người nói là bằng chứng thu hẹp phòng — thiếu nó, "phòng ngủ" từ bố
        # vẫn bị hỏi lại dù hệ thống thừa biết phòng của họ.
        "speaker_home_room": effective_home_room,
        "speaker_private_room": effective_private_room,
        "last_device_id": req.last_device_id,
        "recent_dialogue": recent,
        "live_device_states": live_device_states,
        "live_sensors": live_sensors,
        "model_client": client,
    }
    # Chỉ offload khi có model network. Fast path giữ nguyên thread của DB session,
    # tương thích SQLite/test integrations có check_same_thread.
    result = (
        await run_in_threadpool(pipeline_bridge.reason, **reason_kwargs)
        if client is not None
        else pipeline_bridge.reason(**reason_kwargs)
    )
    latency_ms = (datetime.now(UTC) - started).total_seconds() * 1000
    response = _map_response(
        result,
        trace_id=trace_id,
        session_id=session_id,
        latency_ms=latency_ms,
        client=client,
        memories_used=0,
        reused_routine=None,
    )
    response.location = LocationOut(
        resolved=estimate.is_confident,
        room=estimate.room,
        confidence=estimate.confidence,
        source=estimate.source,
        evidence=list(estimate.evidence),
        candidates=[room for room, _ in estimate.candidates],
    )
    return response


def _presence_from_sensors(live_sensors: list[dict] | None) -> list[str]:
    """Rút danh sách phòng có người từ cảm biến hiện diện thật (live_sensors)."""
    rooms: list[str] = []
    for s in live_sensors or []:
        if str(s.get("sensor_type")) == "presence" and float(s.get("value", 0.0)) >= 1.0 and s.get("room"):
            rooms.append(str(s["room"]))
    return rooms
