"""Vá lược đồ nhẹ — thêm cột/chỉ mục còn thiếu vào bảng ĐÃ tồn tại.

``create_all`` chỉ tạo bảng mới, KHÔNG thêm cột/chỉ mục vào bảng cũ. Nên khi model
thêm cột (vd ``Habit.enabled``, ``Device.room_id``), máy nào còn ``app.db`` cũ sẽ
500 khi chạm bảng đó. Đây là cầu nối tối thiểu thay cho Alembic đầy đủ: dò cột/chỉ
mục thiếu rồi ALTER/CREATE cho khớp model, chạy mỗi lần khởi động (idempotent).

Không thay thế Alembic cho việc đổi/xoá cột hay migration phức tạp — chỉ lo trường
hợp *thêm* cột/chỉ mục, vốn là 90% nhu cầu ở giai đoạn này.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex

from src.domain.models import Base

logger = logging.getLogger(__name__)


def _scalar_default_sql(col) -> str | None:
    """Chuyển default kiểu vô hướng của cột sang literal SQL; None nếu không có/không phải vô hướng."""
    default = col.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    # bool phải xét TRƯỚC int (bool là subclass của int). Dùng literal TRUE/FALSE thay
    # cho 1/0: Postgres từ chối `BOOLEAN DEFAULT 0` (DatatypeMismatch int→bool), còn SQLite
    # (≥3.23) hiểu TRUE/FALSE thành 1/0 — nên một dạng chạy đúng cho cả hai backend.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def ensure_schema(engine: Engine) -> None:
    """Thêm cột/chỉ mục còn thiếu ở bảng đã tồn tại cho khớp model. An toàn khi DB mới."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # bảng mới — create_all đã lo

        _ensure_columns(engine, inspector, table)
        _ensure_indexes(engine, inspector, table)


def _ensure_columns(engine: Engine, inspector, table) -> None:
    have = {c["name"] for c in inspector.get_columns(table.name)}
    for col in table.columns:
        if col.name in have:
            continue
        coltype = col.type.compile(dialect=engine.dialect)
        ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}"
        default_sql = _scalar_default_sql(col)
        if default_sql is not None:
            ddl += f" DEFAULT {default_sql}"
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info("Vá lược đồ: thêm cột %s.%s", table.name, col.name)
        except Exception:  # noqa: BLE001 — vá hỏng một cột không được chặn khởi động
            logger.exception("Không thêm được cột %s.%s (bỏ qua)", table.name, col.name)


def _ensure_indexes(engine: Engine, inspector, table) -> None:
    have = {i["name"] for i in inspector.get_indexes(table.name)}
    for index in table.indexes:
        if index.name is None or index.name in have:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(CreateIndex(index))
            logger.info("Vá lược đồ: tạo chỉ mục %s", index.name)
        except Exception:  # noqa: BLE001 — chỉ mục là tối ưu, hỏng không được chặn khởi động
            logger.exception("Không tạo được chỉ mục %s (bỏ qua)", index.name)


# --------------------------------------------------------------------------
# Migration CÓ PHIÊN BẢN — cho các DATA migration chỉ được chạy MỘT LẦN.
# ``ensure_schema`` lo phần thêm cột; phần chuyển đổi DỮ LIỆU cũ nằm ở đây.
# --------------------------------------------------------------------------
# (version, mô tả, danh sách câu SQL). Thêm bản mới ở cuối, KHÔNG sửa bản cũ.
_VERSIONED_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "AccessEffect cũ → mới (allowed→accepted, approval→request, xoá blocked)",
        (
            "UPDATE access_rules SET effect='accepted' WHERE effect='allowed'",
            "UPDATE access_rules SET effect='request' WHERE effect='approval'",
            "DELETE FROM access_rules WHERE effect='blocked'",
        ),
    ),
)


def _applied_versions(conn) -> set[int]:
    rows = conn.execute(text("SELECT version FROM schema_versions")).fetchall()
    return {r[0] for r in rows}


def run_versioned_migrations(engine: Engine) -> None:
    """Chạy các migration dữ liệu chưa áp dụng, ghi lại phiên bản (idempotent).

    Bảng ``schema_versions`` giữ các phiên bản đã chạy để không lặp lại. An toàn khi
    DB mới (bảng access_rules rỗng → UPDATE/DELETE không đụng gì).
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_versions ("
                "version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        applied = _applied_versions(conn)

    for version, description, statements in _VERSIONED_MIGRATIONS:
        if version in applied:
            continue
        try:
            with engine.begin() as conn:
                for sql in statements:
                    conn.execute(text(sql))
                conn.execute(text("INSERT INTO schema_versions (version) VALUES (:v)"), {"v": version})
            logger.info("Migration v%s đã chạy: %s", version, description)
        except Exception:  # noqa: BLE001 — một migration hỏng không được chặn khởi động
            logger.exception("Migration v%s thất bại (bỏ qua): %s", version, description)
