"""Xác nhận thói quen mới (HABIT_CONFIRM): ứng viên, emit, bỏ qua, xác nhận, và WS.

Luồng này trước đây chưa có test. Các case khoá đúng những điều người dùng dựa vào:
- Thói quen mới chỉ là ứng viên, chưa ghi DB cho tới khi người dùng bấm xác nhận.
- Đang chờ thì không hỏi lặp lại; bỏ qua thì không hỏi lại trong 24h, kể cả sau restart.
- Hoãn trên xác nhận thói quen được coi là bỏ qua, không bị gỡ im lặng.
- Mỗi xác nhận chỉ phát đúng một event WS.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from src.api import ws
from src.config import get_settings
from src.core import clock
from src.domain.enums import SuggestionKind
from src.domain.models import Habit, HabitConfirmRejection
from src.memory import habits
from src.services.automation import AutomationEngine, Suggestion, registry
from tests.helpers_habits import DEMO_DEVICE_SLUG, freeze_vn, ids, log_evidence, vn


@pytest.fixture(autouse=True)
def _isolated_automation(monkeypatch):
    # Không gọi dữ liệu thời tiết thật: tắt đường realtime để vòng quét chỉ chạy luật nội bộ.
    monkeypatch.setenv("ENVIRONMENT_REALTIME_ENABLED", "false")
    get_settings.cache_clear()
    registry.clear()
    yield
    registry.clear()
    clock.reset()
    get_settings.cache_clear()


def _teach_pattern(session) -> tuple[int, int, int]:
    """Bo bật máy lạnh lúc 22:30 giờ VN trong bốn ngày: đủ bằng chứng cho một thói quen mới."""
    hh, user, device = ids(session)
    for day in (1, 3, 5, 7):
        log_evidence(
            session,
            hh=hh,
            user=user,
            device=device,
            action="turn_on",
            when=vn(day),
        )
    # Commit trước khi vòng quét mở session riêng, nếu không SQLite báo database is locked.
    session.commit()
    freeze_vn(9)  # bây giờ = 22:30 VN ngày 9
    return hh, user, device


def _confirms(suggestions: list[Suggestion]) -> list[Suggestion]:
    return [s for s in suggestions if s.kind == SuggestionKind.HABIT_CONFIRM]


async def _pending_confirm_id(client, headers) -> str:
    listed = (await client.get("/api/v1/agent/suggestions", headers=headers)).json()
    confirms = [s for s in listed if s["kind"] == SuggestionKind.HABIT_CONFIRM]
    assert len(confirms) == 1, listed
    return confirms[0]["id"]


def test_new_habit_is_only_a_candidate_until_confirmed(seeded) -> None:
    hh, user, _ = _teach_pattern(seeded)

    candidates = habits.detect_new_habits(seeded, household_id=hh)

    assert len(candidates) == 1
    assert (candidates[0].user_id, candidates[0].hour) == (user, 22)
    saved = seeded.scalars(select(Habit).where(Habit.household_id == hh, Habit.hour == 22)).all()
    assert saved == [], "thói quen mới chưa được ghi trước khi người dùng xác nhận"


async def test_engine_asks_once_while_the_suggestion_is_pending(seeded) -> None:
    _teach_pattern(seeded)

    first = await AutomationEngine().run_once()
    second = await AutomationEngine().run_once()

    assert len(_confirms(first)) == 1
    assert _confirms(second) == [], "đang chờ thì không phát đề xuất trùng"


async def test_accepting_saves_the_habit(client, login, seeded) -> None:
    hh, user, _ = _teach_pattern(seeded)
    await AutomationEngine().run_once()
    headers = await login("bo")
    suggestion_id = await _pending_confirm_id(client, headers)

    response = await client.post(
        f"/api/v1/agent/suggestions/{suggestion_id}",
        headers=headers,
        json={"accepted": True},
    )

    assert response.status_code == 200, response.text
    seeded.expire_all()
    habit = seeded.scalar(
        select(Habit).where(
            Habit.household_id == hh,
            Habit.user_id == user,
            Habit.device_slug == DEMO_DEVICE_SLUG,
            Habit.hour == 22,
        )
    )
    assert habit is not None and habit.source == "learned"


async def test_rejection_survives_restart_and_expires_after_24h(client, login, seeded) -> None:
    _teach_pattern(seeded)
    await AutomationEngine().run_once()
    headers = await login("bo")
    suggestion_id = await _pending_confirm_id(client, headers)

    response = await client.post(
        f"/api/v1/agent/suggestions/{suggestion_id}",
        headers=headers,
        json={"accepted": False},
    )
    assert response.status_code == 200, response.text
    seeded.expire_all()
    assert seeded.scalar(select(func.count()).select_from(HabitConfirmRejection)) == 1

    registry.clear()  # mô phỏng server restart: bộ nhớ trống, chỉ còn DB
    assert _confirms(await AutomationEngine().run_once()) == [], "vẫn trong 24h, không hỏi lại"

    freeze_vn(10, hour=23)  # hơn 24h sau lần bỏ qua
    assert len(_confirms(await AutomationEngine().run_once())) == 1


async def test_snooze_on_a_confirm_counts_as_rejection(client, login, seeded) -> None:
    """Hoãn không áp dụng cho xác nhận thói quen: không được gỡ đề xuất mà không ghi gì."""
    _teach_pattern(seeded)
    await AutomationEngine().run_once()
    headers = await login("bo")
    suggestion_id = await _pending_confirm_id(client, headers)

    response = await client.post(
        f"/api/v1/agent/suggestions/{suggestion_id}",
        headers=headers,
        json={"accepted": False, "snooze_minutes": 30},
    )

    assert response.status_code == 200, response.text
    seeded.expire_all()
    assert seeded.scalar(select(func.count()).select_from(HabitConfirmRejection)) == 1
    registry.clear()
    assert _confirms(await AutomationEngine().run_once()) == []


async def test_confirm_is_broadcast_as_one_suggestion_event(monkeypatch, seeded) -> None:
    _teach_pattern(seeded)
    (confirm,) = _confirms(await AutomationEngine().run_once())
    broadcast = AsyncMock()
    monkeypatch.setattr(ws.manager, "broadcast", broadcast)

    await ws.on_suggestion(confirm)

    assert broadcast.await_count == 1
    payload = broadcast.await_args.args[1]
    assert payload["type"] == "suggestion"
    assert payload["suggestion"]["kind"] == SuggestionKind.HABIT_CONFIRM
