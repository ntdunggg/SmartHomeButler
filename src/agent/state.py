"""Top-level AgentState cho pipeline multi-agent 5 tầng (spec §55).

Đây là state của orchestrator MỚI (`src/agent/pipeline.py`), tách hẳn khỏi
`AgentState` cũ trong `src/agent/graph.py` (pipeline cũ giữ nguyên để test cũ xanh).

Mỗi khoá ánh xạ một artifact trong luồng §56:
    runtime_context → semantic_analysis → semantic_goal → requirement_ledger →
    memory_evidence → preference_distribution → device_proposals → candidate_plans →
    selected_plan → validation_result → policy_decision → execution_result → feedback
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from src.agent.schemas import (
    AuditRecord,
    CandidateEnergyPlan,
    DeviceProposal,
    ExecutionResult,
    MemoryEvent,
    PowerLoad,
    PreferenceDistribution,
    RequirementLedger,
    RuntimeContext,
    SemanticGoal,
    SufficiencyDecision,
    ValidatedAction,
    ValidationResult,
)


class AgentState(TypedDict, total=False):
    """State đầy đủ của LangGraph 5-layer agent (spec §55)."""

    # ---- Đầu vào ----
    conversation_id: str
    user_message: str
    user_id: str | None
    household_id: int | None
    speaker_role: str  # Role value: owner | member
    now: datetime
    timezone: str
    focus_room: str | None
    speaker_location: str | None
    speaker_location_source: str
    speaker_location_confidence: float
    speaker_home_room: str | None
    speaker_private_room: str | None
    recent_dialogue: list[str] | None
    # Thiết bị đã tương tác gần nhất — mỏ neo anaphora tier-2 (§14) khi caller cấp trực
    # tiếp (khác tier-3 suy từ Ledger cùng conversation_id, xem `_last_device_from_ledger`).
    last_device_id: str | None
    live_device_states: dict[str, dict[str, Any]] | None
    live_sensors: list[dict[str, Any]] | None

    # ---- Layer 1: Perception ----
    normalized: Any  # NormalizedUtterance (facts tất định từ normalizer)
    runtime_context: RuntimeContext
    power_load: PowerLoad

    # ---- Layer 2: Understanding ----
    semantic_analysis: dict[str, Any]  # ambiguity/sufficiency status (§10-11)
    sufficiency_decision: SufficiencyDecision
    semantic_goal: SemanticGoal | None
    # Provenance tất định: True = goal suy diễn/fuzzy; False = explicit action + entity.
    # Validator dùng cờ này để cấm security action tự phát mà không chặn lệnh
    # security tường minh trước khi authorization/policy được chạy.
    is_inferred_goal: bool
    clarification_question: str | None
    clarification_reason: str | None
    requires_clarification: bool
    # Vai trò lượt (§5): new_goal | continuation | clarification_answer | cancellation | topic_switch.
    turn_intent: str
    # Dấu vết kế thừa grounding khi CONTINUATION (§4 preserve evidence).
    continuation_provenance: str
    # Provenance field-level do Context Resolver tạo trong chính lượt này (§14).
    evidence_trace: list[dict[str, Any]]

    # ---- Layer 3: Cognitive state, Memory, Preference ----
    requirement_ledger: RequirementLedger
    memory_evidence: list[MemoryEvent]
    profile_evidence: list[dict[str, Any]]
    preference_distribution: dict[str, PreferenceDistribution]  # dimension → dist

    # ---- Layer 4: Multi-agent planning + energy ----
    subgoals: list[Any]  # list[Subgoal]
    device_proposals: list[DeviceProposal]
    candidate_plans: list[CandidateEnergyPlan]
    selected_plan: CandidateEnergyPlan | None

    # ---- Layer 5: Harness ----
    validated_plan: list[ValidatedAction]
    dropped_noops: list[ValidatedAction]
    revalidate_rejected: list[str]  # entity bị loại ở bước revalidate-live (§50)
    validation_result: ValidationResult | None
    validation_errors: list[Any]
    authorization: Any  # AuthorizationResult
    policy_decision: dict[str, Any]  # {decision, priority_hit, reasons}
    execution_result: ExecutionResult | None
    observation: dict[str, Any]  # cô đọng cái đã xảy ra sau execute (§56 observe)
    audit: AuditRecord | None

    # ---- Layer 3 (rl reuse) ----
    rl_state: Any  # RLState đã mã hoá ở infer_preferences, dùng lại cho rl_update (§54)
    decision_context: dict[str, Any] | None  # snapshot ngữ cảnh chuẩn hoá, dùng lại cho HITL/feedback

    # ---- Feedback (§54) ----
    feedback: dict[str, Any]  # đầu vào có cấu trúc: accepted/rejected/corrected_value/dimension
    feedback_signal: Any  # FeedbackSignal đã diễn giải (interpreter §34)
    memory_written: dict[str, Any]  # {turn_id, event_id} đã ghi (write path §25)
    rl_update_result: dict[str, Any] | None  # {chosen, corrected} Q sau cập nhật (§32)

    # ---- Output ----
    final_status: str
    route_kind: str  # "answer" khi câu hỏi trạng thái/năng lực được trả lời trực tiếp
    reply: str
    # Lời từ chối riêng cho phần ngoài phạm vi của một yêu cầu mixed-domain; phần
    # smart-home vẫn tiếp tục qua planning/safety như bình thường.
    scope_refusal: str
    errors: list[str]
    llm_calls_count: int
    # Local observability survives even when external tracing is unavailable/quota-limited.
    node_latency_ms: dict[str, float]
    diagnostics: dict[str, Any]
