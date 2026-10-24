from src import main


async def test_startup_restores_pending_suggestions_before_automation(monkeypatch) -> None:
    events: list[str] = []

    def load_from_db(_session) -> int:
        events.append("restore")
        return 2

    async def start_engine() -> None:
        events.append("start")

    async def stop_engine() -> None:
        events.append("stop")

    async def close_agent() -> None:
        events.append("close")

    monkeypatch.setattr(main.registry, "load_from_db", load_from_db)
    monkeypatch.setattr(main.engine, "start", start_engine)
    monkeypatch.setattr(main.engine, "stop", stop_engine)
    monkeypatch.setattr(main, "close_agent", close_agent)

    async with main.lifespan(main.app):
        assert events[:2] == ["restore", "start"]

    assert events[-2:] == ["stop", "close"]
