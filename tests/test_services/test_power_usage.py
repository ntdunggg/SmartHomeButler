"""Estimated power usage sensor contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from src.core import clock
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device
from src.services.power_usage import (
    accumulated_energy_kwh,
    energy_timeseries,
    estimated_power_sensor,
)


def test_estimated_power_sensor_uses_seeded_live_device_states(seeded):
    sensor = estimated_power_sensor(seeded, household_id=1)

    assert sensor["slug"] == "cam_bien_cong_suat"
    assert sensor["sensor_type"] == "power"
    assert sensor["unit"] == "kW"
    assert sensor["estimated"] is True
    assert sensor["unknown"] is False
    assert sensor["value"] == 0.01
    assert sensor["mode"] == "NORMAL"


def test_estimated_power_sensor_returns_zero_when_all_metered_devices_are_off(seeded):
    devices = seeded.scalars(select(Device).where(Device.household_id == 1)).all()
    for device in devices:
        state = dict(device.state or {})
        if "power" in state:
            state["power"] = "off"
            device.state = state
    seeded.commit()

    sensor = estimated_power_sensor(seeded, household_id=1)

    assert sensor["value"] == 0
    assert sensor["unknown"] is False


# --------------------------------------------------------------------------
# Số điện tích luỹ (kWh) hôm nay + tháng này — suy từ ActionLog × watt × thời lượng.
# --------------------------------------------------------------------------

# 12:00 (giờ VN) ngày 2026-08-27 = 05:00 UTC. Đóng băng để "now" xác định.
_NOW_UTC = datetime(2026, 8, 27, 5, 0, tzinfo=UTC)


@pytest.fixture
def frozen_now():
    """Đóng băng đồng hồ demo tại _NOW_UTC, tự trả về giờ thật sau test."""
    clock.set_clock(mode="frozen", frozen_ms=int(_NOW_UTC.timestamp() * 1000))
    try:
        yield _NOW_UTC
    finally:
        clock.reset()


def _device_id(session, slug: str) -> int:
    return session.scalar(select(Device).where(Device.slug == slug)).id


def _log(session, *, slug: str, action: str, at: datetime) -> None:
    session.add(
        ActionLog(
            household_id=1,
            device_id=_device_id(session, slug),
            action=action,
            status=ActionStatus.EXECUTED,
            created_at=at,
        )
    )
    session.commit()


def test_kwh_thiet_bi_dang_bat_tinh_toi_hien_tai(seeded, frozen_now):
    """Điều hoà (900W) bật 2 tiếng trước, chưa tắt → 900W × 2h = 1.8 kWh hôm nay."""
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_on", at=frozen_now - timedelta(hours=2))

    usage = accumulated_energy_kwh(seeded, household_id=1)

    assert usage["today_kwh"] == pytest.approx(1.8, abs=1e-3)
    assert usage["month_kwh"] == pytest.approx(1.8, abs=1e-3)
    assert usage["estimated"] is True


def test_kwh_bat_tat_tron_ven(seeded, frozen_now):
    """Bật lúc now-3h, tắt lúc now-1h → chạy 2h → 1.8 kWh."""
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_on", at=frozen_now - timedelta(hours=3))
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_off", at=frozen_now - timedelta(hours=1))

    usage = accumulated_energy_kwh(seeded, household_id=1)
    assert usage["today_kwh"] == pytest.approx(1.8, abs=1e-3)


def test_kwh_cat_theo_ranh_gioi_ngay(seeded, frozen_now):
    """Bật 22h hôm qua (VN), tắt 01h hôm nay (VN): hôm nay chỉ tính 1h, tháng tính 3h.

    0h VN = 17:00 UTC hôm trước. Bật 15:00 UTC, tắt 18:00 UTC.
    """
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_on", at=datetime(2026, 8, 26, 15, 0, tzinfo=UTC))
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_off", at=datetime(2026, 8, 26, 18, 0, tzinfo=UTC))

    usage = accumulated_energy_kwh(seeded, household_id=1)
    assert usage["today_kwh"] == pytest.approx(0.9, abs=1e-3)  # 1h × 900W
    assert usage["month_kwh"] == pytest.approx(2.7, abs=1e-3)  # 3h × 900W


def test_kwh_bo_qua_thiet_bi_0w(seeded, frozen_now):
    """Rèm (0W) mở/đóng không đóng góp; và action không phải bật/tắt nguồn bị bỏ qua."""
    _log(seeded, slug="rem_phong_con", action="open", at=frozen_now - timedelta(hours=5))
    # đèn chỉnh độ sáng nhưng CHƯA từng turn_on → không có khoảng bật nào
    _log(seeded, slug="den_ngu_con", action="set_brightness", at=frozen_now - timedelta(hours=4))

    usage = accumulated_energy_kwh(seeded, household_id=1)
    assert usage["today_kwh"] == 0.0
    assert usage["month_kwh"] == 0.0


def test_kwh_nha_trong_khong_co_log_bang_khong(seeded, frozen_now):
    usage = accumulated_energy_kwh(seeded, household_id=1)
    assert usage == {"today_kwh": 0.0, "month_kwh": 0.0, "estimated": True}


# --------------------------------------------------------------------------
# energy_timeseries — chuỗi kWh theo ngày/tháng để vẽ đồ thị.
# --------------------------------------------------------------------------

def test_series_day_phan_bo_dung_ngay(seeded, frozen_now):
    """Điều hoà 900W bật 2h hôm nay → chỉ ngày hôm nay có 1.8 kWh."""
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_on", at=frozen_now - timedelta(hours=2))
    series = energy_timeseries(
        seeded, household_id=1, granularity="day", start=date(2026, 8, 26), end=date(2026, 8, 27)
    )
    by_day = {b["start"]: b["kwh"] for b in series["buckets"]}
    assert by_day == {"2026-08-26": 0.0, "2026-08-27": 1.8}
    assert series["total_kwh"] == 1.8
    assert series["granularity"] == "day"


def test_series_khoang_vat_qua_hai_ngay(seeded, frozen_now):
    """Bật 22h hôm qua (VN), tắt 01h hôm nay → chia đúng: 26/08 có 2h, 27/08 có 1h."""
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_on", at=datetime(2026, 8, 26, 15, 0, tzinfo=UTC))
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_off", at=datetime(2026, 8, 26, 18, 0, tzinfo=UTC))
    series = energy_timeseries(
        seeded, household_id=1, granularity="day", start=date(2026, 8, 26), end=date(2026, 8, 27)
    )
    by_day = {b["start"]: b["kwh"] for b in series["buckets"]}
    # Ranh giới ngày VN = 17:00 UTC. Khoảng 15-18h UTC: 15-17h thuộc 26/08 (2h→1.8),
    # 17-18h thuộc 27/08 (1h→0.9).
    assert by_day["2026-08-26"] == pytest.approx(1.8, abs=1e-3)
    assert by_day["2026-08-27"] == pytest.approx(0.9, abs=1e-3)


def test_series_month_buckets(seeded, frozen_now):
    """Theo tháng: mỗi tháng 1 điểm, điện rơi vào tháng thiết bị chạy."""
    _log(seeded, slug="dieu_hoa_phong_con", action="turn_on", at=frozen_now - timedelta(hours=2))
    series = energy_timeseries(
        seeded, household_id=1, granularity="month", start=date(2026, 7, 1), end=date(2026, 8, 31)
    )
    labels = [b["start"] for b in series["buckets"]]
    assert labels == ["2026-07-01", "2026-08-01"]
    by_month = {b["start"]: b["kwh"] for b in series["buckets"]}
    assert by_month["2026-08-01"] == 1.8
    assert by_month["2026-07-01"] == 0.0


def test_series_validate(seeded, frozen_now):
    with pytest.raises(ValueError):
        energy_timeseries(seeded, household_id=1, granularity="week", start=date(2026, 8, 1), end=date(2026, 8, 2))
    with pytest.raises(ValueError):
        energy_timeseries(seeded, household_id=1, granularity="day", start=date(2026, 8, 5), end=date(2026, 8, 1))
    with pytest.raises(ValueError):  # >366 ngày
        energy_timeseries(seeded, household_id=1, granularity="day", start=date(2024, 1, 1), end=date(2026, 8, 27))
