"""Checkpointer cho LangGraph — bền vững qua Postgres khi cấu hình, in-memory khi dev/test.

``InMemorySaver`` (mặc định của agent) giữ state hội thoại đang chờ duyệt (HITL)
trong RAM — server restart là mất trắng, người dùng bấm "Đồng ý" nhưng hệ thống
không còn nhớ ngữ cảnh. Khi ``DATABASE_URL`` trỏ tới Postgres, đổi sang
``AsyncPostgresSaver`` để state sống qua restart; SQLite (dev/test) không có
adapter tương ứng nên giữ nguyên in-memory — không đổi hành vi test hiện có.

Được gọi từ ``src.services.agent_runner`` để dựng đồ thị, không đụng tới
pipeline orchestration vì graph được compile ở tầng agent
tham số ``checkpointer``.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from src.config import get_settings

logger = logging.getLogger(__name__)


def _to_libpq_conn_string(database_url: str) -> str:
    """AsyncPostgresSaver dùng chuỗi kết nối psycopg thuần, không phải dialect SQLAlchemy."""
    return database_url.replace("postgresql+psycopg://", "postgresql://")


class CheckpointerHandle:
    """Bọc checkpointer cùng context manager cần đóng khi shutdown (nếu có).

    In-memory saver không giữ tài nguyên ngoài nào, nhưng Postgres saver mở một
    connection pool phải đóng đúng lúc app tắt. Gói chung vào một handle để nơi
    gọi (``agent_runner``) xử lý cả hai loại theo cùng một cách.
    """

    def __init__(self, checkpointer: Any, closer: Any = None) -> None:
        """Nhận checkpointer đã dựng và (tuỳ chọn) context manager cần đóng khi shutdown."""
        self.checkpointer = checkpointer
        self._closer = closer

    async def close(self) -> None:
        """Đóng tài nguyên nền (connection pool Postgres); không làm gì với in-memory."""
        if self._closer is not None:
            await self._closer.__aexit__(None, None, None)


async def build_checkpointer() -> CheckpointerHandle:
    """Trả về checkpointer phù hợp với ``DATABASE_URL`` hiện tại."""
    settings = get_settings()
    if not settings.database_url.startswith("postgres"):
        logger.info("Checkpointer: in-memory (DATABASE_URL không phải Postgres)")
        return CheckpointerHandle(InMemorySaver())

    try:
        async_postgres_saver_cls = importlib.import_module(
            "langgraph.checkpoint.postgres.aio"
        ).AsyncPostgresSaver
    except ImportError:
        logger.warning(
            "DATABASE_URL là Postgres nhưng thiếu gói langgraph-checkpoint-postgres — "
            "dùng tạm in-memory, cài `pip install langgraph-checkpoint-postgres` để bật bền vững."
        )
        return CheckpointerHandle(InMemorySaver())

    conn_string = _to_libpq_conn_string(settings.database_url)
    cm = async_postgres_saver_cls.from_conn_string(conn_string)
    checkpointer = await cm.__aenter__()
    await checkpointer.setup()
    logger.info("Checkpointer: Postgres (bền vững qua restart)")
    return CheckpointerHandle(checkpointer, closer=cm)
