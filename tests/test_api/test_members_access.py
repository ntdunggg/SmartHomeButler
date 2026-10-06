"""Chủ hộ cấp quyền truy cập thiết bị cho thành viên (3 trạng thái) — API members/access."""

from __future__ import annotations

from httpx import AsyncClient

from src.domain.enums import AccessEffect


async def _member_id(client: AsyncClient, owner_headers: dict, username: str) -> int:
    members = (await client.get("/api/v1/members", headers=owner_headers)).json()
    return next(m["id"] for m in members if m["username"] == username)


async def _room_id(client: AsyncClient, owner_headers: dict, name: str) -> int:
    rooms = (await client.get("/api/v1/rooms", headers=owner_headers)).json()
    return next(r["id"] for r in rooms if r["name"] == name)


async def test_thanh_vien_moi_duoc_cap_san_quyen_phong_rieng(client: AsyncClient, login):
    """Tạo thành viên mới có phòng riêng → tự có 'accepted' thiết bị phòng đó, không trắng quyền."""
    owner = await login("bo")
    kids_room = await _room_id(client, owner, "Phòng ngủ con")

    created = await client.post(
        "/api/v1/members",
        headers=owner,
        json={
            "username": "con_moi",
            "full_name": "Trần Tân Binh",
            "password": "demo1234",
            "role": "member",
            "private_room_id": kids_room,
        },
    )
    assert created.status_code == 201
    mid = created.json()["id"]

    access = (await client.get(f"/api/v1/members/{mid}/access", headers=owner)).json()
    by_slug = {d["device_slug"]: d for d in access["devices"]}
    # Thiết bị phòng con: được cấp sẵn 'accepted' (kể cả điều hoà high_power).
    assert by_slug["den_ngu_con"]["effect"] == "accepted"
    assert by_slug["dieu_hoa_phong_con"]["effect"] == "accepted"
    # Thiết bị phòng khác: vẫn chưa cấp.
    assert by_slug["den_chum_phong_khach"]["effect"] == "none"

    # Đăng nhập tài khoản mới và bật đèn phòng mình được ngay.
    child = await login("con_moi")
    ctrl = await client.post("/api/v1/devices/den_ngu_con/control", headers=child, json={"action": "turn_on"})
    assert ctrl.status_code == 200


async def test_cap_an_ninh_accepted_can_xac_nhan(client: AsyncClient, login):
    owner = await login("bo")
    mid = await _member_id(client, owner, "con_nho")

    body = {"rules": [{"device_slug": "khoa_cua_chinh", "effect": "accepted"}]}
    # Chưa xác nhận → 409 kèm mã.
    resp = await client.put(f"/api/v1/members/{mid}/access", headers=owner, json=body)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "confirm_security_required"

    # Xác nhận → thành công.
    body["confirm_security"] = True
    resp2 = await client.put(f"/api/v1/members/{mid}/access", headers=owner, json=body)
    assert resp2.status_code == 200


async def test_cap_thiet_bi_thuong_accepted_khong_can_xac_nhan(client: AsyncClient, login):
    owner = await login("bo")
    mid = await _member_id(client, owner, "con_nho")
    body = {"rules": [{"device_slug": "den_ngu_con", "effect": "accepted"}]}
    resp = await client.put(f"/api/v1/members/{mid}/access", headers=owner, json=body)
    assert resp.status_code == 200


async def test_hieu_luc_sau_khi_cap_request(client: AsyncClient, login):
    """Cấp 'request' cho thiết bị thường → thành viên dùng phải qua duyệt (HITL)."""
    owner = await login("bo")
    mid = await _member_id(client, owner, "con_nho")
    await client.put(
        f"/api/v1/members/{mid}/access",
        headers=owner,
        json={"rules": [{"device_slug": "den_ngu_con", "effect": "request"}]},
    )
    child = await login("con_nho")
    resp = await client.post("/api/v1/devices/den_ngu_con/control", headers=child, json={"action": "turn_on"})
    assert resp.status_code == 200
    assert resp.json()["requires_approval"] is True


async def test_cap_ca_phong_bulk(client: AsyncClient, login):
    """Cấp 'accepted' cho cả một phòng → mọi thiết bị phòng đó dùng được, không đụng phòng khác."""
    owner = await login("bo")
    mid = await _member_id(client, owner, "con_nho")

    # Tìm room_id của Phòng bếp (không có thiết bị an ninh) qua danh sách access.
    access = (await client.get(f"/api/v1/members/{mid}/access", headers=owner)).json()
    kitchen = next(d for d in access["devices"] if d["room"] == "Phòng bếp")
    room_id = kitchen["room_id"]

    resp = await client.post(
        f"/api/v1/members/{mid}/access/room/{room_id}", headers=owner, json={"effect": "accepted"}
    )
    assert resp.status_code == 200
    body = resp.json()
    kitchen_devices = [d for d in body["devices"] if d["room"] == "Phòng bếp"]
    assert kitchen_devices and all(d["effect"] == "accepted" for d in kitchen_devices)
    # Phòng khác vẫn chưa cấp — trừ phòng riêng của con (Phòng ngủ con) đã được
    # seed cấp sẵn 'accepted' theo quyền phòng riêng.
    other = [d for d in body["devices"] if d["room"] not in ("Phòng bếp", "Phòng ngủ con")]
    assert all(d["effect"] == "none" for d in other)

    # Con dùng được đèn bếp ngay.
    child = await login("con_nho")
    ctrl = await client.post("/api/v1/devices/den_bep/control", headers=child, json={"action": "turn_on"})
    assert ctrl.status_code == 200


async def test_cap_ca_phong_co_an_ninh_can_xac_nhan(client: AsyncClient, login):
    owner = await login("bo")
    mid = await _member_id(client, owner, "con_nho")
    access = (await client.get(f"/api/v1/members/{mid}/access", headers=owner)).json()
    living = next(d for d in access["devices"] if d["room"] == "Phòng khách")  # có khoá + camera
    room_id = living["room_id"]

    resp = await client.post(
        f"/api/v1/members/{mid}/access/room/{room_id}", headers=owner, json={"effect": "accepted"}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "confirm_security_required"


async def test_thanh_vien_tu_xem_quyen_cua_minh(client: AsyncClient, login, grant_access):
    grant_access("con_nho", "den_ngu_con", effect=AccessEffect.ACCEPTED)
    child = await login("con_nho")
    body = (await client.get("/api/v1/members/me/access", headers=child)).json()
    granted = next(d for d in body["devices"] if d["device_slug"] == "den_ngu_con")
    assert granted["effect"] == "accepted" and granted["allowed"] is True
    # thiết bị chưa cấp thì effect = none
    ungranted = next(d for d in body["devices"] if d["device_slug"] == "binh_nong_lanh")
    assert ungranted["effect"] == "none" and ungranted["allowed"] is False


async def test_xin_duyet_map_tao_thong_bao_chu_ho(client: AsyncClient, login, grant_access):
    grant_access("con_nho", "dieu_hoa_phong_con", effect=AccessEffect.REQUEST)
    child = await login("con_nho")
    r = await client.post("/api/v1/devices/dieu_hoa_phong_con/control", headers=child, json={"action": "turn_on"})
    assert r.json()["requires_approval"] is True
    owner = await login("bo")
    notes = (await client.get("/api/v1/notifications", headers=owner)).json()
    assert any(n["notification_type"] == "approval_requested" for n in notes)
