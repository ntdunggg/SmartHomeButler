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
(``detect_new_habits`` làm ở cuối), nếu không nó đóng băng và đề xuất mãi (#3).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.core import clock
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device, Habit, HabitConfirmRejection
from src.iot.registry import spec_for
from src.services.audit import SOURCE_AUTOMATION

LOOKBACK_DAYS = 30

# Thời gian không hỏi lại một thói quen mới mà người dùng đã bỏ qua.
HABIT_REJECTION_TTL = timedelta(hours=24)

# Nguồn gốc một thói quen. "manual" = người dùng tự thêm qua giao diện; vòng học
# không được sinh, ghi đè hay decay loại này (nó không có bằng chứng ActionLog nên
# sẽ bị hạ về 0 ngay nếu không loại trừ). "learned" = do vòng quét tự tổng hợp.
SOURCE_LEARNED = "learned"
SOURCE_MANUAL = "manual"

# Chữ ký một thói quen: (user_id, device_slug, action, hour)
Signature = tuple[int | None, str, str, int]


@dataclass
class NewHabitCandidate:
    """Thói quen tiềm năng vừa phát hiện lần đầu, chưa ghi vào DB.

    Được trả về bởi ``detect_new_habits()`` để caller (automation engine) quyết
    định có nên emit Suggestion xác nhận trước khi lưu hay không.
    """

    user_id: int | None
    device_slug: str
    action: str
    hour: int
    occurrences: int
    confidence: float
    params: dict = field(default_factory=dict)

    @property
    def signature(self) -> Signature:
        return (self.user_id, self.device_slug, self.action, self.hour)


def detect_new_habits(session: Session, *, household_id: int) -> list[NewHabitCandidate]:
    """Quét lịch sử, cập nhật thói quen ĐÃ CÓ, và trả về danh sách thói quen MỚI TINH.

    Với thói quen chưa từng có trong DB, hàm này KHÔNG gọi ``_upsert()`` — thay vào đó
    trả về ``NewHabitCandidate`` để automation engine emit
    Suggestion HABIT_CONFIRM, cho user quyết định trước khi lưu.

    Với thói quen ĐÃ có trong DB (update confidence/occurrences), vẫn gọi ``_upsert()``
    bình thường vì đây chỉ là cập nhật số liệu thống kê, không phải tạo mới.
    """
    settings = get_settings()
    now = clock.now()
    since = now - timedelta(days=LOOKBACK_DAYS)

    rows = session.execute(
        select(ActionLog, Device.slug)
        .join(Device, Device.id == ActionLog.device_id)
        .where(
            ActionLog.household_id == household_id,
            ActionLog.status == ActionStatus.EXECUTED,
            ActionLog.created_at >= since,
            ActionLog.created_at <= now,
            ActionLog.source != SOURCE_AUTOMATION,
        )
    ).all()

    buckets: dict[Signature, set] = defaultdict(set)
    bucket_params: dict[Signature, Counter] = defaultdict(Counter)
    observed_days: set = set()

    for log, slug in rows:
        local = clock.to_local(log.created_at)
        day = local.date()
        observed_days.add(day)
        sig = (log.user_id, slug, log.action, local.hour)
        buckets[sig].add(day)
        if log.params:
            bucket_params[sig][json.dumps(log.params, sort_keys=True, ensure_ascii=False)] += 1

    total_days = len(observed_days) or 1
    new_candidates: list[NewHabitCandidate] = []
    strong: set[Signature] = set()
    manual_sigs = _manual_signatures(session, household_id=household_id)

    for sig, days in buckets.items():
        if sig in manual_sigs:
            continue
        occurrences = len(days)
        if occurrences < settings.habit_min_occurrences:
            continue
        user_id, slug, action, hour = sig
        confidence = min(1.0, occurrences / total_days)

        # Kiểm tra thói quen đã có trong DB chưa
        existing = session.scalar(
            select(Habit).where(
                Habit.household_id == household_id,
                Habit.user_id == user_id,
                Habit.device_slug == slug,
                Habit.action == action,
                Habit.hour == hour,
            )
        )

        if existing is None:
            # Thói quen MỚI TINH — trả về để caller emit confirm, KHÔNG lưu ngay
            if confidence >= settings.habit_min_confidence:
                new_candidates.append(
                    NewHabitCandidate(
                        user_id=user_id,
                        device_slug=slug,
                        action=action,
                        hour=hour,
                        occurrences=occurrences,
                        confidence=confidence,
                        params=_top_params(bucket_params.get(sig)),
                    )
                )
        else:
            # Thói quen ĐÃ CÓ — cập nhật số liệu bình thường
            _upsert(
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

    _decay_stale(session, household_id=household_id, strong=strong, buckets=buckets, total_days=total_days)
    session.flush()
    return new_candidates


def commit_new_habit(
    session: Session,
    *,
    household_id: int,
    candidate: NewHabitCandidate,
) -> Habit:
    """Ghi một ``NewHabitCandidate`` đã được user xác nhận vào bảng Habit.

    Gọi từ ``decide_suggestion()`` khi user bấm Đồng ý trên popup HABIT_CONFIRM.
    """
    return _upsert(
        session,
        household_id=household_id,
        user_id=candidate.user_id,
        slug=candidate.device_slug,
        action=candidate.action,
        hour=candidate.hour,
        occurrences=candidate.occurrences,
        confidence=candidate.confidence,
        params=candidate.params,
    )


def record_habit_rejection(
    session: Session,
    *,
    household_id: int,
    candidate: NewHabitCandidate,
    now: datetime | None = None,
) -> None:
    """Ghi nhớ user đã bỏ qua xác nhận thói quen này: không hỏi lại trong ``HABIT_REJECTION_TTL``.

    Gọi từ ``decide_suggestion()`` khi user bấm Bỏ qua trên popup HABIT_CONFIRM.
    """
    now = now or clock.now()
    # Dọn các dòng đã hết hạn của cả hộ để bảng không phình mãi.
    session.execute(
        delete(HabitConfirmRejection).where(
            HabitConfirmRejection.household_id == household_id,
            HabitConfirmRejection.expires_at <= now,
        )
    )
    session.execute(delete(HabitConfirmRejection).where(*_rejection_match(household_id, candidate)))
    session.add(
        HabitConfirmRejection(
            household_id=household_id,
            user_id=candidate.user_id,
            device_slug=candidate.device_slug,
            action=candidate.action,
            hour=candidate.hour,
            expires_at=now + HABIT_REJECTION_TTL,
        )
    )
    session.flush()


def is_habit_rejected(
    session: Session,
    *,
    household_id: int,
    candidate: NewHabitCandidate,
    now: datetime | None = None,
) -> bool:
    """Thói quen này còn trong thời gian không hỏi lại (user đã bỏ qua chưa tới 24h)?"""
    now = now or clock.now()
    found = session.scalar(
        select(HabitConfirmRejection.id)
        .where(*_rejection_match(household_id, candidate), HabitConfirmRejection.expires_at > now)
        .limit(1)
    )
    return found is not None


def _rejection_match(household_id: int, candidate: NewHabitCandidate) -> tuple:
    # SQLAlchemy dịch so sánh với None thành IS NULL, nên thói quen không gắn user vẫn khớp đúng.
    return (
        HabitConfirmRejection.household_id == household_id,
        HabitConfirmRejection.user_id == candidate.user_id,
        HabitConfirmRejection.device_slug == candidate.device_slug,
        HabitConfirmRejection.action == candidate.action,
        HabitConfirmRejection.hour == candidate.hour,
    )


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
