"""Các enum dùng chung cho toàn hệ thống."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Vai trò trong hộ gia đình — đề bài yêu cầu 2 vai trò đăng nhập."""

    OWNER = "owner"  # Chủ hộ
    MEMBER = "member"  # Thành viên


class RiskLevel(StrEnum):
    """Mức rủi ro của thiết bị, quyết định có cần Human-in-the-loop hay không."""

    NORMAL = "normal"  # Đèn, TV, quạt, rèm...
    HIGH_POWER = "high_power"  # Điều hoà, bình nóng lạnh — tốn điện
    SECURITY = "security"  # Khoá cửa, camera — chạm tới an ninh


class AccessEffect(StrEnum):
    """Quyền chủ hộ cấp cho một thành viên với một thiết bị cụ thể.

    Ba mức khi ĐÃ cấp. Không có bản ghi nào cho cặp (thành viên, thiết bị) nghĩa là
    CHƯA CẤP — thành viên không thấy và không dùng được thiết bị. Quyền không còn
    suy ra từ phòng riêng; ``private_room_id`` chỉ giữ làm ngữ cảnh. Xem
    ``src/core/permissions.py``.
    """

    ACCEPTED = "accepted"  # Cho dùng ngay, không cần chủ hộ, im lặng
    ALERT = "alert"  # Cho dùng ngay nhưng gửi thông báo cho chủ hộ
    REQUEST = "request"  # Phải xin — chủ hộ duyệt từng lần (HITL)


class ActionType(StrEnum):
    """Loại hành động agent có thể đề xuất lên một thiết bị.

    Đây là tập đóng: tầng suy luận chỉ được chọn trong danh sách này, không sinh
    chuỗi tự do — chống hallucination tên hành động (GOALS §12 R1)."""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    TOGGLE = "toggle"
    SET = "set"  # đặt tham số cụ thể: nhiệt độ, độ sáng...
    OPEN = "open"  # rèm, cửa sổ
    CLOSE = "close"
    INCREASE = "increase"
    DECREASE = "decrease"
    LOCK = "lock"  # khoá cửa — thiết bị an ninh, chỉ chủ hộ (evaluate quyết)
    UNLOCK = "unlock"  # mở khoá cửa


class ConfidenceStatus(StrEnum):
    """Phân loại độ tin cậy của NLU để quyết định đi thẳng / hỏi lại / bỏ.

    Ngưỡng khớp thiết kế NLU 3 lớp (PLAN §4): đủ chắc thì lập kế hoạch luôn,
    khoảng giữa là mơ hồ cần đúng một câu hỏi làm rõ (clarification rate ≤ 15%,
    GOALS §8), dưới ngưỡng thấp thì không đoán."""

    CONFIDENT = "confident"  # đủ chắc để lập kế hoạch
    AMBIGUOUS = "ambiguous"  # cần một câu hỏi làm rõ
    UNCERTAIN = "uncertain"  # không đủ tin cậy, không đoán bừa

    @classmethod
    def from_score(cls, score: float) -> ConfidenceStatus:
        if score >= 0.88:
            return cls.CONFIDENT
        if score >= 0.5:
            return cls.AMBIGUOUS
        return cls.UNCERTAIN


class ValidationStatus(StrEnum):
    """Trạng thái kiểm duyệt của một ExecutionPlan.

    Kế hoạch do tầng suy luận đề xuất luôn bắt đầu ở PENDING. Chưa được Safety
    Validator (tầng deterministic) phê duyệt thì không có đường nào xuống Executor
    — bất biến S1, "LLM đề xuất, code quyết định"."""

    PENDING = "pending"  # chưa qua validator
    VALIDATED = "validated"  # validator cho phép chạy
    REJECTED = "rejected"  # validator từ chối


class DeviceType(StrEnum):
    LIGHT = "light"
    AIR_CONDITIONER = "air_conditioner"
    TV = "tv"
    CURTAIN = "curtain"
    WINDOW = "window"
    DOOR_LOCK = "door_lock"
    CAMERA = "camera"
    WATER_HEATER = "water_heater"
    AIR_PURIFIER = "air_purifier"
    VACUUM = "vacuum"
    FAN = "fan"
    SPEAKER = "speaker"
    HEATER = "heater"
    DISHWASHER = "dishwasher"
    COFFEE_MACHINE = "coffee_machine"
    WASHING_MACHINE = "washing_machine"
    GARAGE_DOOR = "garage_door"
    RANGE_HOOD = "range_hood"
    MICROWAVE = "microwave"


# `selector.domain` do LLM soạn là TỪ VỰNG MỞ: model quen nói theo giọng Home Assistant
# ("media_player", "climate", "cover") trong khi registry chỉ biết `DeviceType`. Trước đây mỗi
# nơi tự dịch một kiểu — `device_grounder` có bảng alias riêng còn `planning/manager` thì
# không — nên một domain hợp lệ với nơi này lại là "không biết" với nơi kia và bị bỏ IM LẶNG
# (§4: một luật ngữ nghĩa chỉ được có MỘT hiện thực). Bảng dưới đây là nguồn sự thật DUY NHẤT.
#
# Một họ (family) ánh xạ sang NHIỀU device type — đó là mơ hồ THẬT, không phải chi tiết cài đặt:
# người gọi phải tự quyết cách chọn trong tập trả về, không được ngầm lấy phần tử đầu.
_DOMAIN_FAMILIES: dict[str, frozenset[str]] = {
    "media_player": frozenset({DeviceType.TV.value, DeviceType.SPEAKER.value}),
    "media": frozenset({DeviceType.TV.value, DeviceType.SPEAKER.value}),
    "climate": frozenset({DeviceType.AIR_CONDITIONER.value}),
    "cover": frozenset({DeviceType.CURTAIN.value}),
    "lock": frozenset({DeviceType.DOOR_LOCK.value}),
    "purifier": frozenset({DeviceType.AIR_PURIFIER.value}),
}

_DEVICE_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in DeviceType)


def device_types_for_domain(domain: str | None) -> frozenset[str]:
    """`selector.domain` (từ vựng mở của LLM) → tập `DeviceType` của registry.

    Trả về:
      * chính nó, khi domain ĐÚNG là một DeviceType — domain chính xác KHÔNG được nới rộng
        sang anh em cùng họ ("speaker" không được khớp TV);
      * tập của họ, khi domain là bí danh họ ("media_player" → {tv, speaker});
      * tập RỖNG, khi domain ngoài từ vựng — người gọi phải xử lý tường minh chứ không
        được coi như "không có ràng buộc" (sẽ nới sai) hay bỏ qua im lặng (sẽ mất subgoal).
    """
    if not domain:
        return frozenset()
    normalized = str(domain).strip().lower()
    if normalized in _DEVICE_TYPE_VALUES:
        return frozenset({normalized})
    return _DOMAIN_FAMILIES.get(normalized, frozenset())


class Capability(StrEnum):
    """Khả năng của thiết bị — quyết định tool nào gọi được lên thiết bị đó."""

    ON_OFF = "on_off"
    BRIGHTNESS = "brightness"
    TEMPERATURE = "temperature"
    FAN_SPEED = "fan_speed"
    POSITION = "position"  # rèm, cửa sổ: 0-100%
    LOCK = "lock"
    VOLUME = "volume"
    # Bổ sung để đỡ các thiết bị nhiều trạng thái (khớp giao diện frontend)
    COLOR_TEMP = "color_temp"  # đèn: nhiệt độ màu (K)
    COLOR = "color"  # đèn: màu RGB
    HVAC_MODE = "hvac_mode"  # điều hoà: Lạnh/Sưởi/Tự động/Tắt
    OPERATION_MODE = "operation_mode"  # bình nóng lạnh: Eco/Nhanh/Tắt
    AWAY_MODE = "away_mode"  # bình nóng lạnh: chế độ vắng nhà
    MEDIA_CONTROL = "media_control"  # media: phát/tạm dừng/bài trước/bài sau
    MEDIA_SOURCE = "media_source"  # media: nguồn phát
    PRESET_MODE = "preset_mode"  # quạt/máy lọc: Tự động/Đêm/Turbo
    OSCILLATE = "oscillate"  # quạt/máy lọc: đảo chiều
    VACUUM_CONTROL = "vacuum_control"  # robot: bắt đầu/dừng/về dock/dọn điểm/định vị
    PROGRAM = "program"  # máy rửa bát: chương trình


class SensorType(StrEnum):
    PM25 = "pm25"
    AQI = "aqi"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    RAIN = "rain"
    SUNLIGHT = "sunlight"
    WIND_SPEED = "wind_speed"
    UV_INDEX = "uv_index"
    PRESENCE = "presence"


class ActionStatus(StrEnum):
    """Trạng thái một hành động trong log lịch sử."""

    EXECUTED = "executed"
    EXECUTING = "executing"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DENIED = "denied"  # Bị chặn bởi phân quyền
    FAILED = "failed"
    NO_OP = "no_op"
    BLOCKED_BY_CONFLICT = "blocked_by_conflict"
    EXPIRED = "expired"  # Yêu cầu duyệt để quá hạn (không ai duyệt trong thời hạn)


class ConflictType(StrEnum):
    """Các nhóm xung đột agent phải phát hiện."""

    WASTE = "waste"  # Bình nóng lạnh khi cả nhà vắng
    CONTRADICTION = "contradiction"  # Bật điều hoà khi cửa sổ đang mở
    SAFETY = "safety"  # Mở khoá cửa khi không có ai ở nhà
    HABIT_SCHEDULE = "habit_schedule"  # Lệnh đụng thói quen cùng thiết bị cùng giờ


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SuggestionKind(StrEnum):
    """Nguồn gốc của một đề xuất chủ động từ agent."""

    AIR_QUALITY = "air_quality"
    WEATHER = "weather"
    AWAY = "away"
    HABIT = "habit"
