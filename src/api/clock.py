"""Endpoint đồng hồ mô phỏng — cho FE đẩy trạng thái đồng hồ nội bộ lên backend.

FE gọi ``PUT /clock`` mỗi khi người dùng Đặt giờ / Đóng băng / Về giờ thật (đúng
lúc nó ghi localStorage), để backend và FE dùng chung một mốc thời gian. ``GET``
để đọc lại trạng thái + giờ mô phỏng hiện tại.

Ranh giới an toàn:
- Chỉ CHỦ HỘ được đặt (``PUT``); thành viên chỉ đọc (``GET``). Tránh trẻ nhỏ /
  thành viên dời giờ nghiệp vụ của cả nhà (#9).
- Toàn bộ endpoint TẮT ở production qua ``settings.clock_endpoint_enabled`` (#11).
- Đồng hồ là một mốc TOÀN CỤC cho cả tiến trình (xem ``core/clock.py``), không
  tách theo hộ (#10): chấp nhận được cho demo một hộ; đa hộ thì việc một hộ đổi
  giờ ảnh hưởng hộ khác là hạn chế đã biết — muốn tách theo hộ phải luồn
  ``household_id`` xuống tận ``_utcnow`` (dấu thời gian cột), là thay đổi lớn.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from src.api.deps import CurrentUser, OwnerUser
from src.config import get_settings
from src.core import clock
from src.models.schemas import ClockOut, ClockState

router = APIRouter(prefix="/clock", tags=["clock"])


def _guard_enabled() -> None:
    if not get_settings().clock_endpoint_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Đồng hồ mô phỏng đã tắt.")


def _to_out() -> ClockOut:
    state = clock.get_state()
    return ClockOut(
        offset_ms=state["offset_ms"],
        mode=state["mode"],
        frozen_ms=state["frozen_ms"],
        now=clock.now(),
        real_now=datetime.now(UTC),
    )


@router.get("", response_model=ClockOut)
async def get_clock(_user: CurrentUser) -> ClockOut:
    """Đọc trạng thái đồng hồ mô phỏng + giờ hiện tại (thành viên nào cũng xem được)."""
    _guard_enabled()
    return _to_out()


@router.put("", response_model=ClockOut)
async def set_clock(payload: ClockState, _owner: OwnerUser) -> ClockOut:
    """Đặt trạng thái đồng hồ mô phỏng — CHỈ CHỦ HỘ (FE gửi {offset_ms, mode, frozen_ms})."""
    _guard_enabled()
    clock.set_clock(offset_ms=payload.offset_ms, mode=payload.mode, frozen_ms=payload.frozen_ms)
    return _to_out()
