"""Requirement Ledger store (spec §17) — canonical conversation state.

Ledger là bản ghi CHÍNH của trạng thái hội thoại (§P4): planner đọc ledger thay vì
raw chat history. Store ở đây chỉ lo lưu/đọc theo `conversation_id`; logic merge nằm
ở `ledger_updater.py` (tách read/merge khỏi persistence).

V1 lưu in-memory (đủ cho một tiến trình / test). Tầng service có thể thay bằng adapter
DB mà không đổi contract — giống cách các store khác trong repo tách interface.
"""

from __future__ import annotations

from src.agent.schemas import RequirementLedger


class LedgerStore:
    """Lưu trữ RequirementLedger theo conversation_id (in-memory, V1)."""

    def __init__(self) -> None:
        self._by_conversation: dict[str, RequirementLedger] = {}

    def load(self, conversation_id: str) -> RequirementLedger:
        """Đọc ledger hiện tại; tạo mới rỗng nếu hội thoại chưa có (không lỗi)."""
        existing = self._by_conversation.get(conversation_id)
        if existing is not None:
            # Trả bản sao để caller merge không đụng bản đang lưu (immutability boundary).
            return existing.model_copy(deep=True)
        return RequirementLedger(conversation_id=conversation_id)

    def save(self, ledger: RequirementLedger) -> None:
        """Ghi đè ledger cho hội thoại."""
        self._by_conversation[ledger.conversation_id] = ledger.model_copy(deep=True)

    def reset(self, conversation_id: str) -> None:
        self._by_conversation.pop(conversation_id, None)


# Store mặc định dùng chung trong tiến trình (service có thể inject store riêng).
_DEFAULT_STORE = LedgerStore()


def get_default_store() -> LedgerStore:
    return _DEFAULT_STORE
