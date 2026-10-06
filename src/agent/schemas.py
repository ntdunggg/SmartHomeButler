"""Schema chuẩn cho hệ multi-agent 5 tầng (SMART_HOME_MULTI_AGENT_SPEC_v2).

Nguyên tắc MỘT NGUỒN: các abstraction đã có ở `src/nlu/schemas` (RuntimeContext §7.3,
SemanticGoal §12, DesiredOutcome, ValidatedAction, CandidatePlan, ValidationResult)
được **re-export** ở đây thay vì định nghĩa lại — spec §71 ưu tiên tái dùng abstraction.

File này CHỈ bổ sung các schema mà spec yêu cầu nhưng codebase chưa có:

- ``PowerLoad`` (§8)                — NORMAL / MODERATE / CRITICAL + watts
- ``RequirementLedger`` (§17-18)    — canonical conversation state
- ``PreferenceDistribution`` (§33)  — phân phối lựa chọn theo context
- ``DeviceProposal`` (§37)          — đề xuất của một specialist (chưa execute)
- ``CandidateEnergyPlan`` (§39-40)  — plan ứng viên + điểm số năng lượng/comfort
- ``MemoryEvent`` (§21) + ``TurnRecord`` (§20) — EMem event-centric memory
- ``ProfileFact`` (§24)             — semantic/profile memory
- ``ExecutionResult`` (§51)         — kết quả thực thi vật lý
- ``AuditRecord`` (§53)             — bản ghi kiểm toán

Mọi list/dict default dùng ``Field(default_factory=...)`` — không mutable default.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --- Re-export nguồn chân lý sẵn có (spec §71: tái dùng abstraction, không định nghĩa lại) ---
from src.nlu.schemas import (  # noqa: F401
    CandidateAction,
    CandidatePlan,
    ClarificationRequest,
    DesiredOutcome,
    Device,
    RuntimeContext,
    SemanticGoal,
    SensorReading,
    ValidatedAction,
    ValidationDecision,
    ValidationError,
    ValidationResult,
)

__all__ = [
    "CandidateAction",
    "CandidateEnergyPlan",
    "CandidatePlan",
    "ClarificationRequest",
    "DesiredOutcome",
    "Device",
    "PowerMode",
    "PowerLoad",
    "LedgerBoundState",
    "LedgerGroupExclusionState",
    "LedgerNoChangeState",
    "RequirementLedger",
    "PreferenceDistribution",
    "DeviceProposal",
    "ProposalAction",
    "MemoryEvent",
    "MemoryFact",
    "TurnRecord",
    "ProfileFact",
    "ExecutionResult",
    "ExecutedAction",
    "AuditRecord",
    "RuntimeContext",
    "SemanticGoal",
    "SensorReading",
    "SufficiencyDecision",
    "PolicyPriority",
    "ValidatedAction",
    "ValidationDecision",
    "ValidationError",
    "ValidationResult",
]


# ---------------------------------------------------------------------------
# §8 — Power Load model
# ---------------------------------------------------------------------------
class PowerMode(StrEnum):
    """Power state chuẩn hoá (spec §8). Threshold nằm trong config, KHÔNG hardcode."""

    NORMAL = "NORMAL"
    MODERATE = "MODERATE"
    CRITICAL = "CRITICAL"


class PowerLoad(BaseModel):
    """Snapshot tải điện hiện tại của căn nhà (spec §7.3 `power`, §8)."""

    model_config = ConfigDict(extra="forbid")

    current_watts: float = Field(..., ge=0.0)
    mode: PowerMode = PowerMode.NORMAL
    moderate_threshold_w: float = 3500.0
    critical_threshold_w: float = 5000.0
    # §7.4: chưa có snapshot tải → KHÔNG biết công suất thật (không được coi mặc định 0W/NORMAL là
    # quan sát). current_watts/mode khi đó chỉ là ước lượng bảo thủ; consumer phải biết là unknown.
    unknown: bool = False


# ---------------------------------------------------------------------------
# §13 — Semantic Sufficiency Gate decisions
# ---------------------------------------------------------------------------
class SufficiencyDecision(StrEnum):
    """Kết quả Sufficiency Gate (spec §13) — không chỉ dựa vào confidence."""

    PROCEED = "PROCEED"
    RESOLVE_CONTEXT = "RESOLVE_CONTEXT"
    CLARIFY = "CLARIFY"
    ABSTAIN = "ABSTAIN"


# ---------------------------------------------------------------------------
# §17-18 — Requirement Ledger (canonical conversation state)
# ---------------------------------------------------------------------------
class LedgerEvidence(BaseModel):
    """Một mẩu bằng chứng cho một giá trị đã chốt trong ledger (spec §14 trace)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    value: Any = None
    source: str = "user_utterance"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    turn: int | None = None


class LedgerBoundState(BaseModel):
    """Typed numeric guard authored from deterministic continuation parsing."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern="^(min|max)$")
    dimension: str = Field(min_length=1)
    value: float
    scope: str = "*"
    turn: int | None = None


class LedgerGroupExclusionState(BaseModel):
    """A durable exclusion from an explicitly grounded device group."""

    model_config = ConfigDict(extra="forbid")

    excluded_device_ids: list[str] = Field(default_factory=list)
    anchor_device_ids: list[str] = Field(default_factory=list)
    room: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    turn: int | None = None


class LedgerNoChangeState(BaseModel):
    """Explicit request to preserve current state for a grounded scope."""

    model_config = ConfigDict(extra="forbid")

    device_ids: list[str] = Field(default_factory=list)
    room: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    turn: int | None = None


class RequirementLedger(BaseModel):
    """Canonical conversation state (spec §17).

    Mỗi user turn: read → interpret delta → merge → **preserve confirmed constraints**
    → **preserve rejected assumptions** (§17.1). KHÔNG rebuild từ raw history mỗi lượt.
    Planner đọc ledger này thay vì raw chat (§P4).
    """

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = ""
    current_goal: dict[str, Any] = Field(default_factory=dict)
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)
    desired_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    # Ràng buộc người dùng nêu rõ, phải sống qua nhiều lượt (§73 invariant 8).
    constraints: list[str] = Field(default_factory=list)
    # Canonical typed state. `constraints` remains a compatibility mirror for old
    # snapshots/API consumers; new semantic consumers read these fields first.
    bounds: list[LedgerBoundState] = Field(default_factory=list)
    group_exclusions: list[LedgerGroupExclusionState] = Field(default_factory=list)
    no_change: list[LedgerNoChangeState] = Field(default_factory=list)
    preferences_in_scope: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    # Câu hỏi làm rõ ĐANG TREO (§15, §17): lưu TƯỜNG MINH thay vì tái dựng từ raw history
    # mỗi lượt. Nhờ đó lượt sau nhận diện "trả lời clarification" một cách tất định và giữ
    # được dấu vết (đã hỏi field gì, ở lượt nào, cho mục tiêu nào). Rỗng = không treo gì.
    # Cấu trúc: {"fields": [...], "question": str, "turn": int, "raw_utterance": str}.
    pending_clarification: dict[str, Any] = Field(default_factory=dict)
    # Assumption người dùng đã bác bỏ — planner KHÔNG được tái tạo (§18, invariant 9).
    rejected_assumptions: list[str] = Field(default_factory=list)
    evidence: list[LedgerEvidence] = Field(default_factory=list)
    # Trace nguyên bản của lần context-resolution gần nhất đã cập nhật ledger.
    # `evidence` ở trên giữ lịch sử field-level theo turn; trường này giữ cả supporting
    # evidence để pipeline bridge/audit có thể tái dựng chính xác vì sao field được chọn.
    last_resolution_evidence_trace: list[dict[str, Any]] = Field(default_factory=list)
    last_updated_turn: int = 0
    # Salience Stack (§14, docs/BLUEPRINT_CONTEXT_TRANSDUCER): ngăn xếp thiết bị NỔI BẬT qua các
    # lượt (cũ→mới), để giải đại từ "nó/tắt đi/bật lên" theo thực thể gần nhất TƯƠNG THÍCH capability.
    # Mỗi phần tử: {device_id, room, capabilities, turn, kind, action}.
    # Xem src/context/salience.py.
    salience: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §33 — Preference Distribution (output của Preference RL)
# ---------------------------------------------------------------------------
class PreferenceDistribution(BaseModel):
    """Phân phối lựa chọn theo context (spec §33).

    Planner KHÔNG coi top-1 là chân lý tuyệt đối. `distribution` map giá trị action
    (dạng chuỗi để JSON-safe) → xác suất.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: str  # "temperature" | "brightness" | ...
    context: dict[str, Any] = Field(default_factory=dict)
    distribution: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    def top(self) -> tuple[str, float] | None:
        """Action xác suất cao nhất, hoặc None nếu phân phối rỗng."""
        if not self.distribution:
            return None
        key = max(self.distribution, key=lambda k: self.distribution[k])
        return key, self.distribution[key]


# ---------------------------------------------------------------------------
# §37 — Device Proposal (đề xuất của một specialist, CHƯA execute)
# ---------------------------------------------------------------------------
class ProposalAction(BaseModel):
    """Một hành động đề xuất trong DeviceProposal — vẫn chỉ là proposal (§P2)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1)
    capability: str
    action: str  # value của ActionType (turn_on/turn_off/set/...)
    target: dict[str, Any] = Field(default_factory=dict)
    reason_vi: str = ""


class DeviceProposal(BaseModel):
    """Đề xuất từ một Device Agent/Specialist (spec §37).

    `estimated_power_w` = None nghĩa là "unknown" (spec §42: không bịa power estimate).
    """

    model_config = ConfigDict(extra="forbid")

    agent: str  # "lighting" | "ac" | "shutter" | "media" | "security"
    objective: str = ""
    actions: list[ProposalAction] = Field(default_factory=list)
    estimated_comfort: float = Field(default=0.0, ge=0.0, le=1.0)
    estimated_power_w: float | None = None
    estimated_energy_delta_wh: float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Vi phạm safety phát hiện ở tầng specialist → optimizer coi plan INFEASIBLE (§40).
    safety_violation: bool = False
    evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §39-40 — Candidate energy plan (đầu ra Aggregator, chấm điểm bởi Optimizer)
# ---------------------------------------------------------------------------
class CandidateEnergyPlan(BaseModel):
    """Một plan ứng viên do Aggregator dựng, Energy Optimizer chấm điểm (spec §40)."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    actions: list[ProposalAction] = Field(default_factory=list)
    source_agents: list[str] = Field(default_factory=list)
    comfort: float = Field(default=0.0, ge=0.0, le=1.0)
    preference_match: float = Field(default=0.0, ge=0.0, le=1.0)
    estimated_power_w: float = 0.0
    # Có proposal nào KHÔNG biết công suất (§42 unknown) → giữ cờ để optimizer xử lý bảo thủ,
    # KHÔNG coi unknown = 0 W (miễn phí). estimated_power_w khi đó chỉ là tổng phần ĐÃ biết.
    power_unknown: bool = False
    constraint_penalty: float = 0.0
    # SafetyViolation KHÔNG chỉ là penalty — plan phải INFEASIBLE (spec §40).
    infeasible: bool = False
    infeasible_reason: str = ""
    score: float = 0.0


# ---------------------------------------------------------------------------
# §20-21, §24 — Memory (Turn Store, EMem Event, Profile fact)
# ---------------------------------------------------------------------------
class TurnRecord(BaseModel):
    """Raw evidence của một lượt hội thoại (spec §20)."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    conversation_id: str
    speaker: str = "user"
    text: str = ""
    timestamp: datetime | None = None
    runtime_context_ref: str | None = None


class MemoryFact(BaseModel):
    """Một fact (subject-relation-value) trích từ event (spec §21 `facts`)."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    relation: str
    value: Any = None


class MemoryEvent(BaseModel):
    """Event-like long-term memory (spec §21). KHÔNG lưu chỉ bằng raw chat chunk.

    `event_type` là metadata giúp retrieval, KHÔNG được trở thành closed routine
    taxonomy (spec §21, §P8).
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str = "household_activity"
    actors: list[str] = Field(default_factory=list)
    location: list[str] = Field(default_factory=list)
    start_time: datetime | None = None
    summary: str = ""
    facts: list[MemoryFact] = Field(default_factory=list)
    source_turn_ids: list[str] = Field(default_factory=list)
    embedding: list[float] = Field(default_factory=list)
    # KHÔNG GIAN của `embedding` (tên model + số chiều, vd "text-embedding-3-small-256"
    # hay "hashing-64"). Cosine chỉ có nghĩa giữa hai vector CÙNG không gian; không có
    # nhãn này, ký ức cũ nhúng bằng hashing so với truy vấn nhúng bằng API sẽ lặng lẽ
    # cho điểm 0 và tắt âm thầm phần truy hồi ngữ nghĩa. Rỗng = snapshot cũ (hashing).
    embedding_space: str = ""


class ProfileFact(BaseModel):
    """Semantic/Profile memory — fact tương đối ổn định (spec §24).

    KHÔNG promote một event đơn lẻ thành stable fact nếu evidence chưa đủ (§24, §71).
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    fact: str
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_events: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §48 — Policy priority
# ---------------------------------------------------------------------------
class PolicyPriority(StrEnum):
    """Thứ tự ưu tiên policy (spec §48). Lower KHÔNG override higher."""

    P0_HARD_SAFETY = "P0_hard_safety"
    P1_AUTHORIZATION = "P1_authorization"
    P2_EXPLICIT_CONSTRAINT = "P2_explicit_constraint"
    P3_COMFORT = "P3_comfort"
    P4_ENERGY = "P4_energy"
    P5_CONVENIENCE = "P5_convenience"


# ---------------------------------------------------------------------------
# §51 — Execution result
# ---------------------------------------------------------------------------
class ExecutedAction(BaseModel):
    """Kết quả một action vật lý (spec §51)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    requested: dict[str, Any] = Field(default_factory=dict)
    status: str = "SUCCESS"  # SUCCESS | FAILED | SKIPPED | NO_OP
    reason: str = ""
    resulting_state: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    """Kết quả thực thi cả plan (spec §51).

    Natural-language response MUST dựa trên execution result THẬT (spec §51).
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str = ""
    status: str = "SUCCESS"  # SUCCESS | PARTIAL_SUCCESS | FAILED | NO_OP
    actions: list[ExecutedAction] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §53 — Audit record
# ---------------------------------------------------------------------------
class AuditRecord(BaseModel):
    """Bản ghi kiểm toán mọi hành động vật lý (spec §14, §53)."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime | None = None
    conversation_id: str = ""
    user: str = ""
    input: str = ""
    semantic_goal: dict[str, Any] = Field(default_factory=dict)
    ledger_snapshot: dict[str, Any] = Field(default_factory=dict)
    evidence_trace: list[dict[str, Any]] = Field(default_factory=list)
    memory_used: list[str] = Field(default_factory=list)
    profile_used: list[dict[str, Any]] = Field(default_factory=list)  # stable fact §24 đã dùng
    preference_used: dict[str, Any] = Field(default_factory=dict)
    candidate_plans: list[dict[str, Any]] = Field(default_factory=list)
    selected_plan: dict[str, Any] = Field(default_factory=dict)
    policy_decision: dict[str, Any] = Field(default_factory=dict)
    execution_result: dict[str, Any] = Field(default_factory=dict)
