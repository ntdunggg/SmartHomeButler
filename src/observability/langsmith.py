"""Opt-in LangSmith setup for LangGraph and OpenAI-compatible calls."""

from __future__ import annotations

import logging
import os
from typing import Any

from src.config import Settings

logger = logging.getLogger(__name__)

_wrap_openai: Any = None
try:
    from langsmith.wrappers import wrap_openai as _wrap_openai
except ImportError:  # pragma: no cover - dependency contract is checked at deployment
    pass


def tracing_enabled(settings: Settings) -> bool:
    """Tracing requires an explicit flag and credential; no silent data export."""
    return settings.langsmith_tracing and bool(settings.langsmith_api_key)


def configure_langsmith(settings: Settings) -> bool:
    """Expose validated Settings to LangSmith/LangGraph's environment-based SDK."""
    if not settings.langsmith_tracing:
        return False
    if not settings.langsmith_api_key:
        logger.warning("LangSmith tracing được bật nhưng thiếu LANGSMITH_API_KEY; tracing bị tắt")
        return False
    if _wrap_openai is None:
        logger.warning("LangSmith tracing được bật nhưng package langsmith chưa cài; tracing bị tắt")
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_HIDE_INPUTS"] = str(settings.langsmith_hide_inputs).lower()
    os.environ["LANGSMITH_HIDE_OUTPUTS"] = str(settings.langsmith_hide_outputs).lower()
    if settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    if settings.langsmith_workspace_id:
        os.environ["LANGSMITH_WORKSPACE_ID"] = settings.langsmith_workspace_id
    # Bật tracing là bật luôn hàng rào: lỗi ingest không được vọng ra log vận hành.
    install_tracing_failure_guard()
    return True


# Dấu hiệu LangSmith TỪ CHỐI nhận trace vì hết hạn ngạch/bị chặn nhịp. Ingest chạy ở luồng
# NỀN và log lỗi cho MỖI run, nên khi quota tháng cạn, một phiên chat bình thường sinh hàng
# chục dòng traceback lẫn vào log vận hành — người đọc tưởng lượt suy luận đã hỏng, trong khi
# LLM vẫn trả lời đúng (§2026-08-30: đúng lúc quota cạn thì mọi câu mơ hồ trông như agent hỏng).
_QUOTA_MARKERS = ("usage limit", "rate limit", "429", "too many requests")
_TRACING_LOGGERS = ("langsmith", "langsmith.client")


class _TracingQuotaGuard(logging.Filter):
    """Gộp lỗi ingest của LangSmith thành MỘT cảnh báo, rồi im lặng.

    Hạn ngạch trace là chuyện QUAN SÁT, không phải chuyện điều khiển nhà: nó không được
    làm ồn log, không được làm chậm, và không bao giờ được đổi kết quả một lượt suy luận.
    Lọc ở tầng logging (không đụng SDK) nên đường gọi model không thêm một nhánh nào.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tripped = False

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        if not any(marker in message for marker in _QUOTA_MARKERS):
            return True
        if self.tripped:
            return False  # đã báo một lần; phần còn lại là tiếng ồn lặp lại
        self.tripped = True
        logger.warning(
            "LangSmith từ chối nhận trace (hết hạn ngạch hoặc bị chặn nhịp) — tracing coi như "
            "TẮT cho tiến trình này. Suy luận và điều khiển KHÔNG bị ảnh hưởng; các lỗi ingest "
            "tiếp theo sẽ không được log lại. Tắt hẳn bằng LANGSMITH_TRACING=false."
        )
        return False


_QUOTA_GUARD: _TracingQuotaGuard | None = None


def install_tracing_failure_guard() -> _TracingQuotaGuard:
    """Gắn bộ lọc lên logger của LangSmith (idempotent — gọi lại không chồng filter)."""
    global _QUOTA_GUARD
    if _QUOTA_GUARD is None:
        _QUOTA_GUARD = _TracingQuotaGuard()
        for name in _TRACING_LOGGERS:
            logging.getLogger(name).addFilter(_QUOTA_GUARD)
    return _QUOTA_GUARD


def wrap_openai_client(client: Any, settings: Settings) -> Any:
    """Wrap only configured live clients; offline tests and rule paths stay untouched."""
    if not configure_langsmith(settings):
        return client
    assert _wrap_openai is not None
    return _wrap_openai(client)
