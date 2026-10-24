"""Ledger persistence adapter (spec §17) — SQL-backed RequirementLedger store.

`LedgerStore` (in-memory) giữ contract load/save/reset và đủ cho một tiến trình / test.
Nhưng ledger LÀ trạng thái hội thoại canonical: khi nó chỉ nằm trong RAM, mọi thứ phụ
thuộc mạch hội thoại đứt ở lần restart hoặc khi request rơi vào worker khác —
slot-fill đa lượt ("Bật đèn" → "phòng bếp") mất mốc `raw_utterance` nên hỏi lại, và
ràng buộc durable (§73 invariant #8/#9) biến mất khiến planner có thể hồi sinh đúng
assumption người dùng vừa bác.

`SqlLedgerStore` là adapter DB THAY THẾ ĐƯỢC cho `LedgerStore` (cùng chữ ký), đúng như
`ledger.py` đã dự trù. Nó ghi-xuyên (write-through): cache trong tiến trình phục vụ các
lần đọc nóng của MỘT lượt, DB là nguồn sự thật GIỮA các lượt/tiến trình.

Ledger chạy trên TRANSACTION RIÊNG (`session_scope`), không dùng session của request:
- `get_db` không commit, nên ghi vào session request sẽ mất khi request kết thúc —
  đúng thứ làm slot-fill đa lượt chết ở lượt sau.
- Ngược lại, commit session của request từ đây sẽ vô tình chốt cả những thay đổi mà
  `agent_runner` còn đang dựng dở (action log, approval…) — vượt ranh giới trách nhiệm.
Ledger là state hội thoại, độc lập với việc lượt này có thực thi thành công hay không.
"""

from __future__ import annotations

import logging
from threading import RLock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.schemas import RequirementLedger
from src.db.session import session_scope
from src.domain.models import ConversationLedger

logger = logging.getLogger("agent.ledger")
_SAVE_LOCKS = tuple(RLock() for _ in range(64))


def _row(
    session: Session,
    conversation_id: str,
    *,
    for_update: bool = False,
) -> ConversationLedger | None:
    stmt = select(ConversationLedger).where(ConversationLedger.conversation_id == conversation_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def _append_unique_models(target: list[Any], additions: list[Any]) -> None:
    """Union Pydantic state by JSON value while retaining deterministic order."""
    signatures = {
        repr(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
        for item in target
    }
    for item in additions:
        signature = repr(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
        if signature not in signatures:
            target.append(item.model_copy(deep=True) if hasattr(item, "model_copy") else item)
            signatures.add(signature)


def _merge_concurrent_snapshots(
    stored: RequirementLedger,
    incoming: RequirementLedger,
) -> RequirementLedger:
    """Preserve durable deltas when workers save snapshots from the same base."""
    if incoming.last_updated_turn > stored.last_updated_turn:
        return incoming.model_copy(deep=True)
    if incoming.last_updated_turn < stored.last_updated_turn:
        return stored.model_copy(deep=True)

    # Equal turn numbers mean two workers advanced the same base concurrently.
    # Merge additive evidence; a later sequential turn has a greater number and
    # takes the fast path above, so explicit revocation is not resurrected.
    merged = incoming.model_copy(deep=True)
    merged.conversation_id = incoming.conversation_id or stored.conversation_id
    merged.last_updated_turn = max(stored.last_updated_turn, incoming.last_updated_turn)

    for field_name in ("constraints", "rejected_assumptions"):
        values = list(getattr(stored, field_name))
        for value in getattr(incoming, field_name):
            if value not in values:
                values.append(value)
        setattr(merged, field_name, values)

    for field_name in ("bounds", "group_exclusions", "no_change", "evidence"):
        values = [item.model_copy(deep=True) for item in getattr(stored, field_name)]
        _append_unique_models(values, list(getattr(incoming, field_name)))
        setattr(merged, field_name, values)

    # Salience is also durable evidence. Merge by device and keep the newest turn;
    # this preserves an explicit deferred branch written by another worker.
    salience_by_device: dict[str, dict[str, Any]] = {}
    for item in [*stored.salience, *incoming.salience]:
        device_id = str(item.get("device_id") or "")
        if not device_id:
            continue
        existing = salience_by_device.get(device_id)
        if existing is None or int(item.get("turn", 0)) >= int(existing.get("turn", 0)):
            salience_by_device[device_id] = dict(item)
    merged.salience = sorted(
        salience_by_device.values(),
        key=lambda item: (int(item.get("turn", 0)), str(item.get("device_id") or "")),
    )
    return merged


class SqlLedgerStore:
    """LedgerStore bền vững trên bảng ``conversation_ledgers``.

    Cùng contract với `LedgerStore` nên `PipelineDeps(ledger_store=...)` nhận trực tiếp.
    Lỗi DB KHÔNG được làm hỏng một lượt hội thoại: mọi thao tác rơi về cache trong tiến
    trình và ghi log — mất bền vững vẫn tốt hơn mất cả lượt trả lời.
    """

    def __init__(self, *, household_id: int | None = None) -> None:
        self._household_id = household_id
        self._cache: dict[str, RequirementLedger] = {}

    def load(self, conversation_id: str) -> RequirementLedger:
        """Đọc ledger; hội thoại chưa có → ledger rỗng (không lỗi), như bản in-memory."""
        cached = self._cache.get(conversation_id)
        if cached is not None:
            return cached.model_copy(deep=True)
        payload: dict | None = None
        try:
            with session_scope() as session:
                row = _row(session, conversation_id)
                payload = dict(row.payload or {}) if row is not None else None
        except Exception:  # noqa: BLE001 - DB hỏng không được làm chết lượt hội thoại
            logger.exception("ledger load failed conversation_id=%s", conversation_id)
            return RequirementLedger(conversation_id=conversation_id)
        if payload is None:
            return RequirementLedger(conversation_id=conversation_id)
        try:
            ledger = RequirementLedger.model_validate(payload)
        except Exception:  # noqa: BLE001 - snapshot của schema cũ/hỏng: bắt đầu lại sạch
            logger.warning("ledger payload invalid conversation_id=%s", conversation_id)
            return RequirementLedger(conversation_id=conversation_id)
        # `conversation_id` là khoá tra cứu; đồng bộ phòng khi snapshot cũ ghi trước lúc
        # updater gán field này.
        ledger.conversation_id = conversation_id
        self._cache[conversation_id] = ledger
        return ledger.model_copy(deep=True)

    def save(self, ledger: RequirementLedger) -> None:
        """Merge+upsert snapshot, preserving concurrent durable ledger deltas."""
        conversation_id = ledger.conversation_id
        if not conversation_id:
            # Không có khoá hội thoại thì không có gì để tra lại ở lượt sau — giữ hành vi
            # in-memory thay vì tạo rác trong DB.
            self._cache[conversation_id] = ledger.model_copy(deep=True)
            return
        saved = ledger.model_copy(deep=True)
        try:
            lock = _SAVE_LOCKS[hash(conversation_id) % len(_SAVE_LOCKS)]
            with lock:
                with session_scope() as session:
                    row = _row(session, conversation_id, for_update=True)
                    if row is None:
                        session.add(
                            ConversationLedger(
                                conversation_id=conversation_id,
                                household_id=self._household_id,
                                payload=saved.model_dump(mode="json"),
                                last_updated_turn=saved.last_updated_turn,
                            )
                        )
                    else:
                        try:
                            stored = RequirementLedger.model_validate(dict(row.payload or {}))
                        except Exception:  # noqa: BLE001 - invalid legacy payload is replaced
                            stored = RequirementLedger(conversation_id=conversation_id)
                        saved = _merge_concurrent_snapshots(stored, saved)
                        row.payload = saved.model_dump(mode="json")
                        row.last_updated_turn = saved.last_updated_turn
                        if row.household_id is None:
                            row.household_id = self._household_id
        except Exception:  # noqa: BLE001 - xem docstring lớp
            logger.exception("ledger save failed conversation_id=%s", conversation_id)
        self._cache[conversation_id] = saved.model_copy(deep=True)

    def reset(self, conversation_id: str) -> None:
        self._cache.pop(conversation_id, None)
        try:
            with session_scope() as session:
                row = _row(session, conversation_id)
                if row is not None:
                    session.delete(row)
        except Exception:  # noqa: BLE001 - xem docstring lớp
            logger.exception("ledger reset failed conversation_id=%s", conversation_id)
