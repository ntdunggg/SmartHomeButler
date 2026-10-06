"""Nguồn CHÂN LÝ DUY NHẤT cho phát hiện HƯỚNG tăng/giảm (grounding.md: "increase/decrease phải có
một nguồn sự thật"; §8: luật ngữ nghĩa tổng quát, không map cụm→case).

Trước bản gom này, `src/nlu/understanding.py` (bộ phân loại NLU) và
`src/agent/understanding/turn_intent.py` (bộ xử lý ý-định-lượt) mỗi nơi giữ MỘT bộ từ vựng hướng
riêng, lệch nhau: "nhỏ xuống"/"to lên" (LS-154) nhận diện được ở nơi này nhưng sót ở nơi kia
(turn_intent chỉ dò "<tính từ> hơn", bỏ "<tính từ> lên/xuống/lại/đi"). Gom vựng + logic về đây.

Lưu ý ngữ cảnh: "lên"/"xuống" TRẦN là ĐỘNG TỪ hướng trong lượt tiếp nối ("lên một nấc", "xuống
một chút") NHƯNG là cue ĐẶT-GIÁ-TRỊ trong lệnh có số ("xuống 24 độ" = set 24) — nên hai consumer
chọn TẬP ĐỘNG TỪ khác nhau (xem `INCREASE_VERBS_BARE`/`DECREASE_VERBS_BARE`). Vựng TÍNH TỪ và cơ
chế "<tính từ> <trạng từ định hướng>" là chung, đó chính là chỗ từng lệch gây LS-154.
"""

from __future__ import annotations

import re

# Động từ chỉ hướng TIN CẬY độc lập (không cần bổ ngữ) — dùng chung cả hai consumer.
# ``thêm`` đứng một mình là cue tăng yếu, nhưng khi đi cùng một động từ có hướng rõ
# (``giảm thêm``, ``đóng thêm``, ``làm mát thêm``) nó chỉ mang nghĩa tiếp tục/biên độ.
# Giữ nó trong public vocabulary để các consumer vẫn nhận ra lượt cụt ``thêm chút``;
# ``direction_of`` phân xử độ mạnh thay vì để thứ tự regex vô tình quyết định.
INCREASE_VERBS = ("tăng", "nâng", "mở thêm", "làm ấm", "sưởi ấm", "thêm")
DECREASE_VERBS = ("giảm", "bớt", "hạ", "dịu", "đóng thêm", "làm mát", "làm lạnh")
# "lên"/"xuống" TRẦN: chỉ turn_intent (lượt tiếp nối) coi là động từ hướng; understanding KHÔNG
# (để "xuống X độ" còn là set). Tách riêng để mỗi nơi chọn đúng.
INCREASE_VERBS_BARE = ("lên",)
DECREASE_VERBS_BARE = ("xuống",)

# Tính từ cường độ — CHỈ tính hướng khi đi kèm trạng từ so sánh/định hướng phía sau ("<adj> hơn",
# "to lên", "nhỏ xuống"). Dò trần dễ bắt nhầm DANH TỪ capability ("độ sáng" ≠ "tăng").
INCREASE_ADJ = ("cao", "mạnh", "sáng", "to", "lớn", "nhiều", "ấm", "nóng", "nhanh", "gắt", "đậm", "dày", "rộng")
DECREASE_ADJ = ("thấp", "nhẹ", "tối", "nhỏ", "ít", "mát", "lạnh", "chậm", "mỏng", "yếu", "hẹp")
# Trạng từ định hướng sau tính từ: "hơn" = so sánh (theo cực tính từ); "lên" = tăng; "xuống/lại/đi"
# = giảm. "sáng lên"→tăng, "nhỏ xuống"/"tối đi"→giảm. Public để `understanding.py` dựng pattern
# action_hint từ CÙNG bộ trạng từ (một nguồn sự thật).
INCREASE_ADV = ("hơn", "lên")
DECREASE_ADV = ("hơn", "xuống", "lại", "đi")


def _word(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w)}", text):
            return w
    return None


def _adj_dir(text: str, adjs: tuple[str, ...], advs: tuple[str, ...]) -> str | None:
    """"<tính từ>[hậu tố] <trạng từ định hướng>" — vd "sáng hơn", "to lên", "nhỏ xuống", "tối đi"."""
    for a in adjs:
        for adv in advs:
            if re.search(rf"(?<!\w){re.escape(a)}\w*\s+{re.escape(adv)}", text):
                return f"{a} {adv}"
    return None


def direction_of(text: str, *, bare_verbs: bool = True) -> tuple[str, str]:
    """(direction ∈ {"increase","decrease",""}, evidence) từ bản CÓ DẤU đã chuẩn hoá (normalized).

    `bare_verbs=True` (mặc định, turn_intent): coi "lên"/"xuống" TRẦN là động từ hướng.
    `bare_verbs=False` (understanding): bỏ "lên"/"xuống" trần (nhường cho cue set-giá-trị)."""
    inc_verbs = INCREASE_VERBS + (INCREASE_VERBS_BARE if bare_verbs else ())
    dec_verbs = DECREASE_VERBS + (DECREASE_VERBS_BARE if bare_verbs else ())
    # ``thêm`` is additive discourse, weaker than every explicit direction.
    # Resolve strong cues first so ``giảm/hạ/đóng/làm mát + thêm`` keeps the
    # semantic direction of the operation.  With no strong cue it retains the
    # historical standalone interpretation (increase); a grounded continuation
    # may subsequently reinterpret it as "same direction" using ledger evidence.
    strong_inc = tuple(verb for verb in inc_verbs if verb != "thêm")
    inc_v, dec_v = _word(text, strong_inc), _word(text, dec_verbs)
    if inc_v and not dec_v:
        return "increase", f"increase_verb:{inc_v}"
    if dec_v and not inc_v:
        return "decrease", f"decrease_verb:{dec_v}"
    if inc_v and dec_v:  # cả hai chiều (hiếm) → không quyết được
        return "", "ambiguous_verbs"
    inc_a = _adj_dir(text, INCREASE_ADJ, INCREASE_ADV)
    dec_a = _adj_dir(text, DECREASE_ADJ, DECREASE_ADV)
    if inc_a and not dec_a:
        return "increase", f"increase_cmp:{inc_a}"
    if dec_a and not inc_a:
        return "decrease", f"decrease_cmp:{dec_a}"
    if _word(text, ("thêm",)):
        return "increase", "increase_verb:thêm"
    return "", ""
