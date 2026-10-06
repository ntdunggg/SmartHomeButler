"""Nạp lịch sử hành động từ file JSON (dạng template) vào bảng ``action_logs``.

Dùng cho luồng demo: FE có element kéo-thả, người dùng dựng lịch sinh hoạt tuần
của từng thành viên rồi xuất ra JSON; script này biến JSON đó thành log để agent
học ra thói quen (``learn_habits`` đọc chính bảng ``action_logs``).

JSON dạng template — mỗi hành động khai báo MỘT lần, script tự nhân bản ra nhiều
ngày (vì agent cần hành vi lặp lại nhiều ngày mới thành thói quen)::

    {
      "household": "Căn hộ A-1203",
      "end_date": "2026-08-14",     # "hôm nay" theo ĐỒNG HỒ MÔ PHỎNG; ngày mới nhất
      "days": 7,                    # sinh log cho 7 ngày tính lùi từ end_date
      "reset": true,                # xoá log cũ của hộ trước khi nạp
      "members": [
        {
          "username": "bo",
          "routine": [
            {"hour": 6, "minute": 5, "device_slug": "den_bep", "action": "turn_on", "params": {}}
          ]
        }
      ]
    }

Mốc thời gian: script KHÔNG tự lấy giờ thực để không phá đồng hồ mô phỏng của
team. Nó neo vào ``end_date`` trong JSON (do FE điền theo đồng hồ mô phỏng); chỉ
khi JSON không có ``end_date`` và cũng không truyền ``end_now`` thì mới dùng
giờ thực làm phương án cuối.

Chạy trực tiếp::

    python -m src.db.import_history path/to/actions.json
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core import clock
from src.db.session import session_scope
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device, Household, User
from src.services.audit import SOURCE_USER

DEFAULT_HOUSEHOLD = "Căn hộ A-1203"
DEFAULT_DAYS = 7


def _anchor_date(data: dict, end_now: datetime | None) -> date:
    """Xác định 'hôm nay' để neo lịch sử: JSON.end_date > end_now truyền vào > giờ thực."""
    raw = data.get("end_date")
    if raw:
        return date.fromisoformat(raw)
    if end_now is not None:
        return end_now.date()
    return clock.local_now().date()


def import_history(session: Session, data: dict, *, end_now: datetime | None = None) -> int:
    """Nạp template JSON thành ``action_logs``. Trả về số bản ghi đã tạo."""
    household_name = data.get("household", DEFAULT_HOUSEHOLD)
    household = session.scalar(select(Household).where(Household.name == household_name))
    if household is None:
        return 0

    days = int(data.get("days", DEFAULT_DAYS))
    end = _anchor_date(data, end_now)

    if data.get("reset", True):
        session.execute(delete(ActionLog).where(ActionLog.household_id == household.id))

    users = {u.username: u for u in session.scalars(select(User).where(User.household_id == household.id))}
    devices = {d.slug: d for d in session.scalars(select(Device).where(Device.household_id == household.id))}

    created = 0
    for day_offset in range(days):
        day = end - timedelta(days=day_offset)
        for member in data.get("members", []):
            user = users.get(member.get("username", ""))
            if user is None:
                continue
            for act in member.get("routine", []):
                device = devices.get(act.get("device_slug", ""))
                if device is None:
                    continue
                minute = max(0, min(59, int(act.get("minute", 0))))
                # Giờ trong JSON là giờ ĐỊA PHƯƠNG (VN): đóng dấu theo LOCAL_TZ rồi quy về
                # UTC để learn_habits (gom theo giờ VN) lấy đúng bucket giờ đó.
                ts = datetime(day.year, day.month, day.day, int(act["hour"]), minute, tzinfo=clock.LOCAL_TZ).astimezone(UTC)
                detail = act.get("detail", f"{act['action']} {device.name}")
                session.add(
                    ActionLog(
                        household_id=household.id,
                        user_id=user.id,  # gán đúng người (attribution)
                        device_id=device.id,
                        action=act["action"],
                        params=dict(act.get("params") or {}),
                        status=ActionStatus.EXECUTED,
                        command_text=detail,
                        detail=detail,
                        source=SOURCE_USER,
                        created_at=ts,
                    )
                )
                created += 1

    session.commit()
    return created


def import_file(path: str, *, end_now: datetime | None = None) -> int:
    """Đọc file JSON và nạp vào DB. Trả về số bản ghi đã tạo."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    with session_scope() as session:
        return import_history(session, data, end_now=end_now)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Cách dùng: python -m src.db.import_history <đường-dẫn-file.json>")
        raise SystemExit(1)
    total = import_file(sys.argv[1])
    print(f"Đã nạp {total} bản ghi lịch sử từ {sys.argv[1]}.")
