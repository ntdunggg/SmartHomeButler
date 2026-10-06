"""Estimated household power usage derived from live device state.

Hai đại lượng, cùng một bảng watt ước lượng (``config/energy.yaml``):

- **Công suất tức thời (kW)** — hàm thuần của state sống, tính lại mỗi lần đọc
  (``estimated_power_sensor``).
- **Số điện tích luỹ (kWh)** — tích phân watt × thời lượng suy từ lịch sử bật/tắt
  trong ``ActionLog`` (``accumulated_energy_kwh``). Không cần bảng mới/worker nền:
  tính khi có request, bền qua restart/redeploy vì log nằm ở Postgres.

Cả hai là ƯỚC LƯỢNG (hệ mô phỏng, không có công tơ thật) — UI phải ghi rõ.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.config import get_energy_config
from src.agent.perception.power_monitor import build_power_load, estimate_baseline_watts
from src.core import clock
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device

POWER_SENSOR_SLUG = "cam_bien_cong_suat"
POWER_SENSOR_TYPE = "power"

# Hành động làm đổi việc thiết bị có đang TIÊU ĐIỆN hay không. Chỉ giữ những action
# thực sự bật/tắt nguồn: thiết bị có ON_OFF (đèn, điều hoà, TV, quạt, bình nóng
# lạnh, máy lọc, loa...) dùng turn_on/turn_off; robot hút bụi dùng vacuum_*. Các
# action khác (chỉnh độ sáng/nhiệt độ, mở rèm 0W, khoá cửa 0W, chế độ media...) KHÔNG
# đổi trạng thái tiêu điện nên bỏ qua — tránh mapping sai cho thiết bị mode/0W.
_ON_ACTIONS = {"turn_on", "vacuum_start"}
_OFF_ACTIONS = {"turn_off", "vacuum_stop", "vacuum_return"}
_TOGGLE_ACTIONS = {"toggle"}


def estimated_power_sensor(session: Session, *, household_id: int) -> dict:
    """Return a sensor payload for estimated current household power in kW.

    This is not utility-meter telemetry. It reuses the agent's configured watt
    estimates and current device power states, so the value updates whenever the
    backend device state changes.
    """
    devices = session.scalars(select(Device).where(Device.household_id == household_id)).all()
    live_states = {device.slug: dict(device.state or {}) for device in devices}
    watts = estimate_baseline_watts(live_states)
    load = build_power_load(watts, unknown=not devices)
    return {
        "slug": POWER_SENSOR_SLUG,
        "name": "Công suất ước tính",
        "sensor_type": POWER_SENSOR_TYPE,
        "value": round(load.current_watts / 1000, 2),
        "unit": "kW",
        "room": "",
        "warning": load.mode.value != "NORMAL",
        "mode": load.mode.value,
        "estimated": True,
        "unknown": load.unknown,
    }


def _as_utc(value: datetime) -> datetime:
    """Chuẩn hoá mốc thời gian về UTC tz-aware (cột giờ SQLite hay trả naive)."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _day_start_utc(ref_local: datetime) -> datetime:
    """0h00 hôm nay (giờ địa phương người dùng thấy) quy về UTC."""
    start_local = ref_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(UTC)


def _month_start_utc(ref_local: datetime) -> datetime:
    start_local = ref_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(UTC)


def _on_intervals(session: Session, *, household_id: int, now: datetime) -> dict[int, list[tuple[datetime, datetime]]]:
    """Dựng lại các khoảng thời gian BẬT ``[start, end)`` cho từng thiết bị từ ActionLog.

    Duyệt toàn bộ log EXECUTED của hộ theo thứ tự thời gian (cần cả lịch sử trước
    cửa sổ để biết thiết bị đã bật sẵn từ trước hay chưa). Thiết bị còn đang bật ở
    cuối → đóng khoảng tại ``now``. Giả định trạng thái ban đầu là TẮT (khớp
    initial_state của seed).
    """
    rows = session.scalars(
        select(ActionLog)
        .where(
            ActionLog.household_id == household_id,
            ActionLog.status == ActionStatus.EXECUTED,
            ActionLog.device_id.is_not(None),
        )
        .order_by(ActionLog.created_at, ActionLog.id)
    ).all()

    is_on: dict[int, bool] = {}
    on_since: dict[int, datetime] = {}
    intervals: dict[int, list[tuple[datetime, datetime]]] = {}

    for row in rows:
        dev_id = row.device_id
        if dev_id is None:
            continue  # log không gắn thiết bị → không tính vào tiêu điện theo thiết bị
        ts = _as_utc(row.created_at)
        action = row.action
        currently_on = is_on.get(dev_id, False)

        if action in _ON_ACTIONS:
            turn_on = True
        elif action in _OFF_ACTIONS:
            turn_on = False
        elif action in _TOGGLE_ACTIONS:
            turn_on = not currently_on
        else:
            continue  # không đổi trạng thái tiêu điện

        if turn_on and not currently_on:
            is_on[dev_id] = True
            on_since[dev_id] = ts
        elif not turn_on and currently_on:
            intervals.setdefault(dev_id, []).append((on_since[dev_id], ts))
            is_on[dev_id] = False

    # Thiết bị còn đang bật → tính tới hiện tại.
    for dev_id, on in is_on.items():
        if on:
            intervals.setdefault(dev_id, []).append((on_since[dev_id], now))
    return intervals


def _overlap_hours(interval: tuple[datetime, datetime], lo_bound: datetime, hi_bound: datetime) -> float:
    """Số giờ mà một khoảng bật ``interval`` giao với cửa sổ ``[lo_bound, hi_bound)``."""
    start, end = interval
    lo = max(start, lo_bound)
    hi = min(end, hi_bound)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds() / 3600.0


def accumulated_energy_kwh(session: Session, *, household_id: int) -> dict:
    """Số điện đã dùng (kWh) ước tính cho TOÀN nhà: hôm nay và tháng này.

    Suy từ lịch sử bật/tắt (``ActionLog``) × bảng watt ước lượng, tích phân theo
    thời gian tới ``clock.now()`` (đồng hồ demo). Thiết bị không rõ watt → bỏ qua
    (không bịa, spec §42). Read-only, không lưu trữ thêm.
    """
    now = _as_utc(clock.now())
    ref_local = clock.local_now()
    day_start = _day_start_utc(ref_local)
    month_start = _month_start_utc(ref_local)

    power_table = get_energy_config().get("device_power_w", {})
    device_type = {
        d.id: str(d.device_type)
        for d in session.scalars(select(Device).where(Device.household_id == household_id))
    }

    intervals = _on_intervals(session, household_id=household_id, now=now)

    today_kwh = 0.0
    month_kwh = 0.0
    for dev_id, dev_intervals in intervals.items():
        watts = power_table.get(device_type.get(dev_id))
        if not watts:  # thiết bị đã xoá / không rõ watt / 0W → không đóng góp
            continue
        kw = float(watts) / 1000.0
        for interval in dev_intervals:
            today_kwh += kw * _overlap_hours(interval, day_start, now)
            month_kwh += kw * _overlap_hours(interval, month_start, now)

    return {
        "today_kwh": round(today_kwh, 3),
        "month_kwh": round(month_kwh, 3),
        "estimated": True,
    }


# Trần số bucket để tránh response khổng lồ / quét vô hạn (đã chốt với chủ dự án).
MAX_DAY_BUCKETS = 366
MAX_MONTH_BUCKETS = 24
DEFAULT_DAY_SPAN = 30  # số ngày mặc định khi FE không truyền khoảng


def _local_midnight_utc(d: date) -> datetime:
    """0h00 (giờ VN) của một ngày, quy về UTC tz-aware."""
    return datetime(d.year, d.month, d.day, tzinfo=clock.LOCAL_TZ).astimezone(UTC)


def _first_of_next_month(y: int, m: int) -> datetime:
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return datetime(ny, nm, 1, tzinfo=clock.LOCAL_TZ).astimezone(UTC)


def _day_buckets(start: date, end: date) -> list[tuple[datetime, datetime, str, str]]:
    """(lo_utc, hi_utc, iso_start, label) cho mỗi ngày trong [start, end]."""
    out: list[tuple[datetime, datetime, str, str]] = []
    cur = start
    while cur <= end:
        nxt = cur + timedelta(days=1)
        out.append((_local_midnight_utc(cur), _local_midnight_utc(nxt), cur.isoformat(), cur.strftime("%d/%m")))
        cur = nxt
    return out


def _month_buckets(start: date, end: date) -> list[tuple[datetime, datetime, str, str]]:
    """(lo_utc, hi_utc, iso_start, label) cho mỗi tháng từ tháng của start tới tháng của end."""
    out: list[tuple[datetime, datetime, str, str]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        lo = datetime(y, m, 1, tzinfo=clock.LOCAL_TZ).astimezone(UTC)
        hi = _first_of_next_month(y, m)
        out.append((lo, hi, f"{y:04d}-{m:02d}-01", f"{m:02d}/{y:04d}"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def energy_timeseries(
    session: Session, *, household_id: int, granularity: str, start: date, end: date
) -> dict:
    """Chuỗi số điện (kWh) theo mốc thời gian cho toàn nhà — vẽ đồ thị đường.

    ``granularity`` = ``"day"`` (mỗi ngày 1 điểm, phủ cả 'theo ngày' lẫn khoảng ngày
    tùy chỉnh) hoặc ``"month"`` (mỗi tháng 1 điểm). Ranh giới bucket theo giờ VN.
    Tái dùng cùng lõi tính với ``accumulated_energy_kwh`` (một nguồn sự thật).

    Raise ``ValueError`` nếu tham số sai (caller ánh xạ sang HTTP 422).
    """
    if granularity not in ("day", "month"):
        raise ValueError("granularity chỉ nhận 'day' hoặc 'month'.")
    if start > end:
        raise ValueError("start phải <= end.")

    if granularity == "day":
        buckets = _day_buckets(start, end)
        if len(buckets) > MAX_DAY_BUCKETS:
            raise ValueError(f"Khoảng quá dài: tối đa {MAX_DAY_BUCKETS} ngày.")
    else:
        buckets = _month_buckets(start, end)
        if len(buckets) > MAX_MONTH_BUCKETS:
            raise ValueError(f"Khoảng quá dài: tối đa {MAX_MONTH_BUCKETS} tháng.")

    now = _as_utc(clock.now())
    power_table = get_energy_config().get("device_power_w", {})
    device_type = {
        d.id: str(d.device_type)
        for d in session.scalars(select(Device).where(Device.household_id == household_id))
    }
    intervals = _on_intervals(session, household_id=household_id, now=now)

    # Gộp watt của từng thiết bị (bỏ thiết bị 0W/không rõ) để không lặp tra bảng.
    watt_intervals: list[tuple[float, list[tuple[datetime, datetime]]]] = []
    for dev_id, dev_intervals in intervals.items():
        watts = power_table.get(device_type.get(dev_id))
        if watts:
            watt_intervals.append((float(watts) / 1000.0, dev_intervals))

    out_buckets = []
    total = 0.0
    for lo, hi, iso_start, label in buckets:
        kwh = 0.0
        for kw, dev_intervals in watt_intervals:
            for interval in dev_intervals:
                kwh += kw * _overlap_hours(interval, lo, hi)
        total += kwh
        out_buckets.append({"start": iso_start, "label": label, "kwh": round(kwh, 3)})

    return {
        "granularity": granularity,
        "buckets": out_buckets,
        "total_kwh": round(total, 3),
        "estimated": True,
    }
