"""Bộ nhớ phiên hội thoại (short-term memory).

Lưu ngữ cảnh của cuộc trò chuyện đang diễn ra: lượt nói gần nhất và câu hỏi lại
đang chờ trả lời. Nhờ đó người dùng nói "phòng khách" sau khi agent hỏi "đèn ở
phòng nào?" thì agent hiểu được đang nói tiếp về đèn.

Mặc định lưu trong tiến trình; đặt ``REDIS_URL`` là tự chuyển sang Redis mà phần
gọi không phải sửa gì.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)

MAX_TURNS = 10


class SessionStore:
    """Interface bộ nhớ phiên."""

    def get(self, conversation_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def set(self, conversation_id: str, data: dict[str, Any]) -> None:
        raise NotImplementedError

    def clear(self, conversation_id: str) -> None:
        raise NotImplementedError

    def append_turn(self, conversation_id: str, role: str, text: str) -> None:
        """Ghi thêm một lượt hội thoại, chỉ giữ lại ``MAX_TURNS`` lượt gần nhất."""
        data = self.get(conversation_id)
        turns = data.get("turns", [])
        turns.append({"role": role, "text": text})
        data["turns"] = turns[-MAX_TURNS:]
        self.set(conversation_id, data)

    def set_pending_clarification(self, conversation_id: str, question: str, candidates: list[str]) -> None:
        data = self.get(conversation_id)
        data["pending_clarification"] = {"question": question, "candidates": candidates}
        self.set(conversation_id, data)

    def pop_pending_clarification(self, conversation_id: str) -> dict[str, Any] | None:
        data = self.get(conversation_id)
        pending = data.pop("pending_clarification", None)
        self.set(conversation_id, data)
        return pending


class InMemorySessionStore(SessionStore):
    """Dict có hạn dùng — mặc định, không cần cài Redis."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._data: dict[str, tuple[float, dict[str, Any]]] = {}

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [key for key, (ts, _) in self._data.items() if now - ts > self._ttl]
        for key in expired:
            del self._data[key]

    def get(self, conversation_id: str) -> dict[str, Any]:
        self._purge()
        entry = self._data.get(conversation_id)
        return dict(entry[1]) if entry else {}

    def set(self, conversation_id: str, data: dict[str, Any]) -> None:
        self._data[conversation_id] = (time.monotonic(), dict(data))

    def clear(self, conversation_id: str) -> None:
        self._data.pop(conversation_id, None)


class RedisSessionStore(SessionStore):
    """Adapter Redis — dùng khi chạy nhiều worker cần chia sẻ phiên."""

    def __init__(self, url: str, ttl_seconds: int) -> None:
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._ttl = ttl_seconds

    def _key(self, conversation_id: str) -> str:
        return f"session:{conversation_id}"

    def get(self, conversation_id: str) -> dict[str, Any]:
        raw = self._client.get(self._key(conversation_id))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Dữ liệu phiên %s hỏng, bỏ qua", conversation_id)
            return {}

    def set(self, conversation_id: str, data: dict[str, Any]) -> None:
        self._client.setex(self._key(conversation_id), self._ttl, json.dumps(data, ensure_ascii=False))

    def clear(self, conversation_id: str) -> None:
        self._client.delete(self._key(conversation_id))


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        settings = get_settings()
        if settings.redis_url:
            try:
                _store = RedisSessionStore(settings.redis_url, settings.session_ttl_seconds)
                logger.info("Bộ nhớ phiên: Redis")
            except Exception:
                # Redis chết không được phép làm sập agent — quay về bộ nhớ tiến trình
                logger.exception("Không kết nối được Redis — dùng bộ nhớ trong tiến trình")
                _store = InMemorySessionStore(settings.session_ttl_seconds)
        else:
            _store = InMemorySessionStore(settings.session_ttl_seconds)
    return _store


def set_session_store(store: SessionStore | None) -> None:
    """Thay store — dùng trong test."""
    global _store
    _store = store
