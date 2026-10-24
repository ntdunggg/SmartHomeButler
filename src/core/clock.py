"""Đồng hồ hệ thống có thể mô phỏng — mirror đồng hồ nội bộ của frontend.

Frontend (``demo.html``) giữ một đồng hồ demo dạng ``{offset, mode, frozen}`` với
``simNow = Date.now() + offset`` (ms). Module này là bản sao ở backend để cả hệ
thống có MỘT nguồn thời gian nghiệp vụ thống nhất thay vì mỗi nơi tự gọi giờ thực.

Mô hình (giống hệt FE):
- ``mode = "live"``   → giờ mô phỏng = giờ thực + ``offset_ms`` (trôi theo thời gian thực).
- ``mode = "frozen"`` → giữ nguyên một mốc ``frozen_ms`` (không nhích) để dựng bối cảnh.

Bao gồm CẢ ngày tháng: ``offset_ms``/``frozen_ms`` là mốc epoch đầy đủ nên đổi giờ
lẫn đổi ngày đều được.

``now()`` trả về **mốc UTC thật** mà đồng hồ FE đang đại diện (tz-aware UTC). Khi
offset = 0 (chưa đặt gì) nó bằng đúng ``datetime.now(UTC)`` cũ, nên hành vi mặc
định không đổi. Frontend hiển thị bằng ``new Date(ts)`` nên tự đổi sang giờ VN —
đặt đồng hồ 22:30 (VN) thì lịch sử cũng hiện 22:30.

LƯU Ý: chỉ dùng cho thời gian NGHIỆP VỤ (log, thói quen, ký ức, automation).
KHÔNG dùng cho hạn JWT/bảo mật — auth phải bám giờ thực.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_MODE_LIVE = "live"
_MODE_FROZEN = "frozen"

# Múi giờ nghiệp vụ (giờ người dùng THẤY trên FE). ``now()`` trả về mốc UTC thật;
# học/đọc thói quen cần .hour/.date() theo giờ này để nhãn "22h", ranh giới ngày và
# so khớp "tới giờ" khớp đúng cái người dùng nhìn thấy — không lệch 7 tiếng như UTC.
LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Trạng thái đồng hồ, mirror localStorage "demoClock.v1" của FE
_offset_ms: int = 0
_mode: str = _MODE_LIVE
_frozen_ms: int = 0


def _real_now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _sim_epoch_ms() -> int:
    """Mốc epoch mô phỏng hiện tại (ms) — giống ``simNow()`` của FE."""
    if _mode == _MODE_FROZEN:
        return _frozen_ms
    return _real_now_ms() + _offset_ms


def now() -> datetime:
    """Mốc UTC thật mà đồng hồ mô phỏng đang đại diện (tz-aware UTC).

    Đây là hàm mà mọi nơi cần "bây giờ" (theo nghĩa nghiệp vụ) nên gọi, thay cho
    ``datetime.now(UTC)``.
    """
    return datetime.fromtimestamp(_sim_epoch_ms() / 1000, tz=UTC)


def local_now() -> datetime:
    """Giờ mô phỏng ở múi giờ địa phương (tz-aware, ``LOCAL_TZ``).

    Dùng khi cần ``.hour``/``.date()`` theo giờ NGƯỜI DÙNG THẤY — ví dụ so "tới giờ
    thói quen" hay đếm theo ngày — thay vì lấy phần UTC (lệch 7 tiếng).
    """
    return now().astimezone(LOCAL_TZ)


def to_local(value: datetime) -> datetime:
    """Đổi một mốc sang giờ địa phương. Naive coi như UTC (khớp cách lưu cột giờ)."""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(LOCAL_TZ)


def set_clock(*, offset_ms: int = 0, mode: str = _MODE_LIVE, frozen_ms: int = 0) -> dict:
    """Đặt trạng thái đồng hồ (nhận đúng 3 trường FE gửi lên). Trả về trạng thái mới."""
    global _offset_ms, _mode, _frozen_ms
    _offset_ms = int(offset_ms)
    _mode = _MODE_FROZEN if mode == _MODE_FROZEN else _MODE_LIVE
    _frozen_ms = int(frozen_ms)
    return get_state()


def reset() -> None:
    """Về giờ thật (offset 0, live) — dùng khi 'Về giờ thật' hoặc trong test."""
    global _offset_ms, _mode, _frozen_ms
    _offset_ms, _mode, _frozen_ms = 0, _MODE_LIVE, 0


def get_state() -> dict:
    """Trạng thái thô của đồng hồ, đúng shape FE dùng."""
    return {"offset_ms": _offset_ms, "mode": _mode, "frozen_ms": _frozen_ms}
