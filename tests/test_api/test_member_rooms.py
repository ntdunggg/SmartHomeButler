"""Gán phòng mặc định/phòng riêng cho từng user qua API quản lý thành viên."""

from __future__ import annotations

from src.domain.models import Household, Room


async def _rooms(client, headers) -> dict[str, dict]:
    rows = (await client.get("/api/v1/rooms", headers=headers)).json()
    return {row["name"]: row for row in rows}


async def test_create_and_update_member_exposes_both_room_assignments(client, login):
    owner = await login("bo")
    rooms = await _rooms(client, owner)
    response = await client.post(
        "/api/v1/members",
        headers=owner,
        json={
            "username": "thanh_vien_moi",
            "full_name": "Thành Viên Mới",
            "password": "demo1234",
            "role": "member",
            "home_room_id": rooms["Phòng ngủ bố mẹ"]["id"],
            "private_room_id": rooms["Phòng ngủ con"]["id"],
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["home_room_name"] == "Phòng ngủ bố mẹ"
    assert created["private_room_name"] == "Phòng ngủ con"

    updated_response = await client.patch(
        f"/api/v1/members/{created['id']}",
        headers=owner,
        json={"home_room_id": None, "private_room_id": rooms["Phòng ngủ bố mẹ"]["id"]},
    )
    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()
    assert updated["home_room_id"] is None and updated["home_room_name"] == ""
    assert updated["private_room_name"] == "Phòng ngủ bố mẹ"

    member_headers = await login("thanh_vien_moi")
    me = (await client.get("/api/v1/auth/me", headers=member_headers)).json()
    assert me["home_room_id"] is None
    assert me["private_room_id"] == updated["private_room_id"]


async def test_room_assignments_reject_room_from_another_household(client, login, seeded):
    other_household = Household(name="Hộ khác", address="")
    seeded.add(other_household)
    seeded.flush()
    foreign_room = Room(household_id=other_household.id, name="Phòng ngoài hộ", sort_order=0)
    seeded.add(foreign_room)
    seeded.commit()

    owner = await login("bo")
    members = (await client.get("/api/v1/members", headers=owner)).json()
    child = next(member for member in members if member["username"] == "con_lon")
    response = await client.patch(
        f"/api/v1/members/{child['id']}",
        headers=owner,
        json={"home_room_id": foreign_room.id},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Không tìm thấy phòng mặc định."


async def test_deleting_room_clears_home_and_private_assignments(client, login):
    owner = await login("bo")
    rooms = await _rooms(client, owner)
    kitchen = rooms["Phòng bếp"]
    members = (await client.get("/api/v1/members", headers=owner)).json()
    child = next(member for member in members if member["username"] == "con_lon")
    assigned = await client.patch(
        f"/api/v1/members/{child['id']}",
        headers=owner,
        json={"home_room_id": kitchen["id"], "private_room_id": kitchen["id"]},
    )
    assert assigned.status_code == 200, assigned.text

    deleted = await client.delete(f"/api/v1/rooms/{kitchen['id']}", headers=owner)
    assert deleted.status_code == 204, deleted.text
    refreshed = (await client.get("/api/v1/members", headers=owner)).json()
    child_after = next(member for member in refreshed if member["username"] == "con_lon")
    assert child_after["home_room_id"] is None
    assert child_after["private_room_id"] is None
