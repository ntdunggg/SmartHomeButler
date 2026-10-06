"""Grounding đọc TRẠNG THÁI SỐNG: "chói quá" mà chỉ một đèn đang bật thì chỉ đụng đèn đó.

Đèn đang TẮT không phải nguồn khó chịu → bị loại khỏi kế hoạch giảm sáng. Nhưng nếu KHÔNG có
đèn nào bật thì không có căn cứ để loại → giữ nguyên (không tự đoán). Cũng bỏ action no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.core.interfaces import DeviceSelector
from src.domain.enums import ActionType, Capability
from src.nlu.schemas import CandidateAction, CandidatePlan, DesiredOutcome, Device, RuntimeContext
from src.planning.relative_grounding import ground_candidate_plan

_NOW = datetime(2026, 8, 11, 23, 0, tzinfo=UTC)
_ROOM = "Phòng ngủ con"


def _ctx(*states: tuple[str, str, int]) -> RuntimeContext:
    devs = [
        Device(
            device_id=slug, name=slug, room=_ROOM, device_type="light",
            capabilities=[Capability.ON_OFF, Capability.BRIGHTNESS],
            state={"power": power, "brightness": br},
        )
        for slug, power, br in states
    ]
    return RuntimeContext(now=_NOW, devices=devs, rooms=[_ROOM])


def _goal():
    return type("G", (), {"desired_outcomes": [
        DesiredOutcome(selector=DeviceSelector(domain="light"), relative_change={"brightness": "decrease"}, perceived_state="too_bright")
    ]})()


def _decrease_plan(*slugs: str) -> CandidatePlan:
    return CandidatePlan(
        goal_summary="chói", utterance_type="ENVIRONMENT_REQUEST",
        actions=[CandidateAction(device_id=s, capability=Capability.BRIGHTNESS, action=ActionType.DECREASE, params={}) for s in slugs],
    )


def test_chi_giam_den_dang_bat_bo_den_dang_tat() -> None:
    ctx = _ctx(("den_ngu_con", "on", 80), ("den_ban_hoc", "off", 60))
    out = ground_candidate_plan(_decrease_plan("den_ngu_con", "den_ban_hoc"), _goal(), ctx)
    assert [a.device_id for a in out.actions] == ["den_ngu_con"]
    assert out.actions[0].action == ActionType.SET


def test_khong_den_nao_bat_thi_khong_loc() -> None:
    ctx = _ctx(("den_ngu_con", "off", 80), ("den_ban_hoc", "off", 60))
    out = ground_candidate_plan(_decrease_plan("den_ngu_con", "den_ban_hoc"), _goal(), ctx)
    # Không có bằng chứng để loại → giữ cả hai (grounding vẫn quy về SET).
    assert {a.device_id for a in out.actions} == {"den_ngu_con", "den_ban_hoc"}


def test_bo_action_no_op_khi_da_dung_muc() -> None:
    # Đèn đang bật ở mức tối thiểu 0 → "giảm" không đổi gì → bỏ.
    ctx = _ctx(("den_ngu_con", "on", 0))
    out = ground_candidate_plan(_decrease_plan("den_ngu_con"), _goal(), ctx)
    assert out.actions == []
