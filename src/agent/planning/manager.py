"""Manager Agent (spec §35) — Semantic Goal → device-domain subgoals.

Manager KHÔNG sinh direct API call (spec §35): nó chỉ chia mục tiêu ngữ nghĩa thành
các *subgoal* theo chiều tiện nghi (illumination/temperature/media/...) hoặc theo lệnh
tường minh, rồi để từng Specialist đề xuất thiết bị cụ thể (§36-37).

Phân rã TẤT ĐỊNH từ desired_outcomes có cấu trúc (LLM đã author ở Layer 2) — "LLM
proposes semantic, code decides how" (spec §P1/§P2). KHÔNG map phrase → scene (§66).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.schemas import RuntimeContext, SemanticGoal
from src.domain.action_registry import action_for_semantic
from src.domain.enums import ActionType, device_types_for_domain
from src.iot.registry import domain_instance_count, spec_for
from src.nlu.ontology import UtteranceType
from src.planning.device_grounder import ground_selector

# capability (value enum Capability) → chiều tiện nghi ngữ nghĩa mà specialist phục vụ.
_CAPABILITY_TO_DIMENSION = {
    "brightness": "illumination",
    "color_temp": "illumination",
    "temperature": "temperature",
    "hvac_mode": "temperature",
    "position": "openness",
    "volume": "media",
    "media_control": "media",
    "fan_speed": "air_flow",
    "preset_mode": "air_quality",
    # An ninh đi đường riêng của SecurityAgent, không phải một chiều tiện nghi.
    "lock": "security",
}

# perceived_state (nhãn cảm nhận tự do) → chiều tiện nghi. Chỉ vài neo phổ biến; đây
# KHÔNG phải closed ontology — specialist vẫn nhận cả subgoal theo capability trực tiếp.
_PERCEIVED_TO_DIMENSION = {
    "too_dark": "illumination",
    "dark": "illumination",
    "tối": "illumination",
    "too_bright": "illumination",
    "bright": "illumination",
    "chói": "illumination",
    "cold": "temperature",
    "lạnh": "temperature",
    "hot": "temperature",
    "nóng": "temperature",
    "warm": "temperature",
    "stuffy": "air_quality",
    "bí": "air_quality",
    "khó thở": "air_quality",
    "ngột": "air_quality",
    "bụi": "air_quality",
    "poor_air_quality": "air_quality",
    "humid": "temperature",
    "noisy": "media",
    "ồn": "media",
    "loud": "media",
}

# Device-domain in a semantic selector is still open-ended data from the model,
# but mapping a catalog type to the specialist that owns it is deterministic.
_DOMAIN_TO_DIMENSION = {
    "light": "illumination",
    "tv": "media",
    "speaker": "media",
    "vacuum": "cleaning",
    "air_purifier": "air_quality",
    "fan": "air_flow",
    "air_conditioner": "temperature",
    "heater": "temperature",
    "curtain": "openness",
    "window": "openness",
    # An ninh KHÔNG phải chiều tiện nghi: SecurityAgent chỉ nhận chiều này để có thể TỪ
    # CHỐI hoặc đề xuất KHOÁ có kiểm soát (§38), không bao giờ để tối ưu comfort/energy.
    "door_lock": "security",
    "camera": "security",
}

def _dimension_for_domain(domain: str | None) -> str | None:
    """Chiều tiện nghi cho một `selector.domain` do LLM soạn, qua từ vựng DÙNG CHUNG (§4).

    `_DOMAIN_TO_DIMENSION` chỉ biết đúng tên `DeviceType`. Model lại hay nói giọng Home
    Assistant ("media_player"), và trước đây tra thẳng bằng chuỗi nên trượt → subgoal rơi về
    `dimension="explicit"` mà KHÔNG có explicit_device_id → không specialist nào nhận → plan
    rỗng. Chuẩn hoá về tập DeviceType trước, rồi mới tra chiều.

    Trả None khi domain ngoài từ vựng, hoặc khi các device type trong họ thuộc HAI chiều khác
    nhau — lúc đó "không biết" là câu trả lời trung thực, người gọi tự chọn fallback.
    """
    dimensions = {
        _DOMAIN_TO_DIMENSION[device_type]
        for device_type in device_types_for_domain(domain)
        if device_type in _DOMAIN_TO_DIMENSION
    }
    return dimensions.pop() if len(dimensions) == 1 else None


# Explicit directional commands may name an AC without naming its numeric
# capability ("giảm điều hoà", ...).  Temperature is safe to route now because
# ACAgent has the preference-aware directional contract.  Brightness remains on
# the legacy explicit path until the low-level "decrease to zero" semantics are
# finalized; routing it prematurely changes a continuation into turn_off.
_DIRECTIONAL_NUMERIC_CAPABILITY_BY_DEVICE_TYPE = {
    "air_conditioner": "temperature",
    "heater": "temperature",
}


def _is_numeric_value(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True


def _directional_numeric_capability(
    action: str | None,
    target_state: dict,
    device_spec,
) -> str | None:
    """Return the specialist capability for a value-less directional command.

    A numeric target is authoritative and must stay on the explicit path.  If
    there is no numeric target, the primary numeric capability for the catalog
    device type is selected deterministically and verified against the action
    registry before the subgoal is handed to a specialist.
    """
    if action not in {ActionType.INCREASE.value, ActionType.DECREASE.value}:
        return None
    if any(_is_numeric_value(value) for value in target_state.values()):
        return None

    device_type = getattr(device_spec.device_type, "value", device_spec.device_type)
    capability = _DIRECTIONAL_NUMERIC_CAPABILITY_BY_DEVICE_TYPE.get(str(device_type))
    if capability is None or capability not in {str(cap) for cap in device_spec.capabilities}:
        return None
    return capability if action_for_semantic(capability, action) is not None else None


@dataclass(slots=True)
class Subgoal:
    """Một subgoal cấp device-domain (spec §35). Specialist đọc cái này để đề xuất."""

    dimension: str  # illumination | temperature | openness | media | air_quality | air_flow | explicit
    direction: str | None = None  # increase | decrease | None
    # Capability CỤ THỂ mà goal nêu, khi có (brightness vs color_temp cùng thuộc chiều
    # `illumination`). Specialist phục vụ một chiều vẫn phải biết người dùng nói về đại
    # lượng nào, nếu không "ánh sáng ấm hơn" bị ép thành chỉnh độ sáng. None = goal chỉ
    # nêu cảm nhận, specialist tự chọn đại lượng chính của chiều đó.
    capability: str | None = None
    room: str | None = None
    target_state: dict = field(default_factory=dict)  # giá trị tuyệt đối nếu user nêu rõ
    perceived_state: str = ""
    selector_domain: str | None = None
    # Operational outcomes (for example TV power=on vs speaker power=on) must
    # keep their catalog domain. Relative comfort outcomes may still be solved
    # by an alternative device (for example daylight via curtain vs light).
    selector_is_strict: bool = False
    # Lệnh tường minh: chỉ định thẳng thiết bị + action.
    explicit_device_id: str | None = None
    explicit_action: str | None = None
    # Neo thiết bị cho đường open-ended: khi goal đã ground một tập thiết bị CỤ THỂ (vd continuation
    # kế thừa từ ledger) và outcome KHÔNG phải mục tiêu nhóm (cardinality != "all"), specialist chỉ
    # được đề xuất trong tập này thay vì mọi thiết bị cùng loại/phòng (spec: target_device_ids ground
    # là authoritative trừ khi goal tường minh xin hành động đa thiết bị/nhóm).
    anchor_device_ids: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""


def _is_house_wide_shutdown(outcome, goal: SemanticGoal) -> bool:
    """Outcome này là bước TẮT toàn nhà của một nếp sinh hoạt (rời đi / đi ngủ)?

    Điều kiện đồng thời: nếp sinh hoạt suy diễn (ROUTINE_INTENT), outcome nhắm CẢ NHÓM
    (`cardinality='all'`), KHÔNG nêu phòng nào, và trạng thái đích là tắt.

    "Cả nhà chuẩn bị ra ngoài" từng chỉ tắt thiết bị Phòng khách vì selector không nêu
    phòng nên rơi về `focus_room` (chỗ người nói đứng) — đèn/điều hoà các phòng ngủ bị bỏ
    sót. Chỉ nới cho hướng TẮT: tắt thừa một thiết bị đã tắt là vô hại và có thể hoàn tác,
    còn BẬT thừa ở phòng người khác thì không — nên routine bật ("đón khách", "xem phim")
    vẫn giới hạn theo phòng sinh hoạt. Luật theo cấu trúc outcome, không theo câu chữ.
    """
    if goal.utterance_type != UtteranceType.ROUTINE_INTENT:
        return False
    if outcome.cardinality != "all" or getattr(outcome.selector, "area", None):
        return False
    return str((outcome.target_state or {}).get("power", "")).lower() == "off"


def _domain_has_single_instance(domain: str | None) -> bool:
    """Domain của outcome chỉ có ĐÚNG MỘT thiết bị trong nhà? (goldenset AD-007: "chỉ một máy sưởi").

    "Chỉ có một cái" nghĩa là KHÔNG có gì để phân giải theo phòng — dùng để không ghim một thiết
    bị vận hành đơn nhất (bình nóng lạnh cho nếp "đi tắm") về phòng người nói và làm outcome rơi
    mất. Chung khái niệm với Understanding qua `registry.domain_instance_count` (§4 một nguồn sự thật).
    """
    return domain_instance_count(domain) == 1


def _room_of_selector(
    outcome,
    ctx: RuntimeContext,
    goal_area: str | None = None,
    goal: SemanticGoal | None = None,
) -> str | None:
    # Bước tắt toàn nhà không được thu hẹp về phòng người nói: `target_area` của một nếp
    # sinh hoạt chỉ ghi nơi người nói đứng, không phải giới hạn phạm vi.
    if goal is not None and _is_house_wide_shutdown(outcome, goal):
        return None
    # Thiết bị vận hành ĐƠN NHẤT (đúng một bản trong registry) không có "phòng của người nói":
    # nếp "đi tắm" cần bật bình nóng lạnh — chỉ có một cái, ghim nó về phòng người đứng sẽ khiến
    # bộ lọc phòng của specialist loại sạch ứng viên và outcome rơi mất. Chỉ nới cho outcome
    # nêu thẳng power (on/off) và KHÔNG nhắm cả nhóm; selector nêu phòng rõ thì vẫn tôn trọng.
    if (
        goal is not None
        and goal.utterance_type == UtteranceType.ROUTINE_INTENT
        and outcome.cardinality != "all"
        and not getattr(outcome.selector, "area", None)
        and str((outcome.target_state or {}).get("power", "")).lower() in {"on", "off"}
        and _domain_has_single_instance(getattr(outcome.selector, "domain", None))
    ):
        return None
    # target_area đã qua Semantic Resolver/context evidence nên có ưu tiên cao hơn
    # selector.area do model đề xuất. Điều này ngăn inferred-location bị ground
    # ngược về phòng model đoán.
    #
    # NGOẠI LỆ: khi chính goal liệt kê từ HAI phòng hợp lệ trở lên, một `target_area` duy nhất
    # KHÔNG THỂ đúng cho mọi outcome — nó chỉ ghi được nơi người nói đứng. Đè lên lúc đó là
    # dồn mọi outcome về một phòng ("tiết kiệm điện ở các phòng ngủ" từng bị kéo hết về Phòng
    # khách rồi ra plan rỗng). Phạm vi đa phòng là bằng chứng tất định, không phải model đoán.
    room = getattr(outcome.selector, "area", None)
    if goal_area and goal_area in ctx.rooms and not _goal_spans_multiple_rooms(goal, ctx):
        return goal_area
    if room and room in ctx.rooms:
        return room
    if goal_area and goal_area in ctx.rooms:
        return goal_area
    return ctx.focus_room if ctx.focus_room in ctx.rooms else None


def _goal_spans_multiple_rooms(goal: SemanticGoal | None, ctx: RuntimeContext) -> bool:
    """Các outcome của goal có nêu đích danh từ hai phòng hợp lệ trở lên không?"""
    if goal is None:
        return False
    rooms = {
        area
        for outcome in (goal.desired_outcomes or [])
        if (area := getattr(outcome.selector, "area", None)) and area in ctx.rooms
    }
    return len(rooms) >= 2


def _selector_is_strict(
    outcome,
    selector_domain: str | None,
) -> bool:
    """Giữ đúng domain khi outcome yêu cầu thao tác vận hành hoặc toàn bộ nhóm.

    ``cardinality='all'`` nghĩa là mọi thiết bị khớp selector (ví dụ mọi đèn),
    không phải chọn một domain thay thế tiết kiệm điện hơn (ví dụ rèm).
    """
    if not selector_domain:
        return False
    # Giá trị tuyệt đối là yêu cầu vận hành trên đúng loại thiết bị model đã nêu:
    # curtain.position=closed không được lan sang window; speaker.power=off không
    # được lan sang TV. Chỉ relative comfort mới được phép có phương án thay thế.
    if outcome.target_state:
        return True
    # Cardinality is semantic intent, not a function of today's registry count.
    # ``all lights`` remains strict even if the room currently has one light;
    # otherwise adding/removing a device silently changes which domain may act.
    return outcome.cardinality == "all"


def _ground_labeled_selector(
    outcome,
    ctx: RuntimeContext,
    *,
    room: str | None,
) -> tuple[tuple[str, ...], str | None]:
    """Resolve a role-labelled alternative without encoding an activity or device slug.

    ``cardinality='any'`` means one matching catalog device is sufficient.  The LLM
    describes that interchangeable set through semantic-role labels; deterministic
    grounding chooses an online member from the real registry and Specialists still
    decide the concrete action.  Unlabelled selectors keep their existing domain-wide
    behavior so this does not silently change unrelated goals.
    """
    labels = list(getattr(outcome.selector, "labels", None) or [])
    cardinality = getattr(outcome, "cardinality", "any")
    if not labels or cardinality not in {"any", "one"}:
        return (), None

    selector = outcome.selector.model_dump()
    if room and not selector.get("area"):
        selector["area"] = room
    matched, _warnings = ground_selector(
        selector,
        speaker_location=ctx.speaker_location,
    )
    # Small models sometimes copy a semantic role into both ``domain`` and
    # ``labels``.  A role is not a device type, so the strict intersection is
    # empty even though the label is valid registry evidence.  Retry by role only
    # in that exact shape; keep area/cardinality and never relax an unrelated
    # conflicting domain.
    normalized_domain = str(selector.get("domain") or "").lower()
    normalized_labels = {str(label).lower() for label in labels}
    if not matched and normalized_domain in normalized_labels:
        role_selector = {**selector, "domain": None}
        matched, _warnings = ground_selector(
            role_selector,
            speaker_location=ctx.speaker_location,
        )
    # Nhãn vai trò là bằng chứng THÊM, không phải điều kiện cần: registry gán vai trò không
    # đồng đều (loa phòng khách có "shared_entertainment", loa bếp không) nên một nhãn hợp lý
    # vẫn lọc sạch thiết bị CÓ THẬT và mục tiêu rơi về plan rỗng. Thử lại bỏ nhãn, nhưng CHỈ
    # nhận khi loại thiết bị + phòng để lại ĐÚNG MỘT ứng viên — lúc đó không còn gì để đoán.
    # Nhiều hơn một thì giữ nguyên rỗng: nới ra sẽ thành chọn bừa theo thứ tự registry.
    if not matched:
        typed_selector = {**selector, "labels": []}
        typed_matched, _warnings = ground_selector(
            typed_selector,
            speaker_location=ctx.speaker_location,
        )
        if len(typed_matched) == 1:
            matched = typed_matched

    available = []
    for spec in matched:
        device = ctx.device(spec.slug)
        if device is not None and device.online:
            available.append((spec, device))
    if not available or (cardinality == "one" and len(available) != 1):
        return (), None

    target_power = str((outcome.target_state or {}).get("power", "")).lower()
    already_satisfying = [
        candidate
        for candidate in available
        if target_power and str(candidate[1].state.get("power", "")).lower() == target_power
    ]
    chosen, _device = (already_satisfying or available)[0]
    return (chosen.slug,), chosen.device_type.value


def build_subgoals(goal: SemanticGoal, ctx: RuntimeContext) -> list[Subgoal]:
    """Chia SemanticGoal thành subgoals (spec §35). Lệnh tường minh → subgoal explicit."""
    # Đường tường minh: người dùng nêu rõ thiết bị + động từ.
    if goal.action_hint is not None and goal.target_device_ids:
        # target_actions (nếu có) ghi đè action_hint THEO TỪNG THIẾT BỊ — câu ghép nêu ≥2 hành
        # động khác nhau cho ≥2 đích khác nhau ("bật đèn, còn bình nóng lạnh thì tắt") đã được
        # Understanding tách độc lập; action_hint chung chỉ còn là fallback cho slug không có
        # trong map (không nên xảy ra khi target_actions đã phủ hết target_device_ids).
        per_device_actions = getattr(goal, "target_actions", None) or {}
        # Cùng lẽ đó, GIÁ TRỊ cũng theo từng thiết bị: mỗi mệnh đề của câu ghép mang con số
        # riêng ("mở rèm bếp, sau đó chỉnh đèn bàn ăn xuống 50%"). Dùng chung `parameters` toàn
        # câu sẽ hoặc rỗng (mệnh đề `set` rớt ở validator vì thiếu params) hoặc rải nhầm giá trị
        # của mệnh đề này sang thiết bị của mệnh đề kia.
        per_device_params = getattr(goal, "target_parameters", None) or {}
        subs: list[Subgoal] = []
        for slug in goal.target_device_ids:
            spec = spec_for(slug)
            if spec is None:
                continue
            target_dict = dict(
                per_device_params.get(slug)
                or getattr(goal, "parameters", {})
                or getattr(goal, "target_state", {})
                or getattr(goal, "action_params", {})
                or {}
            )
            action = per_device_actions.get(slug, goal.action_hint)
            directional_capability = _directional_numeric_capability(action, target_dict, spec)
            if directional_capability is not None:
                # Direction without a numeric target is a comfort constraint, not
                # an executable empty proposal.  Let the owning specialist ground
                # it from live state, preference, and profile evidence.  The exact
                # device remains anchored, while an explicit numeric target (e.g.
                # 25°C) stays on the explicit path above this branch.
                subs.append(
                    Subgoal(
                        dimension=_CAPABILITY_TO_DIMENSION[directional_capability],
                        direction=action,
                        capability=directional_capability,
                        room=spec.room,
                        target_state=target_dict,
                        selector_domain=getattr(spec.device_type, "value", spec.device_type),
                        selector_is_strict=True,
                        anchor_device_ids=(slug,),
                        rationale=goal.goal_description or goal.raw_utterance,
                    )
                )
                continue
            subs.append(
                Subgoal(
                    dimension="explicit",
                    room=spec.room,
                    explicit_device_id=slug,
                    explicit_action=action,
                    target_state=target_dict,
                    rationale=goal.goal_description or goal.raw_utterance,
                )
            )
        if subs:
            return subs

    # Đường open-ended: mỗi desired_outcome → subgoal theo capability/perceived_state.
    subs = []
    for outcome in goal.desired_outcomes:
        selector_domain = getattr(outcome.selector, "domain", None)
        # A user-defined routine stores exact device outcomes as EDUs.  Keep the
        # normal specialist/validator path by translating each exact outcome to an
        # explicit subgoal; memory never replays a bus/API command directly.
        exact_device = getattr(outcome.selector, "device_id", None)
        exact_spec = spec_for(exact_device) if exact_device else None
        relative_exact_anchor: tuple[str, ...] = ()
        if exact_device and exact_spec is not None and not outcome.relative_change:
            state = dict(outcome.target_state or {})
            if state.get("power") == "on":
                action = "turn_on"
            elif state.get("power") == "off":
                action = "turn_off"
            elif state.get("locked") is True:
                action = "lock"
            elif state.get("locked") is False:
                action = "unlock"
            elif state.get("position") == 0:
                action = "close"
            elif state.get("position") == 100 and len(state) == 1:
                action = "open"
            else:
                action = "set"
            subs.append(
                Subgoal(
                    dimension="explicit",
                    room=exact_spec.room,
                    explicit_device_id=exact_device,
                    explicit_action=action,
                    target_state=state,
                    rationale=outcome.rationale,
                )
            )
            continue
        if exact_device and exact_spec is not None:
            # A concrete selector may still express a relative comfort change.
            # Keep the validated registry device as an anchor, then let the
            # capability Specialist translate the relative token against live
            # state.  The absolute-action fast path above cannot represent it.
            relative_exact_anchor = (exact_device,)
            selector_domain = exact_spec.device_type.value
        room = _room_of_selector(outcome, ctx, goal.target_area, goal)
        selector_anchor, grounded_domain = _ground_labeled_selector(outcome, ctx, room=room)
        if grounded_domain is not None:
            selector_domain = grounded_domain
        # Anchor CHỈ áp khi goal đã ground thiết bị cụ thể VÀ outcome này không phải mục tiêu
        # nhóm ("all" = mọi thiết bị khớp phải touch — không được thu hẹp về target_device_ids)
        # VÀ việc ground đó là TẤT ĐỊNH (không phải LLM tự đoán qua author_goal — một đoán của
        # LLM không được quyền hất một anchor tất định đã chốt ở lượt trước; nó chỉ đủ tư cách
        # là MỘT ứng viên trong toàn bộ domain+phòng, không phải bộ lọc loại trừ).
        anchor = selector_anchor or relative_exact_anchor or (
            tuple(goal.target_device_ids)
            if goal.target_device_ids and outcome.cardinality != "all" and goal.target_devices_deterministic
            else ()
        )
        made = False
        for cap, token in (outcome.relative_change or {}).items():
            dim = _CAPABILITY_TO_DIMENSION.get(cap, "explicit")
            direction = "increase" if str(token).startswith("increase") else (
                "decrease" if str(token).startswith("decrease") else None
            )
            subs.append(
                Subgoal(
                    dimension=dim,
                    direction=direction,
                    capability=cap,
                    room=room,
                    target_state=dict(outcome.target_state or {}),
                    perceived_state=outcome.perceived_state,
                    selector_domain=selector_domain,
                    selector_is_strict=_selector_is_strict(outcome, selector_domain),
                    anchor_device_ids=anchor,
                    rationale=outcome.rationale,
                )
            )
            made = True
        if not made:
            # Không có relative_change: ưu tiên GIÁ TRỊ TUYỆT ĐỐI (target_state) — suy dimension
            # từ chính capability được nêu (vd sửa "25 độ" → temperature), để specialist phục vụ
            # dimension đó nhận (nếu không thì subgoal 'explicit' không ai đề xuất → no_action).
            # Không có target_state có capability → rơi về perceived_state (than phiền cảm nhận).
            ts = dict(outcome.target_state or {})
            ts_dims = [(_CAPABILITY_TO_DIMENSION[cap], cap) for cap in ts if cap in _CAPABILITY_TO_DIMENSION]
            power_dimension = _dimension_for_domain(selector_domain)
            if "power" in ts and power_dimension is not None:
                ts_dims.append((power_dimension, "power"))
            if ts_dims:
                caps_by_dim: dict[str, str] = {}
                for dim, cap in ts_dims:
                    caps_by_dim.setdefault(dim, cap)
                for dim, cap in caps_by_dim.items():
                    subs.append(
                        Subgoal(
                            dimension=dim, direction=None,
                            capability=cap if cap in _CAPABILITY_TO_DIMENSION else None,
                            room=room, target_state=ts,
                            perceived_state=outcome.perceived_state, selector_domain=selector_domain,
                            selector_is_strict=_selector_is_strict(outcome, selector_domain),
                            anchor_device_ids=anchor,
                            rationale=outcome.rationale,
                        )
                    )
            else:
                dim = _PERCEIVED_TO_DIMENSION.get(
                    outcome.perceived_state or "",
                    _dimension_for_domain(selector_domain) or "explicit",
                )
                subs.append(
                    Subgoal(
                        dimension=dim, direction=None, room=room, target_state=ts,
                        perceived_state=outcome.perceived_state, selector_domain=selector_domain,
                        selector_is_strict=_selector_is_strict(outcome, selector_domain),
                        anchor_device_ids=anchor,
                        rationale=outcome.rationale,
                    )
                )
    return subs
