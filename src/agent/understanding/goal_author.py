"""LLM open-ended goal authoring (spec §12, §P1) — hiểu câu MỚI LẠ → SemanticGoal.

Đây là mảnh khoá cho FR-16 (generalize sang paraphrase/unseen): với câu KHÔNG phải lệnh
tường minh, LLM diễn đạt mục tiêu tự do (`goal_description` + `desired_outcomes` cấp
capability) — KHÔNG intent đóng, KHÔNG map phrase→scene (§8, §66). Sau đó code TẤT ĐỊNH
ép các bất biến lên goal do LLM author (bỏ thiết bị bịa, ràng phòng thật, giữ phủ định/
sửa/huỷ từ normalizer) — "LLM proposes, code decides" (§P2).

Model là `ReasoningModel` (model cấu hình qua `MODEL_NAME` ở production; `FakeReasoningModel` offline
vẫn author goal từ cue tiếng Việt để CI kiểm generalization mà không gọi mạng).
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import nullcontext
from typing import Any

from src.agent.schemas import DesiredOutcome, RuntimeContext, SemanticGoal
from src.agent.understanding.environment_goal import repair_environment_goal
from src.config import get_settings
from src.core.interfaces import DeviceSelector
from src.domain.enums import device_types_for_domain
from src.iot.registry import DEVICE_BY_SLUG
from src.nlu.exclusion_clause import negation_is_confined_to_exclusion, split_exclusion_clause
from src.nlu.model_client import ModelClientError
from src.nlu.normalizer import NormalizedUtterance, analyze
from src.nlu.ontology import UtteranceType
from src.nlu.prompting import SEMANTIC_GOAL_SYSTEM_PROMPT
from src.nlu.prompting.semantic import SEMANTIC_GOAL_PROMPT_VERSION
from src.nlu.prompts import build_reasoning_context
from src.nlu.understanding import mentioned_device_types

logger = logging.getLogger("agent.goal_author")

_HOME_ROOM_ACTIVITIES = frozenset({"sleep", "sleeping", "rest", "resting", "bedtime"})
_QUIET_ACTIVITIES = _HOME_ROOM_ACTIVITIES
_FOCUS_ACTIVITIES = frozenset({"working", "studying", "learning", "focusing", "focus"})
_MEDIA_DOMAINS = frozenset({"tv", "speaker"})
_IDENTITY_BOUND_LOCATION_SOURCES = frozenset({"capture_device", "ble", "client"})

# Declarative capability contracts for activity contexts.  These are not phrase
# scenes and contain no entity IDs: the model chooses the semantic activity, this
# gate only restores a required capability outcome it accidentally omitted, and
# Planning still grounds every selector against the live registry.
_ACTIVITY_OUTCOME_CONTRACTS: dict[str, tuple[dict[str, Any], ...]] = {
    "socializing": (
        {
            "match_domains": {"vacuum"},
            "selector_domain": "vacuum",
            "target_state": {"power": "on"},
            "cardinality": "one",
            "rationale": "Dọn sạch sàn trước khi không gian chung được sử dụng",
        },
        {
            "match_relative": ("temperature", "decrease"),
            "selector_domain": "air_conditioner",
            "relative_change": {"temperature": "decrease_slight"},
            "cardinality": "one",
            "rationale": "Giữ nhiệt độ mát dễ chịu trong không gian chung",
        },
        {
            "match_domains": {"air_purifier", "air_quality", "air_flow", "fan"},
            "selector_domain": "air_purifier",
            "target_state": {"power": "on"},
            "cardinality": "one",
            "rationale": "Giữ không khí sạch và thông thoáng",
        },
        {
            "match_domains": {"media_player", "tv", "speaker", "shared_entertainment"},
            "match_labels": {"shared_entertainment"},
            "selector_domain": "media_player",
            "selector_labels": ("shared_entertainment",),
            "target_state": {"power": "on"},
            "cardinality": "any",
            "rationale": "Chuẩn bị một nguồn giải trí dùng chung",
        },
    ),
    # "đi tắm": chỉ cần nước nóng. Bình nóng lạnh là thiết bị đơn nhất và không gắn với phòng
    # người nói, nên requirement bỏ qua area — Planning tự tìm bản duy nhất trong registry.
    "bathing": (
        {
            "match_domains": {"water_heater"},
            "selector_domain": "water_heater",
            "target_state": {"power": "on"},
            "cardinality": "one",
            "ignore_area": True,
            "rationale": "Làm nóng nước trước khi tắm",
        },
    ),
}


def _outcome_satisfies_contract(outcome: DesiredOutcome, requirement: dict[str, Any]) -> bool:
    domain = str(outcome.selector.domain or "").strip().lower()
    labels = {str(label).strip().lower() for label in (outcome.selector.labels or [])}
    selector_matches = False
    if domain and domain in requirement.get("match_domains", set()):
        selector_matches = True
    if labels & requirement.get("match_labels", set()):
        selector_matches = True
    relative_match = requirement.get("match_relative")
    if relative_match:
        capability, direction = relative_match
        actual = str((outcome.relative_change or {}).get(capability, "")).lower()
        selector_matches = selector_matches or actual.startswith(direction)
    if not selector_matches:
        return False

    required_state = requirement.get("target_state", {})
    return all((outcome.target_state or {}).get(key) == value for key, value in required_state.items())


# Số điều kiện tối đa mà contract được phép TỰ VIẾT THÊM. Một là ranh giới giữa VÁ và DỰNG:
# thiếu đúng một điều kiện là model sơ suất trong một nếp nó đã hình dung đủ; thiếu nhiều hơn
# nghĩa là model đang diễn đạt một mục tiêu KHÁC — hẹp hơn — và contract sẽ ghi đè mục tiêu
# đó bằng một nếp không ai yêu cầu.
_MAX_REPAIRABLE_OMISSIONS = 1


def _complete_activity_contract(goal: SemanticGoal) -> SemanticGoal:
    """Restore omitted capability outcomes from a semantic activity contract."""
    activity = (goal.activity_context or "").strip().lower().replace("-", "_")
    requirements = _ACTIVITY_OUTCOME_CONTRACTS.get(activity)
    if not requirements or goal.polarity == "negative" or goal.is_cancellation:
        return goal

    outcomes = list(goal.desired_outcomes)
    missing = [
        requirement
        for requirement in requirements
        if not any(_outcome_satisfies_contract(outcome, requirement) for outcome in outcomes)
    ]

    # Cổng VÁ-CHỨ-KHÔNG-DỰNG. Nhãn `activity_context` không phân biệt được hai loại câu: model
    # gán "socializing" cho cả "tối nay có bạn tới chơi" (nếp phủ không gian) lẫn "dọn nhà
    # giúp tôi" / "nghe nhạc ở phòng khách" / "ăn tối thôi" (một việc cụ thể). Bằng chứng phân
    # biệt nằm ở SỐ điều kiện còn thiếu: một nếp mà model đã hình dung đủ thì cùng lắm rơi một
    # mục; một việc cụ thể thì trượt gần hết checklist.
    #
    # Đo live 2026-08-31, cùng model, temperature=0, trung vị 3 lượt:
    #   • SH-126 "dọn nhà giúp tôi"      — model soạn ĐÚNG một outcome vacuum, khớp y hệt
    #     reference; contract thêm điều hoà + máy lọc + TV (3 thiếu → dựng).
    #   • SH-143 "nghe nhạc ở phòng khách" — model soạn đúng loa; contract thêm điều hoà +
    #     máy lọc + robot hút bụi (3 thiếu → dựng).
    #   • SH-127 "ăn tối thôi"            — 4 outcome thành 8 (4 thiếu → dựng).
    #   • SH-147 "chuẩn bị tiếp khách"    — chỉ thiếu điều hoà (1 thiếu → vá, giữ nguyên).
    # Đây là luật ngữ nghĩa chung trên chính hình dạng mục tiêu, không so khớp câu chữ hay
    # case id, và cũng chính là điều docstring của contract vẫn luôn tuyên bố: vá MỘT thiếu sót.
    if len(missing) > _MAX_REPAIRABLE_OMISSIONS:
        return goal

    for requirement in missing:
        outcomes.append(
            DesiredOutcome(
                selector=DeviceSelector(
                    area=None if requirement.get("ignore_area") else goal.target_area,
                    domain=requirement["selector_domain"],
                    labels=list(requirement.get("selector_labels", ())),
                ),
                target_state=dict(requirement.get("target_state", {})),
                relative_change=dict(requirement.get("relative_change", {})),
                cardinality=requirement["cardinality"],
                rationale=requirement["rationale"],
            )
        )
    return goal.model_copy(update={"desired_outcomes": outcomes})


def _gen(model: Any, prompt: str, schema: Any, system_prompt: str, context: dict[str, Any], temp: float) -> Any:
    """Gọi model structured — sync nếu có, else chạy coroutine (khớp graph cũ)."""
    if hasattr(model, "structured_generate_sync"):
        return model.structured_generate_sync(prompt, schema, system_prompt=system_prompt, context=context, temperature=temp)
    import asyncio

    return asyncio.run(model.structured_generate(prompt, schema, system_prompt=system_prompt, context=context, temperature=temp))


def _activity_prefers_home_room(goal: SemanticGoal) -> bool:
    activity = (goal.activity_context or "").strip().lower().replace("-", "_")
    return activity in _HOME_ROOM_ACTIVITIES or activity.startswith("sleep_")


def _sanitize_inferred_outcomes(goal: SemanticGoal) -> list[Any]:
    """Relative comfort must stay relative when the user did not issue an explicit command.

    Models occasionally emit both ``brightness: decrease`` and an invented absolute
    ``brightness: 0``/``power: off``.  The two representations conflict; specialists
    must ground the relative request from live state instead of treating it as "turn off".
    """
    sanitized = []
    activity = (goal.activity_context or "").strip().lower().replace("-", "_")
    quiet_activity = activity in _QUIET_ACTIVITIES or activity.startswith(("sleep_", "rest_"))
    for outcome in goal.desired_outcomes:
        target = dict(outcome.target_state)
        relative = dict(outcome.relative_change)

        if relative:
            for capability in relative:
                target.pop(capability, None)
            if str(target.get("power", "")).lower() == "off":
                target.pop("power", None)

        # Hard activity invariant: an inferred quiet/rest context cannot activate
        # a screen or audio source. Explicit device commands bypass this authoring
        # path, so an actual "bật nhạc khi tôi nghỉ" remains authoritative.
        domain = (outcome.selector.domain or "").strip().lower()
        if quiet_activity and domain in _MEDIA_DOMAINS:
            desired_power = str(target.get("power", "")).lower()
            if desired_power == "on":
                target.pop("power", None)
            target.pop("volume", None)
            quieter = str(relative.get("volume", "")).startswith("decrease")
            explicitly_off = desired_power == "off"
            if not quieter and not explicitly_off:
                continue

        sanitized.append(outcome.model_copy(update={"target_state": target}))
    return sanitized


def _repair_activity_outcomes(
    goal: SemanticGoal,
    outcomes: list[DesiredOutcome],
    *,
    area: str | None,
    preserved_ids: set[str],
) -> list[DesiredOutcome]:
    """Repair structurally incomplete activity outcomes without choosing device IDs."""
    activity = (goal.activity_context or "").strip().lower().replace("-", "_")
    quiet_activity = activity in _QUIET_ACTIVITIES or activity.startswith(("sleep_", "rest_"))
    focus_activity = activity in _FOCUS_ACTIVITIES or activity.startswith(("work_", "study_"))

    def blocked(outcome: DesiredOutcome) -> bool:
        domain = outcome.selector.domain
        room = outcome.selector.area or area
        types = device_types_for_domain(domain)
        labels = {str(label).lower() for label in (outcome.selector.labels or [])}
        candidates = [
            spec.slug
            for spec in DEVICE_BY_SLUG.values()
            if (not room or spec.room == room)
            and spec.device_type.value in types
            and (not labels or bool(labels & {str(role).lower() for role in spec.semantic_roles}))
        ]
        return bool(candidates) and all(slug in preserved_ids for slug in candidates)

    # Small models occasionally put an action token in ``power``.  Normalize the
    # capability shape; Planning still resolves the concrete lock through registry.
    repaired: list[DesiredOutcome] = []
    for outcome in outcomes:
        power = str((outcome.target_state or {}).get("power", "")).lower()
        if power in {"lock", "unlock"}:
            target = dict(outcome.target_state)
            target.pop("power", None)
            target["locked"] = power == "lock"
            repaired.append(
                outcome.model_copy(
                    update={
                        "selector": outcome.selector.model_copy(
                            update={"domain": "door_lock", "labels": []}
                        ),
                        "target_state": target,
                    }
                )
            )
        else:
            repaired.append(outcome)

    # A preservation clause must remove the matching inferred outcome.  Keep a
    # single blocked outcome untouched so a constraint-only request still reaches
    # the existing fail-closed path instead of being silently erased.
    if preserved_ids and (quiet_activity or len(repaired) > 1):
        repaired = [outcome for outcome in repaired if not blocked(outcome)]

    def task_light(*, power: str | None = None) -> DesiredOutcome:
        return DesiredOutcome(
            selector=DeviceSelector(
                area=area,
                domain="light",
                labels=["work_or_study_light"],
            ),
            target_state={"power": power} if power else {},
            relative_change={} if power else {"brightness": "increase_slight"},
            perceived_state="" if power else "too_dark",
            cardinality="one",
            rationale="Ánh sáng tác vụ phù hợp với hoạt động",
        )

    def media_off() -> DesiredOutcome:
        return DesiredOutcome(
            selector=DeviceSelector(
                area=area,
                domain="media_player",
                labels=["shared_entertainment"],
            ),
            target_state={"power": "off"},
            cardinality="all",
            rationale="Không chủ động bật nguồn gây xao nhãng",
        )

    if focus_activity:
        light = next((outcome for outcome in repaired if outcome.selector.domain == "light"), None)
        canonical_light = task_light()
        if light is not None:
            canonical_light = light.model_copy(
                update={
                    "selector": light.selector.model_copy(
                        update={
                            "area": area,
                            "domain": "light",
                            "labels": ["work_or_study_light"],
                        }
                    ),
                    "target_state": {},
                    "perceived_state": "too_dark",
                    "relative_change": {"brightness": "increase_slight"},
                    "cardinality": "one",
                }
            )
        media_was_present = any(
            str(outcome.selector.domain or "").lower() in _MEDIA_DOMAINS | {"media_player"}
            for outcome in repaired
        )
        repaired = [] if blocked(canonical_light) else [canonical_light]
        if media_was_present:
            repaired.append(media_off())

    elif quiet_activity:
        # Sleep/rest never activates a screen.  Reading is the exception for
        # lighting direction: preserve an authored positive reading light instead
        # of replacing it with the usual task-light shutdown.
        positive_light = any(
            outcome.selector.domain == "light"
            and any(str(value).startswith("increase") for value in outcome.relative_change.values())
            for outcome in repaired
        )
        safe_media_off = [
            outcome
            for outcome in repaired
            if str(outcome.selector.domain or "").lower() in _MEDIA_DOMAINS | {"media_player"}
            and str(outcome.target_state.get("power", "")).lower() == "off"
        ]
        repaired = [
            outcome
            for outcome in repaired
            if str(outcome.selector.domain or "").lower() not in _MEDIA_DOMAINS | {"media_player"}
        ]
        if not positive_light:
            off = task_light(power="off")
            if not blocked(off):
                repaired.append(off)
        repaired.extend(safe_media_off or [media_off()])

    # If the model proposed a concrete audio/display family as well as a generic
    # media alternative, the concrete family is stronger semantic evidence.  Keep
    # it and make an upward media adjustment operational by ensuring power is on.
    specific_media = {
        str(outcome.selector.domain or "").lower()
        for outcome in repaired
        if str(outcome.selector.domain or "").lower() in _MEDIA_DOMAINS
    }
    if specific_media:
        repaired = [
            outcome
            for outcome in repaired
            if str(outcome.selector.domain or "").lower() != "media_player"
        ]
        repaired = [
            outcome.model_copy(
                update={"target_state": {**outcome.target_state, "power": "on"}}
            )
            if str(outcome.selector.domain or "").lower() in specific_media
            and any(str(value).startswith("increase") for value in outcome.relative_change.values())
            and str(outcome.target_state.get("power", "")).lower() != "off"
            else outcome
            for outcome in repaired
        ]

    description = f"{goal.goal_description} {goal.intent}".lower()
    energy_saving = "tiết kiệm điện" in description or "energy sav" in description
    if energy_saving:
        repaired = [
            outcome.model_copy(update={"target_state": {"power": "off"}, "cardinality": "all"})
            if not outcome.target_state
            and not outcome.relative_change
            and outcome.perceived_state.strip().lower() == "on"
            else outcome
            for outcome in repaired
        ]
    return repaired


def _finalize(llm_goal: SemanticGoal, *, nu: NormalizedUtterance, ctx: RuntimeContext, utt: str) -> SemanticGoal:
    """Ép bất biến tất định lên goal do LLM author (port từ graph cũ `_finalize_llm_goal`)."""
    # This function is only called for open-ended authoring. Location and device
    # scope must therefore come from deterministic evidence, never from fields the
    # model volunteered despite the semantic-only prompt.
    explicit_area = (
        nu.matched_rooms[0]
        if nu.room_explicit and nu.matched_rooms and nu.matched_rooms[0] in ctx.rooms
        else None
    )
    trusted_capture_area = (
        ctx.speaker_location
        if ctx.speaker_location in ctx.rooms
        and (
            not ctx.speaker_location_source
            or ctx.speaker_location_source in _IDENTITY_BOUND_LOCATION_SOURCES
        )
        else None
    )
    activity_home_area = (
        ctx.speaker_home_room
        if _activity_prefers_home_room(llm_goal)
        and ctx.speaker_home_room
        and ctx.speaker_home_room in ctx.rooms
        else None
    )
    anonymous_presence_area = (
        ctx.speaker_location if ctx.speaker_location in ctx.rooms else None
    )
    focus_area = ctx.focus_room if ctx.focus_room in ctx.rooms else None
    home_fallback = (
        ctx.speaker_home_room
        if ctx.speaker_home_room and ctx.speaker_home_room in ctx.rooms
        else None
    )
    area = explicit_area or trusted_capture_area or activity_home_area or anonymous_presence_area or focus_area or home_fallback
    if explicit_area:
        area_source = "explicit_utterance"
    elif trusted_capture_area:
        area_source = "speaker_capture"
    elif activity_home_area:
        area_source = "activity_home_room"
    elif anonymous_presence_area:
        area_source = "anonymous_presence"
    elif focus_area:
        area_source = "focus_room"
    elif home_fallback:
        area_source = "profile_home_room"
    else:
        area_source = None
    references_resolved = not nu.has_reference
    utype = llm_goal.utterance_type
    if utype in {UtteranceType.UNKNOWN, UtteranceType.SOCIAL_UTTERANCE} and llm_goal.desired_outcomes:
        utype = UtteranceType.ENVIRONMENT_REQUEST
    split = split_exclusion_clause(nu.raw)
    clause_ids: list[str] = []
    preserve_clause = False
    if split is not None:
        exclusion_nu = analyze(
            split.exclusion_clause,
            focus_room=area,
            speaker_home_room=ctx.speaker_home_room,
            speaker_private_room=ctx.speaker_private_room,
        )
        clause_ids = [d for d in exclusion_nu.matched_device_ids if d in DEVICE_BY_SLUG]
        if not clause_ids and area and area_source == "speaker_capture":
            device_types = set(mentioned_device_types(exclusion_nu))
            candidates = [
                spec.slug
                for spec in DEVICE_BY_SLUG.values()
                if spec.room == area and spec.device_type in device_types
            ]
            if len(candidates) == 1:
                clause_ids = candidates
        folded_clause = split.exclusion_clause.lower()
        preserve_clause = any(cue in folded_clause for cue in ("giữ nguyên", "đừng đụng", "không đụng"))
    excluded_ids = list(
        dict.fromkeys(
            ([d for d in llm_goal.excluded_device_ids if d in DEVICE_BY_SLUG] if nu.has_negation else [])
            + ([] if preserve_clause else clause_ids)
        )
    )
    no_change_ids = list(
        dict.fromkeys(
            ([d for d in llm_goal.no_change_device_ids if d in DEVICE_BY_SLUG] if nu.has_negation else [])
            + (clause_ids if preserve_clause else [])
        )
    )
    # Phủ định nằm GỌN trong mệnh đề cấm ("... nhưng đừng tắt điều hoà") không biến cả mục tiêu
    # thành phủ định: mệnh đề chính vẫn là mục tiêu DƯƠNG. Dùng cờ has_negation TOÀN CÂU khiến
    # mệnh đề cấm nuốt mục tiêu chính, agent chỉ đáp "mình sẽ không ..." rồi dừng.
    #
    # NHƯNG chỉ nhả khi lệnh cấm ĐÃ thành ràng buộc cụ thể: `polarity="negative"` còn đang làm
    # PHANH AN TOÀN cho trường hợp có lệnh cấm mà không phân giải được thiết bị bị cấm. Nhả
    # phanh lúc excluded_device_ids rỗng thì plan sẽ chạm đúng thiết bị vừa bị cấm. Fail-closed:
    # không có ràng buộc thay thế thì giữ nguyên phủ định.
    polarity = (
        "negative"
        if nu.has_negation
        and not (
            negation_is_confined_to_exclusion(nu.raw)
            and (excluded_ids or no_change_ids)
        )
        and not ("không có ai" in nu.normalized.lower() or "không ai" in nu.normalized.lower())
        else "affirmative"
    )
    outcomes = _repair_activity_outcomes(
        llm_goal,
        _sanitize_inferred_outcomes(llm_goal),
        area=area,
        preserved_ids=set(excluded_ids) | set(no_change_ids),
    )
    finalized = llm_goal.model_copy(
        update={
            "raw_utterance": utt,
            "utterance_type": utype,
            "target_device_ids": [],
            "target_area": area,
            "target_area_source": area_source,
            "desired_outcomes": outcomes,
            "polarity": polarity,
            "is_correction": llm_goal.is_correction or nu.has_correction,
            "is_cancellation": llm_goal.is_cancellation or nu.has_cancellation,
            "references_resolved": references_resolved,
            "action_hint": None,
            "target_actions": {},
            "target_parameters": {},
            "excluded_device_ids": excluded_ids,
            "no_change_device_ids": no_change_ids,
            "explicit_constraints": [],
            # LLM tự chọn target_device_ids ở đây (không qua alias/ledger tất định) — KHÔNG
            # được trao quyền hard-anchor ngang lệnh tường minh/continuation (xem manager.py).
            "target_devices_deterministic": False,
            "user_defined_routine": False,
            "routine_constraint_only": False,
            "routine_source_event_id": None,
        }
    )
    return _complete_activity_contract(finalized)


def author_goal(
    model: Any,
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    *,
    utterance: str,
    memory_evidence: Any = None,
    confirmed_so_far: dict[str, Any] | None = None,
    semantic_cache: Any = None,
) -> SemanticGoal | None:
    """LLM author một SemanticGoal open-ended cho câu mới lạ. None nếu model lỗi (abstain).

    Fail-closed (§graph cũ): lỗi suy luận ĐÃ BIẾT (ModelClientError) → None để tầng trên
    CLARIFY, KHÔNG bịa goal. Hậu xử lý `_finalize` NGOÀI try để bug lập trình lộ ra.

    `confirmed_so_far` (RECAP — "LLMs Get Lost in Multi-Turn Conversation" §7.1): tóm tắt
    NGẮN, TẤT ĐỊNH các sự kiện/ràng buộc đã chốt trong Ledger cuộc hội thoại NÀY (phòng,
    thiết bị, mục tiêu lượt trước). Chèn lại tường minh vào MỌI lượt tiếp nối thay vì bắt
    model tự dò trong `recent_dialogue` thô — model không "quên" state đã chốt khi hội thoại
    dài ra. `memory_evidence` là bằng chứng liên quan lấy từ EMem/HiGMem (dài hạn, khác phiên,
    xem `_fetch_memory_evidence`), tách khỏi Ledger phiên hiện tại để phân biệt rõ hai nguồn."""
    if model is None or not (utterance or "").strip():
        return None
    scope_room = (
        nu.matched_rooms[0] if nu.matched_rooms else (
            ctx.speaker_location or ctx.focus_room or ctx.speaker_home_room or "household"
        )
    )
    cacheable = bool(
        semantic_cache is not None
        and not memory_evidence
        and not confirmed_so_far
        and not nu.has_reference
        and not nu.has_correction
        and not nu.has_cancellation
        and not nu.has_negation
    )
    cache_material = f"{SEMANTIC_GOAL_PROMPT_VERSION}\0{nu.folded}\0{scope_room}"
    cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
    if cacheable:
        cached = semantic_cache.get(cache_key)
        if cached is not None:
            finalized = _finalize(cached, nu=nu, ctx=ctx, utt=utterance)
            return repair_environment_goal(finalized, nu=nu, ctx=ctx)
    signals = {
        "has_negation": nu.has_negation,
        "has_correction": nu.has_correction,
        "has_cancellation": nu.has_cancellation,
        "has_reference": nu.has_reference,
        "matched_device_ids": list(nu.matched_device_ids),
        "matched_rooms": list(nu.matched_rooms),
    }
    extra: dict[str, Any] = {"signals": signals}
    if memory_evidence:
        # Evidence retrieval is already ranked; a small top slice is enough for
        # authoring and prevents old household history from dominating latency.
        extra["memory_evidence"] = list(memory_evidence)[:4] if isinstance(memory_evidence, list | tuple) else memory_evidence
    if confirmed_so_far:
        extra["confirmed_so_far"] = confirmed_so_far
    scope_rooms = set(nu.matched_rooms)
    if not scope_rooms:
        inferred_room = ctx.speaker_location or ctx.focus_room or ctx.speaker_home_room
        if inferred_room in ctx.rooms:
            scope_rooms.add(inferred_room)
    context = build_reasoning_context(
        ctx,
        utterance=utterance,
        extra=extra,
        scope_rooms=scope_rooms or None,
        # Semantic authoring decides desired capabilities, not whether an already-on
        # device is a no-op. Specialists and validation always re-ground live later.
        include_device_states=False,
        include_sensors=True,
        max_sensor_items=6,
        max_recent_dialogue=4,
    )
    settings = get_settings()
    effort = settings.llm_planning_reasoning_effort
    if hasattr(model, "use_planning_profile"):
        effort_scope = model.use_planning_profile(
            settings.llm_planning_model,
            effort,
            settings.llm_planning_timeout_seconds,
        )
    elif hasattr(model, "use_reasoning_effort"):
        effort_scope = model.use_reasoning_effort(effort)
    else:
        effort_scope = nullcontext(model)
    try:
        with effort_scope:
            llm_goal: SemanticGoal = _gen(
                model, utterance, SemanticGoal, SEMANTIC_GOAL_SYSTEM_PROMPT, context, 0.1
            )
    except ModelClientError as e:
        logger.warning("LLM author_goal failed: %s", e)
        return None
    if cacheable and llm_goal.desired_outcomes:
        semantic_cache.put(cache_key, llm_goal)
    finalized = _finalize(llm_goal, nu=nu, ctx=ctx, utt=utterance)
    return repair_environment_goal(finalized, nu=nu, ctx=ctx)
