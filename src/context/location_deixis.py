"""Bộ giải DEIXIS VỊ TRÍ — "ở đây / phòng này / phòng đó / cùng phòng đó" → MỘT phòng cụ thể.

Slice 2 của Context-Transducer (docs/BLUEPRINT_CONTEXT_TRANSDUCER): câu chỉ nêu vị trí bằng đại từ
chỉ định, cần phân giải thành phòng thật từ NGỮ CẢNH trước khi ground thiết bị ("bật đèn ngủ ở đây"
với người đang ở phòng bố mẹ → đèn ngủ phòng bố mẹ). Module thuần, tất định, test được.

Ưu tiên nguồn phòng (tất định, §14): cảm biến HIỆN DIỆN (đang đứng đâu) → vị trí hội thoại (client
cấp hoặc "chuyển sang phòng X") → phòng CHỐT gần nhất trong ledger (mạch hội thoại). Chỉ trả phòng
HỢP LỆ trong registry; mơ hồ (nhiều phòng cùng hiện diện) → None để tầng trên hỏi lại.
"""

from __future__ import annotations

import re

from src.agent.text import strip_diacritics

# Deixis vị trí, viết KHÔNG DẤU (khớp trên bản strip_diacritics), tách hai lớp theo NGUỒN phòng:
#   HERE (vật lý — "đang ở đâu"): "ở đây/chỗ này/phòng này" → ưu tiên vị trí hội thoại/hiện diện.
#   THAT (hồi chỉ — "phòng vừa nhắc"): "phòng đó/cùng phòng đó/chỗ đó" → ưu tiên phòng CHỐT trong
#   ledger (mạch hội thoại), vì cảm biến hiện diện có thể là MẶC ĐỊNH registry gây hiểu nhầm.
_HERE_PHRASES = ("o day", "o cho nay", "cho nay", "ngay day", "tai day", "phong nay")
_THAT_PHRASES = ("cung phong do", "cung phong nay", "cung phong", "phong do", "cho do", "phong kia")

# Bản CÓ DẤU để CẮT cụm deixis khỏi câu gốc khi viết lại tường minh (Resolve): bỏ đại từ chỉ định
# ("đó/đây") để câu sau khi thay phòng KHÔNG còn has_reference chặn slot-fill/refinement. Dài trước
# ngắn ("cùng phòng đó" trước "phòng đó" trước "cùng phòng") để cắt trọn cụm.
_DEIXIS_DIACRITIC = (
    "cùng phòng đó", "cùng phòng này", "phòng đó", "chỗ đó", "phòng kia", "cùng phòng",
    "ở chỗ này", "ở đây", "chỗ này", "ngay đây", "tại đây", "phòng này",
)


def _match_any(folded: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(p)}", folded) for p in phrases)


def has_location_deixis(text: str) -> bool:
    """Câu có mang cụm deixis vị trí không (HERE hoặc THAT)? (dò không dấu, ranh giới từ)."""
    folded = strip_diacritics(text).lower()
    return _match_any(folded, _HERE_PHRASES) or _match_any(folded, _THAT_PHRASES)


def strip_deixis(text: str) -> str:
    """Bỏ cụm deixis vị trí khỏi câu (viết lại tường minh). Bản có dấu, không phân biệt hoa/thường."""
    out = text
    for phrase in _DEIXIS_DIACRITIC:
        out = re.sub(rf"(?i)(?<!\w){re.escape(phrase)}(?!\w)", " ", out)
    return re.sub(r"\s+", " ", out).strip(" ,.!?")


def _single(rooms: list[str] | tuple[str, ...] | None) -> str | None:
    """Trả phòng DUY NHẤT trong danh sách (mơ hồ khi ≠1 → None)."""
    return rooms[0] if rooms and len(rooms) == 1 else None


def resolve_deictic_room(
    text: str,
    *,
    presence_rooms: list[str] | None = None,
    conversation_location: str | None = None,
    ledger_room: str | None = None,
    valid_rooms: frozenset[str] | tuple[str, ...],
) -> str | None:
    """Phòng mà deixis vị trí trỏ tới, hoặc None nếu câu không có deixis / không phân giải được.

    Ưu tiên theo LỚP deixis:
      • HERE ("ở đây"): conversation_location (client/"chuyển sang" — CHỦ Ý) → hiện diện ĐƠN NHẤT
        → ledger_room. (conversation_location trước hiện diện vì hiện diện có thể là MẶC ĐỊNH.)
      • THAT ("phòng đó/cùng phòng đó"): ledger_room (phòng vừa chốt — hồi chỉ) → conversation_location
        → hiện diện đơn nhất.
    Trả None nếu không deixis hoặc không nguồn phòng hợp lệ nào."""
    folded = strip_diacritics(text).lower()
    is_here = _match_any(folded, _HERE_PHRASES)
    is_that = _match_any(folded, _THAT_PHRASES)
    if not (is_here or is_that):
        return None
    presence = _single(presence_rooms)
    if is_that and not is_here:
        order = (ledger_room, conversation_location, presence)
    else:  # HERE (hoặc lẫn — ưu tiên nghĩa vật lý)
        order = (conversation_location, presence, ledger_room)
    valid = set(valid_rooms)
    for room in order:
        if room and room in valid:
            return room
    return None
