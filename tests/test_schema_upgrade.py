"""Regression tests for upgrading databases created by older releases."""

from sqlalchemy import create_engine, inspect, text

from src.db.session import upgrade_schema


def test_upgrade_schema_adds_approval_episode_id_without_losing_rows(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE approvals (
                    id INTEGER PRIMARY KEY,
                    command_text TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(text("INSERT INTO approvals (id, command_text) VALUES (1, 'bật đèn')"))

    upgrade_schema(engine)
    upgrade_schema(engine)  # Startup upgrades must be idempotent.

    columns = {column["name"] for column in inspect(engine).get_columns("approvals")}
    assert "episode_id" in columns
    with engine.connect() as connection:
        row = connection.execute(text("SELECT id, command_text, episode_id FROM approvals")).one()
    assert tuple(row) == (1, "bật đèn", None)
