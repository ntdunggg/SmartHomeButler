"""Nhận diện + trả lời câu hỏi THỜI TIẾT / CHẤT LƯỢNG KHÔNG KHÍ ngoài trời.

Khác `state_query` (đọc cảm biến trong nhà từ snapshot sống), module này lo câu hỏi về
thời tiết/AQI ngoài trời cho MỘT vị trí — mặc định là toạ độ nhà trong Settings, hoặc một
thành phố người dùng nêu tên. Việc gọi Open-Meteo live + geocoding nằm ở
`src.agent.tools.environment`; ở đây chỉ có phần TẤT ĐỊNH: nhận diện câu hỏi, tách tên
thành phố, và diễn đạt câu trả lời tiếng Việt từ `EnvironmentSnapshot`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from src.agent.text import Pattern, TextView
from src.agent.tools.environment import EnvironmentSnapshot
from src.nlu.normalizer import NormalizedUtterance

Dimension = Literal["summary", "temperature", "rain", "aqi", "humidity", "wind", "uv", "sunlight"]

# Câu MANG chủ đề thời tiết/khí hậu ngoài trời. "trời"/"ngoài trời" một mình chưa đủ (dễ va
# "ngoài trời sáng quá" = than phiền môi trường trong phòng), nên cần kèm cue hỏi hoặc từ
# khí tượng. "dự báo" luôn tính là hỏi thời tiết hiện tại (không làm dự báo nhiều ngày).
_WEATHER_TOPIC = Pattern(
    r"\b(thời tiết|dự báo(?: thời tiết)?|khí hậu|thời tiết ngoài trời)\b"
)
_OUTDOOR_WEATHER_CUE = Pattern(
    r"\b(ngoài trời|ngoài đường)\b.*\b(nóng|lạnh|mát|nắng|mưa|gió|ẩm|thế nào|ra sao|bao nhiêu độ)\b"
    r"|\btrời\b.*\b(nắng|mưa|gió|quang|âm u|nồm|thế nào|ra sao)\b"
    r"|\b(có mưa|đang mưa|sắp mưa|trời mưa)\b"
)
_AQI_TOPIC = Pattern(
    r"\b(aqi|chất lượng không khí|chỉ số không khí|ô nhiễm không khí|pm ?2\.?5|bụi mịn)\b"
)
_UV_TOPIC = Pattern(r"\b(chỉ số uv|tia uv|tia cực tím)\b")

# Cue chọn CHIỀU cụ thể trong câu (nếu không khớp cái nào → summary).
_DIMENSION_CUES: tuple[tuple[Pattern, Dimension], ...] = (
    (Pattern(r"\b(có mưa|đang mưa|trời mưa|mưa không|sắp mưa)\b"), "rain"),
    (_AQI_TOPIC, "aqi"),
    (_UV_TOPIC, "uv"),
    (Pattern(r"\b(bao nhiêu độ|nhiệt độ|nóng|lạnh|mát)\b"), "temperature"),
    (Pattern(r"\b(độ ẩm|ẩm ướt|nồm)\b"), "humidity"),
    (Pattern(r"\b(gió|tốc độ gió|lộng gió)\b"), "wind"),
    (Pattern(r"\b(nắng|cường độ nắng|bức xạ)\b"), "sunlight"),
)

# Từ đứng sau tên thành phố, đánh dấu HẾT phần tên khi người dùng viết thường ("... ở hà nội thế nào").
_CITY_STOPWORDS = (
    "thế nào", "ra sao", "hôm nay", "bây giờ", "hiện tại", "hiện nay", "lúc này",
    "không", "bao nhiêu", "là bao nhiêu", "thế", "nhỉ", "vậy", "ạ", "đang", "có",
)
_CITY_AFTER_PREP = re.compile(
    r"(?:\bở|(?<!hiện )\btại|\bkhu vực|\bthành phố|\btp\.?)\s+(?P<city>.+?)"
    r"(?:\s+(?:" + "|".join(re.escape(w) for w in _CITY_STOPWORDS) + r")\b|[?.!,]|$)",
    re.IGNORECASE,
)
# Cụm CHỮ HOA giữa câu (không phải từ đầu câu, không phải acronym ngắn) → tên riêng = thành phố.
_PROPER_NOUN = re.compile(r"(?<!^)(?<![.?!]\s)\b([A-ZÀ-Ỹ][\wÀ-ỹ]+(?:\s+[A-ZÀ-Ỹ][\wÀ-ỹ]+){0,3})")
_ACRONYM = re.compile(r"^[A-Z]{2,4}$")
_STOP_PROPER = {"Thời", "Trời", "Dự", "Nhiệt", "Độ", "Chất", "Chỉ", "Không", "Cho", "Ngoài", "Bây", "Hôm"}


@dataclass(frozen=True, slots=True)
class WeatherAsk:
    dimension: Dimension
    city: str | None


def _extract_city(raw: str) -> str | None:
    prep = _CITY_AFTER_PREP.search(raw)
    if prep:
        city = " ".join(prep.group("city").split()).strip(" ,.?!")
        city = re.sub(r"(?i)^(?:thành phố|tp\.?)\s+", "", city).strip()
        # "ở đây" / "ngoài trời" không phải tên thành phố.
        if city.lower() not in {"đây", "đấy", "đó", "ngoài trời", "ngoài đường", "nhà"} and len(city) <= 40:
            return city
    for match in _PROPER_NOUN.finditer(raw):
        candidate = match.group(1).strip()
        head = candidate.split()[0]
        if _ACRONYM.match(head) or head in _STOP_PROPER:
            continue
        return candidate
    return None


def detect(nu: NormalizedUtterance) -> WeatherAsk | None:
    """Câu này có phải hỏi thời tiết/AQI ngoài trời không? None nếu không."""
    if nu.has_negation or nu.has_cancellation or nu.matched_device_ids:
        return None
    view = TextView(raw=nu.normalized, folded=nu.folded)
    is_weather = bool(_WEATHER_TOPIC.search(view) or _OUTDOOR_WEATHER_CUE.search(view))
    is_aqi = bool(_AQI_TOPIC.search(view))
    is_uv = bool(_UV_TOPIC.search(view))
    if not (is_weather or is_aqi or is_uv):
        return None

    dimension: Dimension = "summary"
    for pattern, dim in _DIMENSION_CUES:
        if pattern.search(view):
            dimension = dim
            break
    if dimension == "summary" and (is_aqi and not is_weather):
        dimension = "aqi"
    if dimension == "summary" and (is_uv and not is_weather):
        dimension = "uv"

    return WeatherAsk(dimension=dimension, city=_extract_city(nu.raw))


# --- Diễn đạt ------------------------------------------------------------------------

def aqi_label(us_aqi: float) -> str:
    if us_aqi <= 50:
        return "tốt"
    if us_aqi <= 100:
        return "trung bình"
    if us_aqi <= 150:
        return "kém, nhóm nhạy cảm nên hạn chế ra ngoài"
    if us_aqi <= 200:
        return "xấu"
    if us_aqi <= 300:
        return "rất xấu"
    return "nguy hại"


def _sky_label(code: int) -> str:
    if code == 0:
        return "trời quang"
    if code in (1, 2):
        return "ít mây"
    if code == 3:
        return "nhiều mây"
    if code in (45, 48):
        return "sương mù"
    if 51 <= code <= 57:
        return "mưa phùn"
    if 61 <= code <= 67:
        return "có mưa"
    if 71 <= code <= 77:
        return "có tuyết"
    if 80 <= code <= 82:
        return "mưa rào"
    if 85 <= code <= 86:
        return "mưa tuyết"
    if 95 <= code <= 99:
        return "dông"
    return "nhiều mây"


def _n(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def format_answer(snapshot: EnvironmentSnapshot, ask: WeatherAsk, *, place_label: str) -> str:
    at = f"{place_label}" if place_label else "ngoài trời"
    d = ask.dimension

    if d == "rain":
        return f"{at}: đang mưa." if snapshot.is_raining else f"{at}: hiện không mưa."
    if d == "temperature":
        return (
            f"{at}: {_n(snapshot.temperature_c)}°C "
            f"(cảm giác như {_n(snapshot.apparent_temperature_c)}°C)."
        )
    if d == "humidity":
        return f"{at}: độ ẩm {_n(snapshot.humidity_percent)}%."
    if d == "wind":
        return f"{at}: gió {_n(snapshot.wind_speed_kmh)} km/h."
    if d == "uv":
        return f"{at}: chỉ số UV {_n(snapshot.uv_index)}."
    if d == "sunlight":
        return f"{at}: cường độ nắng {_n(snapshot.sunlight_percent)}%."
    if d == "aqi":
        return (
            f"{at}: AQI {_n(snapshot.us_aqi)} ({aqi_label(snapshot.us_aqi)}), "
            f"PM2.5 {_n(snapshot.pm25_ugm3)} µg/m³."
        )

    return (
        f"{at}: {_n(snapshot.temperature_c)}°C (cảm giác {_n(snapshot.apparent_temperature_c)}°C), "
        f"{_sky_label(snapshot.weather_code)}, độ ẩm {_n(snapshot.humidity_percent)}%, "
        f"gió {_n(snapshot.wind_speed_kmh)} km/h. "
        f"AQI {_n(snapshot.us_aqi)} ({aqi_label(snapshot.us_aqi)}), UV {_n(snapshot.uv_index)}."
    )
