"""Máy trạng thái cho thiết bị ảo.

Tách riêng khỏi bus và viết dưới dạng hàm thuần (state vào → state ra) để test
được mà không cần dựng hạ tầng nào. Cả in-memory bus lẫn MQTT bus đều gọi vào đây.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from src.core.errors import DeviceError
from src.domain.action_registry import (
    ACTION_CLOSE,
    ACTION_LOCK,
    ACTION_MEDIA_NEXT,
    ACTION_MEDIA_PAUSE,
    ACTION_MEDIA_PLAY,
    ACTION_MEDIA_PREVIOUS,
    ACTION_OPEN,
    ACTION_SET_AWAY,
    ACTION_SET_BRIGHTNESS,
    ACTION_SET_COLOR,
    ACTION_SET_COLOR_TEMP,
    ACTION_SET_FAN_SPEED,
    ACTION_SET_HVAC_MODE,
    ACTION_SET_OPERATION_MODE,
    ACTION_SET_OSCILLATE,
    ACTION_SET_POSITION,
    ACTION_SET_PRESET,
    ACTION_SET_PROGRAM,
    ACTION_SET_SOURCE,
    ACTION_SET_TEMPERATURE,
    ACTION_SET_VOLUME,
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    ACTION_UNLOCK,
    ACTION_VACUUM_LOCATE,
    ACTION_VACUUM_PAUSE,
    ACTION_VACUUM_RETURN,
    ACTION_VACUUM_SPOT,
    ACTION_VACUUM_START,
    ACTION_VACUUM_STOP,
    ALL_ACTIONS,
    capability_for_action,
    choice_labels,
    parameter_bounds,
    validate_action,
)
from src.domain.enums import DeviceType, SensorType
from src.iot.registry import DeviceSpec

# Choice labels are derived from the domain registry; handlers keep only state mutation.
_HVAC_MODES = choice_labels(ACTION_SET_HVAC_MODE)
_OPERATION_MODES = choice_labels(ACTION_SET_OPERATION_MODE)
_FAN_PRESETS = choice_labels(ACTION_SET_PRESET)
_MEDIA_SOURCES = choice_labels(ACTION_SET_SOURCE)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    state: dict
    detail: str


def _clamp(spec: DeviceSpec, action: str, value: float) -> float:
    bounds = parameter_bounds(action, spec.device_type)
    if bounds is None:
        return value
    low, high = bounds
    return max(low, min(high, value))


def _require_number(action: str, params: dict, key: str) -> float:
    raw = params.get(key)
    if raw is None:
        raise DeviceError(f"Lệnh '{action}' thiếu tham số '{key}'.")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise DeviceError(f"Giá trị '{key}' không hợp lệ: {raw!r}.") from exc


def _require_choice(action: str, params: dict, key: str, allowed: dict[str, str]) -> str:
    """Ép tham số dạng lựa chọn về một giá trị hợp lệ (không phân biệt hoa thường)."""
    raw = str(params.get(key, "")).strip().lower()
    if raw not in allowed:
        raise DeviceError(f"Giá trị '{key}' không hợp lệ: {params.get(key)!r}. Chọn: {', '.join(allowed)}.")
    return raw


def _as_bool(params: dict, key: str) -> bool:
    value = params.get(key, True)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes", "bật", "có"}
    return bool(value)


# --------------------------------------------------------------------------
# Handler cho các thiết bị nhiều trạng thái. Mỗi hàm sửa ``state`` tại chỗ và
# trả về câu mô tả tiếng Việt; kiểm tra capability đã làm ở apply_command.
# --------------------------------------------------------------------------
def _h_color_temp(spec, state, params) -> str:
    value = int(_clamp(spec, ACTION_SET_COLOR_TEMP, _require_number(ACTION_SET_COLOR_TEMP, params, "color_temp")))
    state["color_temp"] = value
    state["power"] = "on"
    return f"Đặt nhiệt độ màu {spec.name} = {value}K."


def _h_color(spec, state, params) -> str:
    color = str(params.get("color", "")).strip()
    if not (color.startswith("#") and len(color) in (4, 7)):
        raise DeviceError(f"Mã màu không hợp lệ: {params.get('color')!r}. Dạng đúng: '#RRGGBB'.")
    state["color"] = color
    state["power"] = "on"
    return f"Đặt màu {spec.name} = {color}."


def _h_hvac_mode(spec, state, params) -> str:
    mode = _require_choice(ACTION_SET_HVAC_MODE, params, "mode", _HVAC_MODES)
    state["hvac_mode"] = mode
    state["power"] = "off" if mode == "off" else "on"
    return f"Đặt điều hoà {spec.name} sang chế độ {_HVAC_MODES[mode]}."


def _h_operation_mode(spec, state, params) -> str:
    mode = _require_choice(ACTION_SET_OPERATION_MODE, params, "mode", _OPERATION_MODES)
    state["operation_mode"] = mode
    state["power"] = "off" if mode == "off" else "on"
    return f"Đặt {spec.name} sang chế độ {_OPERATION_MODES[mode]}."


def _h_away(spec, state, params) -> str:
    away = _as_bool(params, "away")
    state["away"] = away
    return f"{'Bật' if away else 'Tắt'} chế độ vắng nhà cho {spec.name}."


def _h_media_play(spec, state, _params) -> str:
    state["playback"] = "playing"
    state["power"] = "on"
    return f"{spec.name} đang phát."


def _h_media_pause(spec, state, _params) -> str:
    state["playback"] = "paused"
    return f"Đã tạm dừng {spec.name}."


def _h_media_next(spec, _state, _params) -> str:
    return f"{spec.name} chuyển bài tiếp theo."


def _h_media_previous(spec, _state, _params) -> str:
    return f"{spec.name} quay lại bài trước."


def _h_source(spec, state, params) -> str:
    source = _require_choice(ACTION_SET_SOURCE, params, "source", _MEDIA_SOURCES)
    state["source"] = source
    state["power"] = "on"
    return f"Chuyển {spec.name} sang nguồn {_MEDIA_SOURCES[source]}."


def _h_preset(spec, state, params) -> str:
    preset = _require_choice(ACTION_SET_PRESET, params, "preset", _FAN_PRESETS)
    state["preset"] = preset
    state["power"] = "on"
    return f"Đặt {spec.name} sang chế độ {_FAN_PRESETS[preset]}."


def _h_oscillate(spec, state, params) -> str:
    osc = _as_bool(params, "oscillate")
    state["oscillate"] = osc
    return f"{'Bật' if osc else 'Tắt'} đảo chiều cho {spec.name}."


def _vacuum_handler(activity: str, power: str | None, detail: str):
    def handler(spec, state, _params) -> str:
        state["activity"] = activity
        if power is not None:
            state["power"] = power
        return f"{spec.name}: {detail}."

    return handler


def _h_program(spec, state, params) -> str:
    program = str(params.get("program", "")).strip()
    if not program:
        raise DeviceError(f"Lệnh '{ACTION_SET_PROGRAM}' thiếu tham số 'program'.")
    state["program"] = program
    state["power"] = "on"
    return f"Đặt chương trình {spec.name} = {program}."


_EXTENDED_HANDLERS = {
    ACTION_SET_COLOR_TEMP: _h_color_temp,
    ACTION_SET_COLOR: _h_color,
    ACTION_SET_HVAC_MODE: _h_hvac_mode,
    ACTION_SET_OPERATION_MODE: _h_operation_mode,
    ACTION_SET_AWAY: _h_away,
    ACTION_MEDIA_PLAY: _h_media_play,
    ACTION_MEDIA_PAUSE: _h_media_pause,
    ACTION_MEDIA_NEXT: _h_media_next,
    ACTION_MEDIA_PREVIOUS: _h_media_previous,
    ACTION_SET_SOURCE: _h_source,
    ACTION_SET_PRESET: _h_preset,
    ACTION_SET_OSCILLATE: _h_oscillate,
    ACTION_VACUUM_START: _vacuum_handler("cleaning", "on", "bắt đầu dọn dẹp"),
    ACTION_VACUUM_PAUSE: _vacuum_handler("paused", None, "tạm dừng"),
    ACTION_VACUUM_STOP: _vacuum_handler("idle", "off", "dừng lại"),
    ACTION_VACUUM_RETURN: _vacuum_handler("returning", "on", "đang về dock sạc"),
    ACTION_VACUUM_SPOT: _vacuum_handler("spot", "on", "dọn điểm"),
    ACTION_VACUUM_LOCATE: _vacuum_handler("locating", None, "phát tín hiệu định vị"),
    ACTION_SET_PROGRAM: _h_program,
}


def apply_command(spec: DeviceSpec, current: dict, action: str, params: dict | None = None) -> SimulationResult:
    """Áp một lệnh lên trạng thái thiết bị, trả về trạng thái mới.

    Raise ``DeviceError`` nếu hành động không tồn tại hoặc thiết bị không hỗ trợ —
    lỗi này được ghi vào log lịch sử thay vì làm sập request.
    """
    validation = validate_action(
        action,
        params or {},
        capabilities=spec.capabilities,
        device_type=spec.device_type,
    )
    if not validation.ok:
        assert validation.issue is not None
        raise DeviceError(validation.issue.message_vi)
    params = validation.params
    required = capability_for_action(action)
    assert action in ALL_ACTIONS and required is not None

    state = dict(current)

    # Thiết bị nhiều trạng thái đi qua bảng handler riêng cho gọn
    handler = _EXTENDED_HANDLERS.get(action)
    if handler is not None:
        detail = handler(spec, state, params)
        return SimulationResult(state=state, detail=detail)

    if action == ACTION_TURN_ON:
        state["power"] = "on"
        if spec.device_type == DeviceType.AIR_CONDITIONER:
            if state.get("hvac_mode") == "off" or "hvac_mode" not in state:
                state["hvac_mode"] = "cool"
        elif spec.device_type == DeviceType.CAMERA:
            state["recording"] = True
        detail = f"Đã bật {spec.name}."
    elif action == ACTION_TURN_OFF:
        state["power"] = "off"
        if spec.device_type == DeviceType.AIR_CONDITIONER:
            state["hvac_mode"] = "off"
        elif spec.device_type == DeviceType.CAMERA:
            state["recording"] = False
        detail = f"Đã tắt {spec.name}."
    elif action == ACTION_SET_BRIGHTNESS:
        value = _clamp(spec, action, _require_number(action, params, "brightness"))
        state["brightness"] = int(value)
        # Chỉnh độ sáng ngầm hiểu là bật đèn lên
        state["power"] = "off" if value == 0 else "on"
        detail = f"Đặt độ sáng {spec.name} = {int(value)}%."
    elif action == ACTION_SET_TEMPERATURE:
        value = _clamp(spec, action, _require_number(action, params, "temperature"))
        state["temperature"] = int(value)
        state["power"] = "on"
        if spec.device_type == DeviceType.AIR_CONDITIONER:
            if state.get("hvac_mode") == "off" or "hvac_mode" not in state:
                state["hvac_mode"] = "cool"
        detail = f"Đặt nhiệt độ {spec.name} = {int(value)}°C."
    elif action == ACTION_SET_FAN_SPEED:
        value = _clamp(spec, action, _require_number(action, params, "fan_speed"))
        state["fan_speed"] = int(value)
        state["power"] = "off" if value == 0 else "on"
        if spec.device_type == DeviceType.AIR_CONDITIONER and state["power"] == "on":
            if state.get("hvac_mode") == "off" or "hvac_mode" not in state:
                state["hvac_mode"] = "cool"
        detail = f"Đặt tốc độ gió {spec.name} = mức {int(value)}."
    elif action == ACTION_SET_VOLUME:
        value = _clamp(spec, action, _require_number(action, params, "volume"))
        state["volume"] = int(value)
        detail = f"Đặt âm lượng {spec.name} = {int(value)}%."
    elif action in (ACTION_SET_POSITION, ACTION_OPEN, ACTION_CLOSE):
        if action == ACTION_OPEN:
            value = 100.0
        elif action == ACTION_CLOSE:
            value = 0.0
        else:
            value = _clamp(spec, action, _require_number(action, params, "position"))
        state["position"] = int(value)
        detail = f"Đặt {spec.name} ở mức mở {int(value)}%."
    elif action == ACTION_LOCK:
        state["locked"] = True
        detail = f"Đã khoá {spec.name}."
    elif action == ACTION_UNLOCK:
        state["locked"] = False
        detail = f"Đã mở khóa {spec.name}."
    else:  # Registry action without a state handler is a programmer error.
        raise DeviceError(f"Chưa có state handler cho hành động '{action}'.")

    return SimulationResult(state=state, detail=detail)


# --------------------------------------------------------------------------
# Cảm biến môi trường
# --------------------------------------------------------------------------
def drift_sensor(sensor_type: str, value: float, *, rng: random.Random | None = None) -> float:
    """Cho giá trị cảm biến dao động nhẹ để automation có dữ liệu thay đổi theo thời gian."""
    rng = rng or random.Random()
    if sensor_type == SensorType.PM25:
        return max(5.0, min(200.0, value + rng.uniform(-4, 5)))
    if sensor_type == SensorType.TEMPERATURE:
        return max(10.0, min(45.0, value + rng.uniform(-0.5, 0.5)))
    if sensor_type == SensorType.HUMIDITY:
        return max(20.0, min(100.0, value + rng.uniform(-2, 2)))
    if sensor_type == SensorType.SUNLIGHT:
        return max(0.0, min(100.0, value + rng.uniform(-6, 6)))
    # Mưa và hiện diện là nhị phân, chỉ đổi khi có tác động từ bên ngoài
    return value
