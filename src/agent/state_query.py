"""Trả lời câu hỏi TRẠNG THÁI từ snapshot sống — tất định, không gọi LLM.

"Điều hoà đang bật không?", "nhiệt độ phòng khách bao nhiêu?" là câu HỎI, không phải
lệnh. Trước đây chúng rơi vào đường suy luận mục tiêu (node #2) rồi chết vì không có
hành động nào để đề xuất, hoặc bị đẩy sang RAG node vốn chỉ trả một câu giới thiệu
cứng. Nhưng câu trả lời đã nằm sẵn trong `RuntimeContext`: `devices[].state` và
`sensors[]` đều là snapshot LIVE do backend bơm vào.

Nên: đọc thẳng từ context và diễn đạt lại. Không LLM, không đoán — chỉ báo cáo cái
quan sát được. Module thuần, test được offline.
"""

from __future__ import annotations

import re

from src.agent.text import Pattern, TextView
from src.nlu.normalizer import NormalizedUtterance
from src.nlu.schemas import Device, RuntimeContext, SensorReading
from src.nlu.understanding import mentioned_device_types

# Từ khoá cảm biến → loại cảm biến cần đọc. PHẢI khớp theo RANH GIỚI TỪ (`\b`): bỏ dấu
# xong, "ẩm" thành "am" nằm lọt trong "làm", "nắng" thành "nang" nằm trong "chẳng"...
# Khớp chuỗi con sẽ biến "hệ thống làm được gì?" thành câu hỏi độ ẩm (cùng lớp va chạm
# fold đã gặp ở normalizer §2026-08-08). `Pattern` tự sinh bản không dấu từ chính pattern.
_SENSOR_CUES: tuple[tuple[Pattern, str], ...] = (
    (Pattern(r"\b(bụi|pm2\.?5|không khí)\b"), "pm25"),
    (Pattern(r"\b(aqi|chất lượng không khí)\b"), "aqi"),
    (Pattern(r"\b(nhiệt độ|nóng|lạnh|mát)\b"), "temperature"),
    (Pattern(r"\b(độ ẩm|ẩm)\b"), "humidity"),
    (Pattern(r"\b(nắng)\b"), "sunlight"),
    (Pattern(r"\b(gió|tốc độ gió)\b"), "wind_speed"),
    (Pattern(r"\b(uv|tia cực tím)\b"), "uv_index"),
    (Pattern(r"\b(có ai|có người|hiện diện|vắng nhà)\b"), "presence"),
)

# Khoá trạng thái số → (nhãn tiếng Việt, đơn vị).
_NUMERIC_LABELS: dict[str, tuple[str, str]] = {
    "temperature": ("nhiệt độ", "°C"),
    "brightness": ("độ sáng", "%"),
    "fan_speed": ("tốc độ quạt", ""),
    "volume": ("âm lượng", "%"),
    "battery": ("pin", "%"),
}

# Câu hỏi NHÓM: "cái nào", "những gì", "bao nhiêu", "liệt kê"... Tách khỏi từ chỉ trạng thái
# hoạt động vì thứ tự hai vế không cố định trong tiếng Việt ("đèn nào đang bật" nhưng cũng
# "đang bật những gì"); ghép cứng theo thứ tự sẽ bỏ sót nửa số cách hỏi.
_GROUP_CUES = Pattern(r"\b(nào|những|bao nhiêu|mấy|số lượng|liệt kê|danh sách|tất cả|toàn bộ)\b")
_ACTIVE_CUES = Pattern(r"\b(bật|chạy|hoạt động)\b")

# Phạm vi CẢ NHÀ nêu tường minh. Câu hỏi kiểu này không được thu hẹp im lặng về phòng người
# nói: đếm thiếu thiết bị là trả lời SAI, không phải trả lời hẹp.
_HOME_SCOPE_CUES = Pattern(r"\b(cả nhà|trong nhà|toàn nhà|khắp nhà|cả căn nhà|nhà mình)\b")

# Danh từ CHUNG chỉ thiết bị — không khớp alias hay loại thiết bị nào, nên trước bản vá câu
# "có bao nhiêu thiết bị đang bật?" rơi khỏi cổng state-query và bị trả lời như câu hỏi năng lực.
_GENERIC_DEVICE_CUES = Pattern(r"\b(thiết bị|đồ điện|máy móc)\b")


def _view(nu: NormalizedUtterance) -> TextView:
    return TextView(raw=nu.normalized, folded=nu.folded)


def asks_about_home_state(nu: NormalizedUtterance) -> bool:
    """Câu hỏi nhắm vào trạng thái CẢ NHÀ / thiết bị nói chung, không nêu tên thiết bị.

    Cổng tất định của pipeline trước đây chỉ nhận câu hỏi có alias thiết bị, phòng, loại thiết
    bị hoặc cảm biến. "Trong nhà có bao nhiêu thiết bị đang bật?" không khớp cái nào trong số
    đó, dù câu trả lời nằm sẵn trong snapshot sống — nên phải nhận diện thêm bằng phạm vi
    "cả nhà" hoặc danh từ chung "thiết bị"."""
    view = _view(nu)
    return bool(_HOME_SCOPE_CUES.search(view) or _GENERIC_DEVICE_CUES.search(view))


def wants_home_scope(nu: NormalizedUtterance) -> bool:
    """Phạm vi trả lời có phải TOÀN NHÀ không?

    Đúng khi câu nêu thẳng "cả nhà", hoặc hỏi "thiết bị" nói chung mà KHÔNG nêu phòng nào —
    khi đó phòng người nói chỉ là nơi họ đang đứng, không phải phạm vi họ hỏi."""
    view = _view(nu)
    if _HOME_SCOPE_CUES.search(view):
        return True
    return bool(_GENERIC_DEVICE_CUES.search(view)) and not nu.matched_rooms


def _is_active_group_query(view: TextView) -> bool:
    return bool(_GROUP_CUES.search(view) and _ACTIVE_CUES.search(view))


def resolve_state_query_devices(
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    *,
    prior_device_ids: list[str] | tuple[str, ...] = (),
) -> list[Device]:
    """Resolve the device scope of a read-only state query.

    Explicit aliases win.  A bare device type then reuses the same semantic
    vocabulary as command understanding and is narrowed by explicit room,
    compatible devices from the conversation ledger, or current room in that
    order.  The operation is read-only, so returning every remaining device of
    that type is safe and matches questions such as "điều hoà nào đang bật?".
    """
    explicit = [d for d in ctx.devices if d.device_id in set(nu.matched_device_ids)]
    if explicit:
        return explicit

    types = {dtype.value for dtype in mentioned_device_types(nu)}
    if not types:
        return []
    candidates = [d for d in ctx.devices if d.device_type in types]
    if nu.matched_rooms:
        return [d for d in candidates if d.room in set(nu.matched_rooms)]

    # Phạm vi "cả nhà" là TƯỜNG MINH: không được thu hẹp về ledger hay phòng người nói.
    if wants_home_scope(nu):
        return candidates

    prior = set(prior_device_ids)
    carried = [d for d in candidates if d.device_id in prior]
    if carried:
        return carried

    room = ctx.speaker_location or ctx.focus_room
    local = [d for d in candidates if d.room == room] if room else []
    return local or candidates


def _group_label(devices: list[Device]) -> str:
    """Danh xưng cho cả nhóm. Chỉ mượn tên thiết bị khi cả nhóm CÙNG một tên ("điều hoà" ở ba
    phòng); nhóm pha trộn thì tên của phần tử đầu là nhãn sai — bảy bóng đèn khác nhau không
    phải "bảy đèn chùm"."""
    names = {d.name.lower() for d in devices}
    return names.pop() if len(names) == 1 else "thiết bị"


def _summarize_active(
    devices: list[Device],
    *,
    label: str = "thiết bị",
    where: str = "",
    include_room: bool | None = None,
) -> str:
    """Báo cáo nhóm: thiết bị NÀO đang bật, SỐ LƯỢNG trên tổng, và trạng thái từng cái.

    Số lượng là một phần của câu trả lời chứ không phải trang trí: "có bao nhiêu thiết bị đang
    bật" hỏi thẳng con số, và ngay cả câu hỏi liệt kê cũng cần mẫu số để biết bản báo cáo phủ
    bao nhiêu thiết bị — một danh sách trần không phân biệt được "nhà chỉ có ngần ấy" với
    "phạm vi đã bị thu hẹp".

    Rèm đang mở và khoá đang mở KHÔNG được gộp vào con số "đang bật": chúng là trạng thái đáng
    báo cáo nhưng không phải thiết bị có điện đang chạy, và đếm gộp sẽ khai khống mức tiêu thụ
    lẫn số máy đang hoạt động. Chúng đi thành một mục riêng."""
    total = len(devices)
    if include_room is None:
        include_room = len({d.room for d in devices}) > 1

    def lines(group: list[Device]) -> str:
        return "\n".join(_describe_device(d, include_room=include_room) for d in group)

    powered = [d for d in devices if (d.state or {}).get("power") == "on"]
    powered_ids = {d.device_id for d in powered}
    opened = [d for d in devices if d.device_id not in powered_ids and _is_active(d)]

    if not powered and not opened:
        return f"Hiện không có {label} nào đang bật{where} (0/{total} {label})."
    # Phạm vi rút về đúng một thiết bị thì phân số "1/1" là nhiễu — trả lời thẳng trạng thái.
    if total == 1:
        return lines(powered or opened)
    if not powered:
        return f"Không có {label} nào đang bật{where}; {len(opened)}/{total} đang mở:\n" + lines(opened)
    report = f"Đang bật {len(powered)}/{total} {label}{where}:\n" + lines(powered)
    if opened:
        report += f"\nNgoài ra {len(opened)} thiết bị đang mở:\n" + lines(opened)
    return report


def _device_name(dev: Device, *, include_room: bool) -> str:
    return f"{dev.name} ({dev.room})" if include_room else dev.name


def _describe_device(dev: Device, *, include_room: bool = False) -> str:
    """Một câu ngắn mô tả trạng thái hiện tại của thiết bị."""
    state = dev.state or {}
    name = _device_name(dev, include_room=include_room)

    if "locked" in state:
        return f"{name} đang {'khoá' if state['locked'] else 'mở khoá'}."

    if "position" in state:
        pos = state["position"]
        if pos == 0:
            return f"{name} đang đóng."
        if pos == 100:
            return f"{name} đang mở hoàn toàn."
        return f"{name} đang mở {pos}%."

    power = state.get("power")
    if power is None:
        return f"{name}: chưa có dữ liệu trạng thái."
    if power != "on":
        return f"{name} đang tắt."

    # Đang bật → kèm các thông số có ý nghĩa.
    details = [
        f"{label} {state[key]}{unit}" for key, (label, unit) in _NUMERIC_LABELS.items() if state.get(key) is not None
    ]
    if state.get("recording"):
        details.append("đang ghi hình")
    return f"{name} đang bật" + (f", {', '.join(details)}." if details else ".")


def _describe_temperature(dev: Device, *, include_room: bool) -> str:
    """Describe the AC/device setpoint without claiming it is an ambient sensor."""
    state = dev.state or {}
    value = state.get("temperature")
    name = _device_name(dev, include_room=include_room)
    if value is None:
        return f"{name}: chưa có dữ liệu nhiệt độ cài đặt."
    power = state.get("power")
    status = ""
    if power is not None:
        status = f", đang {'bật' if power == 'on' else 'tắt'}"
    return f"{name}: nhiệt độ cài đặt {value}°C{status}."


def _describe_sensor(s: SensorReading) -> str:
    if s.sensor_type == "presence":
        return f"{s.room or 'khu vực này'}: {'có người' if s.value >= 1 else 'không có ai'}."
    if s.sensor_type == "rain":
        return "Ngoài trời đang mưa." if s.value > 0 else "Ngoài trời không mưa."
    value = int(s.value) if float(s.value).is_integer() else round(s.value, 1)
    return f"{s.name}: {value}{s.unit}."


def matched_sensor_types(nu: NormalizedUtterance) -> list[str]:
    view = TextView(raw=nu.normalized, folded=nu.folded)
    matched = [stype for pattern, stype in _SENSOR_CUES if pattern.search(view)]
    # "mưa" bỏ dấu thành "mua", trùng hoàn toàn động từ mua hàng. Chỉ chấp nhận "mua"
    # không dấu khi nó nằm trong một cụm thời tiết; bản có dấu "mưa" vẫn khớp độc lập.
    accented_rain = bool(re.search(r"\bmưa\b", view.raw))
    folded_weather_rain = bool(re.search(r"\b(?:troi|dang|co|bao) mua\b|\bmua (?:khong|chua|roi)\b", view.folded))
    if accented_rain or folded_weather_rain:
        matched.append("rain")
    return list(dict.fromkeys(matched))


def answer_state_query(
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    *,
    devices: list[Device] | None = None,
) -> str:
    """Soạn câu trả lời cho một câu hỏi trạng thái. Rỗng = không trả lời được.

    Ưu tiên thu hẹp theo đúng thứ tự người dùng nêu: thiết bị cụ thể > cảm biến được
    hỏi > phòng nêu trong câu > phòng người nói đang đứng."""
    lines: list[str] = []

    # (1) Thiết bị nêu đích danh hoặc loại thiết bị đã được pipeline ground theo ledger/phòng.
    named = resolve_state_query_devices(nu, ctx) if devices is None else list(devices)
    if named:
        include_room = len(named) > 1
        view = _view(nu)
        if _is_active_group_query(view):
            return _summarize_active(named, label=_group_label(named), include_room=include_room)

        stypes = matched_sensor_types(nu)
        # Khi câu nêu cả thiết bị và "nhiệt độ", state thiết bị là bằng chứng gần hơn cảm biến
        # môi trường. Registry hiện lưu setpoint ở `temperature`, nên diễn đạt đúng là nhiệt độ
        # cài đặt, không gọi nhầm thành nhiệt độ phòng/ngoài trời.
        if "temperature" in stypes:
            return "\n".join(_describe_temperature(d, include_room=include_room) for d in named)

        return "\n".join(_describe_device(d, include_room=include_room) for d in named)

    # (2) Cảm biến được hỏi tới — scope theo phòng nếu câu/vị trí có nêu.
    stypes = matched_sensor_types(nu)
    if stypes:
        sensor_room = nu.matched_rooms[0] if nu.matched_rooms else ctx.speaker_location
        for stype in stypes:
            hits = [s for s in ctx.sensors if s.sensor_type == stype]
            # Cảm biến gắn phòng thì lọc theo phòng; cảm biến ngoài trời (room rỗng) giữ nguyên.
            scoped = [s for s in hits if s.room == sensor_room] or [s for s in hits if not s.room] or hits
            lines.extend(_describe_sensor(s) for s in scoped)
        if lines:
            return "\n".join(lines)

    # (3) Không nêu thiết bị/cảm biến cụ thể → báo cáo thiết bị ĐANG BẬT trong phạm vi đang xét.
    #     Phòng người nói chỉ là mặc định khi câu KHÔNG tự nêu phạm vi; hỏi "cả nhà" mà đáp theo
    #     một phòng thì con số và danh sách đều sai, nên phạm vi tường minh phải thắng.
    if nu.matched_rooms:
        room = nu.matched_rooms[0]
    elif wants_home_scope(nu):
        room = None
    else:
        room = ctx.speaker_location or ctx.focus_room
    scope = [d for d in ctx.devices if d.room == room] if room else list(ctx.devices)
    if not scope:
        return ""
    return _summarize_active(scope, where=f" ở {room}" if room else " trong nhà")


def _is_active(dev: Device) -> bool:
    state = dev.state or {}
    if state.get("power") == "on":
        return True
    # Rèm/cửa mở và khoá đang mở cũng là trạng thái "đang tác động", đáng báo cáo.
    return bool(state.get("position")) or state.get("locked") is False
