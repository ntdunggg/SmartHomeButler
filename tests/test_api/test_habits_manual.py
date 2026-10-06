"""API thêm thói quen thủ công: options / preview / commit.

Kiểm tra phần khớp nối HTTP (auth mọi thành viên, ép user_id là người đăng nhập,
map lỗi 400/409) — logic chi tiết đã có ở test_memory/test_habit_editing.
"""

from __future__ import annotations

from httpx import AsyncClient

_LIGHT = "den_chum_phong_khach"


async def test_options_available_to_any_member(client: AsyncClient, login, grant_access):
    grant_access("con_lon", "den_ngu_con")  # thành viên cần được cấp ít nhất 1 thiết bị
    headers = await login("con_lon")  # thành viên thường (không phải chủ hộ)
    resp = await client.get("/api/v1/habits/options", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["devices"] and body["actions"]
    assert any(a["action"] == "turn_on" for a in body["actions"])


async def test_preview_reports_conflict(client: AsyncClient, login):
    headers = await login("bo")
    payload = {
        "habits": [
            {"device_slug": _LIGHT, "action": "turn_on", "hour": 21, "minute": 0, "params": {}},
            {"device_slug": _LIGHT, "action": "turn_off", "hour": 21, "minute": 30, "params": {}},
        ]
    }
    resp = await client.post("/api/v1/habits/preview", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_conflicts"] is True
    slot = next(s for s in body["slots"] if s["hour"] == 21)
    assert slot["conflict"] is True and len(slot["candidates"]) == 2


async def test_commit_creates_then_lists(client: AsyncClient, login):
    headers = await login("bo")
    payload = {"habits": [{"device_slug": _LIGHT, "action": "set_brightness", "hour": 7, "minute": 20, "params": {"brightness": 55}}]}
    resp = await client.post("/api/v1/habits/commit", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    saved = resp.json()["saved"][0]
    assert saved["source"] == "manual" and saved["minute"] == 20 and saved["params"]["brightness"] == 55

    listed = (await client.get("/api/v1/habits?include_disabled=true", headers=headers)).json()
    assert any(h["id"] == saved["id"] and h["source"] == "manual" for h in listed)


async def test_commit_conflict_returns_409(client: AsyncClient, login):
    headers = await login("bo")
    payload = {
        "habits": [
            {"device_slug": _LIGHT, "action": "turn_on", "hour": 23, "minute": 0, "params": {}},
            {"device_slug": _LIGHT, "action": "turn_off", "hour": 23, "minute": 0, "params": {}},
        ]
    }
    resp = await client.post("/api/v1/habits/commit", json=payload, headers=headers)
    assert resp.status_code == 409, resp.text


async def test_commit_invalid_returns_400_with_reasons(client: AsyncClient, login):
    """Thành viên đặt thói quen lên thiết bị chưa được cấp quyền → 400 kèm lý do."""
    headers = await login("con_nho")  # điều hoà phòng khách — chưa được cấp quyền
    payload = {"habits": [{"device_slug": "dieu_hoa_phong_khach", "action": "turn_on", "hour": 8, "minute": 0, "params": {}}]}
    resp = await client.post("/api/v1/habits/commit", json=payload, headers=headers)
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["invalid"] and "chưa được chủ hộ cấp quyền" in detail["invalid"][0]["reason_vi"]
