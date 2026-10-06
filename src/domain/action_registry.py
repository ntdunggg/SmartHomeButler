"""Deterministic source of truth for Smart Home action contracts.

This module owns static action semantics only: required capability, supported
device types, parameter schema, numeric limits, semantic-to-executable mapping,
expected state, labels, and risk metadata.  Runtime authorization, child lock,
approval, live-state freshness, conflicts, dispatch, and audit stay in the agent
execution boundary.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from src.domain.enums import ActionType, Capability, DeviceType, RiskLevel

ParameterKind = Literal["number", "choice", "boolean", "color", "text"]

# Stable executable action identifiers.  Adapters import these constants instead
# of declaring their own action vocabulary.
ACTION_TURN_ON = "turn_on"
ACTION_TURN_OFF = "turn_off"
ACTION_SET_BRIGHTNESS = "set_brightness"
ACTION_SET_TEMPERATURE = "set_temperature"
ACTION_SET_FAN_SPEED = "set_fan_speed"
ACTION_SET_VOLUME = "set_volume"
ACTION_SET_POSITION = "set_position"
ACTION_OPEN = "open"
ACTION_CLOSE = "close"
ACTION_LOCK = "lock"
ACTION_UNLOCK = "unlock"
ACTION_SET_COLOR_TEMP = "set_color_temp"
ACTION_SET_COLOR = "set_color"
ACTION_SET_HVAC_MODE = "set_hvac_mode"
ACTION_SET_OPERATION_MODE = "set_operation_mode"
ACTION_SET_AWAY = "set_away"
ACTION_MEDIA_PLAY = "media_play"
ACTION_MEDIA_PAUSE = "media_pause"
ACTION_MEDIA_NEXT = "media_next"
ACTION_MEDIA_PREVIOUS = "media_previous"
ACTION_SET_SOURCE = "set_source"
ACTION_SET_PRESET = "set_preset"
ACTION_SET_OSCILLATE = "set_oscillate"
ACTION_VACUUM_START = "vacuum_start"
ACTION_VACUUM_PAUSE = "vacuum_pause"
ACTION_VACUUM_STOP = "vacuum_stop"
ACTION_VACUUM_RETURN = "vacuum_return"
ACTION_VACUUM_SPOT = "vacuum_spot"
ACTION_VACUUM_LOCATE = "vacuum_locate"
ACTION_SET_PROGRAM = "set_program"


class ApprovalPolicy(StrEnum):
    """How dynamic policy should interpret an action's approval metadata."""

    DEVICE_ACCESS = "device_access"


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    kind: ParameterKind
    label_vi: str
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    default: Any = None
    unit: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    input_keys: tuple[str, ...] = ()
    ranges_by_device_type: tuple[tuple[DeviceType, float, float], ...] = ()

    def bounds_for(self, device_type: DeviceType | str | None = None) -> tuple[float, float] | None:
        normalized = _device_type(device_type)
        if normalized is not None:
            for candidate, low, high in self.ranges_by_device_type:
                if candidate is normalized:
                    return low, high
        if self.minimum is None or self.maximum is None:
            return None
        return self.minimum, self.maximum

    @property
    def accepted_input_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.name, *self.input_keys)))

    @property
    def choice_values(self) -> frozenset[str]:
        return frozenset(value for value, _ in self.choices)


@dataclass(frozen=True, slots=True)
class ActionSpec:
    name: str
    capability: Capability
    label_vi: str
    parameter: ParameterSpec | None = None
    semantic_actions: frozenset[ActionType] = field(default_factory=frozenset)
    supported_device_types: frozenset[DeviceType] = field(default_factory=frozenset)
    state_key: str | None = None
    default_delta: float | None = None
    relative_steps: tuple[float, float, float] | None = None
    static_expected_state: tuple[tuple[str, Any], ...] = ()
    power_behavior: Literal["", "on", "off", "nonzero", "choice_off"] = ""
    risk_floor: RiskLevel = RiskLevel.NORMAL
    approval_policy: ApprovalPolicy = ApprovalPolicy.DEVICE_ACCESS
    safety_tags: frozenset[str] = field(default_factory=frozenset)
    habit_order: int | None = None

    def supports_device_type(self, device_type: DeviceType | str | None) -> bool:
        normalized = _device_type(device_type)
        return not self.supported_device_types or normalized is None or normalized in self.supported_device_types


@dataclass(frozen=True, slots=True)
class ContractIssue:
    code: str
    message_vi: str


@dataclass(frozen=True, slots=True)
class ContractValidation:
    params: dict[str, Any]
    issue: ContractIssue | None = None

    @property
    def ok(self) -> bool:
        return self.issue is None


_HVAC_MODES = (("cool", "Lạnh"), ("heat", "Sưởi"), ("auto", "Tự động"), ("off", "Tắt"))
_OPERATION_MODES = (("eco", "Eco"), ("performance", "Nhanh"), ("off", "Tắt"))
_FAN_PRESETS = (("auto", "Tự động"), ("sleep", "Đêm"), ("turbo", "Turbo"))
_MEDIA_SOURCES = (("spotify", "Spotify"), ("youtube", "YouTube"), ("radio", "Radio"), ("hdmi", "HDMI"))


def _number(
    name: str,
    label_vi: str,
    low: float,
    high: float,
    *,
    step: float,
    default: float,
    unit: str,
    input_keys: tuple[str, ...] = (),
    ranges_by_device_type: tuple[tuple[DeviceType, float, float], ...] = (),
) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        kind="number",
        label_vi=label_vi,
        minimum=low,
        maximum=high,
        step=step,
        default=default,
        unit=unit,
        input_keys=input_keys,
        ranges_by_device_type=ranges_by_device_type,
    )


def _choice(name: str, label_vi: str, choices: tuple[tuple[str, str], ...], default: str) -> ParameterSpec:
    return ParameterSpec(name=name, kind="choice", label_vi=label_vi, choices=choices, default=default)


ACTION_SPECS: tuple[ActionSpec, ...] = (
    ActionSpec(ACTION_TURN_ON, Capability.ON_OFF, "Bật", semantic_actions=frozenset({ActionType.TURN_ON}), static_expected_state=(("power", "on"),), habit_order=0),
    ActionSpec(ACTION_TURN_OFF, Capability.ON_OFF, "Tắt", semantic_actions=frozenset({ActionType.TURN_OFF}), static_expected_state=(("power", "off"),), habit_order=1),
    ActionSpec(
        ACTION_SET_BRIGHTNESS, Capability.BRIGHTNESS, "Chỉnh độ sáng",
        parameter=_number("brightness", "Độ sáng", 0, 100, step=5, default=60, unit="%", input_keys=("percent", "level")),
        semantic_actions=frozenset({ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}),
        supported_device_types=frozenset({DeviceType.LIGHT}), state_key="brightness", default_delta=10,
        relative_steps=(15, 30, 50), power_behavior="nonzero", habit_order=8,
    ),
    ActionSpec(
        ACTION_SET_TEMPERATURE, Capability.TEMPERATURE, "Chỉnh nhiệt độ",
        parameter=_number(
            "temperature", "Nhiệt độ", 16, 30, step=1, default=26, unit="°C",
            ranges_by_device_type=((DeviceType.WATER_HEATER, 30, 60),),
        ),
        semantic_actions=frozenset({ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}),
        supported_device_types=frozenset({DeviceType.AIR_CONDITIONER, DeviceType.HEATER, DeviceType.WATER_HEATER}),
        state_key="temperature", default_delta=2, relative_steps=(1, 2, 3), power_behavior="on",
        risk_floor=RiskLevel.HIGH_POWER, habit_order=9,
    ),
    ActionSpec(
        ACTION_SET_FAN_SPEED, Capability.FAN_SPEED, "Chỉnh tốc độ gió",
        parameter=_number("fan_speed", "Tốc độ gió", 0, 3, step=1, default=2, unit="mức", input_keys=("level",)),
        semantic_actions=frozenset({ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}),
        supported_device_types=frozenset({DeviceType.AIR_CONDITIONER, DeviceType.AIR_PURIFIER, DeviceType.FAN}),
        state_key="fan_speed", default_delta=1, relative_steps=(1, 1, 2), power_behavior="nonzero", habit_order=10,
    ),
    ActionSpec(
        ACTION_SET_VOLUME, Capability.VOLUME, "Chỉnh âm lượng",
        parameter=_number("volume", "Âm lượng", 0, 100, step=5, default=25, unit="%", input_keys=("percent", "level")),
        semantic_actions=frozenset({ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}),
        supported_device_types=frozenset({DeviceType.TV, DeviceType.SPEAKER}), state_key="volume",
        default_delta=10, relative_steps=(10, 20, 35), habit_order=12,
    ),
    ActionSpec(
        ACTION_SET_POSITION, Capability.POSITION, "Chỉnh độ mở",
        parameter=_number("position", "Độ mở", 0, 100, step=5, default=100, unit="%", input_keys=("percent", "level")),
        semantic_actions=frozenset({ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}),
        supported_device_types=frozenset({DeviceType.CURTAIN, DeviceType.WINDOW}), state_key="position",
        default_delta=20, relative_steps=(20, 40, 70), habit_order=11,
    ),
    ActionSpec(ACTION_OPEN, Capability.POSITION, "Mở", semantic_actions=frozenset({ActionType.OPEN}), supported_device_types=frozenset({DeviceType.CURTAIN, DeviceType.WINDOW}), static_expected_state=(("position", 100),), habit_order=2),
    ActionSpec(ACTION_CLOSE, Capability.POSITION, "Đóng", semantic_actions=frozenset({ActionType.CLOSE}), supported_device_types=frozenset({DeviceType.CURTAIN, DeviceType.WINDOW}), static_expected_state=(("position", 0),), habit_order=3),
    ActionSpec(ACTION_LOCK, Capability.LOCK, "Khoá", semantic_actions=frozenset({ActionType.LOCK}), supported_device_types=frozenset({DeviceType.DOOR_LOCK}), static_expected_state=(("locked", True),), risk_floor=RiskLevel.SECURITY, safety_tags=frozenset({"security_sensitive"}), habit_order=4),
    ActionSpec(ACTION_UNLOCK, Capability.LOCK, "Mở khoá", semantic_actions=frozenset({ActionType.UNLOCK}), supported_device_types=frozenset({DeviceType.DOOR_LOCK}), static_expected_state=(("locked", False),), risk_floor=RiskLevel.SECURITY, safety_tags=frozenset({"security_sensitive", "explicit_intent_required"}), habit_order=5),
    ActionSpec(
        ACTION_SET_COLOR_TEMP, Capability.COLOR_TEMP, "Chỉnh nhiệt màu",
        parameter=_number("color_temp", "Nhiệt màu", 2200, 6500, step=100, default=4000, unit="K"),
        semantic_actions=frozenset({ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}),
        supported_device_types=frozenset({DeviceType.LIGHT}), state_key="color_temp", default_delta=100,
        relative_steps=(100, 300, 600), power_behavior="on", habit_order=14,
    ),
    ActionSpec(
        ACTION_SET_COLOR, Capability.COLOR, "Đổi màu",
        parameter=ParameterSpec("color", "color", "Màu", default="#ffcc88"),
        semantic_actions=frozenset({ActionType.SET}), supported_device_types=frozenset({DeviceType.LIGHT}),
        state_key="color", power_behavior="on", habit_order=22,
    ),
    ActionSpec(
        ACTION_SET_HVAC_MODE, Capability.HVAC_MODE, "Đặt chế độ điều hoà",
        parameter=_choice("mode", "Chế độ", _HVAC_MODES, "cool"), semantic_actions=frozenset({ActionType.SET}),
        supported_device_types=frozenset({DeviceType.AIR_CONDITIONER}), state_key="hvac_mode",
        power_behavior="choice_off", risk_floor=RiskLevel.HIGH_POWER, habit_order=13,
    ),
    ActionSpec(
        ACTION_SET_OPERATION_MODE, Capability.OPERATION_MODE, "Đặt chế độ vận hành",
        parameter=_choice("mode", "Chế độ", _OPERATION_MODES, "eco"), semantic_actions=frozenset({ActionType.SET}),
        supported_device_types=frozenset({DeviceType.WATER_HEATER}), state_key="operation_mode",
        power_behavior="choice_off", risk_floor=RiskLevel.HIGH_POWER, habit_order=19,
    ),
    ActionSpec(
        ACTION_SET_AWAY, Capability.AWAY_MODE, "Chế độ vắng nhà",
        parameter=ParameterSpec("away", "boolean", "Vắng nhà", required=False, default=True),
        semantic_actions=frozenset({ActionType.SET}), supported_device_types=frozenset({DeviceType.WATER_HEATER}),
        state_key="away", risk_floor=RiskLevel.HIGH_POWER, habit_order=21,
    ),
    ActionSpec(ACTION_MEDIA_PLAY, Capability.MEDIA_CONTROL, "Phát nhạc/video", supported_device_types=frozenset({DeviceType.TV, DeviceType.SPEAKER}), static_expected_state=(("playback", "playing"), ("power", "on")), habit_order=17),
    ActionSpec(ACTION_MEDIA_PAUSE, Capability.MEDIA_CONTROL, "Tạm dừng", supported_device_types=frozenset({DeviceType.TV, DeviceType.SPEAKER}), static_expected_state=(("playback", "paused"),), habit_order=18),
    ActionSpec(ACTION_MEDIA_NEXT, Capability.MEDIA_CONTROL, "Bài tiếp theo", supported_device_types=frozenset({DeviceType.TV, DeviceType.SPEAKER})),
    ActionSpec(ACTION_MEDIA_PREVIOUS, Capability.MEDIA_CONTROL, "Bài trước", supported_device_types=frozenset({DeviceType.TV, DeviceType.SPEAKER})),
    ActionSpec(
        ACTION_SET_SOURCE, Capability.MEDIA_SOURCE, "Chọn nguồn phát",
        parameter=_choice("source", "Nguồn", _MEDIA_SOURCES, "spotify"), semantic_actions=frozenset({ActionType.SET}),
        supported_device_types=frozenset({DeviceType.TV}), state_key="source", power_behavior="on", habit_order=16,
    ),
    ActionSpec(
        ACTION_SET_PRESET, Capability.PRESET_MODE, "Đặt chế độ quạt/lọc",
        parameter=_choice("preset", "Chế độ", _FAN_PRESETS, "auto"), semantic_actions=frozenset({ActionType.SET}),
        supported_device_types=frozenset({DeviceType.AIR_PURIFIER, DeviceType.FAN}), state_key="preset",
        power_behavior="on", habit_order=15,
    ),
    ActionSpec(
        ACTION_SET_OSCILLATE, Capability.OSCILLATE, "Đảo chiều",
        parameter=ParameterSpec("oscillate", "boolean", "Đảo chiều", required=False, default=True),
        semantic_actions=frozenset({ActionType.SET}), supported_device_types=frozenset({DeviceType.AIR_PURIFIER, DeviceType.FAN}),
        state_key="oscillate", habit_order=15,
    ),
    ActionSpec(ACTION_VACUUM_START, Capability.VACUUM_CONTROL, "Bắt đầu hút bụi", supported_device_types=frozenset({DeviceType.VACUUM}), static_expected_state=(("activity", "cleaning"), ("power", "on")), habit_order=6),
    ActionSpec(ACTION_VACUUM_PAUSE, Capability.VACUUM_CONTROL, "Tạm dừng hút bụi", supported_device_types=frozenset({DeviceType.VACUUM}), static_expected_state=(("activity", "paused"),)),
    ActionSpec(ACTION_VACUUM_STOP, Capability.VACUUM_CONTROL, "Dừng hút bụi", supported_device_types=frozenset({DeviceType.VACUUM}), static_expected_state=(("activity", "idle"), ("power", "off")), habit_order=7),
    ActionSpec(ACTION_VACUUM_RETURN, Capability.VACUUM_CONTROL, "Về dock", supported_device_types=frozenset({DeviceType.VACUUM}), static_expected_state=(("activity", "returning"), ("power", "on"))),
    ActionSpec(ACTION_VACUUM_SPOT, Capability.VACUUM_CONTROL, "Dọn điểm", supported_device_types=frozenset({DeviceType.VACUUM}), static_expected_state=(("activity", "spot"), ("power", "on"))),
    ActionSpec(ACTION_VACUUM_LOCATE, Capability.VACUUM_CONTROL, "Định vị robot", supported_device_types=frozenset({DeviceType.VACUUM}), static_expected_state=(("activity", "locating"),)),
    ActionSpec(
        ACTION_SET_PROGRAM, Capability.PROGRAM, "Đặt chương trình",
        parameter=ParameterSpec("program", "text", "Chương trình", default="eco"), semantic_actions=frozenset({ActionType.SET}),
        supported_device_types=frozenset({DeviceType.DISHWASHER}), state_key="program", power_behavior="on",
        risk_floor=RiskLevel.HIGH_POWER, habit_order=20,
    ),
)

ACTION_BY_NAME: dict[str, ActionSpec] = {spec.name: spec for spec in ACTION_SPECS}
ALL_ACTIONS = frozenset(ACTION_BY_NAME)


def _device_type(value: DeviceType | str | None) -> DeviceType | None:
    if value is None or isinstance(value, DeviceType):
        return value
    try:
        return DeviceType(str(value))
    except ValueError:
        return None


def get_action_spec(action: str) -> ActionSpec | None:
    return ACTION_BY_NAME.get(action)


def capability_for_action(action: str) -> Capability | None:
    spec = get_action_spec(action)
    return spec.capability if spec is not None else None


def action_label_vi(action: str) -> str:
    spec = get_action_spec(action)
    return spec.label_vi if spec is not None else action


def habit_action_specs() -> tuple[ActionSpec, ...]:
    return tuple(sorted((spec for spec in ACTION_SPECS if spec.habit_order is not None), key=lambda spec: spec.habit_order or 0))


def actions_for_capabilities(
    capabilities: Iterable[Capability | str],
    *,
    habit_only: bool = False,
    device_type: DeviceType | str | None = None,
) -> tuple[str, ...]:
    values = {cap.value if isinstance(cap, Capability) else str(cap) for cap in capabilities}
    specs = habit_action_specs() if habit_only else ACTION_SPECS
    return tuple(
        spec.name
        for spec in specs
        if spec.capability.value in values and spec.supports_device_type(device_type)
    )


def action_for_semantic(capability: Capability | str, action: ActionType | str) -> ActionSpec | None:
    try:
        cap = capability if isinstance(capability, Capability) else Capability(str(capability))
        semantic = action if isinstance(action, ActionType) else ActionType(str(action))
    except ValueError:
        return None
    return next((spec for spec in ACTION_SPECS if spec.capability is cap and semantic in spec.semantic_actions), None)


def parameter_bounds(action: str, device_type: DeviceType | str | None = None) -> tuple[float, float] | None:
    spec = get_action_spec(action)
    if spec is None or spec.parameter is None or spec.parameter.kind != "number":
        return None
    return spec.parameter.bounds_for(device_type)


def capability_bounds(capability: Capability | str, device_type: DeviceType | str | None = None) -> tuple[float, float] | None:
    try:
        cap = capability if isinstance(capability, Capability) else Capability(str(capability))
    except ValueError:
        return None
    spec = next((item for item in ACTION_SPECS if item.capability is cap and item.parameter is not None and item.parameter.kind == "number"), None)
    return spec.parameter.bounds_for(device_type) if spec and spec.parameter else None


def state_key_for_capability(capability: Capability | str) -> str | None:
    spec = action_for_semantic(capability, ActionType.SET)
    return spec.state_key if spec is not None else None


def semantic_parameter_key(capability: Capability | str) -> str | None:
    spec = action_for_semantic(capability, ActionType.SET)
    if spec is None or spec.parameter is None:
        return None
    return spec.parameter.input_keys[0] if spec.parameter.input_keys else spec.parameter.name


def relative_steps_for(capability: Capability | str) -> tuple[float, float, float] | None:
    spec = action_for_semantic(capability, ActionType.SET)
    return spec.relative_steps if spec is not None else None


def choice_labels(action: str) -> dict[str, str]:
    spec = get_action_spec(action)
    if spec is None or spec.parameter is None:
        return {}
    return dict(spec.parameter.choices)


def _issue(code: str, message_vi: str, params: dict[str, Any]) -> ContractValidation:
    return ContractValidation(params=params, issue=ContractIssue(code, message_vi))


def _normalize_parameter(parameter: ParameterSpec, raw: Any, *, action: str, device_type: DeviceType | str | None) -> ContractValidation:
    if parameter.kind == "number":
        if isinstance(raw, bool):
            return _issue("PARAMETER_TYPE_INVALID", f"Giá trị '{parameter.name}' không hợp lệ: {raw!r}.", {})
        try:
            numeric_value = float(raw)
        except (TypeError, ValueError):
            return _issue("PARAMETER_TYPE_INVALID", f"Giá trị '{parameter.name}' không hợp lệ: {raw!r}.", {})
        bounds = parameter.bounds_for(device_type)
        if bounds is not None and not (bounds[0] <= numeric_value <= bounds[1]):
            return _issue(
                "TARGET_OUT_OF_RANGE",
                f"Giá trị {parameter.name}={raw} ngoài dải [{_display_number(bounds[0])},{_display_number(bounds[1])}], không thực hiện.",
                {},
            )
        normalized: int | float = int(numeric_value) if numeric_value.is_integer() else numeric_value
        return ContractValidation({parameter.name: normalized})
    if parameter.kind == "choice":
        choice_value = str(raw).strip().lower()
        if choice_value not in parameter.choice_values:
            allowed = ", ".join(candidate for candidate, _ in parameter.choices)
            return _issue("CHOICE_NOT_SUPPORTED", f"Giá trị '{parameter.name}' không hợp lệ: {raw!r}. Chọn: {allowed}.", {})
        return ContractValidation({parameter.name: choice_value})
    if parameter.kind == "boolean":
        if isinstance(raw, bool):
            return ContractValidation({parameter.name: raw})
        if isinstance(raw, str):
            token = raw.strip().lower()
            if token in {"1", "true", "on", "yes", "bật", "có"}:
                return ContractValidation({parameter.name: True})
            if token in {"0", "false", "off", "no", "tắt", "không"}:
                return ContractValidation({parameter.name: False})
        if isinstance(raw, int) and raw in (0, 1):
            return ContractValidation({parameter.name: bool(raw)})
        return _issue("PARAMETER_TYPE_INVALID", f"Giá trị '{parameter.name}' không hợp lệ: {raw!r}.", {})
    text_value = str(raw).strip()
    if parameter.kind == "color" and not re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", text_value):
        return _issue("PARAMETER_TYPE_INVALID", f"Mã màu không hợp lệ: {raw!r}. Dạng đúng: '#RRGGBB'.", {})
    if not text_value:
        return _issue("PARAMETER_REQUIRED", f"Lệnh '{action}' thiếu tham số '{parameter.name}'.", {})
    return ContractValidation({parameter.name: text_value})


def validate_action(
    action: str,
    params: dict[str, Any] | None,
    *,
    capabilities: Iterable[Capability | str] | None = None,
    device_type: DeviceType | str | None = None,
    capability_hint: Capability | str | None = None,
) -> ContractValidation:
    raw_params = dict(params or {})
    binding_issue = validate_action_binding(
        action,
        capabilities=capabilities,
        device_type=device_type,
        capability_hint=capability_hint,
    )
    if binding_issue is not None:
        return ContractValidation(params=raw_params, issue=binding_issue)
    spec = get_action_spec(action)
    assert spec is not None
    parameter = spec.parameter
    if parameter is None:
        if raw_params:
            first_unknown = next(iter(raw_params))
            return _issue("PARAMETER_UNKNOWN", f"Hành động '{action}' không nhận tham số '{first_unknown}'.", raw_params)
        return ContractValidation({})
    keys = [key for key in parameter.accepted_input_keys if key in raw_params]
    if not keys:
        if parameter.required:
            return _issue("PARAMETER_REQUIRED", f"Lệnh '{action}' thiếu tham số '{parameter.name}'.", raw_params)
        raw = parameter.default
    else:
        raw = raw_params[keys[0]]
    allowed = set(parameter.accepted_input_keys)
    unknown_key = next((key for key in raw_params if key not in allowed), None)
    if unknown_key is not None:
        return _issue("PARAMETER_UNKNOWN", f"Hành động '{action}' không nhận tham số '{unknown_key}'.", raw_params)
    return _normalize_parameter(parameter, raw, action=action, device_type=device_type)


def validate_action_binding(
    action: str,
    *,
    capabilities: Iterable[Capability | str] | None = None,
    device_type: DeviceType | str | None = None,
    capability_hint: Capability | str | None = None,
) -> ContractIssue | None:
    spec = get_action_spec(action)
    if spec is None:
        return ContractIssue("ACTION_NOT_SUPPORTED", f"Không hỗ trợ hành động '{action}'.")
    if capability_hint is not None:
        hint = capability_hint.value if isinstance(capability_hint, Capability) else str(capability_hint)
        if hint != spec.capability.value:
            return ContractIssue(
                "ACTION_CAPABILITY_MISMATCH",
                f"Hành động '{action}' không thuộc capability '{hint}'.",
            )
    if capabilities is not None:
        values = {cap.value if isinstance(cap, Capability) else str(cap) for cap in capabilities}
        if spec.capability.value not in values:
            return ContractIssue(
                "CAPABILITY_NOT_SUPPORTED",
                f"Thiết bị không hỗ trợ capability '{spec.capability.value}' cho hành động '{action}'.",
            )
    if not spec.supports_device_type(device_type):
        return ContractIssue("DEVICE_TYPE_NOT_SUPPORTED", f"Loại thiết bị không hỗ trợ hành động '{action}'.")
    return None


def validate_state_values(
    values: dict[str, Any],
    *,
    device_type: DeviceType | str | None = None,
) -> ContractIssue | None:
    """Validate numeric desired-state fields through registry-owned ranges."""
    for spec in ACTION_SPECS:
        parameter = spec.parameter
        if parameter is None or parameter.kind != "number" or parameter.name not in values:
            continue
        validation = _normalize_parameter(
            parameter,
            values[parameter.name],
            action=spec.name,
            device_type=device_type,
        )
        if not validation.ok:
            return validation.issue
    return None


def translate_semantic_action(
    capability: Capability | str,
    semantic_action: ActionType | str,
    params: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]] | None:
    try:
        semantic = semantic_action if isinstance(semantic_action, ActionType) else ActionType(str(semantic_action))
    except ValueError:
        return None
    raw_params = dict(params or {})
    # Specialists may express "turn on at value X" with the numeric capability.
    # The executable contract for that request is the corresponding set action,
    # whose state transition also powers the device on.
    if semantic is ActionType.TURN_ON:
        set_spec = action_for_semantic(capability, ActionType.SET)
        if set_spec is not None and set_spec.parameter is not None:
            value = next(
                (raw_params[key] for key in set_spec.parameter.accepted_input_keys if key in raw_params),
                None,
            )
            if value is not None:
                normalized = _normalize_parameter(
                    set_spec.parameter,
                    value,
                    action=set_spec.name,
                    device_type=None,
                )
                return (set_spec.name, normalized.params) if normalized.ok else None
    direct = {
        ActionType.TURN_ON: "turn_on",
        ActionType.TURN_OFF: "turn_off",
        ActionType.OPEN: "open",
        ActionType.CLOSE: "close",
        ActionType.LOCK: "lock",
        ActionType.UNLOCK: "unlock",
    }.get(semantic)
    if direct is not None:
        return direct, {}
    spec = action_for_semantic(capability, semantic)
    if spec is None or spec.parameter is None:
        return None
    if semantic in {ActionType.INCREASE, ActionType.DECREASE}:
        if spec.default_delta is None:
            return None
        sign = 1 if semantic is ActionType.INCREASE else -1
        return spec.name, {"delta": sign * spec.default_delta}
    value = next((raw_params[key] for key in spec.parameter.accepted_input_keys if key in raw_params), None)
    if value is None:
        return None
    normalized = _normalize_parameter(spec.parameter, value, action=spec.name, device_type=None)
    return (spec.name, normalized.params) if normalized.ok else None


def resolve_delta(
    action: str,
    params: dict[str, Any],
    current: dict[str, Any],
    *,
    device_type: DeviceType | str | None = None,
) -> dict[str, Any]:
    if "delta" not in params:
        return dict(params)
    spec = get_action_spec(action)
    if spec is None or spec.parameter is None or spec.parameter.kind != "number" or spec.state_key is None:
        return dict(params)
    resolved = {key: value for key, value in params.items() if key != "delta"}
    base = current.get(spec.state_key)
    if not isinstance(base, int | float) or isinstance(base, bool):
        return resolved
    try:
        value = float(base) + float(params["delta"])
    except (TypeError, ValueError):
        return resolved
    bounds = spec.parameter.bounds_for(device_type)
    if bounds is not None:
        # `max`/`min` trả về CHÍNH đối số thắng, nên khi giá trị vượt biên nó trả về biên — vốn
        # là `int` trong registry. `value` khi đó thôi là float và `.is_integer()` ngay dưới nổ
        # AttributeError. Chỉ vỡ đúng lúc clamp THẬT SỰ cắt (delta đẩy ra ngoài khoảng), nên
        # đường đi thường ngày không lộ ra. Ép lại float để phần dưới có một kiểu duy nhất.
        value = float(max(bounds[0], min(bounds[1], value)))
    resolved[spec.parameter.name] = int(value) if value.is_integer() else value
    return resolved


def expected_state(action: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = get_action_spec(action)
    if spec is None:
        return {}
    expected = dict(spec.static_expected_state)
    if spec.parameter is not None and spec.state_key is not None and spec.parameter.name in params:
        value = params[spec.parameter.name]
        expected[spec.state_key] = value
        if spec.power_behavior == "on":
            expected["power"] = "on"
        elif spec.power_behavior == "off":
            expected["power"] = "off"
        elif spec.power_behavior == "nonzero":
            expected["power"] = "off" if float(value) == 0 else "on"
        elif spec.power_behavior == "choice_off":
            expected["power"] = "off" if value == "off" else "on"
    return expected


def matches_expected_state(action: str, params: dict[str, Any], current: dict[str, Any]) -> bool:
    expected = expected_state(action, params)
    if not expected:
        return False
    for key, wanted in expected.items():
        actual = current.get(key)
        if isinstance(wanted, int | float) and not isinstance(wanted, bool):
            if actual is None:
                return False
            try:
                if float(actual) != float(wanted):
                    return False
            except (TypeError, ValueError):
                return False
        elif actual != wanted:
            return False
    return True


def semantic_target_issue(
    capability: Capability | str,
    semantic_action: ActionType | str,
    target: dict[str, Any],
    *,
    device_type: DeviceType | str | None = None,
) -> ContractIssue | None:
    try:
        cap = capability if isinstance(capability, Capability) else Capability(str(capability))
        semantic = semantic_action if isinstance(semantic_action, ActionType) else ActionType(str(semantic_action))
    except ValueError:
        return ContractIssue("ACTION_NOT_SUPPORTED", f"Hành động '{semantic_action}' không hợp lệ.")
    target_spec = action_for_semantic(cap, ActionType.SET)
    if target_spec is not None and target_spec.parameter is not None:
        target_value = next(
            (target[key] for key in target_spec.parameter.accepted_input_keys if key in target),
            None,
        )
        if target_value is not None:
            validation = _normalize_parameter(
                target_spec.parameter,
                target_value,
                action=target_spec.name,
                device_type=device_type,
            )
            if not validation.ok:
                return validation.issue
    structural = {
        ActionType.OPEN: Capability.POSITION,
        ActionType.CLOSE: Capability.POSITION,
        ActionType.LOCK: Capability.LOCK,
        ActionType.UNLOCK: Capability.LOCK,
    }.get(semantic)
    if structural is not None and cap is not structural:
        return ContractIssue("ACTION_NOT_SUPPORTED", f"Capability '{cap.value}' không hỗ trợ hành động '{semantic.value}'.")
    if semantic in {ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}:
        spec = action_for_semantic(cap, semantic)
        if spec is None or not spec.supports_device_type(device_type):
            return ContractIssue("ACTION_NOT_SUPPORTED", f"Capability '{cap.value}' không hỗ trợ hành động '{semantic.value}'.")
        if spec.parameter is not None:
            value = next((target[key] for key in spec.parameter.accepted_input_keys if key in target), None)
            if semantic is ActionType.SET and value is None:
                return ContractIssue("PARAMETER_REQUIRED", f"Hành động '{semantic.value}' thiếu giá trị '{spec.parameter.name}'.")
            if value is not None:
                validation = _normalize_parameter(spec.parameter, value, action=spec.name, device_type=device_type)
                return validation.issue
    elif semantic is ActionType.TOGGLE:
        return ContractIssue("ACTION_NOT_SUPPORTED", "Hành động 'toggle' chưa có executable action an toàn.")
    return None


def semantic_expected_state(
    capability: Capability | str,
    semantic_action: ActionType | str,
    target: dict[str, Any],
) -> dict[str, Any]:
    """Expected state for the legacy harness before executable translation."""
    try:
        semantic = semantic_action if isinstance(semantic_action, ActionType) else ActionType(str(semantic_action))
    except ValueError:
        return dict(target)
    if semantic is ActionType.LOCK:
        return {"locked": True}
    if semantic is ActionType.UNLOCK:
        return {"locked": False}
    if semantic in {ActionType.TURN_ON, ActionType.OPEN}:
        return {"power": "on", **target}
    if semantic in {ActionType.TURN_OFF, ActionType.CLOSE}:
        return {"power": "off", **target}
    spec = action_for_semantic(capability, semantic)
    if spec is None or spec.parameter is None:
        return dict(target)
    value = next((target[key] for key in spec.parameter.accepted_input_keys if key in target), None)
    if value is None:
        return {"power": "on", **target} if semantic in {ActionType.SET, ActionType.INCREASE, ActionType.DECREASE} else dict(target)
    normalized = _normalize_parameter(spec.parameter, value, action=spec.name, device_type=None)
    if not normalized.ok:
        return dict(target)
    resolved = expected_state(spec.name, normalized.params)
    if semantic in {ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}:
        # Nhánh "không suy được giá trị" ngay phía trên đã coi SET/INCREASE/DECREASE là hàm ý
        # BẬT thiết bị; nhánh này thì quên, nên cùng một câu lệnh lại cho kết quả khác nhau chỉ
        # vì khoá tham số có nằm trong `accepted_input_keys` hay không. Hệ quả thật: "bật loa
        # bếp rồi đặt âm lượng 15" chỉ đặt volume trên chiếc loa ĐANG TẮT — người dùng không
        # nghe thấy gì. Đặt một mức cụ thể chỉ có nghĩa khi thiết bị đang chạy.
        resolved = {"power": "on", **resolved}
    return resolved


def registry_integrity_errors() -> list[str]:
    errors: list[str] = []
    if len(ACTION_BY_NAME) != len(ACTION_SPECS):
        errors.append("duplicate action name")
    semantic_keys: set[tuple[Capability, ActionType]] = set()
    for spec in ACTION_SPECS:
        if spec.parameter is not None:
            bounds = spec.parameter.bounds_for()
            if bounds is not None and bounds[0] > bounds[1]:
                errors.append(f"{spec.name}: invalid range")
            if spec.parameter.kind == "choice" and not spec.parameter.choices:
                errors.append(f"{spec.name}: empty choices")
            if spec.state_key is not None and not spec.parameter.name:
                errors.append(f"{spec.name}: state key without parameter")
        for semantic in spec.semantic_actions:
            key = (spec.capability, semantic)
            if key in semantic_keys:
                errors.append(f"duplicate semantic mapping: {spec.capability.value}/{semantic.value}")
            semantic_keys.add(key)
    return errors


def _display_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
