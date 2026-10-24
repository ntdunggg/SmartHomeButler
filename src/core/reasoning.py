"""Abstraction cho LLM Reasoning Engine + Fake Reasoning Model phục vụ test offline.

Kiến trúc open-ended: production dùng model thật được cấu hình qua ``MODEL_NAME`` qua
`structured_generate`. FakeReasoningModel CHỈ phục vụ test offline/CI — nó suy luận
thô theo vài cue tiếng Việt và **ground xuống thiết bị THẬT** qua `ground_selector`
(không hardcode slug, không bịa thiết bị) để plan offline vẫn hợp lệ với hard gate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ReasoningModel(Protocol):
    """Protocol giao tiếp chuẩn với LLM Provider (OpenAI, Claude, Gemini...)."""

    async def structured_generate(
        self,
        prompt: str,
        output_schema: type[T],
        *,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> T:
        ...


# Cue tiếng Việt → (domain thiết bị, capability, action, target_state) để Fake ground.
# Đây KHÔNG phải template sản phẩm — chỉ là suy luận giả lập cho test offline.
@dataclass(frozen=True, slots=True)
class _EnvCue:
    """Một cue môi trường của Fake, khớp theo RANH GIỚI TỪ chứ không phải substring.

    ``requires_any`` là ngữ cảnh BẮT BUỘC: cùng một tính từ ("ấm áp") nói về ánh sáng
    hay về nhiệt phòng là hai mục tiêu khác nhau, nên cue ánh sáng chỉ được bắt khi câu
    thực sự nêu danh từ ánh sáng. Không có nó thì cue nhiệt độ nuốt luôn "ánh sáng ấm áp".
    """

    words: tuple[str, ...]
    domain: str
    capability: str
    action: str
    target_state: dict[str, Any] = field(default_factory=dict)
    requires_any: tuple[str, ...] = ()

    def matches(self, utt: str) -> bool:
        if self.requires_any and not any(_has_word(utt, ctx) for ctx in self.requires_any):
            return False
        return any(_has_word(utt, word) for word in self.words)


def _has_word(text: str, phrase: str) -> bool:
    """Khớp `phrase` theo ranh giới từ.

    Substring làm "mắt" khớp cue "mát" và "làm" khớp cue "ấm" — sai đích ngay từ tầng
    goal. Ranh giới từ giữ nguyên độ phủ của cue mà bỏ hết va chạm trong-từ đó.
    """
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


_LIGHT_NOUNS = ("ánh sáng", "anh sang", "đèn", "den", "độ sáng", "do sang")

_ENV_CUES: tuple[_EnvCue, ...] = (
    # Nhiệt MÀU của đèn đứng TRƯỚC nhiệt độ phòng: "ánh sáng ấm áp hơn" là color_temp,
    # không phải bật điều hoà sưởi. Ấm hơn = Kelvin THẤP hơn nên hướng là `decrease`.
    _EnvCue(("ấm", "am", "ấm áp", "am ap", "vàng", "vang", "dịu", "diu"), "light", "color_temp", "decrease", requires_any=_LIGHT_NOUNS),
    _EnvCue(("trắng", "trang", "lạnh", "lanh"), "light", "color_temp", "increase", requires_any=_LIGHT_NOUNS),
    _EnvCue(("nóng", "nong", "oi", "nực", "nuc", "mát", "mat"), "air_conditioner", "temperature", "decrease"),
    _EnvCue(("lạnh", "lanh", "rét", "ret", "ấm", "am"), "air_conditioner", "temperature", "increase"),
    _EnvCue(("tối", "toi", "thiếu sáng", "thieu sang", "mờ", "sáng sủa", "sang sua", "đọc sách", "doc sach"), "light", "brightness", "increase"),
    _EnvCue(("sáng quá", "sang qua", "chói", "choi", "xem phim", "phim", "đi ngủ", "di ngu", "chuẩn bị ngủ", "buồn ngủ"), "light", "brightness", "decrease"),
    _EnvCue(("bí", "bi", "khó thở", "kho tho", "ngột", "ngot", "bụi", "bui", "không khí", "khong khi", "mùi", "mui"), "air_purifier", "on_off", "turn_on"),
    _EnvCue(("ẩm quá", "am qua", "độ ẩm", "do am", "khô hơn", "kho hon"), "air_conditioner", "hvac_mode", "set", {"hvac_mode": "dry"}),
    _EnvCue(("ồn", "ồn ào", "on ao", "yên tĩnh", "yen tinh"), "tv", "volume", "decrease"),
)


class FakeReasoningModel:
    """Fake/Spy LLM cho unit test: đếm số lần gọi + sinh output hợp lệ theo schema."""

    def __init__(self) -> None:
        self.call_count: int = 0
        self.calls: list[dict[str, Any]] = []

    def structured_generate_sync(
        self,
        prompt: str,
        output_schema: type[T],
        *,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> T:
        self.call_count += 1
        ctx = context or {}
        self.calls.append({"call_index": self.call_count, "schema": output_schema.__name__, "context": ctx})
        utt = str(ctx.get("utterance") or prompt or "").lower()

        from src.nlu.schemas import (
            ApprovalRecommendation,
            CandidateAction,
            CandidatePlan,
            LLMNextActionDecision,
            LLMUnderstandingResult,
            PlanCritique,
            RequestType,
            SemanticGoal,
        )

        if output_schema is LLMNextActionDecision:
            return self._next_action(utt)
        if output_schema is LLMUnderstandingResult:
            return self._understanding(utt, LLMUnderstandingResult, RequestType)
        if output_schema is SemanticGoal:
            return self._semantic_goal(utt, ctx, SemanticGoal)
        if output_schema is CandidatePlan:
            return self._candidate_plan(utt, ctx, CandidatePlan, CandidateAction)
        if output_schema is PlanCritique:
            return PlanCritique(goal_coverage=1.0, recommended_decision="accept")  # type: ignore[return-value]
        if output_schema is ApprovalRecommendation:
            return ApprovalRecommendation(recommendation="execute", reasoning_summary="Fake low-risk", confidence=0.95)  # type: ignore[return-value]
        return output_schema.model_construct()

    async def structured_generate(
        self,
        prompt: str,
        output_schema: type[T],
        *,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> T:
        return self.structured_generate_sync(
            prompt, output_schema, system_prompt=system_prompt, context=context, temperature=temperature
        )

    # ---- helpers ----
    @staticmethod
    def _next_action(utt: str) -> Any:
        from src.nlu.schemas import LLMNextActionDecision

        act = "build_goal"
        if "hệ thống" in utt or "làm được" in utt or "lỗi" in utt:
            act = "route_to_rag"
        elif "huỷ" in utt or "hủy" in utt or "thôi" in utt:
            act = "cancel"
        elif "không phải" in utt or "ý tôi là" in utt:
            act = "merge_correction"
        return LLMNextActionDecision(action=act, reason_summary="fake", evidence=["fake"], confidence=0.95)

    @staticmethod
    def _understanding(utt: str, schema: type, request_type_cls: Any) -> Any:
        rtc = request_type_cls
        if "hệ thống" in utt or "làm được" in utt or "lỗi u4" in utt:
            rt, plan, rag = rtc.KNOWLEDGE_QUERY, False, True
        elif "huỷ" in utt or "hủy" in utt or "bỏ đi" in utt or ("thôi" in utt and "bật" not in utt and "tắt" not in utt):
            rt, plan, rag = rtc.CANCEL, False, False
        elif any(k in utt for k in ("đi ngủ", "đi nghỉ", "muộn rồi", "nóng", "lạnh", "tối", "bí", "khó thở", "ra ngoài", "đi làm", "xem phim", "tắm")):
            rt, plan, rag = rtc.IMPLICIT_GOAL, True, False
        else:
            rt, plan, rag = rtc.DIRECT_COMMAND, True, False
        return schema(
            request_type=rt, utterance_type=rt.value.upper(), selected_intent=utt[:40] or "goal",
            confidence=0.95, requires_planning=plan, requires_rag=rag,
            is_cancellation="huỷ" in utt or "thôi" in utt, is_correction="không phải" in utt or "nhầm" in utt,
        )

    @staticmethod
    def _semantic_goal(utt: str, ctx: dict[str, Any], schema: type) -> Any:
        from src.core.interfaces import DeviceSelector
        from src.nlu.exclusion_clause import split_exclusion_clause
        from src.nlu.schemas import DesiredOutcome

        split = split_exclusion_clause(utt)
        positive_utt = split.positive_clause if split is not None else utt
        social = any(_has_word(utt, cue) for cue in ("chào", "xin chào", "hello", "hi", "cảm ơn", "cám ơn"))
        utype = (
            "SOCIAL_UTTERANCE"
            if social
            else (
                "ROUTINE_INTENT"
                if any(k in utt for k in ("đi ngủ", "chuẩn bị ngủ", "nghỉ", "ra ngoài", "đi làm", "xem phim", "tắm"))
                else "ENVIRONMENT_REQUEST"
            )
        )
        # Mức cảm nhận suy thô từ cường độ câu (để grounding offline có delta xác định).
        suffix = "_slight" if any(k in utt for k in ("hơi", "hoi", "chút", "chut")) else (
            "_large" if any(k in utt for k in ("cóng", "cong", "rất", "rat", "lắm", "lam")) else ""
        )
        outcomes: list[DesiredOutcome] = []
        activity_context: str | None = None

        def operational_outcome(
            domain: str,
            *,
            labels: tuple[str, ...] = (),
            area: str | None = None,
            cardinality: str = "one",
        ) -> DesiredOutcome:
            return DesiredOutcome(
                selector=DeviceSelector(
                    area=area or ctx.get("focus_room"),
                    domain=domain,
                    labels=list(labels),
                ),
                target_state={"power": "on"},
                cardinality=cardinality,
            )

        # Fake model mirrors capability-level activity semantics used by a real
        # semantic author. Concrete devices remain a registry-grounding concern.
        if any(_has_word(positive_utt, cue) for cue in ("dọn", "hút bụi", "vệ sinh sàn")):
            outcomes.append(operational_outcome("vacuum"))
            activity_context = "cleaning"
        elif _has_word(positive_utt, "nghe nhạc"):
            outcomes.append(operational_outcome("speaker"))
            activity_context = "listening_to_music"
        elif any(_has_word(positive_utt, cue) for cue in ("tắm", "tắm rửa")):
            # Bình nóng lạnh là thiết bị đơn nhất, không gắn phòng người nói: area=None để
            # Planning tự tìm bản duy nhất trong registry (khớp _room_of_selector singleton).
            outcomes.append(
                DesiredOutcome(
                    selector=DeviceSelector(area=None, domain="water_heater"),
                    target_state={"power": "on"},
                    cardinality="one",
                )
            )
            activity_context = "bathing"
        elif any(_has_word(positive_utt, cue) for cue in ("xem tv", "xem tivi")):
            outcomes.append(operational_outcome("tv"))
            activity_context = "watching_content"
        elif _has_word(positive_utt, "nắng") and any(
            _has_word(positive_utt, cue) for cue in ("chiếu", "chói", "gắt")
        ):
            outcomes.append(
                DesiredOutcome(
                    selector=DeviceSelector(area=ctx.get("focus_room"), domain="curtain"),
                    relative_change={"position": "decrease"},
                    cardinality="one",
                )
            )
        elif any(_has_word(positive_utt, cue) for cue in ("làm việc", "học", "học bài")):
            outcomes.append(
                DesiredOutcome(
                    selector=DeviceSelector(
                        area=ctx.get("focus_room"),
                        domain="light",
                        labels=["work_or_study_light"],
                    ),
                    relative_change={"brightness": "increase"},
                    cardinality="one",
                )
            )
            activity_context = "working"
        elif any(_has_word(positive_utt, cue) for cue in ("nấu ăn", "nấu nướng")):
            outcomes.append(
                DesiredOutcome(
                    selector=DeviceSelector(
                        area=ctx.get("focus_room"), domain="light", labels=["task_lighting"]
                    ),
                    relative_change={"brightness": "increase"},
                    cardinality="one",
                )
            )
            activity_context = "cooking"
        elif _has_word(positive_utt, "bàn ăn") and any(
            _has_word(positive_utt, cue) for cue in ("sáng", "rõ", "độ sáng")
        ):
            outcomes.append(
                DesiredOutcome(
                    selector=DeviceSelector(
                        area=ctx.get("focus_room"), domain="light", labels=["ambient_lighting"]
                    ),
                    relative_change={"brightness": "increase"},
                    cardinality="one",
                )
            )
        elif any(_has_word(positive_utt, cue) for cue in ("tiếp khách", "đón khách")):
            outcomes.append(
                DesiredOutcome(
                    selector=DeviceSelector(
                        area=ctx.get("focus_room"), domain="light", labels=["shared_light"]
                    ),
                    target_state={"power": "on"},
                    cardinality="any",
                )
            )
            activity_context = "socializing"
        elif "tiết kiệm điện" in positive_utt:
            bedrooms = [room for room in ctx.get("rooms", []) if "phòng ngủ" in str(room).lower()]
            outcomes.extend(
                DesiredOutcome(
                    selector=DeviceSelector(area=room, domain="light"),
                    target_state={"power": "off"},
                    cardinality="all",
                )
                for room in bedrooms
            )
            activity_context = "energy_saving"

        for cue in _ENV_CUES:
            if not outcomes and cue.matches(positive_utt):
                # cap="on_off" (bí quá) không phải capability số → chỉ nêu perceived_state, không có delta.
                rel = {cue.capability: f"{cue.action}{suffix}"} if cue.action in ("increase", "decrease") else {}
                outcomes.append(
                    DesiredOutcome(
                        selector=DeviceSelector(domain=cue.domain),
                        target_state=dict(cue.target_state),
                        perceived_state=cue.words[0],
                        relative_change=rel,
                        # A local complaint without an explicitly named room is
                        # an open comfort goal: an alternative device class may
                        # satisfy it (for example a curtain instead of a light).
                        # An explicitly scoped room complaint keeps the existing
                        # group semantics used by deterministic regression tests.
                        cardinality=(
                            "all"
                            if (ctx.get("signals", {}).get("matched_rooms") or [])
                            else "any"
                        ),
                    )
                )
                utype = "ENVIRONMENT_REQUEST"
                break
        # ROUTINE (ngủ/nghỉ/ra ngoài/xem phim...) là mục tiêu ACTIONABLE dù không có cue môi
        # trường. Production LLM thật LUÔN liệt kê desired_outcomes cho routine; Fake phải
        # mô phỏng cho trung thực, nếu không goal routine sẽ rỗng và bị lưới NOT_ACTIONABLE
        # của validator chặn nhầm (§2026-08-10). Mirror đúng nhánh routine của _candidate_plan.
        if not outcomes and any(k in positive_utt for k in ("đi ngủ", "sắp ngủ", "dễ ngủ", "chuẩn bị ngủ", "nghỉ", "ra ngoài", "đi làm", "xem phim")):
            outcomes.append(
                DesiredOutcome(
                    selector=DeviceSelector(area=ctx.get("focus_room"), domain="light"),
                    target_state={},
                    perceived_state="routine",
                    relative_change={},
                    cardinality="all",
                )
            )
        return schema(
            raw_utterance=ctx.get("utterance", utt) or utt,
            goal_description=utt,
            utterance_type=utype,
            confidence=0.9,
            activity_context=activity_context,
            desired_outcomes=outcomes,
            polarity="negative" if (ctx.get("signals", {}).get("has_negation", False) or "đừng" in utt or any(w in utt for w in ("không bật", "không tắt", "không mở", "không đóng", "không chỉnh"))) else "affirmative",
            is_cancellation=bool(ctx.get("signals", {}).get("has_cancellation", False) or ("huỷ" in utt or "thôi khỏi" in utt or "bỏ đi" in utt)),
            is_correction=bool(ctx.get("signals", {}).get("has_correction", False) or ("nhầm" in utt or "không phải" in utt or "à mà" in utt)),
        )

    @staticmethod
    def _candidate_plan(utt: str, ctx: dict[str, Any], schema: type, action_schema: type) -> Any:
        from src.domain.enums import ActionType, Capability
        from src.planning.grounding import devices_by  # noqa: F401  (đảm bảo registry nạp)

        actions = []
        area = ctx.get("target_area") or ctx.get("room") or ctx.get("speaker_home_room") or ctx.get("speaker_location") or ctx.get("focus_room")
        cue = next((c for c in _ENV_CUES if c.matches(utt)), None)
        if cue is None:
            if "bật đèn" in utt or "bat den" in utt:
                cue = _EnvCue(("bật đèn",), "light", "on_off", "turn_on")
            elif "tắt đèn" in utt or "tat den" in utt:
                cue = _EnvCue(("tắt đèn",), "light", "on_off", "turn_off")
            elif "bật điều hoà" in utt or "bat dieu hoa" in utt:
                cue = _EnvCue(("bật điều hoà",), "air_conditioner", "on_off", "turn_on")
            elif "tắt điều hoà" in utt or "tat dieu hoa" in utt:
                cue = _EnvCue(("tắt điều hoà",), "air_conditioner", "on_off", "turn_off")
            elif any(k in utt for k in ("đi ngủ", "chuẩn bị ngủ", "nghỉ")):
                cue = _EnvCue(("đi ngủ",), "light", "on_off", "turn_off")
            elif "tắm" in utt:
                cue = _EnvCue(("tắm",), "water_heater", "on_off", "turn_on")
        if cue is not None:
            domain, cap_name, act_name, target_state = cue.domain, cue.capability, cue.action, cue.target_state
            from src.planning.device_grounder import ground_selector

            specs, _warn = ground_selector({"domain": domain, "area": area})
            cap = Capability(cap_name)
            action = ActionType(act_name)
            for spec in specs[:3]:
                if cap not in spec.capabilities:
                    continue
                actions.append(
                    action_schema(
                        device_id=spec.slug, capability=cap, action=action,
                        params=dict(target_state), risk_level=spec.risk_level, reason_vi="fake plan",
                    )
                )
        return schema(
            goal_summary=utt, utterance_type="ENVIRONMENT_REQUEST", actions=actions, requires_policy_validation=True,
        )
