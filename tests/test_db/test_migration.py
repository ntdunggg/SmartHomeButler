"""Migration có phiên bản: ghi schema_versions + chuyển AccessEffect cũ → mới (Mục 2)."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from src.db.migrate import ensure_schema, run_versioned_migrations
from src.db.session import get_engine


def test_schema_versions_ghi_v1_khi_seed(seeded):
    engine = get_engine()
    with engine.begin() as conn:
        versions = {r[0] for r in conn.execute(text("SELECT version FROM schema_versions")).fetchall()}
    assert 1 in versions  # v1 đã chạy trong create_tables


def test_migration_chuyen_allowed_thanh_accepted(seeded):
    engine = get_engine()
    with engine.begin() as conn:
        uid = conn.execute(text("SELECT id FROM users WHERE username='con_nho'")).scalar()
        hh = conn.execute(text("SELECT household_id FROM users WHERE id=:u"), {"u": uid}).scalar()
        did = conn.execute(text("SELECT id FROM devices WHERE household_id=:h LIMIT 1"), {"h": hh}).scalar()
        # Chèn luật với giá trị effect CŨ + xoá dấu v1 để migration chạy lại.
        conn.execute(
            text(
                "INSERT INTO access_rules (household_id, user_id, device_id, effect, updated_at) "
                "VALUES (:h, :u, :d, 'allowed', CURRENT_TIMESTAMP)"
            ),
            {"h": hh, "u": uid, "d": did},
        )
        conn.execute(text("DELETE FROM schema_versions WHERE version=1"))

    run_versioned_migrations(engine)

    with engine.begin() as conn:
        eff = conn.execute(
            text("SELECT effect FROM access_rules WHERE user_id=:u AND device_id=:d"), {"u": uid, "d": did}
        ).scalar()
    assert eff == "accepted"


def test_additive_schema_upgrade_restores_home_room_id_column():
    """DB tạo từ model trước feature phải được vá cột nullable khi khởi động."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL, "
                "username VARCHAR(64) NOT NULL, account_id VARCHAR(32), "
                "password_hash VARCHAR(255) NOT NULL, full_name_encrypted TEXT, "
                "role VARCHAR(16) NOT NULL, private_room_id INTEGER, created_at DATETIME)"
            )
        )

    ensure_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("users")}
    assert "home_room_id" in columns
    assert "ix_users_home_room_id" in indexes
