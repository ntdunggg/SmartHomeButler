"""KI-04 API cutover gate cho pipeline V2.

Bất biến: lệnh high-risk/security phải trở thành plan đã ground trước, sau đó
mới bị authorization/policy cho phép hay từ chối. Không case nào được rơi thành
`no_plan`. Mục tiêu suy diễn vị trí phải ground theo location evidence.
"""

from __future__ import annotations

from httpx import AsyncClient

from src.iot.registry import DEVICE_BY_SLUG, LIVING_ROOM

from .test_agent import send


async def _device(client: AsyncClient, headers: dict, slug: str) -> dict:
    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    return next(device for device in dashboard["devices"] if device["slug"] == slug)


async def test_ki04_child_high_power_is_grounded_then_rejected(client: AsyncClient, login):
    headers = await login("con_nho")
    body = await send(client, headers, "bật bình nóng lạnh", conversation_id="ki04-child-power")

    step = next(step for step in body["plan"] if step["device_slug"] == "binh_nong_lanh")
    assert step["allowed"] is False
    assert str(step["status"]).endswith("denied")
    assert "chưa được" in body["response_vi"].lower() or "cấp quyền" in body["response_vi"].lower()
    assert (await _device(client, headers, "binh_nong_lanh"))["state"]["power"] == "off"


async def test_ki04_child_security_is_grounded_then_blocked(client: AsyncClient, login):
    headers = await login("con_lon")
    body = await send(client, headers, "mở cửa chính", conversation_id="ki04-child-security")

    step = next(step for step in body["plan"] if step["device_slug"] == "khoa_cua_chinh")
    assert step["action"] == "unlock"
    assert step["allowed"] is False
    assert str(step["status"]).endswith("denied")
    assert (await _device(client, headers, "khoa_cua_chinh"))["state"]["locked"] is True


async def test_ki04_owner_unlock_is_grounded_authorized_and_executed(client: AsyncClient, login):
    headers = await login("bo")
    body = await send(client, headers, "mở cửa chính", conversation_id="ki04-owner-unlock")

    step = next(step for step in body["plan"] if step["device_slug"] == "khoa_cua_chinh")
    assert step["action"] == "unlock"
    assert step["allowed"] is True
    assert str(step["status"]).endswith("executed")
    assert (await _device(client, headers, "khoa_cua_chinh"))["state"]["locked"] is False


async def test_ki04_inferred_location_is_grounded_into_plan(client: AsyncClient, login):
    headers = await login("bo")
    body = await send(client, headers, "ở đây tối quá", conversation_id="ki04-inferred-location")

    assert body["plan"], "location evidence đã đủ thì V2 không được trả no_plan/clarify"
    slugs = [step["device_slug"] for step in body["plan"]]
    assert all(DEVICE_BY_SLUG[slug].room == LIVING_ROOM for slug in slugs)
    assert not body["pending_approval"]
