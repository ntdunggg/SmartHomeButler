"""Học thói quen từ lịch sử hành động (long-term memory).

Ý tưởng: bảng ``action_logs`` vốn đã ghi lại mọi thao tác kèm thời điểm, nên không
cần thu thập thêm dữ liệu gì. Gom các hành động theo (thiết bị, hành động, khung
giờ) rồi đếm xem hành vi đó lặp lại trong bao nhiêu ngày khác nhau.

``confidence`` = số ngày có hành vi / số ngày quan sát được. Đếm theo *ngày riêng
biệt* chứ không theo số lần, để một hôm bấm mười lần không bị hiểu nhầm thành
thói quen hằng ngày.

Chỉ đếm hành vi do NGƯỜI khởi xướng (``source`` là ``user`` hoặc ``agent``). Hành
động sinh ra từ chính đề xuất của hệ thống (``source='automation'``) bị loại — nếu
đếm, thói quen sẽ tự chứng minh chính nó và ``confidence`` chỉ có tăng. Nhờ loại
nó mà cơ chế tự sửa hoạt động: người dùng ngừng tự làm thì bằng chứng cũ trôi khỏi
cửa sổ 30 ngày, confidence tụt dưới ngưỡng, thói quen ngừng đề xuất.

Cơ chế tự sửa đó chỉ đúng nếu MỖI lượt quét đều TÍNH LẠI mọi thói quen đã học chứ
không chỉ những combo còn bằng chứng: một thói quen mà bằng chứng trôi hết khỏi cửa
sổ sẽ không có bucket nào chạm tới, nên phải hạ ``confidence`` về 0 một cách chủ động
(``learn_habits`` làm ở cuối), nếu không nó đóng băng và đề xuất mãi (#3).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.core import clock
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device, Habit
from src.iot.registry import spec_for
from src.services.audit import SOURCE_AUTOMATION

LOOKBACK_DAYS = 30

# Nguồn gốc một thói quen. "manual" = người dùng tự thêm qua giao diện; vòng học
# không được sinh, ghi đè hay decay loại này (nó không có bằng chứng ActionLog nên
# sẽ bị hạ về 0 ngay nếu không loại trừ). "learned" = do learn_habits tự tổng hợp.
SOURCE_LEARNED = "learned"
SOURCE_MANUAL = "manual"

# Chữ ký một thói quen: (user_id, device_slug, action, hour)
Signature = tuple[int | None, str, str, int]


def learn_habits(session: Session, *, household_id: int) -> list[Habit]:
    """Quét lịch sử và cập nhật bảng thói quen. Trả về các thói quen đủ tin cậy."""
    settings = get_settings()
    # Bám ĐỒNG HỒ MÔ PHỎNG, không phải giờ thực: log được đóng dấu theo clock.now()
    # (xem models._utcnow) nên cửa sổ học cũng phải theo cùng đồng hồ, nếu không khi
    # demo đóng băng/đổi giờ thì cửa sổ trượt khỏi dữ liệu (#1/#4).
    now = clock.now()
    since = now - timedelta(days=LOOKBACK_DAYS)

    rows = session.execute(
        select(ActionLog, Device.slug)
        .join(Device, Device.id == ActionLog.device_id)
        .where(
            ActionLog.household_id == household_id,
            ActionLog.status == ActionStatus.EXECUTED,
            ActionLog.created_at >= since,
            # Cận trên: khi nhảy đồng hồ LÙI, log đóng dấu ở "tương lai" không được
            # học ngay như thể đã xảy ra (#12).
            ActionLog.created_at <= now,
            # Chỉ hành vi do người khởi xướng mới là bằng chứng về thói quen của họ.
            # Bấm "đồng ý" với đề xuất của chính hệ thống thì không phải.
            ActionLog.source != SOURCE_AUTOMATION,
        )
    ).all()

    # (user_id, slug, action, hour) -> tập các ngày xảy ra
    buckets: dict[Signature, set] = defaultdict(set)
    # Cùng khoá -> đếm bộ THAM SỐ hay dùng nhất (mấy độ, mức sáng bao nhiêu) để
    # đề xuất tái hiện đúng giá trị, không gửi params rỗng (#2).
    bucket_params: dict[Signature, Counter] = defaultdict(Counter)
    observed_days: set = set()

    for log, slug in rows:
        # Giờ & NGÀY theo múi giờ địa phương — khớp cái người dùng thấy: hành động
        # 22:30 (VN) gom vào giờ 22 và đúng ngày đó, thay vì 15h/nhầm ngày như UTC
        # (#14/#15).
        local = clock.to_local(log.created_at)
        day = local.date()
        observed_days.add(day)
        sig = (log.user_id, slug, log.action, local.hour)
        buckets[sig].add(day)
        if log.params:
            bucket_params[sig][json.dumps(log.params, sort_keys=True, ensure_ascii=False)] += 1

    total_days = len(observed_days) or 1
    learned: list[Habit] = []
    strong: set[Signature] = set()

    # Chữ ký của các thói quen TẠO TAY — vòng học không đụng vào (không upsert đè, không
    # decay). Người dùng đã tự đặt thì đó là ý muốn tường minh, không phải suy luận từ log.
    manual_sigs = _manual_signatures(session, household_id=household_id)

    for sig, days in buckets.items():
        if sig in manual_sigs:
            continue  # slot đã có thói quen tay chiếm giữ — không tạo learned trùng chữ ký
        occurrences = len(days)
        if occurrences < settings.habit_min_occurrences:
            continue  # chưa đủ ngày -> chưa tạo/không nâng thành thói quen (tránh rác)
        user_id, slug, action, hour = sig
        confidence = min(1.0, occurrences / total_days)
        habit = _upsert(
            session,
            household_id=household_id,
            user_id=user_id,
            slug=slug,
            action=action,
            hour=hour,
            occurrences=occurrences,
            confidence=confidence,
            params=_top_params(bucket_params.get(sig)),
        )
        strong.add(sig)
        if confidence >= settings.habit_min_confidence:
            learned.append(habit)

    _decay_stale(session, household_id=household_id, strong=strong, buckets=buckets, total_days=total_days)
    # Session dùng autoflush=False: đẩy các thay đổi occurrences/confidence xuống DB
    # ngay, để due_habits chạy SAU trong CÙNG vòng quét (run_once) đọc đúng giá trị
    # mới — nếu không, thói quen vừa decay vẫn lọt một vòng đề xuất.
    session.flush()
    return learned


def _top_params(counter: Counter | None) -> dict:
    """Bộ tham số xuất hiện nhiều nhất cho một combo (rỗng nếu combo không có params)."""
    if not counter:
        return {}
    return json.loads(counter.most_common(1)[0][0])


def _decay_stale(
    session: Session,
    *,
    household_id: int,
    strong: set,
    buckets: dict,
    total_days: int,
) -> None:
    """Tính lại các thói quen ĐÃ học mà lượt này KHÔNG còn đủ bằng chứng (#3).

    Combo tụt dưới ngưỡng ngày hoặc trôi hết khỏi cửa sổ 30 ngày sẽ không được vòng
    trên chạm tới, nên confidence sẽ đóng băng và đề xuất mãi. Ở đây hạ nó theo bằng
    chứng HIỆN TẠI (0 nếu không còn gì) để thói quen người dùng đã bỏ tự tắt.
    """
    for habit in session.scalars(select(Habit).where(Habit.household_id == household_id)):
        if habit.source == SOURCE_MANUAL:
            continue  # thói quen tay không có bằng chứng log -> không được decay về 0
        sig = (habit.user_id, habit.device_slug, habit.action, habit.hour)
        if sig in strong:
            continue  # vừa cập nhật ở vòng trên
        occ = len(buckets.get(sig, ()))
        habit.occurrences = occ
        habit.confidence = min(1.0, occ / total_days) if occ else 0.0


def _manual_signatures(session: Session, *, household_id: int) -> set[Signature]:
    """Chữ ký (user, slug, action, hour) của mọi thói quen tạo tay trong hộ."""
    rows = session.execute(
        select(Habit.user_id, Habit.device_slug, Habit.action, Habit.hour).where(
            Habit.household_id == household_id,
            Habit.source == SOURCE_MANUAL,
        )
    ).all()
    return {(uid, slug, action, hour) for uid, slug, action, hour in rows}


def _upsert(
    session: Session,
    *,
    household_id: int,
    user_id: int | None,
    slug: str,
    action: str,
    hour: int,
    occurrences: int,
    confidence: float,
    params: dict | None = None,
) -> Habit:
    habit = session.scalar(
        select(Habit).where(
            Habit.household_id == household_id,
            Habit.user_id == user_id,
            Habit.device_slug == slug,
            Habit.action == action,
            Habit.hour == hour,
        )
    )
    params = params or {}
    description = _describe(slug, action, hour, params)
    if habit is None:
        habit = Habit(
            household_id=household_id,
            user_id=user_id,
            device_slug=slug,
            action=action,
            hour=hour,
            occurrences=occurrences,
            confidence=confidence,
            description_vi=description,
            params=params,
        )
        session.add(habit)
        session.flush()
        return habit

    habit.occurrences = occurrences
    habit.confidence = confidence
    habit.description_vi = description
    # Chỉ đè params khi lượt này học được giá trị — giữ lại giá trị cũ nếu combo lần
    # này không kèm params (vd chuỗi log toàn turn_on/off không mang tham số).
    if params:
        habit.params = params
    return habit


_ACTION_LABEL = {
    "turn_on": "bật",
    "turn_off": "tắt",
    "lock": "khoá",
    "unlock": "mở khoá",
    "open": "mở",
    "close": "đóng",
    "set_temperature": "chỉnh nhiệt độ",
    "set_brightness": "chỉnh độ sáng",
    "set_position": "chỉnh vị trí",
}

# Đơn vị mức độ theo khoá params — để mô tả rõ "70%", "26°C" thay vì chỉ "bật/chỉnh".
_LEVEL_UNIT = {"brightness": "%", "position": "%", "temperature": "°C"}


def _level_phrase(params: dict) -> str:
    """Mức độ hay dùng, dạng ' 70%' / ' 26°C'; rỗng nếu hành động không mang mức."""
    for key, unit in _LEVEL_UNIT.items():
        if key in params:
            return f" {params[key]}{unit}"
    return ""


def _describe(slug: str, action: str, hour: int, params: dict | None = None, minute: int = 0) -> str:
    spec = spec_for(slug)
    name = spec.name if spec else slug
    verb = _ACTION_LABEL.get(action, action)
    # Có phút (thói quen tay) thì ghi rõ mốc "07:15"; không thì giữ nhãn giờ tròn "22h".
    when = f"{hour:02d}:{minute:02d}" if minute else f"{hour:02d}h"
    return f"Thường {verb} {name}{_level_phrase(params or {})} vào khoảng {when}"


def describe(slug: str, action: str, hour: int, params: dict | None = None, minute: int = 0) -> str:
    """Mô tả tiếng Việt cho một thói quen (dùng lại cho thói quen tạo tay)."""
    return _describe(slug, action, hour, params, minute)


def due_habits(session: Session, *, household_id: int, now: datetime | None = None) -> list[Habit]:
    """Các thói quen đang tới giờ và chưa được kích hoạt trong hôm nay.

    Bỏ qua thói quen đang bị hoãn — người dùng đã nói "để 22h15 hẵng tắt" thì
    không hỏi lại cho tới lúc đó.

    Thói quen đã tắt (``enabled=False``) bị loại ngay ở SQL: đây là chỗ DUY NHẤT
    quyết định thói quen nào tới lượt, nên người gọi không phải lọc lại.
    """
    settings = get_settings()
    # Cùng chuẩn giờ ĐỊA PHƯƠNG như lúc học (Habit.hour là giờ VN), và bám đồng hồ
    # mô phỏng — nếu due dùng giờ thực/UTC thì lệch với giờ đã gom, thói quen câm (#1/#4).
    now = now or clock.local_now()
    sim_now = clock.now()
    today = clock.to_local(sim_now).date()

    habits = session.scalars(
        select(Habit).where(
            Habit.household_id == household_id,
            Habit.hour == now.hour,
            Habit.confidence >= settings.habit_min_confidence,
            Habit.enabled.is_(True),
        )
    )

    due: list[Habit] = []
    for habit in habits:
        # Thói quen tay đặt kèm phút (như báo thức): chưa tới phút thì chưa đến lượt.
        # Learned habit luôn minute=0 nên điều kiện này vô hại với chúng.
        if habit.minute and now.minute < habit.minute:
            continue
        # Hoãn chỉ có hiệu lực trong CÙNG ngày và còn ở phía trước: nhảy đồng hồ lùi
        # khiến mốc hoãn cũ rơi vào "tương lai" của một ngày khác — không được để nó
        # khoá thói quen vĩnh viễn (#13).
        if habit.snoozed_until is not None:
            snoozed = clock.to_local(habit.snoozed_until)
            if snoozed.date() == today and snoozed > sim_now:
                continue
        # "Đã chạy hôm nay" chỉ tính khi mốc nằm trong QUÁ KHỨ. Sau khi nhảy lùi, mốc
        # last_triggered cũ ở tương lai là tàn dư — bỏ qua để thói quen không bị câm (#13).
        if habit.last_triggered_at is not None:
            triggered = clock.to_local(habit.last_triggered_at)
            if triggered <= sim_now and triggered.date() == today:
                continue
        due.append(habit)
    return due


def snooze(session: Session, habit: Habit, *, until: datetime) -> None:
    """Hoãn một thói quen tới thời điểm chỉ định (chỉ áp dụng cho hôm nay)."""
    habit.snoozed_until = until


def mark_triggered(session: Session, habit: Habit) -> None:
    # Đóng dấu theo đồng hồ mô phỏng để mốc "đã chạy hôm nay" nằm cùng dòng thời
    # gian với due_habits (#4/#13).
    habit.last_triggered_at = clock.now()
