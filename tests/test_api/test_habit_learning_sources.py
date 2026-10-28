from sqlalchemy import delete, select

from src.config import get_settings
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device, Habit
from tests.helpers_habits import learn_confirmed


async def test_map_and_chat_actions_both_become_habit_evidence(
    client,
    login,
    seeded,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HABIT_MIN_OCCURRENCES", "1")
    monkeypatch.setenv("HABIT_MIN_CONFIDENCE", "0.05")
    get_settings.cache_clear()

    seeded.execute(delete(Habit))
    seeded.execute(delete(ActionLog))
    seeded.commit()

    headers = await login("bo")
    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    by_slug = {device["slug"]: device for device in dashboard["devices"]}

    kitchen_action = "turn_off" if by_slug["den_bep"]["state"].get("power") == "on" else "turn_on"
    map_response = await client.post(
        "/api/v1/devices/den_bep/control",
        headers=headers,
        json={"action": kitchen_action, "params": {}},
    )
    assert map_response.status_code == 200
    assert map_response.json()["ok"] is True

    living_action = (
        "turn_off"
        if by_slug["den_chum_phong_khach"]["state"].get("power") == "on"
        else "turn_on"
    )
    verb = "tắt" if living_action == "turn_off" else "bật"
    chat_response = await client.post(
        "/api/v1/agent/command",
        headers=headers,
        json={"message": f"{verb} đèn phòng khách"},
    )
    assert chat_response.status_code == 200

    seeded.expire_all()
    executed = seeded.execute(
        select(ActionLog.source, Device.slug, ActionLog.action)
        .join(Device, Device.id == ActionLog.device_id)
        .where(ActionLog.status == ActionStatus.EXECUTED)
    ).all()
    assert ("user", "den_bep", kitchen_action) in executed
    assert ("agent", "den_chum_phong_khach", living_action) in executed

    learned = learn_confirmed(seeded, household_id=1)
    signatures = {(habit.device_slug, habit.action) for habit in learned}
    assert ("den_bep", kitchen_action) in signatures
    assert ("den_chum_phong_khach", living_action) in signatures
