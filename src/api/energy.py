"""Số điện đã dùng (kWh) ước tính cho toàn nhà.

Chỉ đọc, CHỈ CHỦ HỘ: tính động từ lịch sử bật/tắt (``ActionLog``) mỗi lần gọi,
không lưu trữ thêm, bền qua restart/redeploy. Xem ``src/services/power_usage.py``.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, status

from src.api.deps import DbSession, OwnerUser
from src.core import clock
from src.models.schemas import EnergySeriesOut, EnergyUsageOut
from src.services.power_usage import (
    DEFAULT_DAY_SPAN,
    accumulated_energy_kwh,
    energy_timeseries,
)

router = APIRouter(prefix="/energy", tags=["energy"])


@router.get("/usage", response_model=EnergyUsageOut)
async def energy_usage(owner: OwnerUser, session: DbSession) -> EnergyUsageOut:
    """Số điện ước tính hôm nay và tháng này của hộ (chỉ chủ hộ)."""
    return EnergyUsageOut(**accumulated_energy_kwh(session, household_id=owner.household_id))


@router.get("/series", response_model=EnergySeriesOut)
async def energy_series(
    owner: OwnerUser,
    session: DbSession,
    granularity: str = Query("day", pattern="^(day|month)$"),
    start: date | None = Query(None, description="Ngày đầu (YYYY-MM-DD), giờ VN. Mặc định 30 ngày trước."),
    end: date | None = Query(None, description="Ngày cuối (YYYY-MM-DD), giờ VN. Mặc định hôm nay."),
) -> EnergySeriesOut:
    """Chuỗi số điện toàn nhà theo ngày/tháng để vẽ đồ thị (chỉ chủ hộ).

    Thiếu ``start``/``end`` → mặc định ``DEFAULT_DAY_SPAN`` ngày gần nhất tính tới
    hôm nay (giờ VN). Khoảng quá dài (>366 ngày / >24 tháng) trả 422.
    """
    today = clock.local_now().date()
    if end is None:
        end = today
    if start is None:
        start = end - timedelta(days=DEFAULT_DAY_SPAN - 1)
    try:
        data = energy_timeseries(
            session, household_id=owner.household_id, granularity=granularity, start=start, end=end
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None
    return EnergySeriesOut(**data)
