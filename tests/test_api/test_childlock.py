"""Khoá trẻ em: chủ hộ bật/tắt; chặn thành viên với thiết bị nhạy cảm dù đã cấp."""

from __future__ import annotations

from httpx import AsyncClient

from src.domain.enums import AccessEffect


async def test_thanh_vien_khong_bat_duoc_khoa(client: AsyncClient, login):
    headers = await login("con_nho")
    resp = await client.put("/api/v1/household/child-lock", headers=headers, json={"enabled": True})
    assert resp.status_code == 403  # chỉ chủ hộ


async def test_bat_khoa_chan_thiet_bi_nhay_cam_da_cap(client: AsyncClient, login, grant_access):
    # Chủ hộ cấp con quyền dùng điều hoà phòng con (high_power).
    grant_access("con_nho", "dieu_hoa_phong_con", effect=AccessEffect.ACCEPTED)
    grant_access("con_nho", "den_ngu_con", effect=AccessEffect.ACCEPTED)  # normal

    owner = await login("bo")
    child = await login("con_nho")

    # Chưa khoá: con bật được điều hoà.
    assert (await client.post("/api/v1/devices/dieu_hoa_phong_con/control", headers=child, json={"action": "turn_on"})).status_code == 200

    # Chủ hộ bật khoá trẻ em.
    r = await client.put("/api/v1/household/child-lock", headers=owner, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True

    # Con bị chặn điều hoà (nhạy cảm)...
    blocked = await client.post("/api/v1/devices/dieu_hoa_phong_con/control", headers=child, json={"action": "turn_off"})
    assert blocked.status_code == 403 and "khoá trẻ em" in blocked.json()["detail"].lower()
    # ...nhưng đèn (thường) vẫn dùng được.
    assert (await client.post("/api/v1/devices/den_ngu_con/control", headers=child, json={"action": "turn_on"})).status_code == 200

    # Chủ hộ vẫn điều khiển được thiết bị nhạy cảm khi khoá đang bật.
    assert (await client.post("/api/v1/devices/dieu_hoa_phong_con/control", headers=owner, json={"action": "turn_off"})).status_code == 200

    # Tắt khoá → con dùng lại được.
    await client.put("/api/v1/household/child-lock", headers=owner, json={"enabled": False})
    assert (await client.post("/api/v1/devices/dieu_hoa_phong_con/control", headers=child, json={"action": "turn_on"})).status_code == 200


async def test_get_trang_thai_khoa(client: AsyncClient, login):
    headers = await login("con_nho")
    body = (await client.get("/api/v1/household/child-lock", headers=headers)).json()
    assert body["enabled"] is False


async def test_doi_khoa_tao_thong_bao_cho_thanh_vien(client: AsyncClient, login):
    owner = await login("bo")
    await client.put("/api/v1/household/child-lock", headers=owner, json={"enabled": True})
    child = await login("con_nho")
    notes = (await client.get("/api/v1/notifications", headers=child)).json()
    assert any(n["notification_type"] == "child_lock_changed" for n in notes)
