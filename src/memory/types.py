"""Kiểu dữ liệu trung gian giữa tầng lưu trữ và pipeline suy luận.

Graph node KHÔNG được nhìn thấy ORM: nó nhận `MemoryItem` thuần, nên tầng truy hồi test
được mà không cần DB, và đổi backend lưu trữ không phải sửa graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Ba tầng bộ nhớ. `short_term` không nằm ở đây (nó là Pending Intent Stack trong
# SessionStore, đã có sẵn) — chỉ episodic và long-term mới cần truy hồi theo điểm.
KIND_EPISODE = "episode"
KIND_TRAIT = "trait"
KIND_HABIT = "habit"


@dataclass(frozen=True)
class MemoryItem:
    """Một mẩu ký ức đã sẵn sàng đưa vào prompt như BẰNG CHỨNG.

    `text` là câu tiếng Việt cho LLM đọc. `signals` giữ khoá tất định để chấm điểm.
    `polarity` phân biệt bằng chứng THUẬN (người dùng đã chấp nhận) với bằng chứng
    NGHỊCH (đã từ chối) — bỏ bằng chứng nghịch đi là cách chắc chắn để agent lặp lại
    đúng cái đề xuất vừa bị chối.
    """

    kind: str
    text: str
    confidence: float = 0.0
    polarity: str = "positive"  # positive | negative
    room: str | None = None
    hour: int | None = None
    devices: tuple[str, ...] = ()
    # Khoá GOM NHÓM cho consolidation (ổn định, do tầng ngữ nghĩa đặt — thường là snake_case
    # tiếng Anh). KHÔNG dùng để khớp với câu nói: nó không cùng ngôn ngữ với người dùng.
    situation_label: str = ""
    # Bề mặt KHỚP TRUY HỒI: token tiếng Việt lấy từ câu nói cũ + mô tả mục tiêu cũ. Tách
    # khỏi `situation_label` vì hai thứ này phục vụ hai việc khác nhau — trộn lại thì hoặc
    # không bao giờ khớp (nhãn tiếng Anh vs câu tiếng Việt), hoặc gom nhóm sai.
    match_tokens: tuple[str, ...] = ()
    trait_key: str = ""
    value: dict[str, Any] = field(default_factory=dict)
    source_id: int | None = None
    age_days: float = 0.0

    def as_evidence(self) -> dict[str, Any]:
        """Hình dạng đưa vào prompt — cố tình KHÔNG chứa danh sách hành động cũ.

        Nếu prompt nhìn thấy `proposed_actions` của lần trước, mô hình sẽ chép lại thay vì
        lập kế hoạch cho hoàn cảnh hiện tại — đúng thứ kiến trúc này cấm."""
        out: dict[str, Any] = {"loại": self.kind, "nội_dung": self.text, "độ_tin_cậy": round(self.confidence, 2)}
        if self.polarity == "negative":
            out["ghi_chú"] = "người dùng ĐÃ TỪ CHỐI điều tương tự trước đây"
        if self.room:
            out["phòng"] = self.room
        return out


@dataclass(frozen=True)
class MemoryRecall:
    """Kết quả một lần truy hồi, kèm lý do — để debug được vì sao agent nhớ ra thứ này."""

    items: tuple[MemoryItem, ...] = ()
    dropped_conflicts: tuple[str, ...] = ()

    def to_prompt_payload(self) -> list[dict[str, Any]]:
        return [i.as_evidence() for i in self.items]


@dataclass(frozen=True)
class RoutineCandidate:
    """Một THÓI QUEN đã học, đủ để TÁI DÙNG (khác `MemoryItem` vốn chỉ là bằng chứng).

    Điểm mấu chốt: `goal` là một SemanticGoal ở mức CAPABILITY (goal_description +
    desired_outcomes với DeviceSelector), KHÔNG phải danh sách hành động cụ thể. Khi tình
    huống lặp lại, agent dùng lại chính CÁCH HIỂU này rồi để Planner ground xuống thiết bị
    thật theo trạng thái hiện tại — nên bất biến "ký ức là bằng chứng, không phải kế hoạch"
    vẫn giữ (không bao giờ replay `proposed_actions`; goal vẫn phải qua validator + gate).

    `match_tokens` là bề mặt khớp truy hồi (token tiếng Việt từ câu nói đại diện), CÙNG quy
    ước fold dấu với `MemoryItem.match_tokens`. `hours` là histogram giờ (feed lịch trình)."""

    trait_key: str
    goal: dict[str, Any]
    confidence: float = 0.0
    room: str | None = None
    match_tokens: tuple[str, ...] = ()
    hours: tuple[int, ...] = ()
    user_id: int | None = None
    age_days: float = 0.0
