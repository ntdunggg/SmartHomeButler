"""Mô phỏng lịch sử hành động 1 tuần cho 4 thành viên với 4 chu kỳ sinh hoạt khác nhau.

Mục đích: tạo đủ dữ liệu ``action_logs`` để ``learn_habits``/``consolidate`` hình
thành được **hồ sơ AI** riêng cho từng người. Khi DB trống (vừa seed xong), hồ sơ
của cả 4 người đều rỗng — không có gì để demo. File này lấp khoảng đó bằng dữ liệu
giả nhưng có quy luật, mỗi người một nếp sinh hoạt:

- Bố (bo): dậy sớm, lo việc nhà — sáng bật bếp/bình nóng lạnh, tối khoá cửa.
- Mẹ (me): nấu nướng buổi chiều tối — bếp, máy lọc, điều hoà, máy rửa bát.
- Anh (con_lon): học buổi tối — đèn bàn học, điều hoà phòng con.
- Em (con_nho): ngủ sớm — chỉ chỉnh đèn ngủ (thiết bị thường, đúng quyền trẻ nhỏ).

Điểm quan trọng (Mảnh 1 — attribution): mỗi bản ghi được gán đúng ``user_id`` của
người gây ra hành động, nên ``learn_habits`` quy đúng thói quen về đúng người.

Chạy trực tiếp::

    python -m src.db.simulate_history

Mặc định ``reset=False`` — log mô phỏng được CỘNG THÊM vào lịch sử đang có, không
xoá gì. Đổi lại: chạy file này nhiều lần sẽ nhân đôi/nhân ba dữ liệu mô phỏng, làm
``occurrences`` phồng lên (``confidence`` thì không, vì nó đếm theo ngày riêng biệt).
Muốn kết quả tất định thì truyền ``reset=True`` — nhưng nó xoá SẠCH log của hộ, kể
cả log thật do người dùng tạo ra.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core import clock
from src.db.session import session_scope
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device, Household, User
from src.services.audit import SOURCE_USER

DEMO_HOUSEHOLD = "Căn hộ A-1203"
SIM_DAYS = 7

# Một hành động trong nếp sinh hoạt: (giờ, phút, slug thiết bị, action, params, mô tả)
Action = tuple[int, int, str, str, dict, str]

# Bốn nếp sinh hoạt khác nhau, khoá theo username seed sẵn (xem src/db/seed.py)
ROUTINES: dict[str, list[Action]] = {
    "bo": [
        (6, 5, "den_bep", "set_brightness", {"brightness": 70}, "Đặt Đèn bếp 70%"),
        (6, 20, "binh_nong_lanh", "turn_on", {}, "Bật Bình nóng lạnh"),
        (6, 40, "rem_phong_khach", "set_position", {"position": 60}, "Kéo Rèm 60%"),
        (22, 30, "khoa_cua_chinh", "lock", {}, "Khoá Cửa ra vào chính"),
        (22, 45, "den_chum_phong_khach", "turn_off", {}, "Tắt Đèn chùm"),
    ],
    "me": [
        (17, 0, "den_bep", "turn_on", {}, "Bật Đèn bếp"),
        (17, 10, "may_loc_phong_khach", "turn_on", {}, "Bật Máy lọc không khí"),
        (18, 0, "dieu_hoa_phong_khach", "set_temperature", {"temperature": 26}, "Đặt điều hoà 26°C"),
        (19, 30, "may_rua_bat", "turn_on", {}, "Bật Máy rửa bát"),
        (21, 0, "den_ngu_bo_me", "set_brightness", {"brightness": 40}, "Đặt Đèn ngủ 40%"),
    ],
    "con_lon": [
        (20, 0, "den_ban_hoc", "turn_on", {}, "Bật Đèn bàn học"),
        (20, 15, "dieu_hoa_phong_con", "set_temperature", {"temperature": 25}, "Đặt điều hoà 25°C"),
        (23, 0, "den_ban_hoc", "turn_off", {}, "Tắt Đèn bàn học"),
    ],
    "con_nho": [
        (6, 45, "den_ngu_con", "turn_on", {}, "Bật Đèn ngủ"),
        (21, 0, "den_ngu_con", "set_brightness", {"brightness": 30}, "Đặt Đèn ngủ 30%"),
        (21, 20, "den_ngu_con", "turn_off", {}, "Tắt Đèn ngủ"),
    ],
}

# Xác suất một người "quên" một hành động trong ngày — để confidence < 1.0 cho thực tế
_SKIP_PROBABILITY = 0.15


def simulate_week(session: Session, *, household_id: int, days: int = SIM_DAYS, rng_seed: int = 42, reset: bool = False) -> int:
    """Tạo log lịch sử ``days`` ngày gần nhất cho các thành viên của hộ.

    Trả về số bản ghi đã tạo. Mặc định KHÔNG xoá gì — log mô phỏng cộng thêm vào lịch
    sử đang có. ``reset=True`` xoá sạch log cũ của hộ (cả log thật) trước khi tạo lại.
    """
    rng = random.Random(rng_seed)

    if reset:
        session.execute(delete(ActionLog).where(ActionLog.household_id == household_id))

    users = {u.username: u for u in session.scalars(select(User).where(User.household_id == household_id))}
    devices = {d.slug: d for d in session.scalars(select(Device).where(Device.household_id == household_id))}

    today = clock.local_now().date()
    created = 0

    # Từ xa nhất (days ngày trước) tới hôm qua, để log tăng dần theo thời gian
    for day_offset in range(days, 0, -1):
        day = today - timedelta(days=day_offset)
        for username, actions in ROUTINES.items():
            user = users.get(username)
            if user is None:
                continue
            for hour, minute, slug, action, params, detail in actions:
                if rng.random() < _SKIP_PROBABILITY:
                    continue  # hôm đó người này quên làm việc này
                device = devices.get(slug)
                if device is None:
                    continue
                # Giờ trong ROUTINES là giờ ĐỊA PHƯƠNG (VN): đóng dấu theo LOCAL_TZ rồi quy
                # về UTC để khớp với learn_habits (gom theo giờ VN). Kẹp phút trong [0,59]
                # để jitter không đẩy hành động sang giờ khác (giữ đúng bucket giờ).
                m = max(0, min(59, minute + rng.randint(-8, 8)))
                ts = datetime(day.year, day.month, day.day, hour, m, tzinfo=clock.LOCAL_TZ).astimezone(UTC)
                session.add(
                    ActionLog(
                        household_id=household_id,
                        user_id=user.id,  # Mảnh 1: quy đúng hành động về đúng người
                        device_id=device.id,
                        action=action,
                        params=params,
                        status=ActionStatus.EXECUTED,
                        command_text=detail,
                        detail=detail,
                        source=SOURCE_USER,
                        latency_ms=rng.randint(20, 60),
                        created_at=ts,
                    )
                )
                created += 1

    session.commit()
    return created


def run(*, household_name: str = DEMO_HOUSEHOLD, days: int = SIM_DAYS) -> None:
    """Đảm bảo đã seed, tạo lịch sử mô phỏng, rồi in tóm tắt số log theo từng người."""
    from src.db.seed import run_seed

    run_seed()  # chắc chắn có hộ + 4 user + thiết bị trước khi tạo log

    with session_scope() as session:
        household = session.scalar(select(Household).where(Household.name == household_name))
        if household is None:
            print(f"Chưa có hộ '{household_name}' — hãy chạy seed trước.")
            return
        total = simulate_week(session, household_id=household.id, days=days)

        print(f"Đã tạo {total} bản ghi lịch sử cho {days} ngày gần nhất:")
        for username in ROUTINES:
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                continue
            count = len(list(session.scalars(select(ActionLog.id).where(ActionLog.user_id == user.id))))
            print(f"  - {username:8} ({user.full_name or username}): {count} hành động")


if __name__ == "__main__":
    run()
