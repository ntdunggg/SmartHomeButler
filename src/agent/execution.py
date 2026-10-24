"""Cầu nối proposal → lệnh thực thi cho pipeline open-ended.

Pipeline sinh `CandidateAction` (capability + ActionType trừu tượng). Bus/simulator
lại nhận chuỗi hành động cụ thể (`set_temperature`, `set_brightness`, ...). Module
này DỊCH giữa hai tầng và chấm quyền từng bước — tái dùng `resolve_delta` /
`matches_expected_state` (no-op) và `permissions.resolve_access` (cùng cổng quyền
với nút bấm trực tiếp), không viết lại.

Tăng/giảm tương đối được biểu diễn bằng `{"delta": ±step}` rồi resolve sang giá trị
tuyệt đối theo trạng thái hiện tại của thiết bị (giống node plan cũ).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.agent.harness.state_resolution import matches_expected_state, resolve_delta
from src.core.permissions import resolve_access
from src.domain.action_registry import translate_semantic_action
from src.domain.models import User
from src.nlu.schemas import CandidateAction


def translate_action(action: CandidateAction) -> tuple[str, dict[str, Any]] | None:
    """Translate a semantic candidate through the shared action registry."""
    return translate_semantic_action(action.capability, action.action, action.params)


def build_executable_steps(
    actions: list[CandidateAction],
    *,
    devices_by_slug: dict[str, Any],
    session: Session,
    user: User,
) -> list[dict[str, Any]]:
    """Dựng danh sách bước thực thi (khớp PlanStepOut) từ CandidateAction.

    `devices_by_slug`: hàng Device trong DB (để đọc trạng thái hiện tại + tên/phòng).
    Mỗi bước đi qua CÙNG cổng quyền ``resolve_access`` như nút bấm trực tiếp — nên
    lệnh giọng nói của thành viên cũng tôn trọng quyền per-device (không còn bị chấm
    bằng ma trận rủi ro cũ). Bước được đánh dấu no-op nếu thiết bị đã đúng trạng thái.
    """
    steps: list[dict[str, Any]] = []
    for action in actions:
        device = devices_by_slug.get(action.device_id)
        if device is None:
            continue
        translated = translate_action(action)
        if translated is None:
            continue
        sim_action, raw_params = translated
        current = dict(getattr(device, "state", {}) or {})
        params = resolve_delta(sim_action, raw_params, current)
        decision = resolve_access(session, user=user, device=device)
        steps.append(
            {
                "device_slug": action.device_id,
                "device_name": device.name,
                "room": device.room,
                "action": sim_action,
                "capability": action.capability.value,
                "params": params,
                "reason_vi": action.reason_vi or "",
                "risk_level": str(device.risk_level),
                "allowed": decision.allowed,
                "requires_approval": decision.requires_approval,
                "notify_owner": decision.notify_owner,
                "deny_reason_vi": "" if decision.allowed else decision.reason_vi,
                "skipped": matches_expected_state(sim_action, params, current),
            }
        )
    return steps
