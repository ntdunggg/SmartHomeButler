"""Kết nối database.

Cùng một đoạn code chạy được với SQLite (mặc định, không cần cài gì) lẫn
Postgres (khi chạy docker-compose) — chỉ khác giá trị ``DATABASE_URL``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings
from src.domain.models import Base


def _prepare_sqlite_path(url: str) -> None:
    """Tạo sẵn thư mục chứa file SQLite, nếu không SQLAlchemy sẽ báo lỗi."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    db_path = Path(url[len(prefix) :])
    if db_path.parent and str(db_path.parent) not in (".", ""):
        db_path.parent.mkdir(parents=True, exist_ok=True)


def _normalize_db_url(url: str) -> str:
    """Ép Postgres dùng driver psycopg v3 (dialect duy nhất project đã cài).

    Postgres managed (Render, Railway…) thường cấp URL dạng ``postgresql://`` hoặc
    ``postgres://``. SQLAlchemy mặc định chọn psycopg2 cho các dạng này — mà project
    không cài psycopg2 → lỗi khi khởi động. Đổi sang ``postgresql+psycopg://`` để dùng
    psycopg v3. SQLite và URL đã có ``+psycopg`` được giữ nguyên.
    """
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url = _normalize_db_url(settings.database_url)
    _prepare_sqlite_path(url)

    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # FastAPI chạy handler trên nhiều thread; SQLite mặc định chặn điều đó.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def upgrade_schema(engine: Engine | None = None) -> None:
    """Apply the small, additive migrations needed by an existing database.

    ``create_all`` only creates missing tables; it deliberately does not add new
    columns to tables that already exist.  Keep additive upgrades here so a dev
    database created by an older checkout remains usable after an update.
    """
    engine = engine or get_engine()
    schema = inspect(engine)
    if not schema.has_table("approvals"):
        return

    approval_columns = {column["name"] for column in schema.get_columns("approvals")}
    if "episode_id" not in approval_columns:
        # Constant identifiers and portable INTEGER syntax make this valid for
        # both the default SQLite database and PostgreSQL deployments.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE approvals ADD COLUMN episode_id INTEGER"))


def create_tables() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Vá cột/chỉ mục còn thiếu trên DB cũ (create_all không tự thêm vào bảng có sẵn)
    from src.db.migrate import ensure_schema, run_versioned_migrations

    ensure_schema(engine)
    # Migration dữ liệu có phiên bản (vd AccessEffect cũ → mới) — chỉ chạy một lần.
    run_versioned_migrations(engine)
    upgrade_schema(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session có commit/rollback tự động — dùng cho code ngoài request cycle."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Xoá cache engine — dùng trong test khi đổi DATABASE_URL."""
    get_engine.cache_clear()
    get_session_factory.cache_clear()
