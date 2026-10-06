"""Turn-intent classifier (spec §5, §17) — phân biệt vai trò của MỘT lượt trong hội thoại.

Layer 2 hiện phân biệt tốt "câu trả lời phòng" (slot-fill) và "lệnh mới", nhưng CHƯA nhận
diện lượt CHỈ mang một *modifier* tiếp nối mục tiêu đã ground ("mạnh hơn chút", "thêm một
chút nữa", "mở lại một nửa", "à 25 độ", "không, 60 thôi"). Những lượt này tự thân KHÔNG có
đích thực thi, nên tầng author dựng goal rỗng/ngữ cảnh → sufficiency gate hỏi lại thừa.

Module này phân loại lượt thành 5 vai trò (spec §5) DỰA TRÊN TÍN HIỆU NGÔN NGỮ TỔNG QUÁT,
KHÔNG map cụm→hành động (§8, §66, §P8):

    NEW_GOAL             — mục tiêu mới độc lập.
    CONTINUATION         — tiếp nối/tinh chỉnh/sửa mục tiêu đã ground (mang modifier, không đích mới).
    CLARIFICATION_ANSWER — trả lời câu hỏi làm rõ (chỉ nêu phòng/thiết bị).
    CANCELLATION         — huỷ.
    TOPIC_SWITCH         — nêu thiết bị/phòng KHÁC mục tiêu trước → không kế thừa (cô lập ngữ cảnh).

Với CONTINUATION, `build_continuation_goal` TÁI DỰNG mục tiêu trước từ Ledger (phòng/thiết bị/
chiều/ràng buộc/provenance đã chốt) rồi áp modifier — grounding được KẾ THỪA, không suy lại,
nên câu tiếp nối cụt vẫn đủ để PROCEED. Đây là "code decides grounding" (§P2), model không
cần đoán lại đích từ mảnh câu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from src.agent.schemas import DesiredOutcome, RequirementLedger, RuntimeContext, SemanticGoal
from src.agent.text import strip_diacritics
from src.domain.action_registry import capability_bounds
from src.iot.registry import ROOM_ALIASES, spec_for
from src.nlu.direction import direction_of
from src.nlu.normalizer import NormalizedUtterance
from src.nlu.ontology import UtteranceType
from src.nlu.understanding import HALF_TOKENS, mentioned_capability


class TurnIntent(StrEnum):
    NEW_GOAL = "new_goal"
    CONTINUATION = "continuation"
    CLARIFICATION_ANSWER = "clarification_answer"
    CANCELLATION = "cancellation"
    TOPIC_SWITCH = "topic_switch"
    # Loại MỘT/VÀI thiết bị khỏi mục tiêu NHÓM đã chốt lượt trước ("trừ đèn ngủ ra", "loa thì
    # thôi") — thu hẹp phạm vi, KHÁC cancellation (huỷ cả mục tiêu) và correction (đổi hẳn đích).
    EXCLUSION_REFINEMENT = "exclusion_refinement"


# --- Từ vựng CỰC TÍNH (polarity) đã GOM về `src/nlu/direction.py` (nguồn sự thật DUY NHẤT, §8).
# turn_intent dùng bản `bare_verbs=True`: coi "lên"/"xuống" trần là động từ hướng (lượt tiếp nối
# "lên một nấc"/"xuống một chút"). Dò trên bản CÓ DẤU vì "nữa"(=thêm) và "nửa"(=½) chỉ khác dấu.
_SLIGHT = ("chút", "tí", "xíu", "tẹo", "chút xíu")
# Cue "đặt tới giá trị": số đi kèm các từ này = GIÁ TRỊ TUYỆT ĐỐI dù có động từ hướng
# ("hạ xuống 42 độ" = set 42, không phải giảm 42). Không có cue + có động từ hướng = delta tương đối.
_SET_CUE = ("xuống", "còn", "về", "thành", "đến", "tới", "lên")
# "nữa" = tiếp tục ĐÚNG chiều đang làm (không tự mang cực tính). "một nửa"/"nửa" = phân nửa (½).
_CONTINUE = ("nữa",)
# Cue đặt-tới chỉ có nghĩa khi nó đứng NGAY TRƯỚC con số ("hạ xuống 42", "giảm còn 20").
# Dò ở bất kỳ đâu trong câu thì các từ rất thường gặp ("tới", "về", "lên") biến mọi câu có
# số thành lệnh gán: "tôi có 3 người bạn sắp TỚI" từng ghi độ sáng = 3%.
_SET_CUE_BEFORE_NUM = re.compile(rf"(?:{'|'.join(_SET_CUE)})\s+(?:mức\s+)?$")
# Từ vựng phân nửa dùng CHUNG với `nlu.understanding` (§4: một nguồn sự thật). Trước đây mỗi nơi
# giữ một bản, nên lượt ĐẦU "mở rèm một nửa" mở hết 100% còn lượt TIẾP NỐI lại đúng 50%.
_HALF = HALF_TOKENS

# Dimension số điều chỉnh được, theo thứ tự ưu tiên khi thiết bị có nhiều capability.
_ADJUSTABLE_DIMS = ("temperature", "brightness", "position", "fan_speed", "volume")

_NUM_UNIT = re.compile(r"(\d+)\s*(độ|°|%|phần trăm)?")
_BOUND = re.compile(
    r"\b(không\s+quá|tối\s+đa|nhiều\s+nhất|không\s+dưới|"
    r"không\s+(?:(?:bật|tắt|mở|đóng|chỉnh)\s+)?thấp\s+hơn|"
    r"ít\s+nhất|tối\s+thiểu)\s*(\d+)\s*(độ|°|%|phần\s+trăm)?"
)
_BOUND_FOLDED = re.compile(
    r"\b(khong\s+qua|toi\s+da|nhieu\s+nhat|khong\s+duoi|"
    r"khong\s+(?:(?:bat|tat|mo|dong|chinh)\s+)?thap\s+hon|"
    r"it\s+nhat|toi\s+thieu)\s*(\d+)\s*(do|°|%|phan\s+tram)?"
)


@dataclass(slots=True)
class Modifier:
    """Một chỉnh sửa tương đối/tuyệt đối rút từ lượt tiếp nối (không phải đích mới)."""

    kind: str  # "relative" | "absolute" | "fraction" | "bound" | "exclusion"
    direction: str = ""  # "increase" | "decrease" | "" (rỗng = tiếp tục chiều cũ)
    magnitude: str = ""  # "" | "slight" | "large"
    value: int | None = None
    unit: str = ""  # "" | "độ" | "%"
    dimension: str = ""  # explicit deterministic capability, when already resolved
    is_correction: bool = False
    evidence: list[str] = field(default_factory=list)
    # kind="exclusion" only: thiết bị bị LOẠI khỏi mục tiêu nhóm đã chốt lượt trước.
    excluded_ids: list[str] = field(default_factory=list)


# Cụm LOẠI TRỪ một/vài thiết bị khỏi mục tiêu NHÓM đã chốt lượt trước ("trừ đèn ngủ ra", "loa
# thì thôi", "bỏ quạt ra", "không tính rèm") — khác NEGATION (phủ định hành động sắp làm) và
# CORRECTION (đổi hẳn đích): đây là THU HẸP phạm vi thiết bị, giữ nguyên hành động/outcome.
_EXCLUSION_LEAD = ("trừ", "bỏ qua", "không tính", "khỏi tính")
_EXCLUSION_TRAIL = ("thì thôi", "thì bỏ", "thì khỏi")


def _has_exclusion_cue(text: str) -> bool:
    if _contains_word(text, _EXCLUSION_LEAD) is not None:
        return True
    stripped = text.rstrip(" .!?")
    return any(stripped.endswith(w) for w in _EXCLUSION_TRAIL)


def detect_exclusion(nu: NormalizedUtterance, ledger: RequirementLedger) -> list[str] | None:
    """Thiết bị bị LOẠI khỏi mục tiêu đã chốt lượt trước, hoặc None nếu câu không phải loại trừ.
    Chỉ nhận khi câu KHÔNG mang động từ điều khiển riêng (không phải lệnh mới độc lập) và mục
    tiêu trước có ÍT NHẤT MỘT thiết bị đã chốt.

    Trả DANH SÁCH RỖNG (không phải None) khi cue loại trừ khớp nhưng thiết bị NÊU RA không
    trùng thiết bị nào đã chốt ("trừ đèn ngủ" sau khi nhóm chỉ có đèn chùm phòng khách — không
    có "đèn ngủ" trong phạm vi). Đây là loại trừ VÔ NGHĨA với phạm vi hiện tại: caller (
    `build_continuation_goal`) giữ NGUYÊN mục tiêu gốc thay vì coi là không hiểu — khác None
    (câu không mang cue loại trừ nào cả, không phải lượt này)."""
    text = nu.normalized or ""
    prior_devices = set((ledger.confirmed_facts or {}).get("devices") or [])
    if not prior_devices:
        return None
    if not nu.has_action_verb:
        if not _has_exclusion_cue(text):
            return None
        return [d for d in nu.matched_device_ids if d in prior_devices]
    # Có động từ + PHỦ ĐỊNH + nêu thiết bị KHÔNG trùng phạm vi đang treo ("đừng đóng rèm" sau
    # khi nhóm đang treo là 2 đèn phòng ngủ, không phải rèm) — câu này CÓ đích riêng (rèm) nhưng
    # đích đó nằm NGOÀI phạm vi đang treo, nên phủ định là VÔ NGHĨA với kế hoạch hiện tại → giữ
    # nguyên (loại trừ RỖNG). KHÁC topic-switch thật: topic-switch không mang phủ định (đưa ra
    # đích MỚI độc lập); ở đây phủ định là tín hiệu "đừng làm X" chứ không phải "giờ làm X".
    if nu.has_negation and nu.matched_device_ids and not (set(nu.matched_device_ids) & prior_devices):
        return []
    return None


# Động từ bật/tắt/mở/đóng/khoá → action_hint tương ứng (cùng vựng với reply-verb map ở
# `pipeline_bridge.is_pure_prohibition` — tái dùng, không định nghĩa lại ánh xạ verb→hint).
_VERB_TO_ACTION_HINT: dict[str, str] = {
    "bật": "turn_on", "khởi động": "turn_on",
    "tắt": "turn_off",
    "mở khoá": "unlock", "mở khóa": "unlock",
    "mở": "open",
    "đóng": "close",
    "khoá": "lock", "khóa": "lock",
}


def _negated_verb_matches_pending(nu: NormalizedUtterance, ledger: RequirementLedger) -> bool:
    """Phủ định mang ĐÚNG động từ của hành động đang treo, nhưng còn CỤM BỔ NGHĨA khác ("đừng
    bật CHẾ ĐỘ MẠNH" sau "Bật điều hoà") — không nêu thiết bị/phòng mới. KHÁC huỷ trắng (bare,
    xử lý ở `normalizer._CANCELLATION_BARE`, không còn gì ngoài động từ) và khác loại trừ thiết
    bị (nêu thiết bị khác). Đây là ràng buộc mà hệ hiện KHÔNG mô hình hoá được (mode/mức không
    có capability riêng) → cách an toàn nhất là GIỮ NGUYÊN mục tiêu treo, bỏ qua phần bổ nghĩa
    không thực thi được, thay vì hỏi lại hoặc từ chối trắng."""
    if not (nu.has_negation and nu.has_action_verb):
        return False
    if nu.matched_device_ids or nu.matched_rooms:
        return False
    text = nu.normalized or ""
    # "hết"/"hẳn" (phủ định + cực hạn) có xử lý RIÊNG, cụ thể hơn (→ ½ hoặc tiếp tục chiều với
    # biên độ nhẹ, xem `detect_modifier`) — nhường đường cho nhánh đó thay vì tái sử dụng nguyên
    # mục tiêu treo không đổi.
    if _contains_word(text, _EXTREME_PERCENT + _EXTREME_ENDPOINT) is not None:
        return False
    pending_hint = (ledger.current_goal or {}).get("action_hint")
    if not pending_hint:
        return False
    return any(hint == pending_hint and _contains_word(text, (verb,)) for verb, hint in _VERB_TO_ACTION_HINT.items())


def detect_outcome_exclusion(nu: NormalizedUtterance, ledger: RequirementLedger) -> list[str] | None:
    """Loại MỘT thiết bị khỏi mục tiêu CẢM NHẬN (outcome/desired_outcomes) đã chốt lượt trước
    ("đừng đóng rèm" sau "cho phòng ngủ tối hơn") — khác `detect_exclusion` (đó là mục tiêu đã
    LIỆT KÊ RÕ thiết bị trong `confirmed_facts.devices`; outcome goal chưa resolve thiết bị nào
    ở tầng author, chỉ resolve lúc specialists chạy). Chỉ nhận khi câu phủ định + có động từ +
    NÊU RÕ đúng một thiết bị, và ledger đang treo MỘT outcome (không phải lệnh liệt kê thiết bị)."""
    if not (nu.has_negation and nu.has_action_verb and nu.matched_device_ids):
        return None
    if not ledger.desired_outcomes:
        return None
    return list(nu.matched_device_ids)


def _contains_word(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w)}", text):
            return w
    return None


def _direction_of(text: str) -> tuple[str, str]:
    """(direction, evidence) — DELEGATE về nguồn sự thật chung (`direction_of`), gồm cả "<adj> lên/
    xuống/lại/đi" (LS-154: "to lên"/"nhỏ xuống" trước đây turn_intent chỉ dò "<adj> hơn" nên sót)."""
    return direction_of(text, bare_verbs=True)



# PHỦ ĐỊNH + CỰC HẠN — người dùng chặn một CHIỀU đi tới điểm cực, không chọn ½ hay đổi chiều.
# "hết" ngụ ý mức PHẦN TRĂM đầy đủ (mở hết=100%, đóng hết=0%) → quy về PHÂN NỬA (½), bước
# trung dung an toàn khi không có chiều tương đối nào để kế thừa. "hẳn" ngụ ý ĐIỂM DỪNG của một
# CHIỀU đang đổi (tắt hẳn=về 0, giảm hẳn=giảm rất nhiều) → TIẾP TỤC chiều đã kế thừa (rỗng,
# `build_continuation_goal`/`_rel_token` tự điền `prior_direction` từ ledger) với biên độ NHẸ,
# không đẩy tới cực. Chỉ tính khi có PHỦ ĐỊNH (không đụng "tắt hết đèn" — lượng từ nhóm thiết
# bị, ngữ cảnh khác hẳn, không mang phủ định).
_EXTREME_PERCENT = ("hết",)
_EXTREME_ENDPOINT = ("hẳn",)


def detect_modifier(nu: NormalizedUtterance) -> Modifier | None:
    """Rút modifier từ tín hiệu ngôn ngữ TỔNG QUÁT. None nếu lượt không mang chỉnh sửa nào.

    Ưu tiên: PHỦ ĐỊNH+CỰC HẠN > GIÁ TRỊ TUYỆT ĐỐI (số + không phải delta) > ½ > TƯƠNG ĐỐI
    (động từ/so sánh/tiếp tục)."""
    text = nu.normalized or ""
    ev: list[str] = []
    direction, dir_ev = _direction_of(text)

    if nu.has_negation:
        if _contains_word(text, _EXTREME_PERCENT) is not None:
            ev.append("negated_extreme:hết→half")
            return Modifier(kind="fraction", value=50, is_correction=nu.has_correction, evidence=ev)
        if _contains_word(text, _EXTREME_ENDPOINT) is not None:
            ev.append("negated_extreme:hẳn→slight_continue")
            return Modifier(kind="relative", direction="", magnitude="slight",
                            is_correction=nu.has_correction, evidence=ev)

    # 1) Giá trị tuyệt đối: một con số. Là ABS khi (a) không có động từ hướng (thuần "à 25 độ",
    #    "60 thôi"), HOẶC (b) có cue đặt-tới ("xuống/còn/về 42"). Có động từ hướng mà KHÔNG cue
    #    ("giảm 2 độ") = delta tương đối, KHÔNG phải set (tránh hiểu nhầm về giá trị tuyệt đối).
    m = _NUM_UNIT.search(text)
    if m and m.group(1):
        has_set_cue = _SET_CUE_BEFORE_NUM.search(text[: m.start(1)]) is not None
        unit = (m.group(2) or "").strip()
        normalized_unit = "độ" if unit in ("độ", "°") else ("%" if unit in ("%", "phần trăm") else "")
        if not direction or has_set_cue:
            # Số TRẦN (không đơn vị, không cue đặt-tới) chỉ được coi là setpoint khi nó là
            # toàn bộ nội dung lượt — xem `_is_bare_value_turn`. Không thoả thì lượt này
            # KHÔNG mang modifier số; nó rơi xuống NEW_GOAL để tầng trên diễn giải, thay vì
            # ghi một giá trị vào thiết bị của lượt trước.
            # Tên capability đi kèm số cũng là ngữ cảnh gán giá trị hợp lệ ("âm lượng 25
            # thôi", "độ sáng 40") — dùng lại `mentioned_capability` của tầng NLU (§4) thay
            # vì liệt kê từ vựng lần thứ hai ở đây.
            if (
                not normalized_unit
                and not has_set_cue
                and mentioned_capability(text) is None
                and not _is_bare_value_turn(text, m.group(1))
            ):
                return None
            ev.append(f"value={m.group(1)}{unit}")
            return Modifier(kind="absolute", value=int(m.group(1)),
                            unit=normalized_unit,
                            is_correction=nu.has_correction, evidence=ev)
        # Có hướng mạnh và không có cue đặt-tới: con số là BIÊN ĐỘ, không
        # phải setpoint. Giữ nó trên modifier để continuation có thể tính từ giá trị
        # tuyệt đối đã chốt trong ledger (vd 25 - 5 = 20).
        ev.extend(item for item in (dir_ev, f"delta={m.group(1)}{unit}") if item)
        return Modifier(
            kind="relative",
            direction=direction,
            value=int(m.group(1)),
            unit=normalized_unit,
            is_correction=nu.has_correction,
            evidence=ev,
        )

    # 2) Phân nửa (½) — token "một nửa"/"nửa" CÓ DẤU (tách khỏi "nữa"=thêm).
    if _contains_word(text, _HALF):
        ev.append("fraction=half")
        return Modifier(kind="fraction", value=50, is_correction=nu.has_correction, evidence=ev)

    # 3) Tương đối: chiều từ động từ/so sánh, hoặc "nữa" = tiếp tục chiều cũ.
    cont = _contains_word(text, _CONTINUE)
    if not (direction or cont):
        return None
    if dir_ev:
        ev.append(dir_ev)
    if cont:
        ev.append("continue:nữa")
    magnitude = "slight" if _contains_word(text, _SLIGHT) else ""
    return Modifier(kind="relative", direction=direction, magnitude=magnitude,
                    is_correction=nu.has_correction, evidence=ev)


def detect_bound_constraint(nu: NormalizedUtterance) -> Modifier | None:
    """Parse a numeric min/max continuation without turning it into an exact setpoint."""
    match = _BOUND.search(nu.normalized or "") or _BOUND_FOLDED.search(nu.folded or "")
    if match is None:
        return None
    cue, raw_value, unit = match.groups()
    folded_cue = cue.replace(" ", "")
    min_cues = ("dưới", "thấphơn", "ítnhất", "tốithiểu", "duoi", "thaphon", "itnhat", "toithieu")
    kind = "min" if any(key in folded_cue for key in min_cues) else "max"
    normalized_unit = "độ" if unit in {"độ", "°", "do"} else ("%" if unit else "")
    return Modifier(
        kind="bound",
        direction=kind,
        value=int(raw_value),
        unit=normalized_unit,
        evidence=[f"bound:{kind}:{raw_value}{normalized_unit}"],
    )


def has_grounded_prior(ledger: RequirementLedger | None) -> bool:
    """Ledger có MỘT mục tiêu đã ground ở lượt trước để kế thừa không? (phòng/thiết bị/outcome)."""
    if ledger is None:
        return False
    cg = ledger.current_goal or {}
    facts = ledger.confirmed_facts or {}
    return bool(cg and (facts.get("devices") or facts.get("room") or ledger.desired_outcomes))


def introduces_new_scope(nu: NormalizedUtterance, ledger: RequirementLedger) -> bool:
    """Lượt này nêu thiết bị/phòng KHÁC mục tiêu trước → đổi phạm vi (không kế thừa; §73 cô lập)."""
    prior_devices = set(ledger.confirmed_facts.get("devices") or [])
    prior_room = ledger.confirmed_facts.get("room")
    if nu.matched_device_ids and set(nu.matched_device_ids) - prior_devices:
        return True
    if nu.matched_rooms and prior_room and nu.matched_rooms[0] != prior_room:
        return True
    return False


def _is_slot_answer(nu: NormalizedUtterance) -> bool:
    """Chỉ nêu phòng/thiết bị, không động từ/tham chiếu/modifier → trả lời slot (phòng/thiết bị)."""
    if nu.has_action_verb or nu.has_reference:
        return False
    # "Điều hoà phòng bố mẹ 25 độ" / "loa âm lượng 35" là phép
    # gán giá trị rút gọn, không phải câu trả lời slot. Nếu nuốt ở đây,
    # pipeline ghép nó vào raw goal cũ và kéo theo thiết bị stale.
    if re.search(r"(?<!\w)\d+(?!\w)", nu.normalized or ""):
        return False
    return bool(nu.matched_rooms or nu.matched_device_ids)


_ROOM_REFINEMENT_FILLERS = (
    "ý tôi là", "ý mình là", "không phải", "không", "à", "ở", "trong",
    "là", "mà", "cơ", "thôi", "nhé", "nha", "nhỉ", "ạ", "đấy", "đó",
)


# Một con số KHÔNG kèm đơn vị chỉ là setpoint khi con số CHÍNH LÀ nội dung của lượt
# ("60 thôi", "à 25"). Khi nó nằm lẫn trong câu có nội dung khác thì nó là dữ kiện của câu,
# không phải giá trị điền vào mục tiêu lượt trước. Nhận nhầm thì một câu ngoài phạm vi ĐỔI
# TRẠNG THÁI NHÀ: đo thật trên server §2026-08-30, "1 cộng 1" nối sau lượt bàn về đèn phòng
# khách ra "Đặt độ sáng Đèn chùm = 1%".
#
# Đơn vị (độ/%) hoặc cue đặt-tới ("xuống 42") tự nó đã chứng minh ý định gán giá trị, nên chỉ
# số TRẦN mới phải qua cửa này. Hư từ dùng lại danh sách của room-refinement (§4: một nguồn
# sự thật cho "từ không mang nội dung"), cộng các động từ đặt-giá-trị.
_VALUE_ONLY_FILLERS = _ROOM_REFINEMENT_FILLERS + _SET_CUE + _SLIGHT + _CONTINUE + (
    "đặt", "chỉnh", "để", "set", "mức", "cho", "đi", "ừ", "ok", "vâng", "được",
)


def _is_bare_value_turn(text: str, number: str) -> bool:
    """Con số TRẦN có phải toàn bộ nội dung của lượt không (ngoài hư từ/tiểu từ)?"""
    rest = re.sub(rf"(?<!\w){re.escape(number)}(?!\w)", " ", text or "", count=1)
    rest = re.sub(r"[^\w\s]", " ", rest)
    for word in sorted(_VALUE_ONLY_FILLERS, key=len, reverse=True):
        rest = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", " ", rest)
    return not rest.split()


def _is_room_only_refinement(nu: NormalizedUtterance) -> bool:
    """Whether the turn only replaces/refines the room slot.

    This deliberately checks the residue after removing the resolved room and
    discourse/location fillers.  Therefore ``ở phòng ngủ bố mẹ mà`` is a room
    correction, while ``phòng bếp nóng quá`` retains substantive content and is
    still a genuine topic switch.
    """
    if (
        nu.has_action_verb
        or nu.has_reference
        or nu.matched_device_ids
        or len(nu.matched_rooms) != 1
        or re.search(r"(?<!\w)\d+(?!\w)", nu.normalized or "")
    ):
        return False

    room = nu.matched_rooms[0]
    residue = nu.folded
    aliases = (room.lower(), *ROOM_ALIASES.get(room, ()))
    for alias in sorted({strip_diacritics(item.lower()) for item in aliases}, key=len, reverse=True):
        residue = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", " ", residue)
    for filler in sorted(
        {strip_diacritics(item) for item in _ROOM_REFINEMENT_FILLERS}, key=len, reverse=True
    ):
        residue = re.sub(rf"(?<!\w){re.escape(filler)}(?!\w)", " ", residue)
    return not re.sub(r"[\W_]+", "", residue)


def _names_device_type(nu: NormalizedUtterance) -> bool:
    """Câu tự nêu loại thiết bị (kể cả alias chung như TV/đèn) hay không."""
    from src.agent.text import TextView
    from src.nlu.understanding import _match_device_types

    view = TextView(raw=nu.normalized, folded=nu.folded)
    return bool(_match_device_types(view))


def classify(
    nu: NormalizedUtterance, ledger: RequirementLedger | None, *, has_pending_clarification: bool = False
) -> tuple[TurnIntent, Modifier | None]:
    """Phân loại vai trò của lượt hiện tại + rút modifier nếu là CONTINUATION."""
    if nu.has_cancellation:
        return TurnIntent.CANCELLATION, None

    grounded = has_grounded_prior(ledger)

    # Loại trừ thiết bị khỏi mục tiêu nhóm ("trừ X ra", "X thì thôi") — kiểm TRƯỚC cả slot-answer
    # lẫn modifier thường: câu loại trừ thường KHÔNG mang động từ/modifier nên nếu không bắt ở
    # đây, nó bị `_is_slot_answer` nuốt thành CLARIFICATION_ANSWER (chỉ set field, không thu hẹp
    # nhóm) hoặc rơi xuống NEW_GOAL (mất grounding hoàn toàn).
    if grounded and ledger is not None:
        excluded = detect_exclusion(nu, ledger)
        # [] (cue khớp nhưng KHÔNG thiết bị nào trùng phạm vi) vẫn là EXCLUSION_REFINEMENT — chỉ
        # None (không mang cue loại trừ nào) mới bỏ qua nhánh này. Xem docstring detect_exclusion.
        if excluded is not None:
            return TurnIntent.EXCLUSION_REFINEMENT, Modifier(
                kind="exclusion", excluded_ids=excluded,
                evidence=[f"exclude:{','.join(excluded)}" if excluded else "exclude:none_in_scope"],
            )

        # Loại thiết bị khỏi mục tiêu CẢM NHẬN (outcome) đã chốt — "đừng đóng rèm" sau "cho
        # phòng tối hơn". Kiểm SAU detect_exclusion (đó ưu tiên khi có danh sách thiết bị rõ).
        outcome_excluded = detect_outcome_exclusion(nu, ledger)
        if outcome_excluded:
            return TurnIntent.EXCLUSION_REFINEMENT, Modifier(
                kind="outcome_exclusion", excluded_ids=outcome_excluded,
                evidence=[f"exclude_from_outcome:{','.join(outcome_excluded)}"],
            )

        # Phủ định mang ĐÚNG động từ đang treo nhưng còn cụm bổ nghĩa không mô hình hoá được
        # ("đừng bật chế độ mạnh") — giữ nguyên mục tiêu treo, bỏ qua phần bổ nghĩa.
        if _negated_verb_matches_pending(nu, ledger):
            return TurnIntent.EXCLUSION_REFINEMENT, Modifier(
                kind="exclusion", excluded_ids=[], evidence=["negated_qualifier:keep_pending"],
            )

    # Khi đang treo clarify, một câu slot-only là câu trả lời thật. Ngoài trạng thái đó,
    # phòng/thiết bị mới phải tạo hard topic boundary trước khi generic slot logic có thể
    # nuốt các environmental request như "Phòng bếp nóng quá".
    if has_pending_clarification and _is_slot_answer(nu):
        return TurnIntent.CLARIFICATION_ANSWER, None
    # A scope-only room answer is also a correction/refinement when no explicit
    # clarification is pending.  Check it before the hard topic boundary, but
    # keep environmental requests with substantive residue as TOPIC_SWITCH.
    if grounded and _is_room_only_refinement(nu):
        return TurnIntent.CLARIFICATION_ANSWER, None
    if grounded and ledger is not None and introduces_new_scope(nu, ledger):
        return TurnIntent.TOPIC_SWITCH, None
    if grounded and _is_slot_answer(nu):
        return TurnIntent.CLARIFICATION_ANSWER, None

    bound = detect_bound_constraint(nu)
    if bound is not None and grounded and ledger is not None:
        return TurnIntent.CONTINUATION, bound

    mod = detect_modifier(nu)
    if mod is not None and grounded and ledger is not None:
        # Có device-type tường minh + giá trị ("TV âm lượng 20") là
        # mục tiêu độc lập. Không được kế thừa thiết bị trước chỉ vì
        # alias chung chưa map được slug cho tới khi có room-carry.
        if _names_device_type(nu) and not nu.has_reference:
            return TurnIntent.NEW_GOAL, None
        # Đổi phạm vi (thiết bị/phòng khác) hoặc tham chiếu ngầm → KHÔNG kế thừa như modifier.
        if introduces_new_scope(nu, ledger) or nu.has_reference:
            return TurnIntent.TOPIC_SWITCH if introduces_new_scope(nu, ledger) else TurnIntent.NEW_GOAL, None
        return TurnIntent.CONTINUATION, mod

    if ledger is not None and grounded and introduces_new_scope(nu, ledger):
        return TurnIntent.TOPIC_SWITCH, None
    return TurnIntent.NEW_GOAL, None


# ---------------------------------------------------------------------------
# Continuation goal builder — kế thừa grounding lượt trước rồi áp modifier
# ---------------------------------------------------------------------------
def _excluded_by_ledger(ledger: RequirementLedger, device_id: str) -> bool:
    """Thiết bị đang bị `avoid:`/`keep_off:` trong Ledger (§18, §73 invariant 8)?

    Một mục tiêu tiếp nối DƯƠNG không được tự kế thừa làm anchor đúng thiết bị mà lượt
    trước vừa ràng buộc tránh/giữ tắt — nếu không, lệnh tiếp nối tự mâu thuẫn ngay tại
    tầng ground (vd phủ định một thiết bị ảo resolve về đèn thật, rồi lượt sau "tăng thêm"
    kế thừa đúng đèn đó dù nó đang bị `keep_off`)."""
    if any(device_id in state.device_ids for state in ledger.no_change):
        return True
    if any(device_id in state.excluded_device_ids for state in ledger.group_exclusions):
        return True
    for c in ledger.constraints:
        kind, _, slug = c.partition(":")
        if slug == device_id and kind in ("avoid", "keep_off"):
            return True
    return False


def _primary_dim(device_id: str) -> str | None:
    spec = spec_for(device_id)
    if spec is None:
        return None
    caps = {c.value for c in spec.capabilities}
    for dim in _ADJUSTABLE_DIMS:
        if dim in caps:
            return dim
    return None


def _shared_primary_dim(device_ids: list[str]) -> str | None:
    """Capability số chung của một nhóm target, theo cùng thứ tự ưu tiên đơn.

    Nhóm đèn đã chốt có thể bị thu hẹp bởi constraint trước lượt "giảm
    thêm". Số lượng target không được làm mất dimension nếu mọi target còn lại
    cùng hỗ trợ nó.
    """
    specs = [spec_for(device_id) for device_id in device_ids]
    if not specs or any(spec is None for spec in specs):
        return None
    common = set.intersection(*({cap.value for cap in spec.capabilities} for spec in specs if spec is not None))
    return next((dim for dim in _ADJUSTABLE_DIMS if dim in common), None)


def _dim_for(modifier: Modifier, prior_dim: str | None, device_id: str | None) -> str | None:
    """Chọn dimension áp modifier: đơn vị → dim; else dim lượt trước; else capability chính thiết bị."""
    if modifier.dimension:
        return modifier.dimension
    if modifier.kind == "absolute":
        if modifier.unit == "độ":
            return "temperature"
        # "%" hoặc số trần → dùng dim lượt trước (đèn=brightness, rèm=position...), else primary.
    if prior_dim:
        return prior_dim
    return _primary_dim(device_id) if device_id else None


def _prior_dim_of(outcome: dict) -> str | None:
    for key in ("relative_change", "target_state"):
        d = outcome.get(key) or {}
        if isinstance(d, dict) and d:
            return next(iter(d.keys()))
    return None


def _rel_token(modifier: Modifier, prior_direction: str) -> str:
    direction = modifier.direction
    # "thêm" is lexically increase when isolated, but in a grounded follow-up
    # it means continue the established semantic direction.  Thus "thêm nữa"
    # after cooling remains cooling, while the standalone modifier still keeps
    # its generic increase interpretation.
    if prior_direction and "increase_verb:thêm" in modifier.evidence:
        direction = prior_direction
    direction = direction or prior_direction or "increase"
    return f"{direction}_{modifier.magnitude}" if modifier.magnitude else direction


def _apply_modifier_to(
    outcome: dict,
    modifier: Modifier,
    device_id: str | None,
    fallback_direction: str = "",
    fallback_dim: str | None = None,
) -> dict:
    """Áp modifier lên MỘT outcome dict (giữ selector/labels lượt trước)."""
    data = dict(outcome)
    prior_dim = _prior_dim_of(outcome) or fallback_dim
    dim = _dim_for(modifier, prior_dim, device_id)
    if dim is None:
        return data
    if modifier.kind in ("absolute", "fraction"):
        ts = dict(data.get("target_state") or {})
        ts[dim] = modifier.value
        data["target_state"] = ts
        rc = dict(data.get("relative_change") or {})
        rc.pop(dim, None)  # giá trị tuyệt đối thay chiều tương đối cùng dim
        data["relative_change"] = rc
    else:  # relative
        prior_direction = ""
        prc = outcome.get("relative_change") or {}
        if isinstance(prc, dict) and dim in prc:
            prior_direction = str(prc[dim]).split("_")[0]
        rc = dict(data.get("relative_change") or {})
        rc[dim] = _rel_token(modifier, prior_direction or fallback_direction)
        data["relative_change"] = rc
        data.pop("target_state", None)
    return data


def _resolve_dim(modifier: Modifier, prior_dim: str | None, device_hint: str | None) -> str | None:
    if modifier.dimension:
        return modifier.dimension
    if modifier.kind == "absolute" and modifier.unit == "độ":
        return "temperature"
    if modifier.kind == "fraction":
        # ½ chỉ có nghĩa với dimension bị chặn 0–100 (độ mở/độ sáng). Ưu tiên dim lượt trước.
        return prior_dim or (_primary_dim(device_hint) if device_hint else None)
    return prior_dim or (_primary_dim(device_hint) if device_hint else None)


def _build_goal(cg: dict, modifier: Modifier, *, prior_room: str | None, prior_devices: list[str],
                outcomes: list[DesiredOutcome], action_hint: str | None, parameters: dict,
                excluded_device_ids: list[str] | None = None,
                explicit_constraints: list[str] | None = None) -> SemanticGoal:
    prior_label = cg.get("raw_utterance") or cg.get("goal_description") or "prior_goal"
    # Provenance NHÌN THẤY ĐƯỢC (§4, §14): kế thừa grounding từ mục tiêu nào + modifier nào.
    provenance = f"continuation<-[{prior_label}] +{'; '.join(modifier.evidence)}"
    return SemanticGoal(
        intent="continuation",
        raw_utterance=cg.get("raw_utterance", ""),
        goal_description=f"{cg.get('goal_description') or 'điều chỉnh tiếp'} — {provenance}",
        utterance_type=UtteranceType(cg.get("utterance_type", UtteranceType.ENVIRONMENT_REQUEST.value)),
        confidence=0.8,
        desired_outcomes=outcomes,
        action_hint=action_hint,
        parameters=parameters,
        target_area=prior_room,
        target_device_ids=list(prior_devices),
        excluded_device_ids=list(excluded_device_ids or []),
        explicit_constraints=list(explicit_constraints or []),
        is_correction=modifier.is_correction,
        references_resolved=True,
    )


def build_continuation_goal(
    ledger: RequirementLedger, modifier: Modifier, ctx: RuntimeContext | None = None
) -> SemanticGoal | None:
    """Tái dựng mục tiêu đã ground lượt trước + áp modifier. None nếu không đủ để kế thừa.

    Grounding (phòng/thiết bị) KẾ THỪA từ Ledger, KHÔNG suy lại; ràng buộc/phủ định vẫn sống
    trong Ledger nên validator tự áp. Hai đường:
    - Giá trị TUYỆT ĐỐI / ½ ("à 25 độ", "60 thôi", "mở lại một nửa"): dựng lệnh SET tường minh
      GHIM đúng thiết bị lượt trước (không để optimizer đổ sang thiết bị khác cùng dimension).
    - Tương đối ("mạnh hơn", "thêm nữa"): áp lên desired_outcome (env) hoặc dựng outcome tương
      đối trên capability chính của thiết bị đã chốt (grounding qua specialist theo phòng+chiều).
    """
    facts = ledger.confirmed_facts or {}
    cg = ledger.current_goal or {}
    all_prior_devices = [d for d in (facts.get("devices") or []) if spec_for(d) is not None]
    # Anchor kế thừa cho mục tiêu DƯƠNG tiếp theo loại trừ thiết bị đang bị avoid/keep_off —
    # device_hint (chỉ để suy DIMENSION, vd đèn→brightness) vẫn dùng danh sách chưa lọc.
    prior_devices = [d for d in all_prior_devices if not _excluded_by_ledger(ledger, d)]
    prior_room = facts.get("room")
    device_hint = all_prior_devices[0] if len(all_prior_devices) == 1 else None
    prior_dim = (
        _prior_dim_of(ledger.desired_outcomes[0])
        if ledger.desired_outcomes
        else _shared_primary_dim(prior_devices)
    )
    prior_hint = str(cg.get("action_hint") or "")
    fallback_direction = (
        "decrease" if prior_hint in {"turn_off", "close", "decrease"}
        else ("increase" if prior_hint in {"turn_on", "open", "increase"} else "")
    )

    # Delta số tiếp nối một setpoint đã chốt: tính ra setpoint mới từ LEDGER,
    # không từ snapshot runtime có thể chưa phản ánh action của lượt trước.
    if modifier.kind == "relative" and modifier.value is not None:
        dim = _resolve_dim(modifier, prior_dim, device_hint)
        parameters = dict(cg.get("parameters") or {})
        base_value = parameters.get(dim) if dim else None
        if dim and isinstance(base_value, int | float) and not isinstance(base_value, bool):
            signed_delta = modifier.value if modifier.direction == "increase" else -modifier.value
            target_value = float(base_value) + signed_delta
            device_type = None
            if len(prior_devices) == 1 and (spec := spec_for(prior_devices[0])) is not None:
                device_type = spec.device_type
            if bounds := capability_bounds(dim, device_type):
                target_value = max(bounds[0], min(bounds[1], target_value))
            normalized_target: int | float = int(target_value) if target_value.is_integer() else target_value
            return _build_goal(
                cg,
                modifier,
                prior_room=prior_room,
                prior_devices=prior_devices,
                outcomes=[],
                action_hint="set",
                parameters={dim: normalized_target},
            )

    if modifier.kind == "bound" and modifier.value is not None:
        dim = _resolve_dim(modifier, prior_dim, device_hint)
        if dim is None:
            return None
        scope = prior_devices[0] if len(prior_devices) == 1 else (prior_room or "*")
        prior_outcomes = [DesiredOutcome.model_validate(o) for o in (ledger.desired_outcomes or [])]
        return _build_goal(
            cg,
            modifier,
            prior_room=prior_room,
            prior_devices=prior_devices,
            outcomes=prior_outcomes,
            action_hint=cg.get("action_hint"),
            parameters=dict(cg.get("parameters") or {}),
            explicit_constraints=[f"bound:{modifier.direction}:{dim}:{modifier.value}:{scope}"],
        )

    # --- Loại trừ thiết bị khỏi mục tiêu NHÓM đã chốt (EXCLUSION_REFINEMENT, §5). Giữ NGUYÊN
    #     hành động/outcome lượt trước, chỉ thu hẹp danh sách thiết bị. ---
    if modifier.kind == "exclusion":
        remaining = [d for d in prior_devices if d not in modifier.excluded_ids]
        if not remaining:
            return None  # loại hết → không còn đích, để clarify thật (không đoán bừa)
        prior_outcomes = [DesiredOutcome.model_validate(o) for o in (ledger.desired_outcomes or [])]
        return _build_goal(cg, modifier, prior_room=prior_room, prior_devices=remaining,
                           outcomes=prior_outcomes, action_hint=cg.get("action_hint"), parameters={},
                           excluded_device_ids=modifier.excluded_ids)

    # --- Loại thiết bị khỏi mục tiêu CẢM NHẬN (outcome) đã chốt — "đừng đóng rèm" sau "cho
    #     phòng tối hơn". KHÁC nhánh "exclusion" ở trên: outcome goal chưa resolve thiết bị nào
    #     ở tầng author (target_device_ids rỗng), nên không có "nhóm" để thu hẹp — ghi thẳng
    #     excluded_device_ids để specialists (đọc field này khi gather_proposals) tự tránh thiết
    #     bị đó khi hiện thực hoá outcome bằng đường khác. ---
    if modifier.kind == "outcome_exclusion":
        prior_outcomes = [DesiredOutcome.model_validate(o) for o in (ledger.desired_outcomes or [])]
        if not prior_outcomes:
            return None
        return _build_goal(cg, modifier, prior_room=prior_room, prior_devices=[],
                           outcomes=prior_outcomes, action_hint=cg.get("action_hint"), parameters={},
                           excluded_device_ids=modifier.excluded_ids)

    # --- Giá trị tuyệt đối / phân nửa → lệnh SET tường minh, ghim thiết bị đã chốt. ---
    if modifier.kind in ("absolute", "fraction") and modifier.value is not None:
        dim = _resolve_dim(modifier, prior_dim, device_hint)
        if dim is None or not prior_devices:
            return None
        return _build_goal(cg, modifier, prior_room=prior_room, prior_devices=prior_devices,
                           outcomes=[], action_hint="set", parameters={dim: modifier.value})

    # Một lệnh explicit tăng/giảm đã ground theo nhóm không cần quay lại snapshot để suy
    # setpoint. Giữ nguyên semantic action trên đúng tập anchor còn lại sau constraint; nếu
    # không, snapshot eval chưa phản ánh lượt trước có thể biến "giảm tiếp" thành turn_off.
    if (
        modifier.kind == "relative"
        and not ledger.desired_outcomes
        and prior_hint in {"increase", "decrease"}
        and prior_devices
    ):
        direction = modifier.direction or fallback_direction or prior_hint
        if "increase_verb:thêm" in modifier.evidence:
            direction = fallback_direction or prior_hint
        return _build_goal(
            cg,
            modifier,
            prior_room=prior_room,
            prior_devices=prior_devices,
            outcomes=[],
            action_hint=direction,
            parameters={},
        )

    # --- Tương đối → giữ đường capability (open-ended). ---
    outcomes: list[DesiredOutcome] = []
    if ledger.desired_outcomes:
        for od in ledger.desired_outcomes:
            applied = _apply_modifier_to(
                od,
                modifier,
                device_hint,
                fallback_direction,
                prior_dim,
            )
            if prior_room:
                applied["selector"] = {**(applied.get("selector") or {}), "area": prior_room}
            outcomes.append(DesiredOutcome.model_validate(applied))
    else:
        base = {"selector": {"area": prior_room} if prior_room else {}}
        applied = _apply_modifier_to(
            base,
            modifier,
            device_hint,
            fallback_direction,
            prior_dim,
        )
        oc = DesiredOutcome.model_validate(applied)
        if not (oc.relative_change or oc.target_state):
            return None  # không suy được dimension → không kế thừa (để clarify thật)
        outcomes.append(oc)
    if not outcomes:
        return None
    return _build_goal(cg, modifier, prior_room=prior_room, prior_devices=prior_devices,
                       outcomes=outcomes, action_hint=None, parameters={})
