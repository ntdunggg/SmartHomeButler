"""Cổng tất định: NÊU SỞ THÍCH / PHẢN HỒI CHẤP NHẬN → NHỚ, KHÔNG hành động lượt này (§34).

Tách khỏi turn_intent (vốn lo lượt tiếp nối/huỷ/loại-trừ): ở đây phân biệt hai loại câu mà
pipeline cũ hiểu NHẦM thành mệnh lệnh cho lượt này —

  • KHAI BÁO SỞ THÍCH / LUẬT ĐỨNG ("tôi thường để điều hoà 25 độ khi ngủ", "đừng bao giờ tắt
    quạt khi tôi ngủ", "nếu tôi đi ngủ thì giữ điều hoà", "lần sau mở 40% thôi"): người dùng
    NÊU điều mình thường muốn / một luật đứng — phải GHI NHỚ, không thực thi ngay (spec §34:
    preference là bằng chứng để học, không phải kế hoạch lượt này).
  • PHẢN HỒI CHẤP NHẬN ("ừ mức này được", "ổn đấy, cứ mức này"): sau khi trợ lý vừa làm, người
    dùng xác nhận kết quả — không còn hành động nào để làm thêm.

Cả hai ⇒ quyết định NO_ACTION (route "answer"). Guard quan trọng: câu có mốc ACT-NOW ("hôm
nay", "lần này", "bây giờ") là MỆNH LỆNH cho lượt này dù có từ ngữ sở thích — KHÔNG nuốt thành
nhớ ("chỉ hôm nay thôi: để 22 độ" vẫn phải thực thi). Tất định theo cấu trúc câu, không phải
bảng tra câu → ý định.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.agent.text import Pattern, TextView
from src.nlu.normalizer import NormalizedUtterance

# Mốc ACT-NOW: câu này là mệnh lệnh cho LƯỢT NÀY, không phải khai báo sở thích lâu dài.
# Chặn TRƯỚC mọi marker sở thích ("chỉ hôm nay thôi: để 22 độ", "hôm nay bật 100% nhưng đây
# không phải thói quen") — vẫn phải thực thi.
_ACT_NOW = Pattern(r"\b(hôm nay|lần này|bây giờ|ngay bây giờ|ngay lúc này|lúc này)\b")

# KHAI BÁO SỞ THÍCH: "tôi thích/không thích", "tôi thường/hay (thích|muốn|để)", "thường thích".
# KHÔNG bắt "tôi muốn" trần — "tôi muốn bật đèn" là mệnh lệnh lịch sự, không phải sở thích.
_PREFERENCE_DECL = Pattern(
    r"(\btôi (thường|hay) (thích|muốn|để)\b|\btôi (không )?thích\b|\bthường thích\b|"
    r"\btôi (thường|hay)\b)"
)
# THÓI QUEN / TẦN SUẤT: "tối nào ... cũng", "mỗi khi/lần", "sau 11 giờ" (khung giờ đứng),
# "lần sau" (dành cho lần tới, không phải bây giờ).
_HABITUAL = Pattern(
    r"((tối|sáng|trưa|chiều|đêm|ngày) nào\b|\bmỗi (khi|lần|tối|sáng|ngày)\b|"
    r"\bsau \d+ giờ\b|\blần sau\b)"
)
# LUẬT ĐỨNG PHỦ ĐỊNH: "đừng bao giờ...", "đừng tự động..." — khác "đừng tắt X" (phủ định lượt này).
_STANDING_PROHIBITION = Pattern(r"\bđừng (bao giờ|tự động)\b")
# LUẬT ĐIỀU KIỆN: "nếu ... thì", "khi ... thì / khi ... tôi (không) thích/muốn".
_CONDITIONAL_RULE = Pattern(r"(\bnếu\b.*\bthì\b|\bkhi\b.*(\bthì\b|\btôi (không )?(thích|muốn)\b))")
# PHẢN CHIẾU QUÁ KHỨ → nêu mức muốn cho lần tới ("hôm qua 40% hơi tối, 50% hợp hơn").
_PAST_REFLECTION = Pattern(r"\bhôm qua\b")

# PHẢN HỒI CHẤP NHẬN kết quả vừa thực hiện: không còn gì để làm thêm.
# KHÔNG gồm "để nguyên/để vậy" (trùng câu HUỶ "thôi để nguyên") — chỉ cụm xác nhận MỨC.
_ACCEPT_FEEDBACK = Pattern(
    r"(\bmức này (được|vừa|ổn|hợp)|\bcứ (giữ|để )?mức này\b|\bvừa (rồi|ý)\b|\bđúng ý\b|"
    r"\b(ổn|tốt|hợp) (đấy|rồi|lắm)\b)"
)
_REJECT_FEEDBACK = Pattern(
    r"(\bkhông (ổn|thích|được|hợp)\b|\b(sáng|tối|lạnh|nóng|to|nhỏ) quá\b|"
    r"\bmở (quá|nhiều)\b|\bđừng dùng mức này\b)"
)
_CORRECTION_FEEDBACK = Pattern(
    r"(\bthực ra\b|\bhợp hơn\b|\btốt hơn\b|\bdễ chịu hơn\b|\blần sau\b)"
)


@dataclass(frozen=True, slots=True)
class ConversationalFeedback:
    """Structured write-path input derived from a non-action conversational turn."""

    route: str  # store_preference | accept | reject | correction | correction_only
    kind: str  # preference_stated | explicit_accept | explicit_reject | slight_adjustment
    dimension: str | None = None
    value: int | None = None
    subject: str | None = None
    room: str | None = None
    direction: str | None = None


_NUMBER = re.compile(r"(?<!\w)(\d{1,3})(?!\w)")


def _preference_dimension(view: TextView) -> str | None:
    """Infer only registry capability dimensions; return None when evidence is weak."""
    if any(view.has(token) for token in ("quạt", "tốc độ", "fan")):
        return "fan_speed"
    if any(view.has(token) for token in ("âm lượng", "tiếng", "loa", "tv")):
        return "volume"
    if any(view.has(token) for token in ("độ sáng", "sáng", "tối", "đèn")):
        return "brightness"
    if any(view.has(token) for token in ("nhiệt độ", "điều hòa", "điều hoà", "lạnh", "mát", "ấm")):
        return "temperature"
    if any(view.has(token) for token in ("độ mở", "rèm", "cửa sổ")):
        return "position"
    return None


def _feedback_direction(view: TextView, dimension: str | None) -> str | None:
    """Translate evaluative language into the adjustment direction it implies."""
    if dimension == "brightness":
        return "decrease" if view.has("sáng") else ("increase" if view.has("tối") else None)
    if dimension == "temperature":
        return "increase" if view.has("lạnh") else ("decrease" if view.has("nóng") else None)
    if dimension == "volume":
        return "decrease" if view.has("to") else ("increase" if view.has("nhỏ") else None)
    if dimension == "position" and any(view.has(token) for token in ("mở", "nhiều")):
        return "decrease"
    return None


def interpret_preference_feedback(
    nu: NormalizedUtterance,
    *,
    assistant_acted: bool,
    prior_devices: list[str] | tuple[str, ...] = (),
    prior_room: str | None = None,
    prior_dimension: str | None = None,
) -> ConversationalFeedback | None:
    """Return a structured signal suitable for the real memory write path.

    Classification remains the existing deterministic gate.  This function only
    extracts registry/capability evidence already present in the turn or the single
    immediately-prior target; it never invents a device from a generic preference.
    """
    route = classify_preference_feedback(nu, assistant_acted=assistant_acted)
    if route is None:
        return None
    view = TextView(raw=nu.normalized, folded=nu.folded)
    matches = list(_NUMBER.finditer(nu.normalized)) or list(_NUMBER.finditer(nu.folded))
    numeric = [m for m in matches if not (nu.normalized[m.end():].lstrip().startswith("giờ"))]
    value = int(numeric[-1].group(1)) if numeric else None
    subject = nu.matched_device_ids[0] if len(nu.matched_device_ids) == 1 else None
    if subject is None and route in {"accept", "reject", "correction", "correction_only"} and len(prior_devices) == 1:
        subject = prior_devices[0]
    room = nu.matched_rooms[0] if len(nu.matched_rooms) == 1 else prior_room
    dimension = _preference_dimension(view) or prior_dimension
    kind = {
        "store_preference": "preference_stated",
        "accept": "explicit_accept",
        "reject": "explicit_reject",
        "correction": "slight_adjustment",
        "correction_only": "slight_adjustment",
    }[route]
    return ConversationalFeedback(
        route=route,
        kind=kind,
        dimension=dimension,
        value=value,
        subject=subject,
        room=room,
        direction=_feedback_direction(view, dimension),
    )


def classify_preference_feedback(nu: NormalizedUtterance, *, assistant_acted: bool) -> str | None:
    """Phân loại tất định câu NÊU-SỞ-THÍCH / CHẤP-NHẬN → "store_preference" | "accept" | None.

    None = không phải hai loại trên; để pipeline hiểu/lập kế hoạch như bình thường.

    `assistant_acted` (ledger vừa lượt trước có thiết bị/outcome đã chốt) phân biệt hai câu
    trông giống hệt nhau về mặt cú pháp "nếu X thì Y": ngay SAU khi trợ lý vừa hành động, đó
    là LỆNH ĐIỀU KIỆN tức thời cho phiên này ("bật quạt" rồi "nếu vẫn nóng thì tăng lên" — chờ
    trigger cùng phiên, PROCEED khi trigger tới); đứng MỘT MÌNH đầu hội thoại, đó là LUẬT ĐỨNG
    dài hạn ("nếu tôi nói đi ngủ thì vẫn giữ điều hoà chạy" — không có hành động nào vừa xảy ra
    để nó tiếp nối) → NO_ACTION lưu lại. Do đó chỉ nhận "nếu...thì"/"khi...thì" khi KHÔNG có
    hành động vừa chốt lượt trước."""
    view = TextView(raw=nu.normalized, folded=nu.folded)
    if _ACT_NOW.search(view):
        return None  # mệnh lệnh cho lượt này — không nuốt thành nhớ dù có từ ngữ sở thích
    # Phản hồi về hành động vừa xong được ưu tiên hơn marker thói quen như "lần sau".
    # Ví dụ "Không ổn, đừng dùng mức này lần sau" là reject, không phải một preference mới.
    if assistant_acted:
        if _ACCEPT_FEEDBACK.search(view):
            return "accept"
        if _HABITUAL.search(view) and _NUMBER.search(nu.normalized):
            return "correction_only"
        if _CORRECTION_FEEDBACK.search(view) and _NUMBER.search(nu.normalized):
            return "correction"
        if _REJECT_FEEDBACK.search(view):
            return "reject"
    if (
        _PREFERENCE_DECL.search(view)
        or _HABITUAL.search(view)
        or _STANDING_PROHIBITION.search(view)
        or _PAST_REFLECTION.search(view)
        or (not assistant_acted and _CONDITIONAL_RULE.search(view))
    ):
        return "store_preference"
    return None
