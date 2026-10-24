"""Hiểu ngôn ngữ ở tầng **tất định** (deterministic baseline).

Kiến trúc open-ended: việc *diễn giải mục tiêu tự do* (paraphrase không keyword, yêu
cầu môi trường gián tiếp, routine chưa từng thấy) là việc của LLM — được author ở
node `llm_build_semantic_goal` trong graph qua `ReasoningModel.structured_generate`.

Module này CHỈ lo phần tất định, an toàn, test được offline:
- Tín hiệu chuẩn hoá (phủ định / sửa lời / huỷ / tham chiếu / thiết bị-phòng khớp).
- Điều khiển tường minh: có động từ (bật/tắt/đặt/tăng/giảm) + thiết bị đã khớp →
  dựng `SemanticGoal` với `action_hint` + target rõ ràng (đây là parse lệnh tường
  minh, KHÔNG phải ánh xạ intent→plan hay template).
- Điều khiển hội thoại: huỷ / xác nhận / từ chối / xã giao / câu hỏi.

Mọi thứ còn lại (không thiết bị + không động từ, hoặc paraphrase) → trả `goal=None`
với `note` để node LLM tiếp quản. KHÔNG đoán target.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from src.agent.text import Pattern, TextView
from src.context.state_filter import reduce_by_state
from src.domain.enums import Capability, DeviceType
from src.iot.registry import DEVICE_BY_SLUG
from src.nlu.direction import direction_of
from src.nlu.exclusion_clause import split_exclusion_clause
from src.nlu.normalizer import Ambiguity, NormalizedUtterance, analyze
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import IntentCandidate, RuntimeContext, SemanticGoal
from src.planning.grounding import devices_by

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model client (Protocol giữ lại cho typing ở graph; authoring thật đi qua
# ReasoningModel.structured_generate chứ không qua đây nữa).
# ---------------------------------------------------------------------------
class ModelClient(Protocol):
    def understand(self, *, utterance: str, context: dict[str, Any]) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Bảng luật tiếng Việt — CHỈ nhận diện điều khiển tường minh + hội thoại.
# KHÔNG có bảng routine/environment (đó là việc của LLM).
# ---------------------------------------------------------------------------
_CONFIRM = Pattern(r"^(ừ|u|ừm|um|ok|okê|oke|đồng ý|dong y|được|duoc|vâng|vang|có|co|đúng|dung)\b")
_REJECT = Pattern(r"^(không|khong|thôi|thoi|không phải|khong phai|sai rồi|sai roi)\b")
_NOT_REJECT = Pattern(r"^(không khí|khong khi|không gian|khong gian|không sao|khong sao)")
_QUESTION = Pattern(
    r"(\?|\bcó .* không\b|\bco .* khong\b|\bbao nhiêu\b|\bbao nhieu\b|\bcòn .* không\b|\bcon .* khong\b|\bthế nào\b|\bthe nao\b|\bra sao\b|\bthì sao\b|\bthi sao\b|\bđang .* không\b|\bdang .* khong\b)"
)
# Khung HỎI TRẠNG THÁI: "điều hoà đang bật không?", "đèn còn sáng không?", "nhiệt độ bao
# nhiêu?". Động từ trong đó ("bật", "sáng") mô tả trạng thái đang được hỏi, KHÔNG phải mệnh
# lệnh — nên khung này phải được xét TRƯỚC nhánh lệnh tường minh, nếu không mọi câu hỏi có
# động từ đều bị thực thi như lệnh.
# "chưa" Ở CUỐI (kèm tiểu từ nhỉ/nhé/ạ...) là khung hỏi yes/no về trạng thái: "cửa đã khoá
# chưa?", "điều hoà bật chưa nhỉ", "không biết đèn tắt chưa". Động từ trong đó ("khoá",
# "bật") mô tả trạng thái ĐANG HỎI, không phải mệnh lệnh — nên khung này (như các khung khác)
# xét TRƯỚC nhánh lệnh, nếu không một câu hỏi bị thực thi thành hành động (§2026-08-10 ind2).
# CHỈ khớp khi "chưa" ở CUỐI câu: "đèn chưa sáng" (chưa trước động từ, giữa câu) là câu trần
# thuật/mục tiêu ngầm → KHÔNG dính, vẫn để LLM diễn giải.
# Đuôi nghi vấn CÓ/KHÔNG ("đang bật không?", "còn pin không?"): "không" phải đứng CUỐI (trước
# dấu/particle), KHÔNG đi kèm ĐỘNG TỪ ngay sau. "không đổi/không tắt/không bật" là MỆNH ĐỀ PHỦ
# ĐỊNH trong một lệnh ("bật 24 độ nhưng KHÔNG ĐỔI nhiệt độ"), không phải câu hỏi — nếu không loại,
# "phòng con"(fold→"con") va "còn" + "không đổi" biến cả lệnh thành hỏi trạng thái (NC-004).
_Q_TAIL_NEG = r"không\b(?!\s*(?:đổi|thay đổi|bật|tắt|mở|đóng|khoá|khóa|chỉnh|hạ|tăng|giảm|làm))"
_STATE_QUESTION = Pattern(
    rf"(\bđang .* {_Q_TAIL_NEG}|\bcòn .* {_Q_TAIL_NEG}|\bcó .* {_Q_TAIL_NEG}|\bbao nhiêu\b|\bthế nào\b|\bra sao\b|\bthì sao\b|\bthi sao\b"
    r"|\bchưa\b\s*(?:nhỉ|nhé|à|ạ|vậy|thế|đấy|nhở)?\s*\??\s*$"
    # "đèn đang bật à/hả?", "điều hoà đang chạy hử?" — đuôi nghi vấn à/hả/hử sau "đang" là HỎI
    # trạng thái, không phải lệnh. Động từ ("bật") tả trạng thái đang được hỏi (H01).
    r"|\bđang\b[^?]*\b(à|hả|hử)\b"
    # Câu LỰA CHỌN "đang bật hay tắt?", "mở hay đóng?" — hỏi trạng thái hiện tại, không sai khiến (H03).
    r"|\b(bật|tắt|mở|đóng|khoá|khóa)\s+hay\s+(bật|tắt|mở|đóng|khoá|khóa)\b)"
)

# Câu hỏi KIẾN THỨC / chẩn đoán về thiết bị (→ RAG, KHÔNG đọc snapshot, KHÔNG thực thi). Nêu
# thiết bị KHÔNG biến nó thành hỏi-trạng-thái hay lệnh: "máy nước nóng có cảnh báo an toàn gì",
# "máy rửa bát báo lỗi thì cần kiểm tra gì". Chỉ marker chẩn đoán rõ (không "cần/nên" trần —
# "cần bật đèn" là lệnh). Xét TRƯỚC nhánh lệnh tường minh để câu hỏi không bị thực thi.
_KNOWLEDGE_QUESTION = Pattern(
    r"\b(cảnh báo|canh bao|báo lỗi|bao loi|báo lối|an toàn|an toan|vệ sinh|ve sinh|bảo dưỡng|bao duong|"
    r"bao lâu|bao lau|làm sao|lam sao|làm thế nào|lam the nao|thế nào để|the nao de|tại sao|tai sao|"
    r"vì sao|vi sao|hướng dẫn|huong dan|cần kiểm tra|can kiem tra|kiểm tra gì|kiem tra gi|nghĩa là gì|nghia la gi)\b"
)
# "tắt đèn có được không?" là YÊU CẦU lịch sự, không phải câu hỏi trạng thái — "có ... không"
# ở đây chỉ là đuôi xin phép. Loại trừ để không biến lệnh thành câu hỏi.
# Đuôi CÂU HỎI XÁC NHẬN ("… đúng không?", "… phải không?", "… đúng chưa?") — "không"/"chưa" ở đây
# là hạt nghi vấn "có đúng vậy không", KHÔNG phải phủ định/huỷ. Nếu để nguyên, "máy rửa bát đang
# TẮT đúng KHÔNG?" bị has_negation=True → cổng `_STATE_QUESTION and not has_negation` chặn → rơi
# nhầm sang lệnh turn_off (TS-006). Bắt ở CUỐI câu, force INFORMATION_QUESTION trước khi phủ định
# kịp làm nhiễu (tương đương "cắt đuôi rồi mới phân loại ý định").
_CONFIRM_QUERY_TAIL = Pattern(
    r"\b(đúng không|phải không|đúng chứ|phải chứ|đúng chưa|xong chưa|chưa nhỉ|đúng nhỉ|phải nhỉ)\b[\s?.!,]*$"
)
_POLITE_TAIL = Pattern(r"\bcó (được|thể|nên|ổn)\b")

# Khung KHẢO SÁT hiện trạng: "đèn nào đang bật?", "đang bật những gì?", "liệt kê thiết bị đang
# chạy". Từ để hỏi (nào/gì/bao nhiêu/mấy) đi cùng thể tiếp diễn "đang" là mô tả hiện trạng đang
# được HỎI: tiếng Việt không dùng "đang" cho mệnh lệnh. Thiếu khung này, "đèn nào đang bật?"
# khớp động từ "bật" rồi bị thực thi thành turn_on — một câu hỏi bật sáng cả nhà.
# Từ ghép hỏi số lượng RỤNG âm tiết khi gõ nhanh: "có bao thiết bị đang bật" (thiếu "nhiêu")
# là câu hỏi y hệt "có bao nhiêu…". Nhận cả dạng cụt — trừ "bao gồm" (động từ, không phải từ
# để hỏi). Cổng này còn hàng rào `_COMMAND_VERB_FIRST` nên nới ở đây an toàn.
_HOW_MANY = r"bao(?:\s+nhiêu)?(?!\s*gồm)"
_SURVEY_QUESTION = Pattern(
    rf"\b(nào|gì|{_HOW_MANY}|mấy)\b[^?.!]{{0,24}}\bđang\b"
    rf"|\bđang\b[^?.!]{{0,24}}\b(nào|gì|{_HOW_MANY}|mấy)\b"
    r"|\b(liệt kê|danh sách)\b"
)
# Một MỆNH LỆNH cũng chứa được cặp đó khi nó lọc theo hiện trạng: "tắt cái nào đang bật". Phân
# biệt bằng VỊ TRÍ — động từ sai khiến mở đầu câu thì đây là lệnh có điều kiện, không phải hỏi.
_COMMAND_VERB_FIRST = Pattern(r"^\W*(bật|mở|tắt|đóng|khoá|khóa|chỉnh|đặt|tăng|giảm|hạ|nâng|dừng|ngắt)\b")
_SOCIAL = Pattern(r"^(chào|chao|hi|hello|xin chào|xin chao|cảm ơn|cam on|haha|hihi|ok thôi|vui quá)\b")

# "kéo" (rèm/mành) mơ hồ CẢ HAI chiều tuỳ trạng từ hướng đi kèm SAU đối tượng — "kéo rèm LẠI"/
# "kéo rèm VÀO" = đóng (khép rèm lại), "kéo rèm RA"/"kéo rèm LÊN" = mở (kéo rèm lên/mở ra).
# KHÔNG thêm "kéo" trần vào một trong hai chiều (sẽ đoán bừa) — chỉ nhận khi CÓ trạng từ hướng
# đi cùng, cho phép tối đa vài từ ở giữa (tên thiết bị/phòng: "kéo rèm phòng khách lại").
_KEO_GAP = r"(?:\s+\S+){0,4}?"
_TURN_ON = Pattern(rf"\b(bật|bat|mở|mo|khởi động|khoi dong|chạy|chay|lên|len)\b|\bkéo{_KEO_GAP}\s+(?:ra|lên)\b")
# Power/open verb thật, loại bare directional adverb "lên". Dùng khi một câu đồng thời
# có hướng numeric mạnh ("tăng lên"): "lên" khi đó không được nuốt cả action thành turn_on.
_TURN_ON_OPERATIONAL = Pattern(rf"\b(bật|bat|mở|mo|khởi động|khoi dong|chạy|chay)\b|\bkéo{_KEO_GAP}\s+(?:ra|lên)\b")
# Tiếng Việt viết TỪ GHÉP thành các âm tiết RỜI NHAU, nên `\btắt\b` khớp cả âm tiết cuối của
# "tóm tắt" (summarize) và biến một lệnh BẬT thành TẮT ("bật TV rồi tóm tắt cuốn sách" → turn_off).
# Đây KHÔNG phải va chạm bỏ dấu: hai âm tiết trùng nhau ngay ở bản CÓ DẤU, nên `accepts_folded`
# (vốn chỉ gác lớp bỏ dấu) không đỡ được — guard phải nằm trong chính pattern.
# Gom về MỘT bảng thay vì rải lookbehind viết tay cho từng cụm: mỗi động từ điều khiển kèm các
# âm tiết đứng trước biến nó thành từ ghép mang nghĩa khác.
_COMPOUND_PREFIXES: dict[str, tuple[str, ...]] = {
    "tắt": ("tóm", "vắn", "sơ"),  # tóm tắt / vắn tắt / sơ tắt — đều là "tóm lược", không phải tắt
    "đóng": ("khởi",),  # "khởi động" bỏ dấu thành "khoi dong" TRÙNG "đóng"
    "bật": ("nổi",),  # "nổi bật" = prominent, không phải lệnh bật
    "mở": ("cởi", "rộng"),  # "cởi mở" / "rộng mở" = tính từ, không phải lệnh mở
}


def _not_compound(verb: str) -> str:
    """`verb` nhưng KHÔNG khớp khi nó là âm tiết cuối của một từ ghép khác (bảng trên).

    `Pattern` tự sinh bản bỏ dấu từ chuỗi này, nên lookbehind viết một lần có hiệu lực cho
    CẢ hai lượt khớp (có dấu và bỏ dấu) — không cần nhân đôi bằng tay.
    """
    return "".join(f"(?<!{prefix} )" for prefix in _COMPOUND_PREFIXES.get(verb, ())) + verb


# "tất" (trong lượng từ "tất cả") fold thành "tat" TRÙNG "tắt" (off) → "mở/bật tất cả đèn" bị
# hiểu thành TẮT (turn_off xét trước turn_on). Lookahead loại "tắt/tat" khi ngay sau là "cả":
# "tất cả" không còn kích hoạt off; "tắt hết"/"tắt tất cả đèn"/"tắt đèn" vẫn off bình thường
# (chỉ "tắt cả" trần — hiếm — không còn bắt, dùng "tắt hết" thay thế).
_TURN_OFF = Pattern(
    rf"\b({_not_compound('tắt')}(?!\s*cả)|{_not_compound('đóng')}|khép|khep)\b"
    rf"|\bkéo{_KEO_GAP}\s+(?:lại|vào)\b"
)
_DEVICE_REST = Pattern(r"\b(nghỉ|nghi)\b")
# Động từ khoá cửa. "mở khoá" là MỞ (unlock) nên phải loại trước khi bắt "khoá" = lock.
# "chốt" (chốt cửa) cũng là khoá — thiếu nó thì "chốt hết cửa" không ra action_hint (no_goal).
_UNLOCK = Pattern(r"\b(mở khoá|mở khóa|mo khoa|mở khoa|mo khóa)\b")
_LOCK = Pattern(r"\b(khoá|khóa|khoa|chốt|chot)\b")
# Hướng tăng/giảm được phân giải bởi `direction_of` ở đúng thời điểm parse action.
# Không dựng lại hai regex ở đây: nếu cả "giảm" và cue yếu "thêm" cùng khớp, thứ tự
# `if` sẽ vô tình đổi cực tính mà nguồn sự thật chung đã phân giải chính xác.
# "chỉnh" (điều chỉnh) bỏ dấu = "chinh" TRÙNG "chính" (cửa CHÍNH, cổng CHÍNH) → "cửa chính" bị
# nhận nhầm có động từ set. Lookbehind loại khi đứng sau cửa/cổng (strip_diacritics tự áp cho cả
# bản không dấu: cửa→cua, cổng→cong). Bỏ "chinh" trần (thừa: "chỉnh" đã tự fold thành "chinh").
_SET = Pattern(r"\b(đặt|dat|(?<!cửa )(?<!cổng )chỉnh|để|de|set)\b")
# "hơn" = so sánh → con số đi kèm là DELTA, không phải trị tuyệt đối (dùng ở _device_action_hint).
_COMPARATIVE = Pattern(r"\b(hơn|hon)\b")

_RE_TEMPERATURE = re.compile(r"(\d{1,2})\s*(?:độ|do)\b")
_RE_PERCENT = re.compile(r"(\d{1,3})\s*(?:%|phần trăm|phan tram)")
# Lượng PHÂN SỐ nói bằng chữ. "mở rèm một nửa" mang GIÁ TRỊ hệt "mở rèm 50%", nhưng trước bản vá
# chỉ `turn_intent` (lượt TIẾP NỐI) biết token này, còn lượt ĐẦU thì không — nên "mở rèm khoảng
# một nửa" rơi về `open` trần và mở hết 100%. Đặt ở tầng `nlu` để CẢ HAI consumer dùng chung một
# bộ từ (§4); `turn_intent` import lại từ đây thay vì giữ bản sao riêng.
# CÓ DẤU là bắt buộc: "nửa" (½) và "nữa" (thêm) chỉ khác dấu, gộp lại sẽ biến "tăng nữa" thành 50%.
HALF_TOKENS = ("một nửa", "phân nửa", "nửa")
_RE_LEVEL = re.compile(r"\bmức\s*(\d)|\bmuc\s*(\d)")
_RE_BARE_NUMBER = re.compile(r"(?<!\w)(\d{1,3})(?!\w)")
# Số sau cue đặt-đến là GIÁ TRỊ TUYỆT ĐỐI, kể cả khi câu có động từ
# hướng: "tăng quạt LÊN mức 3", "giảm nó XUỐNG 25". Không có cue thì
# "giảm 2 độ" vẫn là delta tương đối.
_ABSOLUTE_VALUE_CUE = Pattern(r"\b(đặt|để|set|xuống|lên|còn|về|thành|đến|tới)\b")

# Danh từ CAPABILITY được nhắc trong một lệnh chỉnh không nêu thiết bị ("giảm bớt độ sáng").
# Dùng để tiếp nối hội thoại: chỉ carry thiết bị lượt trước khi nó HỖ TRỢ đúng capability
# đang nói tới — tránh "giảm nhiệt độ" đổ nhầm sang một chiếc đèn. Xét nhiệt độ TRƯỚC độ sáng
# ("nhiệt độ" chứa "độ", không được nuốt nhầm thành brightness).
# DANH TỪ dimension RÕ NGHĨA (tầng 1): thắng trước các tính từ trần. Cần thiết vì bỏ dấu làm
# nhiều tính từ va nhau — "dịu mắt" fold thành "diu mat" TRÙNG "mát" (nhiệt độ), nên một câu
# "giảm ánh sáng cho dịu mắt" sẽ bị chấm nhầm là chỉnh nhiệt độ (rồi ground vào điều hoà thay
# vì đèn). Nêu rõ "ánh sáng/độ sáng/đèn" thì chắc chắn là brightness, bất kể tính từ đi kèm.
_CAP_TEMPERATURE_STRONG = Pattern(r"\b(nhiệt độ|nhiet do|độ nóng|do nong|độ lạnh|do lanh|nhiệt|nhiet)\b")
_CAP_BRIGHTNESS_STRONG = Pattern(r"\b(ánh sáng|anh sang|độ sáng|do sang|độ chói|do choi|đèn|den|dimmer)\b")
_CAP_VOLUME_STRONG = Pattern(r"\b(âm lượng|am luong|volume)\b")
# "mành"(=rèm) fold thành "manh" TRÙNG "mạnh"(=tính từ cường độ, "mạnh hơn chút nữa") →
# lookahead loại riêng "mạnh hơn" (so sánh, đặc thù tính từ) để câu tiếp nối cường độ không
# bị hiểu nhầm thành nhắc tới rèm/mành. KHÔNG loại "lên" — "mành lên"/"mở mành lên" (kéo rèm)
# là câu lệnh rèm hợp lệ, "lên" quá chung (đi kèm nhiều động từ) để dùng làm tín hiệu loại trừ.
_CAP_POSITION_STRONG = Pattern(r"\b(độ mở|do mo|rèm|rem|mành(?!\s*hơn)|manh(?!\s*hon))\b")

# TÍNH TỪ / từ trần (tầng 2): yếu hơn, chỉ xét khi không có danh từ dimension rõ nghĩa.
_CAP_TEMPERATURE = Pattern(r"\b(nóng|nong|lạnh|lanh|mát|mat|ấm hơn|am hon)\b")
_CAP_BRIGHTNESS = Pattern(r"\b(sáng|sang|tối|toi)\b")
_CAP_VOLUME = Pattern(r"\b(tiếng|tieng|nhạc|nhac)\b")
_CAP_FANSPEED = Pattern(r"\b(gió|gio|quạt|quat|tốc độ|toc do)\b")
_CAP_POSITION = Pattern(r"\b(rèm|rem|mành(?!\s*hơn)|manh(?!\s*hon))\b")


def _mentioned_capability(view: TextView) -> Capability | None:
    """Capability số được NHẮC trong câu chỉnh không thiết bị, hoặc None nếu không rõ.

    Hai tầng: danh từ dimension rõ nghĩa (ánh sáng/nhiệt độ/âm lượng) thắng trước các tính từ
    trần (sáng/nóng/mát) — tránh va chạm bỏ dấu (mắt↔mát) chọn nhầm capability."""
    if _CAP_TEMPERATURE_STRONG.search(view):
        return Capability.TEMPERATURE
    if _CAP_BRIGHTNESS_STRONG.search(view):
        return Capability.BRIGHTNESS
    if _CAP_VOLUME_STRONG.search(view):
        return Capability.VOLUME
    if _CAP_POSITION_STRONG.search(view):
        return Capability.POSITION
    if _CAP_TEMPERATURE.search(view):
        return Capability.TEMPERATURE
    if _CAP_BRIGHTNESS.search(view):
        return Capability.BRIGHTNESS
    if _CAP_VOLUME.search(view):
        return Capability.VOLUME
    if _CAP_FANSPEED.search(view):
        return Capability.FAN_SPEED
    if _CAP_POSITION.search(view):
        return Capability.POSITION
    return None


_NUMERIC_CAPS = (
    Capability.BRIGHTNESS,
    Capability.TEMPERATURE,
    Capability.FAN_SPEED,
    Capability.VOLUME,
    Capability.POSITION,
)

# action_hint điều chỉnh MÔI TRƯỜNG có thể ground theo capability + phòng (không cần nêu thiết bị).
_ENV_ADJUST_HINTS = frozenset({"increase", "decrease", "set", "turn_on", "turn_off"})

# Loại thiết bị điều khiển ĐÚNG dimension MÔI TRƯỜNG cho mỗi capability. Chặn ground lan sang
# thiết bị chỉ TRÙNG capability nhưng khác nghĩa: "giảm nhiệt độ [phòng]" là AC/máy sưởi, KHÔNG
# phải bình nóng lạnh (nhiệt độ NƯỚC). Thiết bị đó phải được NÊU RÕ tên mới đụng tới.
_AMBIENT_TYPES: dict[Capability, frozenset[DeviceType]] = {
    Capability.BRIGHTNESS: frozenset({DeviceType.LIGHT}),
    Capability.TEMPERATURE: frozenset({DeviceType.AIR_CONDITIONER, DeviceType.HEATER}),
    Capability.VOLUME: frozenset({DeviceType.SPEAKER, DeviceType.TV}),
    Capability.FAN_SPEED: frozenset({DeviceType.FAN, DeviceType.AIR_CONDITIONER}),
    Capability.POSITION: frozenset({DeviceType.CURTAIN, DeviceType.WINDOW}),
}


def _resolve_capability_in_room(view: TextView, *, action_hint: str | None, target_area: str | None) -> list[str]:
    """Bất biến ĐỦ NGỮ NGHĨA: điều chỉnh nêu DIMENSION (ánh sáng/nhiệt độ/âm lượng...) + PHÒNG
    nhưng KHÔNG nêu thiết bị → phân giải MỌI thiết bị trong phòng HỖ TRỢ capability đó.

    "cho phòng sáng sủa lên tí" (bếp) / "giảm bớt ánh sáng" (phòng con): đã biết dimension +
    scope(phòng) + chiều + có capability tương thích → HÀNH ĐỘNG ĐƯỢC; thiếu con số cụ thể
    KHÔNG phải lý do hỏi lại (vagueness ≠ missing critical info). Con số tương đối do
    tầng planning/state resolution suy từ live state — không để tầng này bịa magnitude.

    KHÔNG có phòng (target_area None) thì KHÔNG ground — trả [] để tầng trên hỏi 'phòng nào?'
    (giữ 'tăng nhiệt độ lên tí'/'giảm độ sáng' không phòng ở dạng clarify). Không nhận diện
    được dimension cũng trả [] ('làm gì đó đi' vẫn hỏi lại). An ninh bị loại (exclude_security)."""
    if action_hint not in _ENV_ADJUST_HINTS or target_area is None:
        return []
    # Lệnh BẬT/TẮT nêu danh từ thiết bị ("tắt đèn") đi đường resolve-theo-loại (đòi phòng NÊU
    # RÕ) — KHÔNG tự gom theo focus, nếu không "tắt đèn" với focus_room sẽ tắt cả phòng thay vì
    # hỏi lại. Nhưng lệnh CHỈNH capability ("tăng nhiệt độ điều hoà", "giảm độ sáng đèn") thì
    # ground theo phòng là ĐÚNG ngữ nghĩa (đủ dimension + phòng + chiều) — cho qua, vì thiết bị
    # có thể chỉ có alias-kèm-phòng ("điều hoà phòng khách") nên tên trần không match slug nào.
    cap = _mentioned_capability(view)
    if cap is None:
        return []
    allowed = _AMBIENT_TYPES.get(cap)
    if not allowed:
        return []
    named_types = set(_match_device_types(view))
    # Nếu câu nêu loại thiết bị, type đó thu hẹp capability resolver:
    # "TV âm lượng 20" chỉ ground TV (không cả loa); "tăng nhiệt độ
    # điều hoà" chỉ ground AC. Riêng on/off vẫn để type-in-room resolver lo.
    if named_types:
        if action_hint in ("turn_on", "turn_off"):
            return []
        allowed = frozenset(set(allowed) & named_types)
        if not allowed:
            return []
    from src.planning.grounding import devices_by

    return [
        s.slug
        for s in devices_by(area=target_area, exclude_security=True)
        if cap in s.capabilities and s.device_type in allowed
    ]


def _carry_last_device_for_adjust(
    view: TextView, *, action_hint: str | None, target_area: str | None, last_device_id: str | None
) -> str | None:
    """Tiếp nối hội thoại: lệnh CHỈNH capability KHÔNG nêu thiết bị ("giảm bớt độ sáng") ngay sau
    khi vừa điều chỉnh MỘT thiết bị → hiểu là tiếp tục trên thiết bị đó.

    Chỉ nhận khi thiết bị lượt trước (a) ở ĐÚNG phòng đang nói tới (hoặc câu không nêu phòng)
    và (b) HỖ TRỢ capability được nhắc (hoặc câu không nêu capability nhưng thiết bị có mức số).
    Trả về slug để carry, hoặc None nếu không đủ điều kiện (để tầng trên hỏi lại — không đoán)."""
    if action_hint not in ("increase", "decrease", "set"):
        return None
    # Câu tự nêu MỘT LOẠI thiết bị ("TV âm lượng 20") không được
    # carry mỏ neo khác cùng capability (loa). Type-in-room retry sẽ ground TV trong
    # phòng đã chốt. Câu anaphora ("quạt của nó") đã có target ở _resolve_targets
    # trước khi tới hàm này, nên guard không làm mất nó.
    named_types = _match_device_types(view)
    if named_types:
        # "quạt" có thể là dimension fan_speed của máy lọc/AC, không phải
        # một thiết bị FAN độc lập. Chỉ chặn carry khi registry thực sự có
        # một thiết bị thuộc type đó trong scope; "TV" trong phòng khách thì chặn
        # loa, còn FAN không tồn tại thì cho phép carry máy lọc có fan_speed.
        typed = devices_by(
            device_types=tuple(named_types),
            area=target_area,
            exclude_security=False,
        )
        if typed:
            return None
    if not last_device_id or last_device_id not in DEVICE_BY_SLUG:
        return None
    spec = DEVICE_BY_SLUG[last_device_id]
    if target_area is not None and spec.room != target_area:
        return None
    cap = _mentioned_capability(view)
    if cap is not None:
        return last_device_id if cap in spec.capabilities else None
    return last_device_id if any(c in spec.capabilities for c in _NUMERIC_CAPS) else None


def _canonicalize_numeric_params(
    params: dict[str, Any],
    view: TextView,
    targets: list[str],
) -> dict[str, Any]:
    """Neo `value` chung vào capability số cụ thể từ câu + registry.

    Planner chọn capability từ khoá tham số. Nếu giữ `value` hoặc action
    increase/decrease, lệnh "giảm loa xuống 25" bị rơi về ON_OFF. Chỉ suy
    từ registry khi có đúng một target và nó chỉ có một dimension số."""
    if not params:
        return {}
    cap = _mentioned_capability(view)
    if cap is None and len(targets) == 1:
        spec = DEVICE_BY_SLUG.get(targets[0])
        numeric = [candidate for candidate in _NUMERIC_CAPS if spec and candidate in spec.capabilities]
        if len(numeric) == 1:
            cap = numeric[0]
    out = dict(params)
    raw_value = out.pop("value", None)
    if raw_value is None:
        return out
    if cap is None:
        # Chưa biết dimension thì không bịa key; caller vẫn có thể clarify.
        out["value"] = raw_value
        return out
    out[cap.value] = raw_value
    return out


# ---------------------------------------------------------------------------
# Selector NHÓM / LƯỢNG TỪ ("tắt hết đèn", "tất cả thiết bị", "chốt hết cửa").
#
# Từ loại trần ("đèn") CỐ Ý để mơ hồ ở normalizer (nhiều đèn trùng alias → UNRESOLVED),
# nên lệnh nhóm rơi ra với target rỗng rồi bị hỏi lại. Ở đây: khi có LƯỢNG TỪ ("hết/tất
# cả/mọi/cả nhà") + từ loại thiết bị (và/hoặc phòng), ta phân giải THẲNG ra danh sách slug
# thật bằng `devices_by` — không cần LLM. Bắt buộc phải có lượng từ: "tắt đèn" (không lượng
# từ, nhiều đèn) VẪN mơ hồ và hỏi lại như cũ; chỉ "tắt HẾT đèn" mới nghĩa là tất cả.
# ---------------------------------------------------------------------------
# "cả <số>" ("cả ba cửa sổ", "cả hai điều hoà", "cả 3 máy lọc") cũng là lượng từ NHÓM: người
# dùng đếm ra đủ số thiết bị và muốn tác động tất cả. Nhận theo SỐ bất kỳ thay vì liệt kê từng
# cụm, để "cả bốn/cả năm" tự chạy. "cả" TRẦN không tính — "hạ cả điều hoà phòng bố mẹ và phòng
# con" dùng "cả" làm từ nhấn, không phải đếm nhóm.
# "các" là mạo từ SỐ NHIỀU và cũng chỉ phạm vi toàn thể y như "tất cả": "đóng các cửa sổ" =
# mọi cửa sổ. Thiếu nó, lệnh nhóm hoàn toàn đủ nghĩa bị coi là thiếu phòng rồi hỏi ngược lại
# "Phòng khách, Phòng ngủ bố mẹ, hay Phòng ngủ con ạ?" — hỏi đúng thứ người dùng vừa nói rõ.
# An toàn vì `_resolve_group_targets` còn đòi thêm TỪ LOẠI THIẾT BỊ: "các bạn"/"các phòng ngủ"
# không khớp loại nào nên không thành lệnh nhóm.
_GROUP_QUANTIFIER = Pattern(
    r"\b(tất cả|toàn bộ|mọi|hết thảy|cả nhà|hết|các|cả (?:hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|\d+))\b"
)
# "cả nhà" / "trong nhà" / "thiết bị" trần (không nêu loại) ⇒ MỌI thiết bị điều khiển được.
# KHÔNG chứa "tất cả" (đó là lượng từ) để "tắt tất cả đèn" chỉ tắt đèn, không tắt cả nhà.
_ALL_DEVICES = Pattern(r"\b(thiết bị|đồ điện|đồ đạc|mọi thứ|mọi đồ)\b")

# Từ loại tiếng Việt → DeviceType. Đa từ đặt trước từ đơn để khớp cụ thể trước (máy lọc
# không khí trước khi chỉ thấy "máy"). Pattern tự sinh bản không dấu nên không cần liệt kê.
_TYPE_PATTERNS: dict[DeviceType, Pattern] = {
    DeviceType.AIR_CONDITIONER: Pattern(r"\b(điều hoà|điều hòa|máy lạnh|điều hoa)\b"),
    DeviceType.WATER_HEATER: Pattern(r"\b(bình nóng lạnh|bình nước nóng|máy nước nóng)\b"),
    DeviceType.AIR_PURIFIER: Pattern(r"\b(máy lọc không khí|máy lọc|lọc không khí)\b"),
    DeviceType.DISHWASHER: Pattern(r"\b(máy rửa bát|máy rửa chén|rửa bát|rửa chén)\b"),
    DeviceType.VACUUM: Pattern(r"\b(robot hút bụi|máy hút bụi|hút bụi|robot)\b"),
    DeviceType.HEATER: Pattern(r"\b(máy sưởi|lò sưởi|sưởi)\b"),
    DeviceType.COFFEE_MACHINE: Pattern(r"\b(máy pha cà phê|máy pha cafe)\b"),
    DeviceType.WASHING_MACHINE: Pattern(r"\b(máy giặt)\b"),
    DeviceType.GARAGE_DOOR: Pattern(r"\b(cửa gara|cửa nhà xe)\b"),
    DeviceType.RANGE_HOOD: Pattern(r"\b(máy hút mùi|máy hút khói)\b"),
    DeviceType.MICROWAVE: Pattern(r"\b(lò vi sóng)\b"),
    DeviceType.WINDOW: Pattern(r"\b(cửa sổ)\b"),
    DeviceType.LIGHT: Pattern(r"\b(đèn)\b"),
    DeviceType.TV: Pattern(r"\b(tivi|ti vi|tv|vô tuyến)\b"),
    DeviceType.CURTAIN: Pattern(r"\b(rèm)\b"),
    DeviceType.FAN: Pattern(r"\b(quạt)\b"),
    DeviceType.SPEAKER: Pattern(r"\b(loa)\b"),
    DeviceType.CAMERA: Pattern(r"\b(camera)\b"),
    DeviceType.DOOR_LOCK: Pattern(r"\b(cửa|khoá|khóa|chốt)\b"),
}
# Tín hiệu KHOÁ rõ ràng — dùng để tách "cửa" của khoá cửa khỏi "cửa" trong "cửa sổ".
_LOCK_NOUN = Pattern(r"\b(khoá|khóa|chốt)\b")
# Loại thuộc nhóm an ninh: chỉ đụng tới khi người dùng NÊU RÕ (không lẫn khi "tắt hết đèn").
_SECURITY_TYPES = frozenset({DeviceType.DOOR_LOCK, DeviceType.CAMERA})

# Khu vực được nêu như scope của một lệnh thiết bị nhưng không ánh xạ được vào ROOMS. Chỉ dùng
# sau khi đã có động từ hành động + loại/alias thiết bị, nên không nuốt câu than phiền trần như
# "phòng bí quá". Giới hạn tối đa hai từ sau "phòng" để không ăn cả mệnh đề còn lại.
_UNAVAILABLE_AREA = Pattern(r"\b(phòng\s+[^\W\d_]+(?:\s+[^\W\d_]+)?|hành lang|ban công|sân(?: trước| sau)?)\b")


@dataclass(frozen=True, slots=True)
class Understanding:
    goal: SemanticGoal | None
    candidates: tuple[IntentCandidate, ...] = field(default=())
    model_used: bool = False
    abstained: bool = False
    note: str = ""
    utterance_type: UtteranceType | None = None


# "chỉ <verb> thôi" — RÚT GỌN tự sửa trong câu: người dùng vừa nêu lệnh đầy đủ (có số) rồi
# CHỐT LẠI chỉ giữ đúng một động từ ("bật 24 độ, nhưng không đổi nhiệt độ, CHỈ BẬT THÔI" = chỉ
# bật, bỏ 24 độ). Tổng quát qua vựng động từ điều khiển, không map cụm→case cụ thể (§8).
_REDUCE_PATTERNS: tuple[tuple[Pattern, str], ...] = tuple(
    (Pattern(rf"\bchỉ {verb} thôi\b"), hint)
    for verb, hint in (
        ("mở khoá", "unlock"),
        ("mở khóa", "unlock"),
        ("bật", "turn_on"),
        ("tắt", "turn_off"),
        ("mở", "open"),
        ("đóng", "close"),
        ("khoá", "lock"),
        ("khóa", "lock"),
    )
)


def _only_action_reduction(view: TextView) -> str | None:
    """action_hint nếu câu chốt "chỉ <verb> thôi" (rút gọn về đúng động từ, bỏ tham số số), else None."""
    for pat, hint in _REDUCE_PATTERNS:
        if pat.search(view):
            return hint
    return None


def _extract_params(view: TextView) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if m := _RE_TEMPERATURE.search(view.raw) or _RE_TEMPERATURE.search(view.folded):
        params["temperature"] = int(m.group(1))
    if m := _RE_PERCENT.search(view.raw) or _RE_PERCENT.search(view.folded):
        params["percent"] = int(m.group(1))
    elif any(re.search(rf"(?<!\w){re.escape(tok)}", view.raw) for tok in HALF_TOKENS):
        # Chỉ khi câu KHÔNG nêu phần trăm tường minh: "mở 30% chứ không phải một nửa" thì con số
        # người dùng nói thắng. Khớp trên bản CÓ DẤU để không nuốt "nữa" (=thêm).
        params["percent"] = 50
    if m := _RE_LEVEL.search(view.raw) or _RE_LEVEL.search(view.folded):
        params["level"] = int(next(g for g in m.groups() if g))
    # Số trần chỉ có nghĩa gán khi câu nêu dimension ("âm lượng 20") hoặc
    # cue đặt-đến ("xuống 25"). Guard này không bắt ordinal "cái thứ 2".
    if not params and (_mentioned_capability(view) is not None or _ABSOLUTE_VALUE_CUE.search(view)):
        if m := _RE_BARE_NUMBER.search(view.raw) or _RE_BARE_NUMBER.search(view.folded):
            params["value"] = int(m.group(1))
    return params


def _device_action_hint(view: TextView, params: dict[str, Any], *, has_device: bool) -> str | None:
    """Trả action_hint tường minh cho lệnh điều khiển, hoặc None."""
    # "cho <thiết bị> nghỉ" is an explicit stop only when a physical device is
    # named.  Bare "tôi đi nghỉ" remains an open-ended routine request.
    if has_device and _DEVICE_REST.search(view):
        return "turn_off"
    # Khoá cửa xử lý TRƯỚC các động từ khác: "khoá" không rơi vào turn_off, "mở khoá"
    # phải là unlock (không nhầm sang lock/turn_on — trước bản vá này KHÔNG có nhánh trả
    # "unlock" nào cả nên rơi xuống _TURN_ON, khiến is_pure_prohibition/policy gate không
    # nhận ra đây là hành động trên capability lock). Việc chặn/cho phép do evaluate() quyết ở dưới.
    if _UNLOCK.search(view):
        return "unlock"
    if _LOCK.search(view):
        return "lock"
    # So sánh "… hơn X độ" = DELTA (mát hơn 1 độ = giảm 1), KHÔNG phải đặt tuyệt đối X. Chỉ "xuống/
    # lên/đến/còn X độ" mới là set. Nếu có hướng tăng/giảm kèm "hơn" thì giữ increase/decrease,
    # để delta grounding cộng/trừ từ trạng thái hiện tại (tránh "mát hơn 1 độ" → đặt máy lạnh 1°C).
    direction, _direction_evidence = direction_of(view.raw, bare_verbs=False)
    comparative_delta = bool(_COMPARATIVE.search(view) and direction)
    if not comparative_delta and params and _ABSOLUTE_VALUE_CUE.search(view):
        return "set"
    if not comparative_delta and (
        params.get("temperature") is not None or (params.get("percent") is not None and _SET.search(view))
    ):
        return "set"
    # An explicit operational verb owns the action.  Additive discourse such
    # as "tắt thêm TV" or "mở thêm rèm" must not turn into a numeric increase
    # merely because it also contains "thêm".
    if _TURN_OFF.search(view):
        return "turn_off"
    if _TURN_ON.search(view) and (not direction or _TURN_ON_OPERATIONAL.search(view)):
        return "turn_on"
    if direction:
        return direction
    if _SET.search(view) and ("percent" in params or "level" in params):
        return "set"
    if has_device and _SET.search(view):
        return "set"
    return None


def _resolve_targets(nu: NormalizedUtterance, last_device_id: str | None) -> tuple[list[str], bool]:
    """Trả (target_device_ids, references_resolved)."""
    if nu.matched_device_ids:
        return list(nu.matched_device_ids), True
    if nu.has_reference:
        if last_device_id and last_device_id in DEVICE_BY_SLUG:
            return [last_device_id], True
        return [], False
    return [], True


def _match_device_types(view: TextView) -> list[DeviceType]:
    """Các DeviceType xuất hiện trong câu (từ loại trần). Có thể nhiều loại cùng lúc."""
    types = [dtype for dtype, pat in _TYPE_PATTERNS.items() if pat.search(view)]
    # "cửa sổ" khớp cả WINDOW lẫn DOOR_LOCK (vì chứa "cửa"). Bỏ DOOR_LOCK trừ khi có tín
    # hiệu khoá rõ ("khoá"/"chốt") — mở cửa sổ KHÔNG được kéo theo mở/khoá cửa.
    if DeviceType.WINDOW in types and DeviceType.DOOR_LOCK in types and not _LOCK_NOUN.search(view):
        types.remove(DeviceType.DOOR_LOCK)
    return types


def unavailable_target(nu: NormalizedUtterance) -> str | None:
    """Tên LOẠI thiết bị người dùng vừa nhắc mà NHÀ NÀY không có — hoặc None.

    Tách "chưa đủ thông tin" (hỏi lại là đúng) khỏi "không tồn tại" (hỏi lại là VÔ NGHĨA).
    Trước bản vá cả hai đều rơi chung vào cổng thiếu-đích, nên "bật quạt trần phòng khách" —
    câu đã NÊU phòng — vẫn bị hỏi ngược "bạn muốn làm ở phòng nào ạ?", và người dùng không có
    cách nào biết rằng đơn giản là nhà không lắp quạt.

    Neo hoàn toàn vào REGISTRY, không hardcode danh sách thiết bị-không-có: loại nhận diện
    được nhưng registry KHÔNG có instance nào. Trả ĐÚNG CỤM NGƯỜI DÙNG GÕ để câu trả lời nói
    lại bằng từ của họ.

    CỐ Ý KHÔNG bắt "phòng lạ" ("đèn phòng tắm"): trong tiếng Việt "phòng" cũng là danh từ trần
    làm chủ ngữ ("phòng bí quá khó thở"), nên mọi luật `phòng\\s+\\w+` đều nuốt nhầm câu than
    phiền bình thường và biến chúng thành "nhà mình không có phòng bí quá". Không có tín hiệu
    tất định nào tách được hai nghĩa đó, nên để tầng hỏi-lại xử lý phòng lạ.
    """
    from src.iot.registry import DEVICE_SPECS

    view = TextView(raw=nu.normalized, folded=nu.folded)
    present = {spec.device_type for spec in DEVICE_SPECS}
    for dtype in _match_device_types(view):
        if dtype in present:
            continue
        hit = _TYPE_PATTERNS[dtype].search(view)
        if hit is not None:
            return hit.group(0)
    # Loại thiết bị có trong nhà nhưng khu vực người dùng chỉ tới không tồn tại trong registry.
    # Đây vẫn là "unsupported", không phải thiếu room: hỏi người dùng chọn một phòng khác sẽ
    # âm thầm đổi ý định của họ. Lấy đúng span họ viết để câu trả lời không bịa tên khu vực.
    if (
        nu.has_action_verb
        and not nu.matched_rooms
        and not nu.partial_rooms
        and (nu.matched_device_ids or _match_device_types(view))
        and (area := _UNAVAILABLE_AREA.search(view)) is not None
    ):
        return nu.normalized[area.start() : area.end()]
    return None


def mentioned_device_types(nu: NormalizedUtterance) -> list[DeviceType]:
    """Return registry device types named generically in an utterance.

    Full aliases such as ``điều hoà phòng khách`` are resolved by the
    normalizer.  Bare type nouns such as ``điều hoà`` intentionally remain
    unresolved for commands unless a room or group quantifier supplies safe
    scope.  Read-only consumers still need the type evidence, so expose the
    same matcher instead of maintaining a second vocabulary.
    """
    view = TextView(raw=nu.normalized, folded=nu.folded)
    return _match_device_types(view)


def _resolve_type_in_room(view: TextView, nu: NormalizedUtterance, *, allow_inferred_room: bool = False) -> list[str]:
    """ "tắt đèn phòng ngủ con": động từ + LOẠI thiết bị + PHÒNG nêu rõ → mọi thiết bị loại đó
    trong phòng, KHÔNG cần lượng từ "hết/tất cả".

    Cần thiết khi phòng có NHIỀU thiết bị cùng loại (2 đèn) nên `_match_devices` không chọn
    được một cái. Mặc định CHỈ chạy khi phòng NÊU RÕ (alias trong câu hoặc "phòng của tôi" →
    phòng riêng người nói) — KHÔNG mở rộng khi phòng chỉ suy từ focus (tín hiệu yếu, có thể
    lạc hậu qua nhiều lượt). `allow_inferred_room=True` nới lỏng ràng buộc này CHỈ cho một
    nguồn phòng SUY LUẬN mạnh hơn focus_room đa lượt thông thường: phòng do một MỆNH ĐỀ KHÁC
    trong CÙNG một câu ghép nêu rõ (xem `_resolve_independent_clauses`) — ví dụ "Đừng khoá cửa
    phòng bố mẹ, còn cửa sổ thì đóng lại" thì mệnh đề sau kế thừa phòng từ mệnh đề trước, KHÔNG
    phải một focus_room mơ hồ ngoài câu.

    Loại an ninh không bao giờ được tự GOM theo phòng. Tuy nhiên, nếu người dùng đã nêu
    rõ cả loại + phòng và catalog chỉ có ĐÚNG MỘT thiết bị an ninh khớp (ví dụ "mở cửa
    phòng khách"), đó là grounding đơn nghĩa và vẫn phải được giữ để đi qua authorization.
    Nếu có nhiều hơn một thiết bị thì trả rỗng để hỏi lại, không đoán bừa."""
    if not nu.matched_rooms or (not allow_inferred_room and not nu.room_explicit):
        return []
    matched_types = _match_device_types(view)
    types = [t for t in matched_types if t not in _SECURITY_TYPES]
    security_types = [t for t in matched_types if t in _SECURITY_TYPES]
    slugs: list[str] = []
    if types:
        # PHÂN PHỐI qua MỌI phòng đã nêu, không chỉ phòng đầu. Alias thiết bị trong registry đều
        # gắn phòng ("cửa sổ phòng khách"), nên câu "mở cửa sổ phòng khách VÀ PHÒNG BỐ MẸ" chỉ
        # khớp alias được vế đầu; nếu ở đây cũng chỉ lấy matched_rooms[0] thì vế sau biến mất
        # hoàn toàn và người dùng nhận về nửa lệnh mà không được báo gì.
        for room in nu.matched_rooms:
            for spec in devices_by(device_types=tuple(types), area=room, exclude_security=True):
                if spec.slug not in slugs:
                    slugs.append(spec.slug)

    # Security grounding is singleton-only. The downstream deterministic authorization
    # gate still decides whether this user may execute or must wait for approval.
    # Xét trên HỢP của mọi phòng đã nêu: nhiều phòng cùng có thiết bị an ninh nghĩa là câu
    # chưa đơn nghĩa → hỏi lại, tuyệt đối không tự gom.
    for dtype in security_types:
        matches = [
            spec
            for room in nu.matched_rooms
            for spec in devices_by(device_types=(dtype,), area=room, exclude_security=False)
        ]
        if len(matches) > 1:
            return []
        if len(matches) == 1 and matches[0].slug not in slugs:
            slugs.append(matches[0].slug)
    return slugs


def retry_device_grounding(nu: NormalizedUtterance, *, action_hint: str | None, room: str) -> list[str]:
    """RETRY ground thiết bị sau khi PHÒNG được Context Resolver 6-tier giải xong (spec §14/§P3:
    Resolve phải viết lại thành lệnh tường minh, không chỉ vá MỘT field rồi bỏ đó).

    `_resolve_type_in_room` bắt buộc `nu.room_explicit` (phòng nêu ngay trong câu) — cố tình,
    để không tự gom theo `focus_room` suy diễn yếu. Hàm này dùng khi phòng đến từ nguồn ĐÁNG TIN
    hơn focus_room suy luận (speaker_location đo được, hoặc phòng đã CHỐT trong Ledger) — caller
    (`resolve_semantics`) chịu trách nhiệm chỉ gọi với phòng đủ tin cậy. Thử type-in-room trước
    (đủ khi câu nêu LOẠI thiết bị: "tắt đèn"/"chỉnh quạt"), rồi capability-in-room (đủ khi câu
    nêu DIMENSION: "tăng nhiệt độ"). Trả [] nếu không phân giải được — để tầng trên hỏi lại,
    KHÔNG đoán bừa."""
    view = TextView(raw=nu.normalized, folded=nu.folded)
    types = [t for t in _match_device_types(view) if t not in _SECURITY_TYPES]
    if types:
        specs = devices_by(device_types=tuple(types), area=room, exclude_security=True)
        seen: set[str] = set()
        slugs: list[str] = []
        for s in specs:
            if s.slug not in seen:
                seen.add(s.slug)
                slugs.append(s.slug)
        if slugs:
            return slugs
    return _resolve_capability_in_room(view, action_hint=action_hint, target_area=room)


def _resolve_clause_devices(clause: str, *, expand: bool) -> list[str]:
    """Thiết bị mà MỘT mệnh đề (dương hoặc loại-trừ) nhắc tới, phân giải ĐỘC LẬP qua registry/alias.

    Dùng cho loại-trừ-trong-câu (`split_exclusion_clause`): map hai mệnh đề rồi trừ. Mệnh đề DƯƠNG
    cho phép MỞ RỘNG nhóm ("các đèn ở phòng X", "tất cả đèn"); mệnh đề LOẠI-TRỪ chỉ lấy khớp alias
    tường minh và trả TẤT CẢ ứng viên (kể cả alias trùng nhiều phòng, vd "đèn ngủ" ở 2 phòng) — phép
    GIAO với tập dương ở tầng gọi tự thu hẹp về đúng phòng, nên không cần đoán phòng ở đây."""
    from src.nlu.normalizer import analyze

    nu_c = analyze(clause)
    ids = list(nu_c.matched_device_ids)
    if expand and not ids:
        view_c = TextView(raw=nu_c.normalized, folded=nu_c.folded)
        ids = _resolve_group_targets(view_c, nu_c, hint=None) or _resolve_type_in_room(view_c, nu_c)
    return ids


def _resolve_clause_for_split(
    clause_text: str,
    ctx: RuntimeContext,
    *,
    sentence_room: str | None,
    carried_devices: list[str] | None = None,
    inherited_action_hint: str | None = None,
) -> tuple[list[str], str | None, bool, dict[str, Any]]:
    """Resolve MỘT mệnh đề (đã tách bởi `split_command_clauses`) ĐỘC LẬP qua registry: trả
    (device_ids, action_hint|None, negated). `negated` đọc từ `NormalizedUtterance.has_negation`
    tính LẠI riêng cho mệnh đề này (không phải cờ toàn câu) — đây là điểm mấu chốt sửa root
    cause chung của cả 2 việc: gộp nhầm hành động khác nhau lên nhiều thiết bị (§ finding 2) VÀ
    gán nhầm phủ định sang sai thiết bị (§ finding 3) — phạm vi của MỖI tín hiệu (thiết bị,
    động từ, phủ định) giờ bám đúng mệnh đề chứa nó, không suy luận mơ hồ từ vị trí câu.

    `sentence_room` — phòng do MỘT mệnh đề (bất kỳ) trong CÙNG câu ghép nêu RÕ BẰNG CHỮ (KHÔNG
    tính focus_room đa lượt — xem cách tính ở `_resolve_independent_clauses`). Nếu mệnh đề này
    không tự nêu phòng nào nhưng `sentence_room` có giá trị, nó được kế thừa VỚI
    `allow_inferred_room=True` (bằng chứng cùng-câu mạnh hơn hẳn focus_room đa lượt mơ hồ, có
    thể đã lạc hậu). Nếu KHÔNG mệnh đề nào trong câu nêu phòng nào cả (`sentence_room=None`),
    hành vi giữ NGUYÊN chặt chẽ như trước (không tự gom theo focus_room đa lượt).

    `device_ids` có thể RỖNG khi mệnh đề không tự map được thiết bị (kể cả qua fallback nhóm/
    loại-trong-phòng) — caller quyết định mức nghiêm ngặt: mệnh đề DƯƠNG rỗng phải huỷ toàn bộ
    phép tách (an toàn hơn đoán bừa); mệnh đề PHỦ ĐỊNH rỗng thì caller BỎ QUA nó (không loại trừ
    nhầm thiết bị khác) — xem `_resolve_independent_clauses`."""
    nu_c = analyze(
        clause_text,
        focus_room=ctx.focus_room,
        speaker_home_room=ctx.speaker_home_room,
        speaker_private_room=ctx.speaker_private_room,
    )
    view_c = TextView(raw=nu_c.normalized, folded=nu_c.folded)
    ids = list(nu_c.matched_device_ids)
    if not ids:
        ids = _resolve_group_targets(view_c, nu_c, hint=None) or _resolve_type_in_room(view_c, nu_c)
    if not ids and sentence_room:
        # Mệnh đề không tự resolve được (kể cả với focus_room đa lượt) — thử lại với phòng do
        # MỘT mệnh đề khác trong CÙNG câu nêu rõ (bằng chứng mạnh hơn, xem docstring ở trên).
        nu_room = analyze(
            clause_text,
            focus_room=sentence_room,
            speaker_home_room=ctx.speaker_home_room,
            speaker_private_room=ctx.speaker_private_room,
        )
        view_room = TextView(raw=nu_room.normalized, folded=nu_room.folded)
        ids = _resolve_group_targets(view_room, nu_room, hint=None) or _resolve_type_in_room(
            view_room, nu_room, allow_inferred_room=True
        )
        if ids:
            nu_c, view_c = nu_room, view_room
    # Phòng phải xét trên bản KHÔNG có fallback focus_room: `analyze(clause, focus_room=...)` tự
    # điền phòng ngầm, nên dùng `nu_c.matched_rooms` sẽ chặn nhầm những mệnh đề tỉnh lược không
    # hề nêu phòng ("đặt quạt mức 3").
    from src.nlu.normalizer import analyze as _analyze_literal

    literal_clause_rooms = _analyze_literal(clause_text).matched_rooms
    if not ids and carried_devices and not nu_c.has_negation and not literal_clause_rooms:
        # Mệnh đề TỈNH LƯỢC trong cùng câu ghép: "bật loa bếp trước RỒI ĐẶT ÂM LƯỢNG XUỐNG 15"
        # — vế sau không nhắc lại thiết bị vì nó nói về chính thiết bị vế trước. Đối ứng của
        # cơ chế kế thừa `sentence_room` đã có. Điều kiện chặt để không suy bừa: vế này không
        # nêu thiết bị NÀO, không nêu phòng nào, không phủ định, và phải tự có động từ rõ ràng
        # (kiểm ngay bên dưới) — nếu không thì phép tách vẫn bị huỷ như cũ.
        ids = list(carried_devices)
    if not ids:
        return [], None, nu_c.has_negation, {}
    if nu_c.has_negation:
        return ids, None, True, {}
    # Params đọc từ CHÍNH mệnh đề này. Trước đây chúng được tính rồi vứt đi ngay tại đây, nên
    # giá trị của mệnh đề sau ("... xuống 50%") không tới được thiết bị của nó.
    params = _extract_params(view_c)
    hint = _device_action_hint(view_c, params, has_device=True)
    if hint is None and not nu_c.has_action_verb:
        hint = inherited_action_hint
    if hint in ("set", "increase", "decrease") and _TURN_ON_OPERATIONAL.search(view_c):
        # Một mệnh đề DUY NHẤT vẫn có thể mang cả hai ý: "bật điều hoà phòng con Ở 24 ĐỘ" =
        # bật nguồn + đặt nhiệt. Động từ đặt-mức thắng ở `hint` (vì có giá trị), nên nếu không
        # ghi lại ý định NGUỒN ở đây thì nó biến mất y hệt trường hợp hai mệnh đề rời.
        params = {**params, "power": "on"}
    return ids, hint, False, params


# Động từ để thiết bị Ở TRẠNG THÁI HOẠT ĐỘNG sau khi thực hiện. Hai mệnh đề cùng nhóm này trên
# CÙNG một thiết bị là BỔ SUNG cho nhau, không mâu thuẫn.
_ENDS_ON_HINTS = frozenset({"turn_on", "open", "unlock", "set", "increase", "decrease"})


def _merge_same_device_actions(first: str, second: str) -> str | None:
    """Hai mệnh đề cùng nhắm MỘT thiết bị → một hành động hợp nhất, hoặc None nếu thật sự mâu thuẫn.

    "Bật loa bếp trước rồi đặt âm lượng xuống 15" là MỘT ý định hai bước trên cùng chiếc loa:
    bật nguồn rồi chỉnh mức. Trước bản vá, hai hint khác nhau bị coi là mâu thuẫn nên cả phép
    tách mệnh đề bị huỷ, câu rơi về một action_hint chung và vế "bật" biến mất — loa nhận âm
    lượng trong khi vẫn đang tắt.

    Chỉ hợp nhất khi CẢ HAI đều để thiết bị ở trạng thái hoạt động; động từ ĐẶT MỨC thắng vì nó
    mang giá trị, và `_explicit_proposal` sẽ tự tách lại thành nguồn + mức. Ngược cực tính
    ("bật ... rồi tắt") vẫn là mâu thuẫn thật và trả None như cũ.
    """
    if first not in _ENDS_ON_HINTS or second not in _ENDS_ON_HINTS:
        return None
    for hint in (first, second):
        if hint in ("set", "increase", "decrease"):
            return hint
    return second


def _resolve_independent_clauses(
    nu: NormalizedUtterance, ctx: RuntimeContext, action_hint: str | None
) -> tuple[list[str], dict[str, str], dict[str, dict[str, Any]], list[str], str | None] | None:
    """Tách câu thành N MỆNH ĐỀ ĐỘC LẬP theo ranh giới cú pháp tổng quát (dấu phẩy / và / rồi /
    còn / nhưng / sau đó — `split_command_clauses`), resolve RIÊNG từng mệnh đề (thiết bị +
    hành động + phủ định của CHÍNH nó), rồi hợp nhất. Thay hẳn 2 cơ chế hẹp trước đây (một cái
    chỉ nhận "còn/nhưng" nối 2 mệnh đề dương, một cái chỉ nhận đúng cấu trúc "đừng X, nhưng Y")
    — cả hai đều bỏ sót câu chỉ dùng dấu phẩy/"và"/"rồi", hoặc gán nhầm phủ định khi mệnh đề
    phủ định không map được thiết bị (§ QA finding 2+3, root cause chung).

    Trả (target_device_ids gộp, target_actions theo slug, excluded_device_ids gộp, action_hint
    đại diện) khi TỪNG mệnh đề DƯƠNG (≥1) tự map được thiết bị + động từ RÕ RÀNG (huỷ toàn bộ
    nếu bất kỳ mệnh đề dương nào không resolve được — an toàn hơn đoán bừa); mệnh đề PHỦ ĐỊNH
    không resolve được thiết bị thì bị BỎ QUA (không đóng góp gì, không huỷ cả câu) — tránh vừa
    mất mệnh đề dương vừa tránh gán nhầm phủ định sang thiết bị KHÔNG liên quan (an toàn hơn là
    trả lời sai sự thật cho user). Trả None nếu câu không tách được ≥2 mệnh đề có ý nghĩa, hoặc
    không có gì để can thiệp (mọi mệnh đề dương cùng MỘT hành động, không có mệnh đề phủ định
    nào — đường action_hint chung hiện tại vốn đã đúng)."""
    from src.nlu.exclusion_clause import split_command_clauses

    segments = split_command_clauses(nu.raw)
    if len(segments) < 2:
        return None
    # Phòng NÊU RÕ BẰNG CHỮ ở BẤT KỲ đâu trong câu ghép (không lẫn focus_room đa lượt của `nu`
    # gốc, vốn có thể đã được `analyze()` tự điền qua fallback) — bằng chứng cùng-câu, dùng để
    # các mệnh đề không tự nêu phòng riêng có thể kế thừa (xem `_resolve_clause_for_split`).
    literal_rooms = analyze(nu.raw).matched_rooms
    sentence_room = literal_rooms[0] if literal_rooms else None

    clauses: list[tuple[list[str], str, bool, dict[str, Any]]] = []
    carried: list[str] = []
    for seg in segments:
        ids, hint, negated, params = _resolve_clause_for_split(
            seg,
            ctx,
            sentence_room=sentence_room,
            carried_devices=carried or None,
            inherited_action_hint=action_hint,
        )
        if ids and not negated:
            carried = list(ids)
        if negated:
            if not ids:
                # Không xác định được thiết bị bị loại trừ — BỎ QUA mệnh đề này thay vì đoán
                # bừa/gán nhầm sang thiết bị của một mệnh đề khác (§ finding 3, an toàn hơn
                # trả lời sai sự thật cho user).
                continue
            clauses.append((ids, "", True, {}))
            continue
        if not ids or hint is None:
            # Mệnh đề DƯƠNG không tự resolve được thiết bị/động từ rõ ràng — không tách rõ
            # ràng, an toàn hơn huỷ toàn bộ phép tách (tránh áp nhầm hành động chéo thiết bị).
            return None
        clauses.append((ids, hint, False, params))

    if len(clauses) < 2:
        return None

    excluded_ids: list[str] = []
    for ids, _hint, negated, _params in clauses:
        if not negated:
            continue
        for d in ids:
            if d not in excluded_ids:
                excluded_ids.append(d)

    target_actions: dict[str, str] = {}
    target_parameters: dict[str, dict[str, Any]] = {}
    ordered_targets: list[str] = []
    distinct_hints: set[str] = set()
    for ids, hint, negated, params in clauses:
        if negated:
            continue
        distinct_hints.add(hint)
        for d in ids:
            if d in excluded_ids:
                continue  # mệnh đề dương không được đụng thiết bị vừa bị mệnh đề khác loại trừ
            merged_power_on = False
            if d in target_actions and target_actions[d] != hint:
                merged = _merge_same_device_actions(target_actions[d], hint)
                if merged is None:
                    # Mâu thuẫn THẬT (bật rồi lại tắt cùng một thiết bị) — không đoán cái nào đúng.
                    return None
                # Một trong hai vế là động từ NGUỒN còn vế kia đặt mức: giữ lại ý định bật một
                # cách tường minh, nếu không nó tan biến khi gộp và thiết bị nhận mức trong khi
                # vẫn đang tắt.
                merged_power_on = "turn_on" in (target_actions[d], hint) or "open" in (
                    target_actions[d],
                    hint,
                )
                hint = merged
            target_actions[d] = hint
            # Giá trị của mệnh đề nào thì thuộc về thiết bị của mệnh đề đó.
            if params or merged_power_on:
                merged_params = dict(target_parameters.get(d) or {})
                merged_params.update(params)
                if merged_power_on:
                    merged_params["power"] = "on"
                target_parameters[d] = merged_params
            if d not in ordered_targets:
                ordered_targets.append(d)

    if not ordered_targets:
        return None
    if (
        not excluded_ids
        and len(distinct_hints) < 2
        and set(ordered_targets) <= set(nu.matched_device_ids)
    ):
        # Không có mệnh đề phủ định nào bị mất, và mọi mệnh đề dương cùng một hành động — đường
        # action_hint chung hiện tại vốn đã đúng NẾU normalizer đã giữ đủ các đích. Khi phép
        # tách khôi phục thêm một đích bị alias dài nuốt mất ("TV và loa phòng khách"), phải
        # giữ kết quả tách thay vì âm thầm bỏ thiết bị đầu câu.
        return None

    primary_hint = next(iter(target_actions.values()), action_hint)
    return ordered_targets, target_actions, target_parameters, excluded_ids, primary_hint


def _resolve_group_targets(view: TextView, nu: NormalizedUtterance, *, hint: str | None) -> list[str]:
    """Phân giải lệnh NHÓM thành danh sách slug thật, hoặc [] nếu không phải lệnh nhóm.

    Điều kiện: có lượng từ ("hết/tất cả/mọi/...") + (từ loại thiết bị HOẶC "thiết bị" trần).
    Thiết bị an ninh (khoá/camera) chỉ được đưa vào khi người dùng nêu RÕ loại đó — để "tắt
    hết đèn" không bao giờ đụng cửa/camera. Phân quyền vẫn do policy gate quyết ở hạ nguồn."""
    if not _GROUP_QUANTIFIER.search(view):
        return []
    # Phòng chỉ lấy khi NÊU RÕ trong câu — "hết" mặc định là cả nhà, không tự thu về focus_room.
    area = nu.matched_rooms[0] if nu.matched_rooms else None
    types = _match_device_types(view)

    if not types:
        if not _ALL_DEVICES.search(view):
            return []
        # "tất cả thiết bị" — mọi thiết bị điều khiển được, KHÔNG gồm an ninh.
        specs = devices_by(area=area, exclude_security=True)
    else:
        sec = [t for t in types if t in _SECURITY_TYPES]
        norm = [t for t in types if t not in _SECURITY_TYPES]
        specs = []
        if norm:
            specs += devices_by(device_types=tuple(norm), area=area, exclude_security=True)
        if sec:
            specs += devices_by(device_types=tuple(sec), area=area, exclude_security=False)

    seen: set[str] = set()
    slugs: list[str] = []
    for s in specs:
        if s.slug not in seen:
            seen.add(s.slug)
            slugs.append(s.slug)
    return slugs


# "đang" là thể TIẾP DIỄN: động từ đứng ngay sau nó MÔ TẢ trạng thái đang có, không sai
# khiến — tiếng Việt không ra lệnh bằng "đang bật". Nhưng _TURN_ON/_TURN_OFF/direction_of
# khớp động từ ở BẤT KỲ vị trí nào, kể cả ngay sau "đang", nên câu thuần mô tả/hỏi hiện
# trạng vẫn ra has_action_verb=True rồi rơi xuống nhánh LỆNH GHI.
#
# Các khung hỏi phía trên chặn phần lớn, nhưng chúng khớp CỤM ĐẦY ĐỦ ("bao nhiêu"): gõ rụng
# một âm tiết ("hiện tại có bao thiết bị đang bật") là thoát mọi cổng → DEVICE_COMMAND
# turn_on ở Phòng khách (ledger thật id 184, §2026-08-30). Một hàng rào an toàn không được
# phụ thuộc vào việc người dùng gõ đủ chữ, nên đây là lớp chặn theo NGỮ PHÁP, độc lập cổng hỏi.
_ASPECT_ACCENTED = re.compile(r"\bđang\s+(\w+)")
_ASPECT_FOLDED = re.compile(r"\bdang\s+(\w+)")
# Ký tự lấp: giữ ranh giới từ (\w) nhưng không khớp động từ tiếng Việt nào.
_VERB_MASK_CHAR = "x"


def _mask_aspect_governed_verbs(view: TextView) -> TextView:
    """Che động từ bị "đang" chi phối, GIỮ NGUYÊN độ dài chuỗi.

    Chỉ che từ NGAY SAU "đang" nên mệnh lệnh thật còn nguyên: "tắt cái nào đang bật" giữ
    "tắt"; "đang nóng quá, bật điều hoà" giữ "bật". Câu chỉ còn động từ trong cụm "đang V"
    thì has_action_verb=False → tệ nhất là hỏi lại, không bao giờ tự ý ghi trạng thái.

    Lấp bằng ký tự CÙNG ĐỘ DÀI thay vì xoá: `TextView.accepts_folded` căn chỉ số raw↔folded
    để chống va chạm bỏ dấu, lệch một ký tự là hỏng cả lớp đó. Dò span trên CẢ hai dạng
    (có dấu và bỏ dấu) rồi lấp ở cả hai, vì người dùng có thể gõ "dang bat" không dấu.
    """
    spans = [m.span(1) for m in _ASPECT_ACCENTED.finditer(view.raw)]
    spans += [m.span(1) for m in _ASPECT_FOLDED.finditer(view.folded)]
    if not spans:
        return view
    raw, folded = list(view.raw), list(view.folded)
    for start, end in spans:
        for i in range(start, end):
            if i < len(raw):
                raw[i] = _VERB_MASK_CHAR
            if i < len(folded):
                folded[i] = _VERB_MASK_CHAR
    return TextView(raw="".join(raw), folded="".join(folded))


def _classify(
    nu: NormalizedUtterance, view: TextView, params: dict[str, Any]
) -> tuple[UtteranceType, str | None, float]:
    """Luật tất định. Trả (utterance_type, action_hint|None, confidence).

    action_hint chỉ được đặt cho ĐIỀU KHIỂN TƯỜNG MINH. Với hội thoại (huỷ/xác
    nhận/...) action_hint=None nhưng utterance_type nói rõ loại."""
    if nu.has_cancellation:
        return UtteranceType.CANCELLATION, None, 0.95

    # Câu hỏi XÁC NHẬN trạng thái ("… đang tắt đúng không?", "… bật rồi phải không?"): đuôi nghi vấn
    # ở CUỐI câu → hỏi trạng thái, KHÔNG phải lệnh. Xét TRƯỚC mọi nhánh lệnh để "không/chưa" trong
    # đuôi không đẩy câu sang turn_off/cancel (TS-006). Thiết bị nêu trong câu vẫn được đẩy lên
    # Salience Stack ở nhánh state-query hạ nguồn (kind=query) → duy trì mạch hội thoại.
    if _CONFIRM_QUERY_TAIL.search(view):
        return UtteranceType.INFORMATION_QUESTION, None, 0.9

    # KHUNG HỎI xét TRƯỚC MỌI nhánh nội dung — cả nhánh hội thoại (xác nhận/từ chối) lẫn
    # nhánh lệnh tường minh.
    #
    # Trước nhánh LỆNH: "điều hoà đang bật không?" có cả thiết bị lẫn động từ "bật", nhưng
    # người dùng đang HỎI chứ không sai khiến — thực thi nó là tự ý đổi trạng thái nhà khi
    # người ta chỉ muốn biết.
    #
    # Trước nhánh XÁC NHẬN: "có" mở đầu câu hỏi là "có" TỒN TẠI/NGHI VẤN ("có bao nhiêu thiết
    # bị đang bật?", "có ai ở nhà không?"), không phải "có" ĐỒNG Ý. `_CONFIRM` neo ^ nên mọi
    # câu hỏi mở đầu bằng có/được/đúng mà chưa khớp alias thiết bị đều bị nuốt thành
    # CONFIRMATION — pipeline không có nhánh nào đọc CONFIRMATION nên câu rơi tiếp xuống
    # authoring LLM và quay ra hỏi lại phòng (§2026-08-29). Một câu hỏi đọc được thẳng từ
    # snapshot sống không bao giờ được hỏi ngược lại người dùng.
    if _STATE_QUESTION.search(view) and not _POLITE_TAIL.search(view) and not nu.has_negation:
        return UtteranceType.INFORMATION_QUESTION, None, 0.85

    # Cùng lý do, cho câu hỏi KHẢO SÁT nhóm ("thiết bị nào đang bật?", "đang bật những gì?").
    if (
        _SURVEY_QUESTION.search(view)
        and not _COMMAND_VERB_FIRST.search(view)
        and not _POLITE_TAIL.search(view)
        and not nu.has_negation
    ):
        return UtteranceType.INFORMATION_QUESTION, None, 0.85

    no_device = not nu.matched_device_ids
    is_short = len(view.folded.split()) <= 4
    if no_device:
        # Câu có PHỦ ĐỊNH không bao giờ là xác nhận. "đừng" fold thành "dung" TRÙNG "đúng"
        # ("dung") trong _CONFIRM, nên "đừng có bật tivi" (thiết bị chưa khớp) bị nhận nhầm
        # thành CONFIRMATION rồi bị LLM đẩy tiếp thành huỷ (§2026-08-10 n02, cùng họ va chạm
        # bỏ dấu đồng ý↔đóng, khởi động↔khỏi). has_negation là tín hiệu tất định — chặn trước.
        if _CONFIRM.search(view) and not nu.has_negation:
            return UtteranceType.CONFIRMATION, None, 0.9
        if is_short and _REJECT.search(view) and not _NOT_REJECT.search(view):
            return UtteranceType.REJECTION, None, 0.9
        if _SOCIAL.search(view):
            return UtteranceType.SOCIAL_UTTERANCE, None, 0.6

    # Khoá/chốt/mở khoá LÀ động từ điều khiển (trước đây thiếu → mọi lệnh khoá phải nhờ LLM,
    # và lệnh khoá NHÓM "chốt hết cửa" không ra action_hint nên rơi thành no_goal). Xét SAU
    # khung hỏi trạng thái ("cửa khoá chưa") nên câu hỏi vẫn được ưu tiên đúng.
    # Dò động từ trên bản ĐÃ CHE cụm "đang V" (xem `_mask_aspect_governed_verbs`): động từ
    # bị thể tiếp diễn chi phối là mô tả trạng thái, không phải mệnh lệnh.
    verb_view = _mask_aspect_governed_verbs(view)
    has_direction = bool(direction_of(verb_view.raw, bare_verbs=False)[0])
    has_action_verb = bool(
        _TURN_ON.search(verb_view)
        or _TURN_OFF.search(verb_view)
        or has_direction
        or _SET.search(verb_view)
        or _LOCK.search(verb_view)
        or _UNLOCK.search(verb_view)
        or (_DEVICE_REST.search(verb_view) and nu.matched_device_ids)
    )

    # Câu hỏi KIẾN THỨC/chẩn đoán ("máy rửa bát báo lỗi thì cần kiểm tra gì") — cũng xét TRƯỚC
    # nhánh lệnh: nêu thiết bị + có marker chẩn đoán là HỎI, không sai khiến. Nếu không, một
    # động từ giả trong câu hỏi ("báo") biến nó thành lệnh và ta tự ý thao tác thiết bị.
    if _KNOWLEDGE_QUESTION.search(view) and not _POLITE_TAIL.search(view) and not nu.has_negation:
        return UtteranceType.INFORMATION_QUESTION, None, 0.8

    # Phép gán số rút gọn không cần động từ "đặt": "TV âm lượng 20",
    # "điều hoà phòng con 25 độ". Chỉ nhận khi có dimension + thiết bị
    # cụ thể/loại thiết bị; "25 độ" trần vẫn là continuation/slot-fill.
    if params and _mentioned_capability(view) is not None and (nu.matched_device_ids or _match_device_types(view)):
        return UtteranceType.DEVICE_COMMAND, "set", 0.9

    # Điều khiển tường minh khi ĐÃ khớp một thiết bị cụ thể.
    if nu.matched_device_ids and has_action_verb:
        hint = _device_action_hint(view, params, has_device=True)
        if hint is not None:
            return UtteranceType.DEVICE_COMMAND, hint, 0.9

    # Câu hỏi trạng thái.
    if _QUESTION.search(view) and not has_action_verb:
        return UtteranceType.INFORMATION_QUESTION, None, 0.85

    # Lệnh tường minh không kèm thiết bị đã khớp (thiếu target → validator hỏi lại).
    if has_action_verb:
        hint = _device_action_hint(view, params, has_device=bool(nu.matched_device_ids))
        if hint is not None:
            return UtteranceType.DEVICE_COMMAND, hint, 0.85

    if nu.has_correction:
        return UtteranceType.CORRECTION, None, 0.85

    # Không có động từ tường minh, không thiết bị → để LLM diễn giải mục tiêu tự do.
    return UtteranceType.UNKNOWN, None, 0.2


def understand(
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    *,
    model_client: ModelClient | None = None,  # noqa: ARG001  (LLM authoring đi qua graph node)
    last_device_id: str | None = None,
) -> Understanding:
    view = TextView(raw=nu.normalized, folded=nu.folded)

    if not re.search(r"[a-z0-9]", view.folded):
        return Understanding(
            goal=None, candidates=(), note="empty_or_non_semantic_utterance", utterance_type=UtteranceType.UNKNOWN
        )

    params = _extract_params(view)
    utype, action_hint, confidence = _classify(nu, view, params)

    # Tự sửa "chỉ <verb> thôi": rút gọn về đúng động từ, bỏ tham số số đã nêu trước đó
    # ("bật 24 độ nhưng không đổi nhiệt độ, chỉ bật thôi" → chỉ turn_on). Chỉ khi đã là lệnh
    # thiết bị (utype DEVICE_COMMAND) để không đụng câu hỏi/hội thoại.
    if utype == UtteranceType.DEVICE_COMMAND:
        reduce_hint = _only_action_reduction(view)
        if reduce_hint is not None:
            action_hint, params = reduce_hint, {}

    # Chỉ dựng goal tất định cho ĐIỀU KHIỂN THIẾT BỊ TƯỜNG MINH. Hội thoại (huỷ/xác
    # nhận/xã giao) và câu chưa hiểu → goal=None, tầng trên/LLM node xử lý.
    if utype != UtteranceType.DEVICE_COMMAND or action_hint is None:
        note = "needs_llm" if utype == UtteranceType.UNKNOWN else utype.value
        return Understanding(goal=None, candidates=(), note=note, utterance_type=utype)

    # --- Câu ghép N MỆNH ĐỀ ĐỘC LẬP (dấu phẩy / và / rồi / còn / nhưng / sau đó — bất kỳ tổ
    #     hợp nào, không chỉ MỘT liên từ cố định): mỗi mệnh đề tự có thiết bị + hành động + phạm
    #     vi phủ định RIÊNG. Thay cho việc dùng CHUNG một action_hint cho mọi target_device_ids
    #     (QA finding 2 — "tắt" luôn thắng "bật" theo ưu tiên regex) VÀ dùng chung has_negation
    #     TOÀN CÂU (QA finding 3 — mệnh đề dương độc lập bị nuốt, hoặc tệ hơn: phủ định bị gán
    #     nhầm sang thiết bị của MỘT mệnh đề khác — an toàn/đúng-đắn, không chỉ UX). Thử TRƯỚC
    #     nhánh mặc định; CHỈ áp khi tách rõ ràng — nếu không, rơi về hành vi cũ bên dưới.
    independent = _resolve_independent_clauses(nu, ctx, action_hint)
    if independent is not None:
        (
            target_ids,
            target_actions,
            target_parameters,
            independent_excluded_ids,
            primary_hint,
        ) = independent
        label = view.raw.strip()[:120] or "device_command"
        goal = SemanticGoal(
            intent=label,
            goal_description=nu.raw,
            utterance_type=utype,
            raw_utterance=nu.raw,
            confidence=confidence,
            action_hint=primary_hint,
            target_device_ids=target_ids,
            target_actions=target_actions,
            target_parameters=target_parameters,
            target_area=None,
            parameters={},
            polarity="affirmative",
            excluded_device_ids=independent_excluded_ids,
            is_correction=nu.has_correction,
            is_cancellation=nu.has_cancellation,
            references_resolved=True,
        )
        candidates = (IntentCandidate(intent=label, confidence=confidence, rationale="rule"),)
        return Understanding(goal=goal, candidates=candidates, utterance_type=utype)

    targets, refs_resolved = _resolve_targets(nu, last_device_id)
    # Lọc theo TRẠNG THÁI THỰC (LC-004, §"Tắt quạt"): nhiều thiết bị cùng loại + hành động rõ hướng
    # (tắt/giảm → giữ đang bật; bật/mở → giữ đang tắt) → rút gọn theo trạng thái sống. Rút được về
    # ĐÚNG MỘT thì dùng luôn (tránh đoán bừa/hỏi thừa — premature assumption/answer bloat 2505.06120);
    # tất cả cùng trạng thái → giữ nguyên để nhánh clarify bên dưới quyết. Xét TRƯỚC khi xoá-mơ-hồ.
    if len(targets) > 1 and action_hint is not None:
        reduced = reduce_by_state(
            targets,
            action_hint=action_hint,
            live_states={d.device_id: d.state for d in ctx.devices},
        )
        if len(reduced) == 1:
            targets, refs_resolved = reduced, True
    # State-qualified type across rooms: "close the window that is open" carries
    # enough physical evidence when exactly one live window is active.  Require an
    # explicit state qualifier; a bare "close a window" remains ambiguous.
    if not targets and re.search(r"\bđang\s+(?:mở|bật|chạy)\b", view.raw):
        named_types = set(_match_device_types(view))
        state_candidates = [d.device_id for d in ctx.devices if d.device_type in {t.value for t in named_types}]
        reduced = reduce_by_state(
            state_candidates,
            action_hint=action_hint,
            live_states={d.device_id: d.state for d in ctx.devices},
        )
        if len(reduced) == 1:
            targets, refs_resolved = reduced, True
    # Alias khớp NHIỀU thiết bị cùng loại mà câu KHÔNG nêu phòng và KHÔNG có lượng từ nhóm ("mở
    # rèm", "kéo rèm lại" khi có 3 rèm) = mơ hồ "thiết bị/phòng nào?" (alias trùng là CHỦ Ý để hỏi
    # lại). KHÔNG ground vào tất cả — bỏ targets để rơi xuống sufficiency gate → clarify. Lệnh NHÓM
    # ("mở hết rèm") có lượng từ vẫn giữ để _resolve_group_targets xử lý; nêu phòng rõ thì
    # ambiguity=clear nên không lọt vào đây.
    if len(targets) > 1 and nu.ambiguity == Ambiguity.UNRESOLVED and not _GROUP_QUANTIFIER.search(view):
        targets, refs_resolved = [], True
    polarity = "negative" if nu.has_negation else "affirmative"
    target_area = nu.matched_rooms[0] if nu.matched_rooms else (ctx.focus_room or None)

    # Lệnh NHÓM ("tắt hết đèn", "tất cả thiết bị", "chốt hết cửa"): normalizer không khớp
    # slug nào (từ loại trần cố ý mơ hồ) → phân giải tất định thành danh sách slug thật. Chỉ
    # khi affirmative: câu phủ định giữ target rỗng để trả lời "sẽ không làm" theo phòng.
    if not targets and not nu.has_negation:
        group = _resolve_group_targets(view, nu, hint=action_hint)
        if group:
            targets, refs_resolved = group, True
            # "hết đèn" (không nêu phòng) là cả nhà — không gắn phòng ngầm từ ngữ cảnh.
            if not nu.matched_rooms:
                target_area = None

    # "tắt đèn phòng ngủ con": loại thiết bị + phòng nêu rõ, phòng có NHIỀU thiết bị cùng loại
    # nên chưa chọn được — hiểu là mọi thiết bị loại đó trong phòng (không cần "hết"). Xét sau
    # lệnh nhóm, trước carry hội thoại.
    if not targets and not nu.has_negation:
        in_room = _resolve_type_in_room(view, nu)
        if in_room:
            targets, refs_resolved = in_room, True

    # PHÂN PHỐI qua các phòng CHƯA được phủ. Alias thiết bị trong registry đều gắn phòng, nên
    # "mở cửa sổ phòng khách VÀ PHÒNG BỐ MẸ" chỉ khớp alias được vế đầu ("cửa sổ phòng bố mẹ"
    # không xuất hiện nguyên vẹn trong câu). Nếu dừng ở đó, người dùng nhận đúng NỬA lệnh mà
    # không được báo gì. Chỉ chạy khi phòng NÊU RÕ và câu không phủ định, và chỉ thêm thiết bị
    # ĐÚNG LOẠI đã nêu — không mở rộng sang loại khác.
    if targets and nu.room_explicit and len(nu.matched_rooms) > 1 and not nu.has_negation:
        covered = {DEVICE_BY_SLUG[t].room for t in targets if t in DEVICE_BY_SLUG}
        uncovered = tuple(r for r in nu.matched_rooms if r not in covered)
        if uncovered:
            extra = _resolve_type_in_room(view, replace(nu, matched_rooms=uncovered))
            for slug in extra:
                if slug not in targets:
                    targets.append(slug)

    # Tiếp nối hội thoại: lệnh chỉnh capability không thiết bị ("giảm bớt độ sáng") ngay sau
    # khi vừa điều chỉnh MỘT thiết bị → carry thiết bị đó (có guard phòng + capability). Xét SAU
    # lệnh nhóm để "giảm hết đèn" vẫn ra danh sách nhóm thay vì một thiết bị lẻ.
    if not targets and not nu.has_negation:
        carried = _carry_last_device_for_adjust(
            view, action_hint=action_hint, target_area=target_area, last_device_id=last_device_id
        )
        if carried is not None:
            targets, refs_resolved = [carried], True

    # Điều chỉnh môi trường có DIMENSION + PHÒNG nhưng không nêu thiết bị ("cho phòng sáng sủa
    # lên tí"): ground mọi thiết bị trong phòng hỗ trợ capability đó. Xét CUỐI — sau nhóm/loại-
    # trong-phòng/carry — để không lấn các nhánh cụ thể hơn (carry thiết bị đơn của hội thoại).
    if not targets and not nu.has_negation:
        in_room_cap = _resolve_capability_in_room(view, action_hint=action_hint, target_area=target_area)
        if in_room_cap:
            targets, refs_resolved = in_room_cap, True

    # --- LOẠI TRỪ TRONG CÂU (§8, §71): "A và B nhưng đừng bật B", "tắt hết đèn trừ đèn đọc sách".
    #     Tách mệnh đề dương/loại-trừ (parser độc lập), phân giải ĐỘC LẬP qua registry rồi TRỪ.
    #     HỘI NHẬP AN TOÀN: chỉ áp khi CẢ HAI mệnh đề map được thiết bị thật VÀ phần còn lại KHÔNG
    #     rỗng — nếu không, giữ nguyên đường phân giải cũ (không đoán bừa). Mục tiêu là DƯƠNG (bật/
    #     tắt phần còn lại) nên polarity=affirmative; thiết bị bị loại ghi vào excluded_device_ids
    #     làm RÀNG BUỘC (ledger derive avoid:, validator chặn) — belt-and-suspenders với phép trừ. ---
    excluded_ids = []
    if action_hint is not None:
        split = split_exclusion_clause(nu.raw)
        if split is not None:
            pos_ids = _resolve_clause_devices(split.positive_clause, expand=True)
            exc_ids = _resolve_clause_devices(split.exclusion_clause, expand=False)
            remove = [d for d in exc_ids if d in pos_ids]
            keep = [d for d in pos_ids if d not in remove]
            if pos_ids and remove and keep:
                targets, refs_resolved = keep, True
                excluded_ids = remove
                polarity = "affirmative"

    # Sau khi anaphora/type/room đã ground xong, đổi tham số chung (`value`) sang
    # capability thật của câu/thiết bị. Planner chỉ được đọc key capability,
    # không tự đoán dimension từ con số.
    params = _canonicalize_numeric_params(params, view, targets)

    label = view.raw.strip()[:120] or "device_command"

    candidates = (IntentCandidate(intent=label, confidence=confidence, rationale="rule"),)

    goal = SemanticGoal(
        intent=label,
        goal_description=nu.raw,
        utterance_type=utype,
        raw_utterance=nu.raw,
        confidence=confidence,
        action_hint=action_hint,
        target_device_ids=targets,
        target_area=target_area if target_area in ctx.rooms else None,
        parameters=params,
        polarity=polarity,
        excluded_device_ids=excluded_ids,
        is_correction=nu.has_correction,
        is_cancellation=nu.has_cancellation,
        references_resolved=refs_resolved,
    )
    return Understanding(goal=goal, candidates=candidates, utterance_type=utype)


def mentioned_capability(text: str) -> Capability | None:
    """Capability dimension được NHẮC trong câu ("nhiệt độ"→TEMPERATURE, "độ sáng"→BRIGHTNESS…),
    hoặc None. Public wrapper để tầng tổng hợp plan chọn ĐÚNG capability cho lệnh tăng/giảm."""
    return _mentioned_capability(TextView.of(text))


def asks_knowledge(nu: NormalizedUtterance) -> bool:
    """Câu hỏi này là KIẾN THỨC/chẩn đoán (→ RAG) chứ không phải hỏi trạng thái sống?"""
    return bool(_KNOWLEDGE_QUESTION.search(TextView(raw=nu.normalized, folded=nu.folded)))
