"""API hồ sơ thành viên: chỉ chủ hộ đọc được, và luôn trong phạm vi hộ mình."""

from __future__ import annotations

from httpx import AsyncClient


async def test_owner_reads_member_profile(client: AsyncClient, login):
    headers = await login("bo")
    members = (await client.get("/api/v1/members", headers=headers)).json()
    member_id = members[0]["id"]

    resp = await client.get(f"/api/v1/members/{member_id}/profile", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == member_id
    # Hình dạng ổn định kể cả khi chưa học được gì: các nhánh luôn có mặt.
    for key in ("preferences", "routines", "habits", "schedule"):
        assert key in body


async def test_non_owner_cannot_read_profile(client: AsyncClient, login):
    """Thành viên thường không phải chủ hộ → không xem được hồ sơ (router chỉ cho chủ hộ)."""
    headers = await login("con_nho")
    resp = await client.get("/api/v1/members/1/profile", headers=headers)
    assert resp.status_code == 403


async def test_unknown_member_is_not_found(client: AsyncClient, login):
    headers = await login("bo")
    resp = await client.get("/api/v1/members/99999/profile", headers=headers)
    assert resp.status_code == 404
