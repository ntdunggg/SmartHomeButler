"""Pydantic schema cho tầng NLU → SemanticGoal → CandidatePlan.

Theo quyết định kiến trúc: **tái dùng và mở rộng** các schema đã có ở
`src/domain/planning.py` (SemanticGoal, DeviceAction, ExecutionPlan,
ClarificationRequest) thay vì định nghĩa lại. Ở đây chỉ bổ sung các
model mà tầng NLU cần thêm: Device view, RuntimeContext, IntentCandidate,
ValidationError/ValidationResult, CandidateAction, CandidatePlan.

Mọi default list/dict dùng `Field(default_factory=...)` — không mutable default.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from src.core.interfaces import DeviceSelector
from src.domain.enums import ActionType, Capability, RiskLevel
from src.domain.planning import (  # noqa: F401  (re-export cho tầng dưới dùng một nguồn)
    ClarificationRequest,
    DeviceAction,
    ExecutionPlan,
)
from src.domain.planning import SemanticGoal as DomainSemanticGoal
from src.nlu.ontology import UtteranceType


class RequestType(StrEnum):
    DIRECT_COMMAND = "direct_command"
    IMPLICIT_GOAL = "implicit_goal"
    KNOWLEDGE_QUERY = "knowledge_query"
    STATE_QUERY = "state_query"
    CLARIFICATION_REPLY = "clarification_reply"
    CORRECTION = "correction"
    CANCEL = "cancel"
    UNKNOWN = "unknown"



# ---------------------------------------------------------------------------
# Runtime context
# ---------------------------------------------------------------------------
class Device(BaseModel):
    """Bản chụp một thiết bị từ Registry đưa vào context. ``device_id`` là slug gốc,
    không bao giờ do LLM sinh (Bước 5)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1)
    name: str
    room: str
    device_type: str
    risk_level: RiskLevel = RiskLevel.NORMAL
    capabilities: list[Capability] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    online: bool = True
    # Nguồn của `state` (spec §7.4): "live" = từ snapshot đo được; "default" = initial_state tĩnh
    # trong registry khi CHƯA có snapshot (KHÔNG phải quan sát thật — consumer phải biết để thận trọng).
    state_source: str = "live"

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


class ContextKind(StrEnum):
    """Phân loại rõ mức chắc chắn của một mẩu ngữ cảnh (Bước 5, Bước 13)."""

    OBSERVATION = "observation"  # sự thật quan sát được (trạng thái thiết bị, giờ)
    INFERENCE = "inference"  # suy ra từ dữ liệu, chưa chắc chắn
    ASSUMPTION = "assumption"  # giả định mặc định khi thiếu bằng chứng


class ContextNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContextKind
    text: str


class SensorReading(BaseModel):
    """Một chỉ số cảm biến đã chụp, kèm phòng để scope theo vị trí người nói.

    ``room`` rỗng nghĩa là cảm biến của cả nhà / ngoài trời (nhiệt độ ngoài, mưa,
    nắng, hiện diện), không thuộc phòng nào."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    sensor_type: str
    value: float
    unit: str = ""
    room: str = ""
    # Metadata độ tin cậy (spec §7.4): observed_at + is_stale + confidence + source. Giá trị tĩnh
    # từ registry (chưa có snapshot) phải được đánh dấu is_stale=True/source="default"/confidence=None
    # (unknown) — KHÔNG trình bày như một reading live.
    observed_at: datetime | None = None
    is_stale: bool = False
    confidence: float | None = None
    source: str = "live"


class RuntimeContext(BaseModel):
    """Ngữ cảnh runtime đã được lọc — chỉ thứ liên quan, không phải toàn bộ catalog."""

    model_config = ConfigDict(extra="forbid")

    now: datetime
    timezone: str = "Asia/Ho_Chi_Minh"
    devices: list[Device] = Field(default_factory=list)
    rooms: list[str] = Field(default_factory=list)
    # Phòng người nói đang đứng — TÍN HIỆU ĐO ĐƯỢC (client/cảm biến gửi), KHÁC với
    # focus_room (phòng suy ra từ hội thoại). Xem ContextKind.OBSERVATION vs ASSUMPTION.
    speaker_location: str | None = None
    # Provenance của vị trí: capture_device/ble là tín hiệu gắn với người nói;
    # presence_sensor chỉ chứng minh có AI ĐÓ trong phòng, không định danh người chat.
    speaker_location_source: str = ""
    speaker_location_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Phòng mặc định và phòng riêng là hai bằng chứng profile khác nhau.
    speaker_home_room: str | None = None
    speaker_private_room: str | None = None
    focus_room: str | None = None
    recent_dialogue: list[str] = Field(default_factory=list)
    # Chỉ số cảm biến đã chụp (live nếu có snapshot, tĩnh nếu không), để LLM kết hợp
    # câu nói + vị trí + trạng thái thiết bị + môi trường khi suy luận mục tiêu/kế hoạch.
    sensors: list[SensorReading] = Field(default_factory=list)
    notes: list[ContextNote] = Field(default_factory=list)

    def device_ids(self) -> set[str]:
        return {d.device_id for d in self.devices}

    def has_device(self, device_id: str) -> bool:
        return any(d.device_id == device_id for d in self.devices)

    def has_room(self, room: str) -> bool:
        return room in self.rooms

    def device(self, device_id: str) -> Device | None:
        return next((d for d in self.devices if d.device_id == device_id), None)


# ---------------------------------------------------------------------------
# Desired outcomes + semantic goal (open-ended, no closed intent set)
# ---------------------------------------------------------------------------
class DesiredOutcome(BaseModel):
    """Một kết quả mong muốn ở mức *capability*, không phải entity_id cụ thể.

    Đây là cách biểu diễn mục tiêu open-ended: LLM mô tả "muốn phòng khách sáng lên",
    "muốn không khí dễ thở hơn" bằng một `DeviceSelector` (theo area/domain/labels/role)
    + trạng thái đích, để planner ground xuống thiết bị thật theo capability — không
    cần intent đóng, không cần template.

    Hai tầng biểu diễn (Semantic State):
    - `perceived_state`: trạng thái người dùng ĐANG CẢM NHẬN ("cold", "too_bright",
      "stuffy") — để giải thích *vì sao* có hành động, không phải để điều khiển.
    - `relative_change`: thay đổi MONG MUỐN ở dạng TƯƠNG ĐỐI theo capability, ví dụ
      {"temperature": "increase_slight"}, {"brightness": "decrease"}. Planner sẽ ground
      delta này thành giá trị TUYỆT ĐỐI bằng trạng thái hiện tại + cảm biến (live context)
      — nhờ vậy không phải bịa con số ở tầng goal (không đoán gần đúng).
    - `target_state`: giá trị số tuyệt đối chỉ đặt khi người dùng nêu rõ hoặc có preference
      làm bằng chứng. Với activity/routine, trạng thái vận hành phi-số như
      ``{"power":"on"}`` được phép biểu diễn điều kiện cần để hoàn thành mục tiêu."""

    model_config = ConfigDict(extra="forbid")

    selector: DeviceSelector
    target_state: dict[str, Any] = Field(default_factory=dict)
    # Cảm nhận hiện tại (nhãn tự do): "cold" / "too_bright" / "stuffy" / "noisy" ...
    perceived_state: str = ""
    # capability (value của enum Capability, vd "temperature", "brightness") → token
    # thay đổi tương đối: increase|decrease (+ hậu tố mức _slight|_large tuỳ chọn).
    relative_change: dict[str, str] = Field(default_factory=dict)
    # one = đúng một thiết bị (nhiều thiết bị tương đương → hỏi lại);
    # any = một thiết bị bất kỳ đủ điều kiện; all = mọi thiết bị khớp.
    cardinality: str = Field(default="any", pattern="^(one|any|all)$")
    rationale: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_relative_change(cls, data: Any) -> Any:
        # LLM author trường này: chấp nhận None hoặc value không phải str, ép về dict[str,str]
        # để một sai lệch định dạng nhỏ không làm hỏng cả outcome (an toàn ở hard gate).
        if not isinstance(data, dict):
            return data
        rc = data.get("relative_change")
        if rc is None:
            data = {**data, "relative_change": {}}
        elif isinstance(rc, dict):
            valid_caps = {cap.value for cap in Capability}
            valid_tokens = {
                "increase", "increase_slight", "increase_large",
                "decrease", "decrease_slight", "decrease_large",
            }
            data = {
                **data,
                "relative_change": {
                    str(k): str(v)
                    for k, v in rc.items()
                    if k in valid_caps and str(v) in valid_tokens
                },
            }
        # `target_state`: nhiều model (đặc biệt họ reasoning gpt-5.*) trả `null` khi KHÔNG có
        # giá trị tuyệt đối — đúng nghĩa "để trống". Ép None/không-phải-dict về {} để một khác
        # biệt định dạng nhỏ không làm cả goal abstain (cùng triết lý coerce ở trên, §P2).
        ts = data.get("target_state")
        if ts is None or not isinstance(ts, dict):
            data = {**data, "target_state": {}}
        return data


class IntentCandidate(BaseModel):
    """Một cách hiểu khả dĩ (nhãn tự do, không thuộc tập đóng). Nhiều candidate gần
    nhau → tín hiệu cần clarification."""

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(..., min_length=1, description="Nhãn ý định tự do, ví dụ 'làm mát phòng khách'")
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""


class SemanticGoal(DomainSemanticGoal):
    """Mở rộng SemanticGoal domain cho tầng NLU open-ended.

    Không còn `primary_intent` thuộc tập đóng: mục tiêu được LLM diễn đạt tự do qua
    `goal_description` (văn bản) + `desired_outcomes` (capability-level). `intent` kế
    thừa từ domain vẫn là một nhãn *tự do* ngắn gọn (để log/hiển thị). `negated` là
    computed từ polarity — không cho tầng đề xuất tự khai giá trị mâu thuẫn."""

    # LLM author schema này: bỏ QUA field lạ (vd LLM thêm 'explanation') thay vì chặn cả
    # kết quả — LLM có thể hiểu đúng nhưng thừa field. Ràng buộc an toàn nằm ở hard gate,
    # không phụ thuộc extra="forbid" ở đây.
    model_config = ConfigDict(extra="ignore")

    utterance_type: UtteranceType
    goal_description: str = ""
    # Hoạt động hiện tại/sắp diễn ra do LLM diễn giải ở mức ngữ nghĩa (vd sleeping,
    # watching_content, working). Code dùng nó như bằng chứng ngữ cảnh, không như scene.
    activity_context: str | None = None
    # Provenance scope do code ghi đè sau authoring; model không có quyền tự cấp nguồn.
    target_area_source: str | None = None
    desired_outcomes: list[DesiredOutcome] = Field(default_factory=list)
    # Chỉ đặt cho ĐIỀU KHIỂN TƯỜNG MINH (turn_on/turn_off/set/increase/decrease) —
    # cho phép tổng hợp trực tiếp lệnh khi người dùng nêu rõ thiết bị + động từ.
    # None với mọi mục tiêu open-ended (LLM tổng hợp qua desired_outcomes + catalog).
    action_hint: str | None = None
    # Ghi đè action_hint THEO TỪNG THIẾT BỊ khi một câu ghép nêu ≥2 hành động khác nhau cho
    # ≥2 đích khác nhau ("Bật đèn lên, còn bình nóng lạnh thì tắt đi") — action_hint ở trên
    # vẫn giữ hành động của mệnh đề ĐẦU TIÊN (tương thích ngược cho code chỉ đọc action_hint,
    # ví dụ is_pure_prohibition), còn Manager (build_subgoals) ưu tiên đọc map này cho từng
    # slug nếu có mặt. Rỗng = hành vi cũ (một action_hint dùng chung mọi target).
    target_actions: dict[str, str] = Field(default_factory=dict)
    # Tham số THEO TỪNG THIẾT BỊ, song hành với `target_actions`. Mỗi mệnh đề của một câu ghép
    # mang giá trị RIÊNG ("mở rèm phòng bếp trước, sau đó chỉnh đèn bàn ăn xuống 50%": 50% chỉ
    # thuộc về đèn bàn ăn). `parameters` ở trên là của CẢ CÂU nên không diễn đạt được điều đó;
    # trước khi có field này, mệnh đề `set` bị dựng KHÔNG params rồi rớt ở validator
    # (`DeviceAction` bắt buộc `set` phải kèm params) — người dùng mất hẳn một vế mà không được
    # báo gì. Rỗng = hành vi cũ (mọi thiết bị dùng chung `parameters`).
    target_parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    is_correction: bool = False
    is_cancellation: bool = False
    references_resolved: bool = True
    assumptions: list[str] = Field(default_factory=list)
    # Ràng buộc PHỦ ĐỊNH cấp thiết bị: "đạt mục tiêu X nhưng TRÁNH thiết bị Y". Khác negated
    # (phủ định cả câu). Câu "nóng nhưng đừng bật điều hoà" = mục tiêu DƯƠNG (làm mát) +
    # excluded_device_ids=[dieu_hoa...]. Planner/optimizer loại các slug này khỏi kế hoạch.
    excluded_device_ids: list[str] = Field(default_factory=list)
    # Deterministically grounded scope whose current state must be preserved.
    # This is separate from a generic exclusion: it records the user's explicit
    # no-change semantics so the ledger does not have to infer it from `avoid:`.
    no_change_device_ids: list[str] = Field(default_factory=list)
    # Canonical deterministic constraints authored by continuation parsing, e.g.
    # ``bound:max:brightness:70:den_ban_hoc``.  LLM-authored free text is never
    # trusted here; only code populates this field.
    explicit_constraints: list[str] = Field(default_factory=list)
    # Provenance của target_device_ids: True = bằng chứng TẤT ĐỊNH (lệnh tường minh, alias
    # khớp trực tiếp, ledger-carry continuation...); False = chỉ là ĐỀ XUẤT tự do của LLM
    # (author_goal open-ended). Manager dùng cờ này để quyết target_device_ids có được quyền
    # hard-anchor (loại các thiết bị khác cùng domain/phòng) hay chỉ là một ứng viên thêm vào,
    # không được phép hất một anchor tất định đã ground từ lượt trước (spec §P1/§P2).
    target_devices_deterministic: bool = True
    # Explicitly taught conditional instruction recalled from EMem.  This is not a
    # built-in scene: source event/turn provenance points back to the user's words.
    user_defined_routine: bool = False
    routine_constraint_only: bool = False
    routine_source_event_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _fill_intent_label(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Base domain yêu cầu `intent: str` (min_length=1). Nếu caller chỉ đưa
        # goal_description, dùng nó làm nhãn để không phải truyền hai lần.
        if not data.get("intent"):
            label = data.get("goal_description") or data.get("raw_utterance") or "goal"
            data = {**data, "intent": str(label)[:120] or "goal"}

        # Coerce polarity: affirmative hoặc negative
        pol = str(data.get("polarity") or "").lower()
        if "neg" in pol or "khong" in pol or "dung" in pol or pol == "false":
            data = {**data, "polarity": "negative"}
        else:
            data = {**data, "polarity": "affirmative"}

        # Coerce utterance_type: invalid -> UNKNOWN
        utype = data.get("utterance_type")
        if not utype:
            data = {**data, "utterance_type": UtteranceType.UNKNOWN}
        else:
            utype_str = str(utype).strip().upper()
            try:
                data = {**data, "utterance_type": UtteranceType(utype_str)}
            except ValueError:
                data = {**data, "utterance_type": UtteranceType.UNKNOWN}

        # Coerce assumptions & target_device_ids -> list[str]
        for key in (
            "assumptions",
            "target_device_ids",
            "excluded_device_ids",
            "no_change_device_ids",
            "explicit_constraints",
        ):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                data = {**data, key: [str(x) for x in val if x is not None]}

        hint = data.get("action_hint")
        valid_actions = {action.value for action in ActionType}
        if hint is not None and str(hint) not in valid_actions:
            data = {**data, "action_hint": None}

        # target_actions là field TẤT ĐỊNH do Understanding tự điền (câu ghép ≥2 hành động
        # khác nhau) — LLM open-ended tự do đôi khi cũng cố điền field này (hallucination,
        # kiểu dữ liệu sai) dù không cần thiết cho đường của nó. Rơi về {} thay vì cho lỗi
        # schema chặn cả goal (giống cách action_hint không hợp lệ được coerce ở trên), để
        # một field caller không thật sự cần không làm SchemaRepairFailed/abstain cả lượt.
        ta = data.get("target_actions")
        if not isinstance(ta, dict):
            data = {**data, "target_actions": {}}
        else:
            cleaned = {
                str(k): str(v)
                for k, v in ta.items()
                if isinstance(k, str) and isinstance(v, str) and v in valid_actions
            }
            data = {**data, "target_actions": cleaned}

        # Same trust rule as target_actions: per-device parameters are authored
        # deterministically for explicit compound commands.  Open-ended models may
        # emit a flat map such as {"brightness": 30}; that shape has no device
        # provenance and must not make an otherwise valid semantic goal abstain.
        tp = data.get("target_parameters")
        if not isinstance(tp, dict):
            data = {**data, "target_parameters": {}}
        else:
            cleaned_parameters = {
                str(device_id): dict(parameters)
                for device_id, parameters in tp.items()
                if isinstance(device_id, str) and isinstance(parameters, dict)
            }
            data = {**data, "target_parameters": cleaned_parameters}

        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def negated(self) -> bool:
        return self.polarity == "negative"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class ValidationSeverity(StrEnum):
    ERROR = "error"  # chặn — không tạo plan
    WARNING = "warning"  # không chặn nhưng ghi lại


class ValidationDecision(StrEnum):
    PROCEED = "proceed"  # tạo candidate plan
    CLARIFY = "clarify"  # bắt buộc hỏi lại


class ValidationError(BaseModel):
    """Một lỗi/khiếm khuyết deterministic validator phát hiện.

    Tên theo contract Bước 4; đây KHÔNG phải pydantic.ValidationError."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message_vi: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    # Token ràng buộc/bằng chứng gây ra lỗi, dạng máy đọc được (vd "keep_off:den_chum_phong_khach").
    # Tầng trình bày cần biết LÝ DO THẬT để không báo nhầm "đã ở trạng thái phù hợp"; parse ngược
    # `message_vi` là một nguồn sự thật thứ hai và sẽ vỡ khi đổi câu chữ.
    subject: str = ""


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    decision: ValidationDecision
    final_confidence: float = Field(..., ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)
    errors: list[ValidationError] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(e.severity == ValidationSeverity.ERROR for e in self.errors)


# ---------------------------------------------------------------------------
# Candidate plan (output cuối của AI Engineer)
# ---------------------------------------------------------------------------
class CandidateAction(DeviceAction):
    """Một hành động đề xuất, kèm lý do và giả định. Vẫn chỉ là proposal — không
    execute. Kế thừa ràng buộc của DeviceAction: bắt buộc device_id + capability."""

    reason_vi: str = ""
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_action_lists(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in ("assumptions", "missing_information"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                data = {**data, key: [str(x) for x in val if x is not None]}
        return data


class CandidatePlan(BaseModel):
    """Kết quả cuối cùng của tầng AI Engineer: candidate plan có cấu trúc.

    Bất biến: ``requires_policy_validation`` LUÔN True — plan này chưa qua policy
    engine, không được phép execute (Bước 9, test 19).

    LLM author schema này: bỏ QUA field lạ ở cấp wrapper (an toàn thật nằm ở hard gate
    soi từng action). Từng `CandidateAction` VẪN strict (kế thừa DeviceAction extra=forbid)
    để không hành động vật lý mờ ám nào lọt qua."""

    model_config = ConfigDict(extra="ignore")

    goal_summary: str = ""
    utterance_type: UtteranceType
    actions: list[CandidateAction] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    requires_policy_validation: bool = True
    requires_confirmation: bool = False
    explanation_vi: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_candidate_plan_before(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        utype = data.get("utterance_type")
        if not utype:
            data = {**data, "utterance_type": UtteranceType.UNKNOWN}
        else:
            utype_str = str(utype).strip().upper()
            try:
                data = {**data, "utterance_type": UtteranceType(utype_str)}
            except ValueError:
                data = {**data, "utterance_type": UtteranceType.UNKNOWN}

        for key in ("assumptions", "missing_information"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                data = {**data, key: [str(x) for x in val if x is not None]}

        actions = data.get("actions")
        if isinstance(actions, list):
            coerced_actions = []
            for act in actions:
                if isinstance(act, dict):
                    for k in ("assumptions", "missing_information"):
                        v = act.get(k)
                        if v is None:
                            act = {**act, k: []}
                        elif isinstance(v, str):
                            act = {**act, k: [v] if v.strip() else []}
                        elif isinstance(v, list):
                            act = {**act, k: [str(x) for x in v if x is not None]}
                    coerced_actions.append(act)
                else:
                    coerced_actions.append(act)
            data = {**data, "actions": coerced_actions}

        return data

    @model_validator(mode="after")
    def _force_policy_validation(self) -> CandidatePlan:
        # Bảo đảm bất biến an toàn bất kể ai khởi tạo: candidate plan không bao giờ
        # tự cho mình "đã qua policy". Coerce thay vì tin đầu vào.
        if not self.requires_policy_validation:
            object.__setattr__(self, "requires_policy_validation", True)
        return self


# ---------------------------------------------------------------------------
# Prompt Section 6 Schemas (Ambiguous Language & Selector-based planning)
# ---------------------------------------------------------------------------
class ResolvedReference(BaseModel):
    raw_reference: str
    resolved_entity_id: str | None = None
    resolved_area: str | None = None
    confidence: float = 1.0


class InterpretationResult(BaseModel):
    utterance_type: str
    intent_candidates: list[IntentCandidate] = Field(default_factory=list)
    resolved_references: list[ResolvedReference] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    requires_clarification: bool = False
    clarification_reason: str | None = None


class CandidatePlanStep(BaseModel):
    step_id: str
    selector: DeviceSelector
    desired_state: dict[str, Any]
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    required: bool = True
    risk_hint: str = "normal"


class ValidatedAction(BaseModel):
    action_id: str
    entity_id: str
    domain: str
    service: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    previous_state: dict[str, Any] | None = None
    desired_state: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    risk_level: str = "normal"
    requires_confirmation: bool = False


class AIPlanResponse(BaseModel):
    status: str = Field(
        ...,
        description="status: clarification_required | plan_proposed | confirmation_required | ready_for_execution | rejected | failed",
    )
    semantic_goal: SemanticGoal | None = None
    validated_plan: list[ValidatedAction] = Field(default_factory=list)
    clarification_question: str | None = None
    approval_decision: str = "reject"
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[ValidationError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM-First Pipeline Schemas (Prompt Section 6, 12, 13, 16)
# ---------------------------------------------------------------------------
class MissingInformation(BaseModel):
    slot_name: str
    description: str
    importance: str = "required"


class Contradiction(BaseModel):
    type: str
    description: str


class Assumption(BaseModel):
    statement: str
    confidence: float = 0.9


class LLMUnderstandingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_type: RequestType = RequestType.DIRECT_COMMAND
    utterance_type: str = "DEVICE_COMMAND"
    intent_candidates: list[IntentCandidate] = Field(default_factory=list)
    selected_intent: str | None = None
    resolved_references: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.95
    requires_clarification: bool = False
    clarification_reason: str | None = None
    clarification_question: str | None = None
    requires_planning: bool = True
    requires_rag: bool = False
    is_correction: bool = False
    is_cancellation: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce_list_fields(cls, data: Any) -> Any:
        """Ép linh hoạt các field metadata thô từ LLM về list[str] để lỗi định dạng không làm hỏng hiểu ý định."""
        if not isinstance(data, dict):
            return data
        for key in ("resolved_references", "missing_information", "contradictions", "assumptions"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                coerced = []
                for item in val:
                    if isinstance(item, dict):
                        desc = item.get("description") or item.get("statement") or item.get("raw_reference") or str(item)
                        coerced.append(str(desc))
                    elif item is not None:
                        coerced.append(str(item))
                data = {**data, key: coerced}
            else:
                data = {**data, key: [str(val)]}
        return data


class LLMGroundedStep(BaseModel):
    step_id: str
    entity_id: str
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    grounding_evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.95


class UnresolvedStep(BaseModel):
    step_id: str
    reason: str


class LLMGroundingResult(BaseModel):
    grounded_steps: list[LLMGroundedStep] = Field(default_factory=list)
    unresolved_steps: list[UnresolvedStep] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_question: str | None = None


class PlanIssue(BaseModel):
    issue_type: str
    description: str
    severity: str = "warning"


class PlanRepair(BaseModel):
    step_id: str
    proposed_change: str


class PlanCritique(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal_coverage: float = 1.0
    issues: list[PlanIssue] = Field(default_factory=list)
    suggested_repairs: list[PlanRepair] = Field(default_factory=list)
    recommended_decision: str = "clarify"  # accept | repair | clarify | reject

    @model_validator(mode="before")
    @classmethod
    def _coerce_list_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in ("issues", "suggested_repairs"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                if key == "issues":
                    data = {**data, key: [{"issue_type": "general", "description": val}]}
                else:
                    data = {**data, key: [{"step_id": "all", "proposed_change": val}]}
            elif isinstance(val, list):
                coerced = []
                for item in val:
                    if isinstance(item, str):
                        if key == "issues":
                            coerced.append({"issue_type": "general", "description": item})
                        else:
                            coerced.append({"step_id": "all", "proposed_change": item})
                    elif isinstance(item, dict):
                        coerced.append(item)
                data = {**data, key: coerced}
        return data


class ApprovalRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendation: str = "confirm"  # reject | clarify | suggest | confirm | execute
    reasoning_summary: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    confidence: float = 0.95

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        val = data.get("risk_factors")
        if val is None:
            data = {**data, "risk_factors": []}
        elif isinstance(val, str):
            data = {**data, "risk_factors": [val] if val.strip() else []}
        return data


class LLMNextActionDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = Field(
        default="build_goal",
        description="action: clarify | build_goal | route_to_rag | merge_correction | cancel | reject",
    )
    reason_summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.95

    @model_validator(mode="before")
    @classmethod
    def _coerce_evidence(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        val = data.get("evidence")
        if val is None:
            data = {**data, "evidence": []}
        elif isinstance(val, str):
            data = {**data, "evidence": [val] if val.strip() else []}
        return data
