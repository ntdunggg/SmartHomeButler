"""Bộ tách MỆNH ĐỀ LOẠI TRỪ trong MỘT câu — độc lập, thuần, test được (spec §8, §71).

"Bật A và B nhưng đừng bật B", "tắt tất cả đèn trừ đèn đọc sách": tách câu gốc thành
``positive_clause`` (mục tiêu dương) và ``exclusion_clause`` (phần loại trừ). Module này CHỈ
TÁCH VĂN BẢN theo tín hiệu ngôn ngữ tổng quát — KHÔNG ánh xạ thiết bị, KHÔNG map cụm→hành động.
Việc phân giải registry/alias và phép trừ do tầng tích hợp (``understand()``) làm SAU, và CHỈ
khi CẢ HAI mệnh đề đều map được thành thiết bị thật (hội nhập an toàn) — nếu không, giữ nguyên
hành vi cũ, không đoán bừa.

Khớp trên bản KHÔNG DẤU (``strip_diacritics``) để "nhưng đừng"/"nhung dung", "trừ"/"tru" đều bắt
được, nhưng vị trí cắt vẫn tính trên TOKEN gốc (giữ nguyên dấu ở output). Từ khoá loại trừ luôn
đi kèm cụm điều khiển/đại từ theo sau (yêu cầu còn NỘI DUNG sau từ khoá) để không cắt nhầm câu
hỏi ("... bật không?") hay câu không có phần loại trừ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.agent.text import strip_diacritics


@dataclass(frozen=True, slots=True)
class ExclusionSplit:
    """Kết quả tách: hai mệnh đề (giữ nguyên dấu) + từ khoá loại trừ đã khớp (bản không dấu)."""

    positive_clause: str
    exclusion_clause: str
    marker: str


# Từ khoá dẫn MỆNH ĐỀ LOẠI TRỪ, viết theo TOKEN đã KHÔNG DẤU. Hai hạng:
#   • loại trừ tường minh: "trừ", "ngoại trừ", "chỉ trừ", "loại trừ";
#   • phủ định + động từ điều khiển: "nhưng đừng", "nhưng không", "đừng <verb>", "không <verb>".
# KHÔNG đưa "chưa/chừa" (folded "chua") vào — va chạm với "chưa" trong câu hỏi ("đã tắt chưa").
# "đừng/không" TRẦN cũng không đưa vào — quá chung; luôn đòi ĐỘNG TỪ điều khiển ngay sau để tránh
# bắt nhầm "không sáng"/"có ai không". Tầng tích hợp còn double-check bằng ánh xạ thiết bị.
_CONTROL_VERBS = ("bat", "tat", "mo", "dong", "khoa", "chinh", "bo", "dung")
_EXCLUSION_MARKERS: tuple[tuple[str, ...], ...] = (
    ("ngoai", "tru"),
    ("chi", "tru"),
    ("loai", "tru"),
    ("nhung", "dung"),
    ("nhung", "khong"),
    ("nhung", "giu", "nguyen"),
    *[("dung", v) for v in _CONTROL_VERBS],
    *[("khong", v) for v in _CONTROL_VERBS],
    ("tru",),
)
# Xét CỤM DÀI trước ở cùng vị trí (2 token trước 1 token): "ngoại trừ" thắng "trừ", "nhưng đừng"
# thắng "đừng bật". Vị trí sớm nhất trong câu vẫn được ưu tiên trước độ dài (quét trái→phải).
_MARKERS_BY_LEN = sorted(_EXCLUSION_MARKERS, key=len, reverse=True)

_TRIM = " ,.;:!?—-"


def _folded_tokens(text: str) -> tuple[list[str], list[str]]:
    """(token gốc, token đã KHÔNG DẤU + thường + bỏ dấu câu bám) — cùng độ dài, ánh xạ 1-1."""
    words = text.split()
    folded = [strip_diacritics(w).lower().strip(_TRIM) for w in words]
    return words, folded


# ---------------------------------------------------------------------------
# Bộ tách N MỆNH ĐỀ ĐỘC LẬP tổng quát (thay cho 2 splitter hẹp trước đây, mỗi cái chỉ nhận
# ĐÚNG một liên từ cố định "nhưng"/"còn"). Một câu lệnh ghép tiếng Việt có thể nối các mệnh
# đề bằng BẤT KỲ tổ hợp nào trong số: dấu phẩy, "và", "rồi", "còn", "nhưng", "sau đó" — không
# chỉ một cặp liên từ benchmark hay gặp. Phạm vi phủ định của mỗi mệnh đề do CHÍNH mệnh đề đó
# quyết định (qua `NormalizedUtterance.has_negation` khi tầng gọi phân tích lại từng đoạn),
# KHÔNG suy từ vị trí trong câu hay cờ has_negation TOÀN CÂU.
# ---------------------------------------------------------------------------

# "còn" là liên từ nối mệnh đề tương phản ("... còn X thì ...") NHƯNG cũng là một phần của cụm
# cố định "còn lại" (remaining) — "tắt hết đèn CÒN LẠI trong bếp" KHÔNG phải ranh giới mệnh đề.
# Lookahead loại "còn" khi theo ngay sau là "lại". Không dùng bản KHÔNG DẤU cho riêng "còn" vì
# "con" (con cái) là một từ THẬT KHÁC, rất phổ biến trong tên phòng ("phòng ngủ con") — gộp
# chung sẽ tách nhầm mọi câu nhắc phòng con. Các liên từ còn lại ít nguy cơ va chạm hơn nên vẫn
# nhận cả bản không dấu để dùng được với input không gõ dấu.
_CLAUSE_BOUNDARY = re.compile(
    r"\s*,\s*"  # dấu phẩy — ranh giới mệnh đề phổ biến nhất, không cần liên từ
    r"|\s+còn(?!\s+lại)\s+"
    r"|\s+(?:và|va|rồi|roi|nhưng|nhung|sau đó|sau do|tiếp đó|tiep do)\s+",
    re.IGNORECASE,
)


def split_command_clauses(text: str) -> list[str]:
    """Tách một câu thành các MỆNH ĐỀ theo ranh giới cú pháp bề mặt TỔNG QUÁT (dấu phẩy hoặc
    liên từ nối mệnh đề phổ biến). CHỈ tách văn bản thô — mỗi mệnh đề có thật sự tự map được
    thiết bị + hành động (và phạm vi phủ định) riêng hay không do tầng gọi xác nhận SAU (hội
    nhập an toàn): nếu bất kỳ mệnh đề DƯƠNG nào không tự resolve được, toàn bộ phép tách phải
    bị huỷ bởi caller — không đoán bừa, không áp nhầm hành động chéo thiết bị."""
    parts = [p.strip(_TRIM) for p in _CLAUSE_BOUNDARY.split(text)]
    return [p for p in parts if p]


def negation_is_confined_to_exclusion(text: str) -> bool:
    """Phủ định của câu có nằm GỌN trong (các) mệnh đề loại trừ không?

    Cờ `has_negation` là thuộc tính của TOÀN CÂU, nên "Tôi sắp ngủ nhưng đừng tắt điều hoà"
    bị đọc thành một mục tiêu phủ định: mệnh đề cấm nuốt luôn mục tiêu chính, agent chỉ đáp
    "mình sẽ không tắt điều hoà" rồi dừng — nếp ngủ không bao giờ được dựng. Nhưng phạm vi phủ
    định thuộc về CHÍNH mệnh đề chứa nó (xem ghi chú bộ tách ở trên), nên câu vẫn còn một mục
    tiêu DƯƠNG khi có ít nhất một mệnh đề không phủ định.

    True ⇔ câu tách được ≥2 mệnh đề, ≥1 mệnh đề phủ định, và ≥1 mệnh đề KHÔNG phủ định.
    Câu chỉ có một mệnh đề ("đừng bật đèn") thực sự là mục tiêu phủ định → False.
    """
    from src.nlu.normalizer import analyze

    clauses = split_command_clauses(text)
    if len(clauses) < 2:
        return False
    negated = [bool(analyze(clause).has_negation) for clause in clauses]
    return any(negated) and not all(negated)


def split_exclusion_clause(text: str) -> ExclusionSplit | None:
    """Tách câu thành (positive, exclusion) tại từ khoá loại trừ ĐẦU TIÊN, hoặc None nếu không có.

    Yêu cầu: có mệnh đề dương ĐỨNG TRƯỚC (từ khoá không ở đầu câu) VÀ còn NỘI DUNG sau từ khoá
    (ứng viên thiết bị bị loại). Bảo đảm "trừ đèn ngủ" đứng một mình (không có phần dương) → None,
    để nhánh loại-trừ-đa-lượt (turn_intent) xử lý, không nhầm sang loại-trừ-trong-câu."""
    words, folded = _folded_tokens(text)
    n = len(folded)
    for i in range(1, n):  # i>=1: phải có ít nhất một token dương phía trước
        for marker in _MARKERS_BY_LEN:
            k = len(marker)
            if i + k <= n and tuple(folded[i : i + k]) == marker and i + k < n:
                positive = " ".join(words[:i]).strip(_TRIM)
                exclusion = " ".join(words[i:]).strip(_TRIM)
                if positive and exclusion:
                    return ExclusionSplit(positive, exclusion, " ".join(marker))
    return None
