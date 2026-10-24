"""Endpoint số điện (kWh) ước tính cho toàn nhà — chỉ chủ hộ (OWNER)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from src.core import clock
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device
from src.main import app  # noqa: F401 — đảm bảo router energy đã đăng ký

_NOW_UTC = datetime(2026, 8, 27, 5, 0, tzinfo=UTC)


def _log_on(seeded, *, slug: str, at: datetime, action: str = "turn_on") -> None:
    device_id = seeded.scalar(select(Device).where(Device.slug == slug)).id
    seeded.add(
        ActionLog(
            household_id=1,
            device_id=device_id,
            action=action,
            status=ActionStatus.EXECUTED,
            created_at=at,
        )
    )
    seeded.commit()


async def test_energy_usage_owner_doc_duoc(client: AsyncClient, login, seeded):
    """Chủ hộ đọc số điện; điều hoà bật 2h → ~1.8 kWh hôm nay."""
    clock.set_clock(mode="frozen", frozen_ms=int(_NOW_UTC.timestamp() * 1000))
    try:
        _log_on(seeded, slug="dieu_hoa_phong_con", at=_NOW_UTC - timedelta(hours=2))
        headers = await login("bo")
        resp = await client.get("/api/v1/energy/usage", headers=headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["today_kwh"] == 1.8
        assert body["month_kwh"] == 1.8
        assert body["estimated"] is True
    finally:
        clock.reset()


async def test_energy_usage_chan_member(client: AsyncClient, login):
    """Trang quản lý số điện chỉ chủ hộ — thành viên bị chặn (403)."""
    headers = await login("con_nho")
    resp = await client.get("/api/v1/energy/usage", headers=headers)
    assert resp.status_code == 403


async def test_energy_usage_can_dang_nhap(client: AsyncClient):
    resp = await client.get("/api/v1/energy/usage")
    assert resp.status_code == 401


async def test_energy_series_day_buckets(client: AsyncClient, login, seeded):
    """Đồ thị theo ngày: mỗi ngày 1 điểm, điện rơi đúng ngày thiết bị chạy."""
    clock.set_clock(mode="frozen", frozen_ms=int(_NOW_UTC.timestamp() * 1000))
    try:
        # Điều hoà (900W) bật 2h trong ngày hôm nay (giờ VN 2026-08-27).
        _log_on(seeded, slug="dieu_hoa_phong_con", at=_NOW_UTC - timedelta(hours=2), action="turn_on")
        _log_on(seeded, slug="dieu_hoa_phong_con", at=_NOW_UTC, action="turn_off")
        headers = await login("bo")
        resp = await client.get(
            "/api/v1/energy/series",
            headers=headers,
            params={"granularity": "day", "start": "2026-08-25", "end": "2026-08-27"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["granularity"] == "day"
        assert [b["start"] for b in body["buckets"]] == ["2026-08-25", "2026-08-26", "2026-08-27"]
        by_day = {b["start"]: b["kwh"] for b in body["buckets"]}
        assert by_day["2026-08-27"] == 1.8
        assert by_day["2026-08-25"] == 0.0
        assert by_day["2026-08-26"] == 0.0
        assert body["total_kwh"] == 1.8
    finally:
        clock.reset()


async def test_energy_series_default_range_30_ngay(client: AsyncClient, login):
    """Không truyền start/end → mặc định 30 ngày gần nhất tính tới hôm nay."""
    clock.set_clock(mode="frozen", frozen_ms=int(_NOW_UTC.timestamp() * 1000))
    try:
        headers = await login("bo")
        resp = await client.get("/api/v1/energy/series", headers=headers)
        assert resp.status_code == 200, resp.text
        buckets = resp.json()["buckets"]
        assert len(buckets) == 30
        assert buckets[-1]["start"] == "2026-08-27"  # hôm nay là điểm cuối
    finally:
        clock.reset()


async def test_energy_series_chan_khoang_qua_dai(client: AsyncClient, login):
    """>366 ngày → 422."""
    headers = await login("bo")
    resp = await client.get(
        "/api/v1/energy/series",
        headers=headers,
        params={"granularity": "day", "start": "2024-01-01", "end": "2026-08-27"},
    )
    assert resp.status_code == 422


async def test_energy_series_chan_member(client: AsyncClient, login):
    headers = await login("con_nho")
    resp = await client.get("/api/v1/energy/series", headers=headers)
    assert resp.status_code == 403
