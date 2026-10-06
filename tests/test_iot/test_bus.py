"""IoT bus in-process: lưu trạng thái và phát tin cho subscriber."""

from __future__ import annotations

import pytest

from src.api import ws
from src.core.errors import NotFoundError
from src.iot.factory import get_bus


async def test_lenh_lam_doi_trang_thai_va_luu_lai(seeded):
    bus = get_bus()
    result = await bus.send_command(1, "den_chum_phong_khach", {"action": "turn_on"})

    assert result.ok
    assert result.state["power"] == "on"
    # Đọc lại từ DB để chắc là đã lưu chứ không chỉ nằm trong bộ nhớ
    assert (await bus.get_state(1, "den_chum_phong_khach"))["power"] == "on"


async def test_subscriber_nhan_duoc_thay_doi(seeded):
    bus = get_bus()
    events: list[tuple[str, dict]] = []

    async def handler(slug: str, state: dict) -> None:
        events.append((slug, state))

    bus.subscribe(handler)
    await bus.send_command(1, "den_chum_phong_khach", {"action": "turn_on"})

    assert events == [("den_chum_phong_khach", {"power": "on", "brightness": 70})]


async def test_device_state_day_cam_bien_cong_suat_realtime(seeded, monkeypatch):
    bus = get_bus()
    messages: list[dict] = []

    async def fake_broadcast(household_id: int, message: dict, **_kwargs) -> None:
        messages.append({"household_id": household_id, **message})

    monkeypatch.setattr(ws.manager, "broadcast", fake_broadcast)
    bus.subscribe(ws.on_device_state)

    await bus.send_command(1, "den_chum_phong_khach", {"action": "turn_on"})

    sensor_messages = [m for m in messages if m["type"] == "sensor_state"]
    assert sensor_messages
    power = sensor_messages[-1]["sensors"][0]
    assert power["slug"] == "cam_bien_cong_suat"
    assert power["sensor_type"] == "power"
    assert power["unit"] == "kW"
    assert power["estimated"] is True
    assert power["value"] >= 0.01


async def test_mot_subscriber_loi_khong_chan_cac_subscriber_khac(seeded):
    """WebSocket của một client chết không được làm mất sự kiện của client khác."""
    bus = get_bus()
    received: list[str] = []

    async def broken(_slug: str, _state: dict) -> None:
        raise RuntimeError("client đã ngắt kết nối")

    async def working(slug: str, _state: dict) -> None:
        received.append(slug)

    bus.subscribe(broken)
    bus.subscribe(working)
    await bus.send_command(1, "den_chum_phong_khach", {"action": "turn_on"})

    assert received == ["den_chum_phong_khach"]


async def test_thiet_bi_khong_ton_tai(seeded):
    with pytest.raises(NotFoundError):
        await get_bus().send_command(1, "may_bay_khong_nguoi_lai", {"action": "turn_on"})


async def test_ho_khac_khong_dieu_khien_duoc_thiet_bi(seeded):
    """Thiết bị thuộc hộ 1; hộ 999 phải không thấy gì cả."""
    with pytest.raises(NotFoundError):
        await get_bus().send_command(999, "den_chum_phong_khach", {"action": "turn_on"})
