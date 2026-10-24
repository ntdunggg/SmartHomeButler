"""Lớp dịch vụ nối bộ nhớ với vòng đời một lượt chat.

Route giữ DB session, pipeline thì không — nên việc nạp bằng chứng và ghi lại kết quả nằm
ở đây, cùng chỗ với `context_service`. Pipeline chỉ nhận `MemoryItem` thuần.

Vòng học: mỗi lượt được ghi lại ở trạng thái `proposed`; khi người dùng đồng ý / từ chối /
sửa, lượt đó được cập nhật và bằng chứng được quy tụ lại. Chỉ tình huống LẶP LẠI đủ nhiều
ngày mới thành hồ sơ dài hạn — một lần không đủ.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from src.core import clock
from src.memory import store
from src.memory.types import MemoryItem

logger = logging.getLogger("services.memory")

# Phản hồi hợp lệ của người dùng cho một lượt đã đề xuất.
FEEDBACK_OUTCOMES = frozenset({"accepted", "rejected", "corrected", "executed"})


def load_for_turn(
    session: Session, *, household_id: int, user_id: int | None = None, now: datetime | None = None
) -> list[MemoryItem]:
    """Nạp ký ức ứng viên cho một lượt. Lỗi ở đây KHÔNG được làm hỏng lượt chat.

    Bộ nhớ là thứ làm agent hiểu tốt hơn, không phải điều kiện để nó hoạt động: mất bộ nhớ
    thì tệ hơn một chút, còn ngã cả pipeline thì hỏng hẳn."""
    try:
        return store.load_candidates(session, household_id=household_id, user_id=user_id, now=now)
    except Exception:  # noqa: BLE001 — chủ ý: bộ nhớ hỏng không được kéo sập lượt chat
        logger.exception("Không nạp được ký ức cho hộ %s — chạy tiếp không có bộ nhớ", household_id)
        return []


def load_routines_for_turn(
    session: Session, *, household_id: int, user_id: int | None = None, now: datetime | None = None
) -> list[Any]:
    """Nạp thói quen đã học để pipeline có thể TÁI DÙNG. Fail-soft như `load_for_turn`:
    không có routine thì agent vẫn author mục tiêu từ đầu như cũ, chỉ mất phần tối ưu."""
    try:
        return store.load_routines(session, household_id=household_id, user_id=user_id, now=now)
    except Exception:  # noqa: BLE001 — routine hỏng không được kéo sập lượt chat
        logger.exception("Không nạp được routine cho hộ %s — chạy tiếp không tái dùng", household_id)
        return []


def record_turn(
    session: Session,
    *,
    household_id: int,
    user_id: int | None,
    utterance: str,
    response: Any,
    room: str | None = None,
    now: datetime | None = None,
) -> int | None:
    """Ghi lượt vừa xử lý vào episodic memory, trả về id để client phản hồi sau.

    Chỉ ghi lượt CÓ mục tiêu hiểu được. Lượt hỏi lại hoặc lỗi model không phải bằng chứng
    về sở thích của ai — ghi vào chỉ làm nhiễu việc quy tụ."""
    goal = getattr(response, "semantic_goal", None)
    plan = getattr(response, "candidate_plan", None)
    if goal is None:
        return None

    now = now or clock.now()
    actions = [
        {
            "device_id": str(a.device_id),
            "capability": a.capability.value if hasattr(a.capability, "value") else str(a.capability),
            "action": a.action.value if hasattr(a.action, "value") else str(a.action),
        }
        for a in (plan.actions if plan else [])
    ]
    devices = sorted({a["device_id"] for a in actions})
    signals: dict[str, Any] = {"devices": devices}
    # Lưu CÁCH HIỂU (mục tiêu mức capability) để consolidate quy thành routine TÁI DÙNG được.
    # Chỉ mục tiêu open-ended có desired_outcomes — lệnh tường minh đã tất định, không cần học.
    raw_outcomes = list(getattr(goal, "desired_outcomes", []) or [])
    if raw_outcomes:
        serialized_outcomes = [
            d.model_dump(mode="json") if hasattr(d, "model_dump") else (d if isinstance(d, dict | list | str | int | float | bool) else str(d))
            for d in raw_outcomes
        ]
        signals["goal"] = {
            "goal_description": getattr(goal, "goal_description", "") or getattr(goal, "primary_intent", ""),
            "desired_outcomes": serialized_outcomes,
            "utterance_type": str(getattr(goal, "utterance_type", "")),
        }
    try:
        goal_desc = (
            getattr(plan, "intent", None)
            or getattr(plan, "goal_summary", None)
            or getattr(goal, "goal_description", None)
            or getattr(goal, "primary_intent", "")
            or utterance
        )
        episode = store.record_episode(
            session,
            household_id=household_id,
            user_id=user_id,
            utterance=utterance,
            goal_description=goal_desc,
            utterance_type=goal.utterance_type,
            # Nhãn tình huống lấy từ intent do tầng ngữ nghĩa tự đặt. Nếu cách diễn đạt
            # đổi thì nhãn đổi và tình huống KHÔNG quy tụ được — đó là hành vi mong muốn:
            # thà không học còn hơn học nhầm một thói quen không có thật.
            situation_label=getattr(goal, "primary_intent", "") or getattr(goal, "intent", ""),
            room=room,
            now=now,
            proposed_actions=actions,
            signals=signals,
            outcome="proposed",
        )
        session.flush()
        return episode.id
    except Exception:  # noqa: BLE001 — ghi nhớ hỏng không được làm hỏng câu trả lời
        logger.exception("Không ghi được episodic memory cho hộ %s", household_id)
        return None


def apply_feedback(
    session: Session,
    *,
    household_id: int,
    episode_id: int,
    outcome: str,
    note: str = "",
    now: datetime | None = None,
) -> bool:
    """Cập nhật phản ứng của người dùng rồi quy tụ lại hồ sơ dài hạn.

    Trả False nếu `outcome` không hợp lệ hoặc lượt không thuộc hộ này."""
    if outcome not in FEEDBACK_OUTCOMES:
        return False

    from src.domain.models import EpisodicMemory

    episode = session.get(EpisodicMemory, episode_id)
    if episode is None or episode.household_id != household_id:
        return False

    store.update_outcome(session, episode_id, outcome=outcome, correction_note=note)
    # Quy tụ ngay sau khi bằng chứng đổi — rẻ (một truy vấn có giới hạn thời gian) và giữ
    # hồ sơ luôn khớp với thứ vừa quan sát được.
    store.consolidate(session, household_id=household_id, now=now)
    session.commit()
    return True
