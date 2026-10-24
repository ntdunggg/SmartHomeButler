"""Semantic/Profile Memory (spec §24) — fact tương đối ổn định về người dùng.

KHÔNG promote một event đơn lẻ thành stable fact nếu evidence chưa đủ (spec §24, §71):
một ProfileFact chỉ được lập/khẳng định khi có tối thiểu `min_supporting_events`.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from src.agent.schemas import MemoryEvent, ProfileFact

# Ngưỡng evidence tối thiểu để promote event → stable fact (spec §24).
_MIN_SUPPORTING_EVENTS = 3


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class ProfileStore:
    """Lưu ProfileFact theo (subject, fact) (in-memory V1)."""

    def __init__(self, *, min_supporting_events: int = _MIN_SUPPORTING_EVENTS) -> None:
        self._facts: dict[tuple[str, str], ProfileFact] = {}
        self._min_support = min_supporting_events

    def get(self, subject: str, fact: str) -> ProfileFact | None:
        return self._facts.get((subject, fact))

    def for_subject(self, subject: str) -> list[ProfileFact]:
        return [f for (s, _), f in self._facts.items() if s == subject]

    def relevant(self, subjects: set[str]) -> list[ProfileFact]:
        """Các stable fact thuộc một tập subject (spec §26 read path) — dùng lọc theo scope goal."""
        return [f for (s, _), f in self._facts.items() if s in subjects]

    def consolidate_from_events(self, events: list[MemoryEvent]) -> list[ProfileFact]:
        """Tự KHÁM PHÁ và promote mọi (subject, relation) đủ evidence từ một tập event (spec §24, §25).

        Gom fact số theo ĐÚNG (subject=thiết bị, relation) — không trộn thiết bị (mỗi đèn có mức
        sáng ưa thích riêng). Chỉ promote nhóm đạt `min_supporting_events`; nhóm chưa đủ giữ nguyên
        (KHÔNG bịa stable fact từ một lần — §24, §71). Idempotent: gọi lại chỉ cập nhật lại giá trị."""
        groups: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
        for ev in events:
            for f in ev.facts:
                if _is_number(f.value):
                    groups[(f.subject, f.relation)].append((float(f.value), ev.event_id))
        promoted: list[ProfileFact] = []
        for (subject, relation), pairs in groups.items():
            if len(pairs) < self._min_support:
                continue
            values = [v for v, _ in pairs]
            supporting = [eid for _, eid in pairs]
            pf = ProfileFact(
                subject=subject,
                fact=relation,
                value={"value": round(mean(values), 2)},
                confidence=min(1.0, len(supporting) / (self._min_support * 2)),
                supporting_events=supporting,
            )
            self._facts[(subject, relation)] = pf
            promoted.append(pf)
        return promoted

    def consolidate(self, subject: str, relation: str, events: list[MemoryEvent]) -> ProfileFact | None:
        """Chưng cất nhiều event thành một stable fact (spec §24, §25).

        Gom các fact cùng (subject-device, relation) qua các event; chỉ promote khi đủ
        `min_supporting_events`. Value = trung bình (số) — biểu diễn preference ổn định.
        Chưa đủ evidence → trả None (KHÔNG bịa stable fact).
        """
        values: list[float] = []
        supporting: list[str] = []
        for ev in events:
            for f in ev.facts:
                if f.relation == relation and isinstance(f.value, int | float) and not isinstance(f.value, bool):
                    values.append(float(f.value))
                    supporting.append(ev.event_id)
        if len(supporting) < self._min_support:
            return None
        fact_name = f"{relation}"
        pf = ProfileFact(
            subject=subject,
            fact=fact_name,
            value={"value": round(mean(values), 2)},
            confidence=min(1.0, len(supporting) / (self._min_support * 2)),
            supporting_events=supporting,
        )
        self._facts[(subject, fact_name)] = pf
        return pf


_DEFAULT_STORE = ProfileStore()


def get_default_store() -> ProfileStore:
    return _DEFAULT_STORE
