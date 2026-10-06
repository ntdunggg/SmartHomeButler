"""Ground mục tiêu TƯƠNG ĐỐI (delta) thành hành động TUYỆT ĐỐI bằng live context.

Tầng Semantic State cho phép goal chỉ nói "ấm hơn một chút" (perceived_state=cold,
relative_change={"temperature": "increase_slight"}) thay vì bịa "25 độ". Planner gọi
`ground_candidate_plan` để quy delta đó thành con số cụ thể dựa trên TRẠNG THÁI HIỆN
TẠI của đúng thiết bị (live nếu có, else initial_state) + mức (slight/normal/large).

Mức cảm nhận còn được CẢM BIẾN MÔI TRƯỜNG điều chỉnh ±1 nấc (`env_notch`): PM2.5 cao →
tăng luồng khí mạnh hơn; phòng đang rất nóng → hạ nhiệt mạnh hơn; nắng gắt → giảm sáng
mạnh hơn. Cảm biến mâu thuẫn rõ thì hạ 1 nấc (làm dịu phản ứng thái quá). Nhờ vậy delta
phản ứng theo hoàn cảnh thật, không chỉ theo cường độ câu nói.

Đây là RESIDUE TẤT ĐỊNH ("LLM đề xuất, code quyết định"): chạy offline được, không
gọi model, để hard gate và execution nhận SET với giá trị thật thay vì INCREASE/DECREASE
mờ. Quy ước param SET khớp `src/agent/execution.translate_action` (temperature→temperature,
brightness/position/volume→percent, fan_speed→level).
"""

from __future__ import annotations

from typing import Any

from src.domain.action_registry import (
    capability_bounds,
    relative_steps_for,
    semantic_parameter_key,
    state_key_for_capability,
)
from src.domain.enums import ActionType, Capability, SensorType
from src.iot.registry import DEVICE_BY_SLUG
from src.nlu.schemas import CandidateAction, CandidatePlan, DesiredOutcome, RuntimeContext

# Từ khoá mức trong token (cả tiếng Việt lẫn Anh) → nhãn mức chuẩn.
_SLIGHT_CUES = ("slight", "little", "bit", "nhe", "nhẹ", "hoi", "hơi", "chut", "chút")
_LARGE_CUES = ("large", "lot", "much", "strong", "manh", "mạnh", "nhieu", "nhiều", "han", "hẳn", "rat", "rất")

# Token tăng/giảm (fallback suy hướng khi ActionType không phải increase/decrease).
_UP_CUES = ("increase", "up", "raise", "higher", "more", "warmer", "brighter", "louder", "+", "tang", "tăng")
_DOWN_CUES = ("decrease", "down", "lower", "less", "cooler", "dimmer", "quieter", "-", "giam", "giảm")


# Thứ tự mức để cộng/trừ nấc (sensor đẩy ±1 nấc, chặn hai đầu).
_MAG_ORDER: tuple[str, ...] = ("slight", "normal", "large")


def magnitude_of(token: str) -> str:
    """Trích mức (slight|normal|large) từ token tương đối; mặc định 'normal'."""
    t = (token or "").lower()
    if any(c in t for c in _SLIGHT_CUES):
        return "slight"
    if any(c in t for c in _LARGE_CUES):
        return "large"
    return "normal"


def _shift_magnitude(spoken: str, notch: int) -> str:
    """Dịch mức cảm nhận theo số nấc từ cảm biến, chặn trong [slight, large]."""
    idx = _MAG_ORDER.index(spoken) if spoken in _MAG_ORDER else 1
    idx = max(0, min(len(_MAG_ORDER) - 1, idx + notch))
    return _MAG_ORDER[idx]


def direction_of(token: str) -> int:
    """+1 nếu token nghiêng 'tăng', -1 nếu 'giảm', 0 nếu không rõ."""
    t = (token or "").lower()
    if any(c in t for c in _UP_CUES):
        return 1
    if any(c in t for c in _DOWN_CUES):
        return -1
    return 0


def _current_value(slug: str, capability: Capability, ctx: RuntimeContext | None) -> float | None:
    """Đọc giá trị hiện tại của capability: ưu tiên live state trong ctx, else initial_state."""
    key = state_key_for_capability(capability)
    if key is None:
        return None
    state: dict[str, Any] = {}
    dev = ctx.device(slug) if ctx is not None else None
    if dev is not None and dev.state:
        state = dev.state
    else:
        spec = DEVICE_BY_SLUG.get(slug)
        if spec is not None:
            state = spec.initial_state
    val = state.get(key)
    if isinstance(val, (int | float)) and not isinstance(val, bool):
        return float(val)
    return None


def ground_relative_value(
    capability: Capability, sign: int, magnitude: str, current: float
) -> tuple[str, int] | None:
    """(capability, hướng, mức, giá trị hiện tại) → (param_key, giá trị tuyệt đối) đã chặn biên.

    None nếu capability không phải loại số ground được."""
    steps = relative_steps_for(capability)
    bounds = capability_bounds(capability)
    param_key = semantic_parameter_key(capability)
    if steps is None or bounds is None or param_key is None or sign == 0:
        return None
    step = steps[{"slight": 0, "normal": 1, "large": 2}.get(magnitude, 1)]
    lo, hi = bounds
    target = max(lo, min(hi, float(current) + sign * step))
    return param_key, int(round(target))


def _magnitude_for(capability: Capability, outcomes: list[DesiredOutcome]) -> str:
    """Tìm mức cảm nhận cho capability trong các desired_outcome (relative_change)."""
    cap_val = capability.value
    for o in outcomes:
        token = o.relative_change.get(cap_val)
        if token:
            return magnitude_of(token)
    return "normal"


def _device_room(slug: str, ctx: RuntimeContext | None) -> str | None:
    dev = ctx.device(slug) if ctx is not None else None
    if dev is not None and dev.room:
        return dev.room
    spec = DEVICE_BY_SLUG.get(slug)
    return spec.room if spec is not None else None


def _sensor_value(ctx: RuntimeContext | None, sensor_type: str, room: str | None) -> float | None:
    """Đọc chỉ số cảm biến: ưu tiên cảm biến ĐÚNG PHÒNG thiết bị, rồi tới cảm biến cả nhà.

    Không lấy cảm biến của phòng KHÁC (tránh dùng nhiệt độ phòng khác để chỉnh phòng này)."""
    if ctx is None:
        return None
    room_val: float | None = None
    global_val: float | None = None
    for s in ctx.sensors:
        if s.sensor_type != sensor_type:
            continue
        if room and s.room == room and room_val is None:
            room_val = s.value
        elif not s.room and global_val is None:
            global_val = s.value
    return room_val if room_val is not None else global_val


def env_notch(capability: Capability, sign: int, ctx: RuntimeContext | None, slug: str) -> int:
    """±1 nấc mức cảm nhận theo môi trường sống, chỉ khi cảm biến vượt ngưỡng.

    +1 khi cảm biến ĐỒNG THUẬN & mạnh với khó chịu (cần chỉnh mạnh hơn lời nói);
    -1 khi cảm biến MÂU THUẪN rõ (làm dịu phản ứng thái quá); 0 khi trung tính/thiếu
    dữ liệu. Hướng khó chịu suy từ `sign` (giảm nhiệt = đang nóng; tăng sáng = đang tối)."""
    room = _device_room(slug, ctx)

    if capability == Capability.FAN_SPEED and sign > 0:
        # Tăng luồng khí (bí/ngột): PM2.5 càng bẩn càng cần mạnh.
        pm = _sensor_value(ctx, SensorType.PM25.value, room)
        if pm is not None:
            if pm >= 55:  # ngưỡng "không tốt cho sức khoẻ"
                return 1
            if pm <= 15:  # không khí sạch → nhẹ tay
                return -1
        return 0

    if capability == Capability.TEMPERATURE:
        amb = _sensor_value(ctx, SensorType.TEMPERATURE.value, room)
        if amb is None:
            return 0
        if sign < 0:  # đang nóng → hạ nhiệt
            return 1 if amb >= 30 else (-1 if amb <= 24 else 0)
        # đang lạnh → tăng nhiệt
        return 1 if amb <= 20 else (-1 if amb >= 26 else 0)

    if capability == Capability.BRIGHTNESS:
        sun = _sensor_value(ctx, SensorType.SUNLIGHT.value, room)
        if sun is None:
            return 0
        if sign < 0:  # chói → giảm sáng
            return 1 if sun >= 60 else (-1 if sun <= 20 else 0)
        # tối → tăng sáng
        return 1 if sun <= 20 else (-1 if sun >= 70 else 0)

    return 0


def _device_is_off(slug: str, ctx: RuntimeContext | None) -> bool:
    """Thiết bị có đang TẮT theo trạng thái sống không (live nếu có, else initial_state)."""
    dev = ctx.device(slug) if ctx is not None else None
    if dev is not None and dev.state:
        state = dev.state
    else:
        spec = DEVICE_BY_SLUG.get(slug)
        state = spec.initial_state if spec is not None else {}
    power = state.get("power")
    if power is None:
        return False
    return str(power).strip().lower() in ("off", "false", "0", "closed")


def _set_target_value(act: CandidateAction, capability: Capability) -> float | None:
    """Giá trị đích của một action SET tuyệt đối theo registry, hoặc None."""
    key = semantic_parameter_key(capability)
    if key is None:
        return None
    v = act.params.get(key)
    if isinstance(v, (int | float)) and not isinstance(v, bool):
        return float(v)
    return None


def ground_candidate_plan(
    plan: CandidatePlan, goal: Any, ctx: RuntimeContext | None
) -> CandidatePlan:
    """Quy action tương đối → SET tuyệt đối bằng live state, VÀ lọc theo TRẠNG THÁI SỐNG.

    - ``increase``/``decrease`` trên capability số → SET giá trị tuyệt đối (mức lấy từ
      ``goal.desired_outcomes[].relative_change`` + cảm biến; nền là live state).
    - Bỏ action NO-OP (đích đã bằng hiện tại).
    - Bỏ action GIẢM một capability trên thiết bị đang TẮT KHI cùng capability đã có thiết bị
      ĐANG BẬT: "chói quá" mà chỉ đèn phòng ngủ bật → chỉ giảm/đụng đèn đang bật, không đụng
      đèn đang tắt (nó không phải nguồn khó chịu). Nếu KHÔNG có thiết bị nào bật thì không lọc
      (giữ nguyên hành vi cũ — không có bằng chứng để loại).
    - Chưa biết giá trị hiện tại → GIỮ NGUYÊN action (không đoán)."""
    outcomes = list(getattr(goal, "desired_outcomes", []) or [])

    # Pass 1: tính (capability, hiện tại, đích, hướng, param_key, ghi chú) cho mỗi action số.
    metas: list[Any] = []
    for act in plan.actions:
        cap = act.capability
        is_rel = act.action in (ActionType.INCREASE, ActionType.DECREASE)
        is_set = act.action == ActionType.SET
        state_key = state_key_for_capability(cap)
        current = _current_value(act.device_id, cap, ctx) if state_key is not None else None
        if not (is_rel or is_set) or state_key is None or current is None:
            metas.append((act, None, None, None, 0, None, ""))
            continue
        value: float | int | None
        param_key: str | None
        if is_rel:
            sign = 1 if act.action == ActionType.INCREASE else -1
            spoken = _magnitude_for(cap, outcomes)
            notch = env_notch(cap, sign, ctx, act.device_id)
            magnitude = _shift_magnitude(spoken, notch)
            resolved_value = ground_relative_value(cap, sign, magnitude, current)
            if resolved_value is None:
                metas.append((act, None, None, None, 0, None, ""))
                continue
            param_key, value = resolved_value
            note = " (cảm biến → mạnh hơn)" if notch > 0 else (" (cảm biến → nhẹ hơn)" if notch < 0 else "")
        else:
            set_value = _set_target_value(act, cap)
            if set_value is None:
                metas.append((act, None, None, None, 0, None, ""))
                continue
            value = set_value
            param_key = semantic_parameter_key(cap)
            if param_key is None:
                metas.append((act, None, None, None, 0, None, ""))
                continue
            sign = 1 if value > current else (-1 if value < current else 0)
            note = ""
        metas.append((act, cap, current, float(value), sign, param_key, note))

    # Capability đang GIẢM có ít nhất một thiết bị ĐANG BẬT → mới đủ căn cứ loại đèn tắt.
    reduce_caps_with_on = {
        cap for act, cap, _c, _v, sign, _p, _n in metas
        if cap is not None and sign < 0 and not _device_is_off(act.device_id, ctx)
    }

    new_actions: list[CandidateAction] = []
    changed = False
    for act, cap, current, value, sign, param_key, note in metas:
        if cap is None:
            new_actions.append(act)
            continue
        if int(round(value)) == int(round(current)):  # no-op
            changed = True
            continue
        if sign < 0 and cap in reduce_caps_with_on and _device_is_off(act.device_id, ctx):
            changed = True  # giảm trên thiết bị đang tắt, trong khi đã có thiết bị bật → bỏ
            continue
        if act.action in (ActionType.INCREASE, ActionType.DECREASE):
            verb = "tăng" if sign > 0 else "giảm"
            new_actions.append(
                act.model_copy(
                    update={
                        "action": ActionType.SET,
                        "params": {param_key: int(round(value))},
                        "reason_vi": act.reason_vi
                        or f"{verb} {cap.value} về {int(round(value))} (từ {int(current)}) theo cảm nhận{note}",
                    }
                )
            )
            changed = True
        else:
            new_actions.append(act)

    if not changed:
        return plan
    return plan.model_copy(update={"actions": new_actions})
