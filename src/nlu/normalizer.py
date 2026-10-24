"""Chuẩn hoá tiếng Việt tất định — chạy TRƯỚC mọi lời gọi LLM.

Mục tiêu: bắt các hiện tượng ngôn ngữ dễ sai (phủ định, sửa lời, huỷ, tham chiếu)
bằng luật, và neo tên thiết bị/phòng về slug chính thức trong Registry. Không đoán
bừa: khi nhiều thiết bị hợp lệ cùng khớp mà không có phòng để phân biệt → đánh dấu
UNRESOLVED để tầng trên hỏi lại.

Tái dùng tiện ích văn bản ở `src/agent/text.py` thay vì viết lại.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from src.agent.text import Pattern, TextView, strip_diacritics
from src.iot.registry import DEVICE_SPECS, ROOMS

# Teencode / viết tắt phổ biến → dạng đầy đủ. Chỉ những cái an toàn, không nhập nhằng.
_TEENCODE: dict[str, str] = {
    "k": "không",
    "ko": "không",
    "kh": "không",
    "khong": "không",
    "dc": "được",
    "đc": "được",
    "r": "rồi",
    "dhoa": "điều hoà",
    "đhoa": "điều hoà",
    "dh": "điều hoà",
    "hoi": "hơi",
    "vs": "với",
    "bjo": "bây giờ",
}

# Phủ định — nguồn lỗi lớn nhất, xử lý bằng luật trước LLM.
# "khỏi" (= khỏi cần) fold thành "khoi" TRÙNG với "khởi" trong "khởi động" (= bật) →
# lookahead loại "khởi động" để không đánh phủ định giả (bug va chạm bỏ dấu).
# "đừng"/"dừng" fold thành "dung" TRÙNG "đúng" (xác nhận) → "đúng rồi/vậy/thế/đấy" bị gắn
# phủ định giả (§2026-08-10, đối xứng với va chạm đừng↔đúng ở _CONFIRM). Lookahead loại các
# ĐUÔI XÁC NHẬN sau "đừng/dừng": "đừng rồi" không phải câu lệnh phủ định thực, còn "đừng bật/
# đừng có/đừng tắt" thì đuôi khác nên vẫn bắt bình thường.
_CONFIRM_TAIL = r"(?!\s+(?:rồi|vậy|thế|đấy|nhỉ))"
# "khỏi" là phủ định trong "khỏi bật đèn", NHƯNG là giới từ chỉ hướng trong "ra khỏi nhà",
# "rời khỏi phòng" — nghĩa ngược hẳn. Không loại trừ thì "ra khỏi nhà thôi" (nếp sinh hoạt
# rời nhà) bị gắn phủ định giả → không sinh được kế hoạch nào. Lookbehind phải CỐ ĐỊNH độ
# dài nên tách từng cái; bỏ dấu không đổi số ký tự nên bản folded vẫn khớp đúng.
_KHOI_NOT_MOTION = r"(?<!ra )(?<!rời )(?<!bước )(?<!chạy )(?<!thoát )(?<!tránh )"
_NEGATION = Pattern(
    rf"\b(đừng{_CONFIRM_TAIL}|dừng{_CONFIRM_TAIL}|{_KHOI_NOT_MOTION}khỏi(?!\s*động)|thôi không|thoi khong|không cần|khong can|"
    r"chưa cần|chua can|đừng có|dung co|không phải bật|khong phai bat)\b"
)
# "không"/"chưa" + động từ hành động (bật/tắt/mở/đóng) NGAY SAU cũng là phủ định — "chưa tắt
# X (vội) đâu" = hoãn/từ chối hành động đó, cùng họ với "không tắt X". Bắt buộc "chưa" đứng
# TRƯỚC động từ (khác hẳn "chưa" CUỐI câu dùng làm tiểu từ hỏi yes/no: "đèn tắt chưa?" — xem
# _STATE_QUESTION) nên không va chạm với nhánh câu hỏi.
_NEGATION_VERB = Pattern(r"\b(không|chưa) (bật|tắt|mở|đóng|bat|tat|mo|dong)\b")

# Sửa lời — người dùng đổi ý giữa chừng.
_CORRECTION = Pattern(
    r"\b(à mà|a ma|nhầm|nham|ý tôi là|y toi la|ý mình là|không phải|khong phai|"
    r"sửa lại|sua lai|đổi thành|doi thanh|nói lại|noi lai)\b"
)
_CORRECTION_COMMA = Pattern(r"\b(không phải|khong phai)\b[^,]*,|,?\s*(không phải|khong phai)")

# Huỷ — bỏ hẳn ý định đang treo.
_CANCELLATION = Pattern(
    r"\b(huỷ|hủy|huy|thôi khỏi|thoi khoi|thôi vậy|thoi vay|bỏ đi|bo di|"
    r"quên đi|quen di|bỏ hết|bo het|khỏi làm|khoi lam|thôi không làm nữa|"
    r"thoi khong lam nua|không làm nữa|khong lam nua|thôi không cần nữa|thoi khong can nua|"
    r"không cần nữa|khong can nua|không cần gì nữa|khong can gi nua|"
    # "đừng <việc> nữa" (khoan, thôi đừng dọn nữa) = HUỶ. KHÔNG đưa "giữ nguyên" vào đây: nó chỉ là
    # huỷ khi ĐỨNG MỘT MÌNH ("thôi giữ nguyên"), còn trong câu có hành động ("tăng sáng bàn học, còn
    # đèn ngủ giữ nguyên") nó là RÀNG BUỘC giữ phần còn lại — xử lý cục bộ ở dialogue, không set cờ toàn cục.
    r"đừng\s+\S+\s+nữa|dung\s+\S+\s+nua|"
    # "thôi không <động từ CỤ THỂ> nữa" — TỔNG QUÁT hoá qua vựng động từ điều khiển (không chỉ
    # "làm" như trên): "thôi không tắt nữa" cũng là huỷ y hệt "thôi không làm nữa".
    r"thôi không (bật|tắt|mở|đóng|khoá|khóa) nữa|thoi khong (bat|tat|mo|dong|khoa) nua|"
    r"không (bật|tắt|mở|đóng|khoá|khóa) nữa|khong (bat|tat|mo|dong|khoa) nua)\b"
)

# "khoan, đừng <động từ>" — phủ định TRẦN nhắm ĐÚNG hành động đang treo, KHÔNG kèm cụm bổ nghĩa
# nào khác (thiết bị/mode/mức độ). Anchor "^...$" trên TOÀN CÂU để loại các câu có phần còn lại
# sau động từ ("đừng bật CHẾ ĐỘ MẠNH", "đừng đóng RÈM", "đừng tắt HẲN") — những câu đó là RÀNG
# BUỘC/loại trừ cục bộ (xử lý ở turn_intent/exclusion), không phải huỷ trắng cả hành động.
_CANCELLATION_BARE = Pattern(
    r"^(khoan[,]?\s*)?(đừng|dừng)\s+(bật|tắt|mở|đóng|khởi động|khoá|khóa)"
    r"\s*(nữa|đi|lại|thôi)?[\s,.!?]*$"
)
# "thôi để nguyên/để vậy" — ĐỨNG MỘT MÌNH (không có nội dung nào khác quanh nó) = huỷ, giữ
# nguyên trạng. Anchor để KHÔNG bắt nhầm khi "để nguyên" là ràng buộc CỤC BỘ trong câu có hành
# động khác (xem giải thích trong _CANCELLATION ở trên).
_CANCELLATION_LEAVE_AS_IS = Pattern(r"^(thôi[,]?\s*)?để (nguyên|vậy)\b[\s,.!?]*$")

# Câu KẾT THÚC hội thoại: người dùng đã hài lòng / cảm ơn / không cần thêm — KHÔNG mang yêu
# cầu mới. Tách khỏi _CANCELLATION vì đây là ĐÓNG lịch sự chứ không phải huỷ một ý định treo.
_CLOSING = Pattern(
    r"\b(đủ rồi|du roi|được rồi|duoc roi|xong rồi|xong roi|ổn rồi|on roi|tốt rồi|tot roi|"
    r"thế thôi|the thoi|vậy thôi|vay thoi|thế là được|the la duoc|thế là ổn|the la on|"
    r"cảm ơn|cam on|cám ơn|nhiêu đó thôi|nhieu do thoi)\b"
)
_HAS_NUMBER = Pattern(r"\d")

# HOÃN THỜI GIAN: người dùng nói chưa cần làm gì BÂY GIỜ (thường kèm mốc tương lai "mai/tối nay")
# → KHÔNG hành động ngay (no_op). "Mai có khách, tối nay chưa cần làm gì." Tín hiệu chốt là cụm
# CHƯA-CẦN-LÀM (không phải chỉ "mai" — "mai bật đèn" vẫn là lệnh, có động từ, sẽ bị loại ở call-site).
_DEFER_NOW = Pattern(
    r"\b(chưa cần làm|chua can lam|chưa cần|chua can|chưa phải làm|chua phai lam|"
    r"khỏi cần làm|khoi can lam|chưa làm gì|chua lam gi|không cần làm gì|khong can lam gi|"
    r"chưa vội|chua voi|để sau|de sau|lát nữa hãy|lat nua hay)\b"
)
# Hoãn MỘT THIẾT BỊ cụ thể: "để máy rửa bát lát nữa". Khác với một
# lệnh có mốc tương lai ("lát nữa bật..."): cấu trúc này đặt đối tượng sau
# "để" và không mang động từ điều khiển. Caller lưu thiết bị với kind=deferred
# để mô tả hồi chỉ ("cái máy ... để lát nữa") có thể tìm lại tất định.
_DEFER_OBJECT_LATER = Pattern(r"\bđể\s+.+\s+lát nữa\b")


def is_deferral(nu: NormalizedUtterance) -> bool:
    """Câu HOÃN: chưa cần làm gì lúc này (no_op). Chỉ khi KHÔNG có động từ hành động của riêng
    nó (để "mai nhớ bật đèn" — có 'bật' — vẫn là lệnh/nhắc, không nuốt thành no_op)."""
    if nu.has_action_verb:
        return False
    # Một mệnh đề hoãn không được nuốt câu hỏi độc lập phía sau:
    # "Thôi chuyện loa để sau. Camera ... thế nào?" phải route STATE_QUERY.
    if "?" in nu.raw:
        return False
    view = TextView(raw=nu.normalized, folded=nu.folded)
    return bool(_DEFER_NOW.search(view) or _DEFER_OBJECT_LATER.search(view))


def is_closing(nu: NormalizedUtterance) -> bool:
    """Câu XÃ GIAO kết thúc hội thoại ("cảm ơn nhé", "thế thôi", "được rồi") — KHÔNG mang yêu
    cầu mới. Chỉ khi KHÔNG có động từ hành động/tham chiếu/thiết bị của riêng nó, để câu có nội
    dung thật ("cảm ơn, bật thêm đèn nữa nhé") vẫn đi đường lệnh bình thường."""
    if nu.has_action_verb or nu.has_reference or nu.matched_device_ids:
        return False
    return bool(_CLOSING.search(TextView(raw=nu.normalized, folded=nu.folded)))

# Tham chiếu — cần context để giải (nó, cái đó, để như cũ...).
_REFERENCE = Pattern(
    r"\b(nó|no|cái đó|cai do|cái kia|cai kia|thiết bị đó|thiet bi do|cái vừa nãy|"
    r"cai vua nay|cái nãy|để như cũ|de nhu cu|như cũ|nhu cu|lúc nãy|luc nay|"
    # Ordinal trên nhóm đã nêu: "cái thứ hai/cái đầu/cái cuối".
    r"cái thứ\s+(?:\d+|nhất|một|hai|ba|tư|bốn|năm)|cái đầu|cái cuối|"
    # Mô tả hồi chỉ, không phụ thuộc tên thiết bị/case cụ thể.
    r"cái\s+\w+\s+(?:mình|tôi)\s+vừa nói|(?:mình|tôi)\s+vừa nói\s+để\s+lát nữa)\b"
)
# Động từ trần không tân ngữ ("bật lên", "tắt đi") cũng là tham chiếu ngầm.
_REFERENCE_BARE = Pattern(
    r"\b(bật lên|bat len|tắt đi|tat di|mở ra|mo ra|đóng lại|dong lai|"
    r"mở khoá lại|mở khóa lại|mo khoa lai|khoá lại|khóa lại|khoa lai)\b"
)

# Động từ hành động tường minh: câu MANG động từ này là LỆNH MỚI, không phải câu trả
# lời trần cho một câu hỏi làm rõ (dùng trong xử lý hội thoại để không nuốt lệnh mới vào
# pending cũ). "khoi" có lookahead loại "khởi động" ở negation nhưng ở ĐÂY "khởi động"
# LÀ động từ hành động nên cứ khớp bình thường.
# "đóng"(=tắt) fold thành "dong" TRÙNG "đồng" trong "đồng ý"(=gật đầu) → lookahead loại
# "đồng ý/đồng í" để câu gật đầu không bị coi là mang động từ hành động (va chạm bỏ dấu).
_ACTION_VERB = Pattern(
    # "bát" trong "máy rửa bát" fold thành "bat" trùng "bật". Loại khi đứng
    # ngay sau "rửa/rua"; nếu không, câu hoãn "để máy rửa bát lát nữa" bị coi
    # là lệnh bật và thực thi sớm.
    r"\b((?<!rửa )bật|(?<!rua )bat|tắt|tat|mở|mo|đóng(?!\s*[ýí])|dong(?!\s*[yi])|khởi động|khoi dong|chạy|chay|"
    r"đặt|dat|(?<!cửa )(?<!cổng )chỉnh|tăng|tang|giảm|giam|thêm|them|bớt|bot|"
    r"khoá|khóa|khoa|mở khoá|mo khoa)\b"
)


# Câu GẬT ĐẦU / TỪ CHỐI cho một việc đang chờ xác nhận (HITL). Chỉ dùng cho câu trả
# lời NGẮN, không mang lệnh riêng — xem classify_reply.
_CONFIRM = Pattern(
    r"\b(đồng ý|dong y|đồng í|dong i|ừ|u|ừm|um|ờ|o|okê|oke|okay|ok|được|duoc|duyệt|duyet|"
    r"xác nhận|xac nhan|chốt|chot|làm đi|lam di|cứ làm|cu lam|đúng rồi|dung roi|"
    r"tiến hành|tien hanh|nhất trí|nhat tri|có|co)\b"
)
_REJECT = Pattern(
    r"\b(thôi|thoi|không|khong|đừng|dung|khỏi|khoi|hủy|huỷ|huy|bỏ|bo|"
    r"từ chối|tu choi|khoan|đừng làm|dung lam|không cần|khong can)\b"
)


class Ambiguity(StrEnum):
    CLEAR = "clear"  # tối đa một thiết bị đích, không nhập nhằng
    RESOLVABLE = "resolvable"  # nhập nhằng nhưng có phòng/context để phân giải
    UNRESOLVED = "unresolved"  # nhiều đích hợp lệ, không đủ căn cứ để chọn


class ConversationalAct(StrEnum):
    """Hành vi hội thoại của MỘT lượt, phân tích ĐỘC LẬP (không carry mục tiêu lượt trước).

    Dùng cho bất biến turn-isolation: một lượt KẾT THÚC/HUỶ (đóng/gật đầu/huỷ) KHÔNG được để
    ngữ cảnh lượt trước hồi sinh thành hành động mới."""

    COMMAND = "command"  # có nội dung hành động riêng (động từ/thiết bị/phòng nêu rõ/con số)
    CLOSE = "close"  # đóng lịch sự: "đủ rồi cảm ơn", "được rồi"
    CANCEL = "cancel"  # huỷ: "bỏ đi", "không cần nữa"
    ACK = "ack"  # gật đầu trần: "ừ", "ok"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NormalizedUtterance:
    raw: str
    normalized: str  # đã hạ chữ thường, gộp khoảng trắng, giãn teencode
    folded: str  # bản không dấu
    has_negation: bool
    has_correction: bool
    has_cancellation: bool
    has_reference: bool
    # Câu có động từ hành động của riêng nó (bật/tắt/khởi động/đóng...) → LỆNH MỚI,
    # không phải câu trả lời trần cho clarification (mặc định False để _synthetic_nu cũ chạy).
    has_action_verb: bool = False
    matched_device_ids: tuple[str, ...] = field(default=())
    matched_rooms: tuple[str, ...] = field(default=())
    # Phòng có được NÊU RÕ (alias trong câu HOẶC "phòng của tôi" → phòng riêng người nói)
    # không, hay chỉ SUY từ focus_room lượt trước. Chỉ phòng nêu rõ mới được gom cả-phòng
    # ("tắt đèn phòng của tôi"); phòng từ focus thì không (tránh tắt nhầm cả phòng đang đứng).
    room_explicit: bool = False
    # Cụm chỉ phòng NÓI CHUNG khớp NHIỀU phòng chuẩn ("phòng ngủ" → Phòng ngủ bố mẹ +
    # Phòng ngủ con). Đây là thông tin THU HẸP, không phải đích: câu "bật đèn phòng ngủ"
    # KHÔNG được bật đèn cả hai phòng, nhưng cũng KHÔNG được coi như người dùng chẳng nói
    # gì. Tách khỏi `matched_rooms` (đã cam kết) để tầng trên hỏi lại đúng hai phòng đó.
    partial_rooms: tuple[str, ...] = field(default=())
    ambiguity: Ambiguity = Ambiguity.CLEAR


def _expand_teencode(text: str) -> str:
    return " ".join(_TEENCODE.get(tok, tok) for tok in text.split())


# Danh từ THIẾT BỊ tiếng Anh → tiếng Việt, để câu trộn Việt-Anh ("bật cái air conditioner
# phòng khách lên") ground được về alias thật (§2026-08-10). CHỈ danh từ thiết bị rõ nghĩa,
# KHÔNG đưa động từ/tính từ (turn on/bright...) — phần đó để LLM diễn giải, tránh dịch máy móc
# làm hỏng câu. Cụm nhiều từ xử lý TRƯỚC (air conditioner trước air), để thay đúng cụm dài.
_EN_DEVICE: tuple[tuple[str, str], ...] = (
    ("air conditioner", "điều hoà"),
    ("air con", "điều hoà"),
    ("air purifier", "máy lọc không khí"),
    ("water heater", "bình nóng lạnh"),
    ("dish washer", "máy rửa bát"),
    ("dishwasher", "máy rửa bát"),
    ("television", "tivi"),
    ("curtains", "rèm"),
    ("curtain", "rèm"),
    ("blinds", "rèm"),
    ("lights", "đèn"),
    ("light", "đèn"),
    ("lamp", "đèn"),
    ("speaker", "loa"),
    ("heater", "máy sưởi"),
    ("vacuum", "robot hút bụi"),
    ("window", "cửa sổ"),
    ("door", "cửa"),
)


# Tên PHÒNG tiếng Anh → tiếng Việt. Cụm dài (master bedroom) trước cụm ngắn (bedroom).
_EN_ROOM: tuple[tuple[str, str], ...] = (
    ("master bedroom", "phòng ngủ bố mẹ"),
    ("parents bedroom", "phòng ngủ bố mẹ"),
    ("parents' bedroom", "phòng ngủ bố mẹ"),
    ("kids room", "phòng ngủ con"),
    ("kids' room", "phòng ngủ con"),
    ("children's room", "phòng ngủ con"),
    ("child's room", "phòng ngủ con"),
    ("living room", "phòng khách"),
    ("bedroom", "phòng ngủ"),
    ("kitchen", "phòng bếp"),
)


def _expand_english_devices(text: str) -> str:
    """Thay danh từ THIẾT BỊ và tên PHÒNG tiếng Anh bằng tiếng Việt (khớp theo RANH GIỚI TỪ,
    cụm dài trước).

    Hoạt động trên text đã lowercase. Không dịch động từ/tính từ — chỉ danh từ thiết bị/phòng,
    để tầng match alias tất định nhận ra. Ý định/ngữ nghĩa còn lại vẫn do LLM lo. Phòng thay
    TRƯỚC để "living room light" → "phòng khách light" → "phòng khách đèn" (không bị "room"
    trong "living room" dính luật khác)."""
    for en, vi in _EN_ROOM:
        text = re.sub(rf"\b{re.escape(en)}\b", vi, text)
    for en, vi in _EN_DEVICE:
        text = re.sub(rf"\b{re.escape(en)}\b", vi, text)
    return text


def _match_rooms(view: TextView) -> tuple[str, ...]:
    # So khớp không phân biệt hoa/thường + qua alias ("phòng con" → "Phòng ngủ con").
    from src.iot.registry import ROOM_ALIASES

    candidates: list[tuple[str, str, int]] = []
    for room in ROOMS:
        aliases = (room.lower(), *ROOM_ALIASES.get(room, ()))
        hits = []
        for alias in aliases:
            folded = strip_diacritics(alias.lower())
            pos = view.folded.find(folded)
            if pos >= 0 and view.has(alias):
                hits.append((folded, pos))
        if hits:
            alias, pos = max(hits, key=lambda item: len(item[0]))
            candidates.append((room, alias, pos))
    # Prefer the most specific overlapping room phrase.  "phòng ngủ bố mẹ"
    # must not also match the canonical generic room "phòng ngủ".
    kept = [
        room
        for room, alias, pos in candidates
        if not any(
            other_room != room
            and len(other_alias) > len(alias)
            and other_pos <= pos
            and pos + len(alias) <= other_pos + len(other_alias)
            for other_room, other_alias, other_pos in candidates
        )
    ]
    return tuple(kept)


def _match_partial_rooms(view: TextView) -> tuple[str, ...]:
    """Cụm chỉ phòng khớp NHIỀU phòng chuẩn — thu hẹp chứ không chọn hộ.

    Người dùng trả lời "phòng ngủ" cho câu hỏi phòng là chuyện bình thường, nhưng căn hộ
    có HAI phòng ngủ nên không alias nào của một phòng đơn khớp được; trước bản vá lượt đó
    ra rỗng và hệ thống lặp lại y nguyên câu hỏi cũ. Ở đây ta suy TỪ REGISTRY: một cụm là
    "phòng ngủ chung" nếu nó là TIỀN TỐ theo từ của ≥2 tên phòng chuẩn. Không có bảng cứng
    nào — thêm/bớt phòng trong registry là luật này tự đổi theo.
    """
    words: list[str] = []
    for room in ROOMS:
        folded_room = strip_diacritics(room.lower())
        parts = folded_room.split()
        # Mọi tiền tố THỰC SỰ (ngắn hơn cả tên) — "phong", "phong ngu" cho "phong ngu con".
        for size in range(1, len(parts)):
            words.append(" ".join(parts[:size]))
    matched: list[str] = []
    for prefix in sorted(set(words), key=len, reverse=True):
        pos = view.folded.find(prefix)
        if pos < 0:
            continue
        # Phải là cụm TRỌN TỪ, không phải một phần của từ dài hơn.
        before_ok = pos == 0 or not view.folded[pos - 1].isalnum()
        end = pos + len(prefix)
        after_ok = end >= len(view.folded) or not view.folded[end].isalnum()
        if not (before_ok and after_ok):
            continue
        hits = [r for r in ROOMS if strip_diacritics(r.lower()).startswith(prefix + " ")]
        # Phải THỰC SỰ thu hẹp: "phòng" trần khớp mọi phòng nên không cho thêm thông tin gì.
        if 2 <= len(hits) < len(ROOMS):
            matched = hits
            break  # tiền tố DÀI nhất thắng: "phòng ngủ" cụ thể hơn "phòng".
    return tuple(matched)


def _alias_hits(view: TextView) -> dict[str, tuple[str, int]]:
    """slug -> (alias đã khớp ở dạng bỏ dấu, vị trí trong câu); alias DÀI NHẤT thắng cho mỗi thiết bị.

    Tách riêng vì hai người dùng cần đúng dữ liệu này: `_match_devices` (chọn đích) và
    `_classify_ambiguity` (phân biệt MỘT cụm khớp nhiều thiết bị với NHIỀU cụm rời nhau).
    Nếu mỗi nơi tự dò lại alias thì hai bên sẽ trôi khỏi nhau (§4).
    """
    best: dict[str, tuple[str, int]] = {}
    for spec in DEVICE_SPECS:
        found: list[tuple[str, int]] = []
        for alias in spec.aliases:
            fa = strip_diacritics(alias)
            pos = view.folded.find(fa)
            if pos < 0 and alias in view.raw:
                pos = view.raw.find(alias)
            if pos >= 0:
                found.append((fa, pos))
        if found:
            best[spec.slug] = max(found, key=lambda c: len(c[0]))  # alias dài nhất cho thiết bị này
    return best


def _match_devices(view: TextView, rooms: tuple[str, ...], *, room_explicit: bool = False) -> tuple[str, ...]:
    """Neo tên thiết bị về slug qua alias, trả về theo THỨ TỰ XUẤT HIỆN trong câu.

    Chỉ thu hẹp khi alias của thiết bị này là *chuỗi con* của alias thiết bị khác
    (cùng cụm, cụ thể hơn): "đèn ngủ" ⊂ "đèn ngủ con" → chọn "đèn ngủ con". KHÔNG gộp
    hai lượt nhắc RỜI NHAU ("đèn phòng khách" và "đèn bếp") chỉ vì một cái dài hơn.
    """
    best = _alias_hits(view)
    if not best:
        return ()

    # Thu hẹp theo bao hàm chuỗi con (cùng cụm, cụ thể hơn).
    slugs = list(best)
    matched = [
        b
        for b in slugs
        if not any(a != b and best[b][0] != best[a][0] and best[b][0] in best[a][0] for a in slugs)
    ]
    if not matched:
        matched = slugs

    if rooms:
        in_room = [slug for slug in matched if any(_spec(slug).room == r for r in rooms)]
        if in_room:
            # Thu hẹp theo phòng là để PHÂN GIẢI những thiết bị cùng khớp MỘT cụm ("rèm" → 4
            # cái rèm). Nó KHÔNG được quyền vứt một thiết bị mà người dùng gọi bằng CỤM RIÊNG
            # của nó: "mở rèm phòng khách và rèm bếp" là HAI đích, nhưng bộ lọc cũ thấy phòng
            # 'Phòng khách' rồi loại thẳng rem_bep, mất hẳn vế sau của câu.
            # Giữ nguyên hành vi phủ quyết an toàn bên dưới: khi KHÔNG thiết bị nào nằm trong
            # phòng đã nêu (in_room rỗng) thì phòng vẫn thắng và không đích nào được giữ.
            span_count: dict[tuple[str, int], int] = {}
            for slug in matched:
                span_count[best[slug]] = span_count.get(best[slug], 0) + 1
            named_alone = [s for s in matched if span_count[best[s]] == 1 and s not in in_room]
            matched = list(dict.fromkeys(in_room + named_alone))
        elif room_explicit:
            # Phòng NÊU TƯỜNG MINH phủ quyết khớp tên, kể cả khi chỉ có một alias khớp.
            # "Bật đèn trần phòng bếp": alias "đèn trần" chỉ đăng ký cho đèn chùm Phòng
            # khách, nên guard `len(matched) > 1` cũ bỏ qua bộ lọc và trả về thiết bị ở
            # phòng NGƯỜI DÙNG KHÔNG HỀ NHẮC. Trả rỗng để tầng sau ground theo
            # capability+phòng hoặc hỏi lại: hỏi lại là kiểu hỏng an toàn, hành động sai
            # phòng thì không. `focus_room` NGẦM không có quyền này — nó chỉ là ưu tiên
            # mềm, nếu không "loa bếp" nói từ phòng khách sẽ bị vứt mất.
            matched = []

    matched.sort(key=lambda s: best[s][1])  # theo thứ tự xuất hiện trong câu
    return tuple(matched)


def _spec(slug: str):  # nội bộ, trả DeviceSpec
    from src.iot.registry import DEVICE_BY_SLUG

    return DEVICE_BY_SLUG[slug]


def _classify_ambiguity(
    devices: tuple[str, ...], rooms: tuple[str, ...], view: TextView | None = None
) -> Ambiguity:
    if len(devices) <= 1:
        return Ambiguity.CLEAR
    # LIỆT KÊ ≠ MƠ HỒ. "bật đèn bếp và đèn bàn ăn" gọi tên hai thiết bị bằng HAI cụm RỜI NHAU —
    # không có gì để phân giải, người dùng muốn CẢ HAI. Mơ hồ thật là khi MỘT cụm khớp nhiều
    # thiết bị ("đèn ngủ" → hai đèn ngủ ở hai phòng): lúc đó mới phải hỏi chọn cái nào.
    # Trước bản vá cả hai đều ra UNRESOLVED, nên mọi lệnh "A và B" bị hỏi lại thay vì thực hiện.
    if view is not None:
        hits = _alias_hits(view)
        spans = {hits[slug] for slug in devices if slug in hits}
        if len(spans) == len(devices):
            return Ambiguity.CLEAR
    # Nhiều thiết bị: nếu tất cả cùng một phòng đã nêu, coi là có thể phân giải;
    # nếu trải nhiều phòng khác nhau mà không nêu phòng → không thể chọn.
    device_rooms = {_spec(slug).room for slug in devices}
    if rooms and device_rooms <= set(rooms):
        return Ambiguity.RESOLVABLE
    return Ambiguity.UNRESOLVED


def classify_reply(nu: NormalizedUtterance) -> str | None:
    """Phân loại câu TRẢ LỜI NGẮN cho một việc đang chờ xác nhận: 'confirm' | 'reject' | None.

    Chỉ áp cho câu NGẮN không mang lệnh riêng: câu có động từ hành động / nêu thiết bị,
    hoặc câu dài (>4 từ, thường là câu hỏi/lệnh mới) → None (để xử lý như lệnh thường).
    Ưu tiên TỪ CHỐI khi lẫn lộn — mặc định an toàn là KHÔNG thực thi."""
    if nu.has_action_verb or nu.matched_device_ids:
        return None
    if len(nu.normalized.split()) > 4:
        return None
    view = TextView(raw=nu.normalized, folded=nu.folded)
    if _REJECT.search(view):
        return "reject"
    if _CONFIRM.search(view):
        return "confirm"
    return None


def classify_conversational_act(nu: NormalizedUtterance) -> ConversationalAct:
    """Phân loại hành vi hội thoại của MỘT lượt, phân tích ĐỘC LẬP với ngữ cảnh trước.

    Nguyên tắc: một câu có NỘI DUNG HÀNH ĐỘNG RIÊNG (động từ, thiết bị nêu rõ, phòng NÊU RÕ
    trong câu, hoặc con số) KHÔNG BAO GIỜ là câu kết thúc — nên "25 độ thôi", "phòng khách
    thôi", "bật mỗi đèn ngủ thôi", "thôi cứ để điều hoà bật" đều là COMMAND, không phải huỷ.
    Chỉ khi câu KHÔNG có nội dung riêng mới xét cue kết thúc/huỷ/gật đầu. Dùng ``room_explicit``
    (phòng NÊU RÕ) chứ không phải ``matched_rooms`` — phòng suy từ focus lượt trước không tính
    là nội dung riêng của lượt này."""
    view = TextView(raw=nu.normalized, folded=nu.folded)
    has_own_content = (
        nu.has_action_verb
        or bool(nu.matched_device_ids)
        or nu.room_explicit
        or bool(_HAS_NUMBER.search(view))
    )
    if has_own_content:
        return ConversationalAct.COMMAND
    if nu.has_cancellation:
        return ConversationalAct.CANCEL
    if _CLOSING.search(view):
        return ConversationalAct.CLOSE
    # ACK chỉ nhận câu CỰC NGẮN (≤2 từ) — cue gật đầu "ờ/o/ừ/u" bỏ dấu trùng khởi đầu nhiều từ
    # thường ("ở đây" → "o day", "úp"...); giới hạn độ dài để không nuốt nhầm câu có nội dung.
    if len(nu.normalized.split()) <= 2 and _CONFIRM.search(view):
        return ConversationalAct.ACK
    return ConversationalAct.UNKNOWN


# "phòng của tôi / phòng mình / phòng riêng" — người nói nhắc PHÒNG RIÊNG của họ. Giải thành
# phòng riêng (suy từ danh tính người nói) chứ không phải phòng đang đứng. "của con" cũng nhận
# vì trẻ hay tự xưng "con" ("bật đèn phòng của con").
_OWN_ROOM = Pattern(
    r"(của tôi|cua toi|của mình|cua minh|của tớ|cua to|của con|cua con|của cháu|cua chau"
    r"|phòng tôi|phong toi|phòng mình|phong minh|phòng riêng|phong rieng|phòng của tôi|phong cua toi)"
)


def analyze(
    text: str,
    *,
    focus_room: str | None = None,
    speaker_home_room: str | None = None,
    speaker_private_room: str | None = None,
) -> NormalizedUtterance:
    """Phân tích tất định một câu.

    ``speaker_private_room`` giải cụm sở hữu; ``speaker_home_room`` là phòng mặc định
    dùng để thu hẹp cụm chung như "phòng ngủ". Caller cũ chỉ truyền home vẫn tương thích.
    """
    normalized = _expand_teencode(_expand_english_devices(re.sub(r"\s+", " ", text.strip().lower())))
    view = TextView(raw=normalized, folded=strip_diacritics(normalized))

    has_negation = bool(_NEGATION.search(view) or _NEGATION_VERB.search(view))
    has_correction = bool(_CORRECTION.search(view) or _CORRECTION_COMMA.search(view))

    # Neo thiết bị/phòng: với câu sửa lời có dấu phẩy ("không phải X, bật Y") chỉ neo ở
    # VẾ SAU dấu phẩy — phần trước là thứ bị phủ nhận, không được lấy làm đích/phòng.
    match_src = normalized.rsplit(",", 1)[-1].strip() if (has_correction and "," in normalized) else normalized
    match_view = TextView(raw=match_src, folded=strip_diacritics(match_src))

    rooms = _match_rooms(match_view)
    # Cụm phòng chung chỉ có nghĩa khi câu CHƯA chốt được phòng nào — "bật đèn phòng ngủ
    # con" đã cam kết rồi thì không còn gì để thu hẹp.
    partial_rooms = () if rooms else _match_partial_rooms(match_view)
    # Phòng nêu RÕ = alias trong câu, HOẶC "phòng của tôi" → phòng riêng người nói. Cả hai đều
    # là ý chỉ định phòng có chủ đích → được phép gom cả-phòng. focus_room thì KHÔNG (chỉ ngầm).
    room_explicit = bool(rooms)
    own_room = speaker_private_room or speaker_home_room
    if not rooms and own_room in ROOMS and _OWN_ROOM.search(view):
        rooms = (own_room,)
        room_explicit = True
        partial_rooms = ()
    # Cụm phòng chung + danh tính người nói: "phòng ngủ" từ bố/mẹ nghĩa là PHÒNG NGỦ CỦA HỌ.
    # Nhà có hai phòng ngủ nên cụm đó tự nó mơ hồ, nhưng người nói là bằng chứng thu hẹp có
    # sẵn — hỏi lại khi chỉ còn đúng một ứng viên hợp lý là bắt người dùng trả lời thứ hệ
    # thống đã biết. Chỉ áp khi phòng riêng của người nói NẰM TRONG tập vừa thu hẹp; nếu
    # không (vd "phòng ngủ" nhưng người nói ở phòng khách) thì vẫn hỏi lại như thường.
    if not rooms and partial_rooms and speaker_home_room in partial_rooms:
        rooms = (speaker_home_room,)
        room_explicit = True
        partial_rooms = ()
    if not rooms and focus_room and focus_room in ROOMS:
        rooms = (focus_room,)
    devices = _match_devices(match_view, rooms, room_explicit=room_explicit)

    # Dự phòng: nếu vế sau vẫn còn nhiều đích, thiết bị nhắc SAU là đích thật.
    if has_correction and len(devices) > 1:
        devices = (devices[-1],)
    has_cancellation = bool(
        _CANCELLATION.search(view) or _CANCELLATION_BARE.search(view) or _CANCELLATION_LEAVE_AS_IS.search(view)
    )
    has_reference = bool(_REFERENCE.search(view) or _REFERENCE_BARE.search(view))
    has_action_verb = bool(_ACTION_VERB.search(view))

    return NormalizedUtterance(
        raw=text,
        normalized=normalized,
        folded=view.folded,
        has_negation=has_negation,
        has_correction=has_correction,
        has_cancellation=has_cancellation,
        has_reference=has_reference,
        has_action_verb=has_action_verb,
        matched_device_ids=devices,
        matched_rooms=rooms,
        room_explicit=room_explicit,
        partial_rooms=partial_rooms,
        ambiguity=_classify_ambiguity(devices, rooms, match_view),
    )
