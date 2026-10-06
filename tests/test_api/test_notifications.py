"""Thông báo cho chủ hộ khi thành viên dùng thiết bị ở trạng thái ALERT."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.enums import AccessEffect
from src.domain.models import Household, Notification


async def test_alert_tao_thong_bao_cho_chu_ho(client: AsyncClient, login, grant_access):
    # Chủ hộ cấp quyền ALERT cho con với một thiết bị.
    grant_access("con_nho", "den_ngu_con", effect=AccessEffect.ALERT)

    # Con điều khiển → chạy ngay (không chặn).
    child = await login("con_nho")
    resp = await client.post("/api/v1/devices/den_ngu_con/control", headers=child, json={"action": "turn_on"})
    assert resp.status_code == 200
    assert resp.json()["ok"] and not resp.json()["requires_approval"]

    # Chủ hộ thấy đúng một thông báo.
    owner = await login("bo")
    notes = (await client.get("/api/v1/notifications", headers=owner)).json()
    assert len(notes) == 1
    assert "den_ngu_con" not in notes[0]["message_vi"]  # thông báo dùng TÊN thiết bị, không phải slug
    assert notes[0]["read"] is False

    # Đánh dấu đã đọc.
    nid = notes[0]["id"]
    assert (await client.post(f"/api/v1/notifications/{nid}/read", headers=owner)).status_code == 204
    notes2 = (await client.get("/api/v1/notifications", headers=owner)).json()
    assert notes2[0]["read"] is True


async def test_accepted_khong_tao_thong_bao(client: AsyncClient, login, grant_access):
    # ACCEPTED: dùng ngay, KHÔNG báo chủ hộ.
    grant_access("con_nho", "den_ngu_con", effect=AccessEffect.ACCEPTED)
    child = await login("con_nho")
    await client.post("/api/v1/devices/den_ngu_con/control", headers=child, json={"action": "turn_on"})

    owner = await login("bo")
    notes = (await client.get("/api/v1/notifications", headers=owner)).json()
    assert notes == []


async def test_push_ws_false_ghi_db_khong_ban_ws(seeded: Session, monkeypatch):
    """V3 chống trùng: push_ws=False vẫn GHI Notification vào DB nhưng KHÔNG đẩy WS 'notification'.

    Đây là cơ chế cốt lõi V3 để owner không nhận 2 thông báo trùng (WS chuyên dụng +
    WS 'notification' chung) cho cùng một approval/conflict."""
    from src.api import notifications, ws

    sent: list[dict] = []

    async def _capture(household_id, payload, **kwargs):
        sent.append(payload)

    monkeypatch.setattr(ws.manager, "broadcast", _capture)

    hh = seeded.scalars(select(Household)).first().id
    owners = notifications.owner_ids(seeded, hh)
    assert owners, "seed phải có ít nhất một chủ hộ"

    # push_ws=False → KHÔNG bắn WS, nhưng DB vẫn có bản ghi cho mỗi người.
    await notifications.notify_recipients(
        seeded, household_id=hh, recipient_ids=owners,
        notification_type="approval_requested", message_vi="x", push_ws=False,
    )
    assert sent == [], "push_ws=False không được bắn bất kỳ WS nào"
    rows = seeded.scalars(
        select(Notification).where(
            Notification.household_id == hh, Notification.notification_type == "approval_requested"
        )
    ).all()
    assert len(rows) == len(owners)

    # push_ws=True (mặc định) → CÓ bắn WS type 'notification'.
    await notifications.notify_recipients(
        seeded, household_id=hh, recipient_ids=owners,
        notification_type="conflict_affected", message_vi="y",
    )
    assert any(p.get("type") == "notification" for p in sent)
