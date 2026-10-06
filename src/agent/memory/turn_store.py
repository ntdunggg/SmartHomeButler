"""Turn Store (spec §20) — lưu raw evidence của từng lượt hội thoại.

Raw turns chỉ được dùng như evidence khi cần (spec §P4, §22): event summary đọc trước,
turn chỉ expand khi thiếu bằng chứng. Store này là nguồn cho HiGMem turn-expansion.
"""

from __future__ import annotations

from src.agent.schemas import TurnRecord


class TurnStore:
    """Lưu TurnRecord theo turn_id + index theo conversation_id (in-memory V1)."""

    def __init__(self) -> None:
        self._by_id: dict[str, TurnRecord] = {}
        self._by_conversation: dict[str, list[str]] = {}

    def add(self, turn: TurnRecord) -> None:
        self._by_id[turn.turn_id] = turn
        self._by_conversation.setdefault(turn.conversation_id, []).append(turn.turn_id)

    def get(self, turn_id: str) -> TurnRecord | None:
        return self._by_id.get(turn_id)

    def get_many(self, turn_ids: list[str]) -> list[TurnRecord]:
        """Đọc nhiều turn theo id (bỏ id không tồn tại) — dùng khi HiGMem expand."""
        return [self._by_id[t] for t in turn_ids if t in self._by_id]

    def for_conversation(self, conversation_id: str) -> list[TurnRecord]:
        return [self._by_id[t] for t in self._by_conversation.get(conversation_id, []) if t in self._by_id]
