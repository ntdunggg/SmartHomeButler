"""Tổng hợp tất định cho ĐIỀU KHIỂN TƯỜNG MINH + các helper capability tái dùng.

Kiến trúc open-ended: mục tiêu mơ hồ/mới lạ do LLM tổng hợp (node `llm_generate_candidate_plan`).
Ở đây CHỈ giữ phần tất định hợp lệ, không phải template/routine:
- `synthesize_direct_plan`: khi người dùng nêu RÕ thiết bị + động từ (action_hint), dựng
  thẳng action 1:1 theo capability thật. Đây là parse lệnh tường minh, chạy offline được
  và làm fallback khi LLM trống — KHÔNG ánh xạ intent→plan, KHÔNG có setpoint bịa sẵn.
- `_onoff_capability` / `_numeric_capability`: quy tắc capability thật (rèm không ON_OFF →
  OPEN/CLOSE qua POSITION; nhiệt độ cần TEMPERATURE) — dùng chung cho gate tất định.
"""

from __future__ import annotations

from src.domain.enums import ActionType, Capability, RiskLevel
from src.iot.registry import DEVICE_BY_SLUG, DeviceSpec
from src.nlu.schemas import CandidateAction, CandidatePlan, SemanticGoal

_HINT_ACTION: dict[str, ActionType] = {
    "set": ActionType.SET,
    "increase": ActionType.INCREASE,
    "decrease": ActionType.DECREASE,
}


def _onoff_capability(spec: DeviceSpec, turn_on: bool) -> tuple[Capability, ActionType] | None:
    """bật/tắt: dùng ON_OFF nếu có; rèm/cửa sổ chỉ có POSITION thì dịch sang mở/đóng."""
    if Capability.ON_OFF in spec.capabilities:
        return Capability.ON_OFF, (ActionType.TURN_ON if turn_on else ActionType.TURN_OFF)
    if Capability.POSITION in spec.capabilities:
        return Capability.POSITION, (ActionType.OPEN if turn_on else ActionType.CLOSE)
    return None


def _numeric_capability(spec: DeviceSpec, params: dict, utterance: str = "") -> Capability | None:
    """Capability số nào đang được điều chỉnh trên thiết bị này.

    Thứ tự ưu tiên có lý do: (1) giá trị tuyệt đối trong params là bằng chứng chắc nhất;
    (2) capability người dùng NÊU RÕ trong câu; (3) mới tới suy đoán theo thiết bị.

    Bước (2) là bắt buộc với lệnh TƯƠNG ĐỐI ("tăng điều hoà lên"): params rỗng theo đúng
    thiết kế (không bịa số), nên nếu chỉ dựa vào params thì mọi lệnh tương đối trên điều hoà
    đều rơi xuống nhánh suy đoán. Trước đây nhánh đó bỏ sót TEMPERATURE, khiến cả "tăng nhiệt
    độ điều hoà" cũng đi chỉnh TỐC ĐỘ QUẠT — sai capability, âm thầm, không báo lỗi."""
    if params.get("temperature") is not None:
        return Capability.TEMPERATURE if Capability.TEMPERATURE in spec.capabilities else None

    if utterance:
        from src.agent.text import TextView, strip_diacritics
        from src.nlu.understanding import _mentioned_capability

        low = utterance.strip().lower()
        named = _mentioned_capability(TextView(raw=low, folded=strip_diacritics(low)))
        if named is not None and named in spec.capabilities:
            return named

    # Suy đoán theo thiết bị khi câu không nêu capability nào. TEMPERATURE đứng trước
    # FAN_SPEED: thiết bị CÓ nhiệt độ (điều hoà, bình nóng lạnh) thì nhiệt độ là núm điều
    # khiển chính, gió chỉ là phụ. Thiết bị không có nhiệt độ (đèn, quạt, loa) không bị ảnh
    # hưởng bởi thứ tự này vì chúng không khớp TEMPERATURE.
    for cap in (
        Capability.TEMPERATURE,
        Capability.BRIGHTNESS,
        Capability.POSITION,
        Capability.FAN_SPEED,
        Capability.VOLUME,
    ):
        if cap in spec.capabilities:
            return cap
    return None


def _direct_actions(goal: SemanticGoal) -> tuple[list[CandidateAction], list[str]]:
    missing: list[str] = []
    actions: list[CandidateAction] = []
    hint = goal.action_hint
    for dev in goal.target_device_ids:
        spec = DEVICE_BY_SLUG.get(dev)
        if spec is None:
            missing.append(f"Không có thiết bị '{dev}'")
            continue
        # Khoá cửa (capability LOCK): mở/bật → mở khoá; khoá/đóng/tắt → khoá. KHÔNG chặn
        # theo rủi ro ở đây — phân quyền (evaluate) mới quyết ai được điều khiển an ninh.
        if Capability.LOCK in spec.capabilities:
            if hint in ("turn_on", "open", "unlock"):
                action = ActionType.UNLOCK
            elif hint in ("lock", "turn_off", "close"):
                action = ActionType.LOCK
            else:
                missing.append(f"Chưa rõ khoá hay mở khoá '{spec.name}'")
                continue
            actions.append(
                CandidateAction(
                    device_id=spec.slug, capability=Capability.LOCK, action=action,
                    params={}, risk_level=spec.risk_level, reason_vi=goal.raw_utterance,
                )
            )
            continue
        if hint in ("turn_on", "turn_off"):
            resolved = _onoff_capability(spec, hint == "turn_on")
            if resolved is None:
                missing.append(f"'{spec.name}' không bật/tắt được")
                continue
            cap, action = resolved
            params: dict = {}
        elif hint == "set":
            if not goal.parameters:
                missing.append(f"Chưa rõ đặt giá trị nào cho '{spec.name}'")
                continue
            numeric_cap = (
                Capability.TEMPERATURE
                if goal.parameters.get("temperature") is not None
                else _numeric_capability(spec, goal.parameters, goal.raw_utterance)
            )
            if numeric_cap is None or numeric_cap not in spec.capabilities:
                missing.append(f"'{spec.name}' không nhận giá trị này")
                continue
            cap = numeric_cap
            action = ActionType.SET
            params = dict(goal.parameters)
        else:  # increase / decrease — hoặc một hint KHÔNG nhận diện được
            # `action_hint` là str tự do do LLM sinh (schemas.py: `str | None`), không phải
            # enum — nên nhánh này KHÔNG được giả định chỉ còn increase/decrease. Model trả
            # hint lạ (vd cả một câu mô tả) từng làm KeyError sập cả pipeline, trong khi mọi
            # lỗi khác quanh đây đều degrade êm qua `missing`. Tra an toàn, giữ đúng nguyên
            # tắc fail-closed: không hiểu thì báo thiếu để hỏi lại, không đoán hành động.
            action_opt = _HINT_ACTION.get(hint or "")
            if action_opt is None:
                missing.append(f"Chưa rõ cần làm gì với '{spec.name}'")
                continue
            numeric_cap = _numeric_capability(spec, goal.parameters, goal.raw_utterance)
            if numeric_cap is None:
                missing.append(f"'{spec.name}' không có mức để tăng/giảm")
                continue
            cap = numeric_cap
            action = action_opt
            params = {}
        actions.append(
            CandidateAction(
                device_id=spec.slug,
                capability=cap,
                action=action,
                params=params,
                risk_level=spec.risk_level,
                reason_vi=goal.raw_utterance,
            )
        )
    return actions, missing


def synthesize_direct_plan(goal: SemanticGoal) -> CandidatePlan:
    """Dựng candidate plan tất định cho lệnh điều khiển tường minh. KHÔNG execute.

    Chỉ áp dụng khi `goal.action_hint` được đặt (turn_on/turn_off/set/increase/decrease).
    Mục tiêu open-ended (action_hint=None) trả plan RỖNG — để LLM tổng hợp."""
    assumptions: list[str] = list(goal.assumptions)
    if goal.negated:
        assumptions.append("Câu có phủ định — không sinh hành động khẳng định")
        return _finish(goal, [], assumptions, [])
    if goal.action_hint is None:
        return _finish(goal, [], assumptions, [])
    actions, missing = _direct_actions(goal)
    return _finish(goal, actions, assumptions, missing)


def _finish(
    goal: SemanticGoal, actions: list[CandidateAction], assumptions: list[str], missing: list[str]
) -> CandidatePlan:
    requires_confirmation = any(a.risk_level in {RiskLevel.HIGH_POWER, RiskLevel.SECURITY} for a in actions) or len(actions) > 3
    return CandidatePlan(
        goal_summary=goal.goal_description or goal.raw_utterance,
        utterance_type=goal.utterance_type,
        actions=actions,
        assumptions=assumptions,
        missing_information=missing,
        requires_confirmation=requires_confirmation,
        requires_policy_validation=True,
        explanation_vi=goal.raw_utterance,
    )
