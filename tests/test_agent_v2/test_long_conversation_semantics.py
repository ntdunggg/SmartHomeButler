"""Regression coverage for long multi-turn semantic state.

These cases exercise structural behavior (room carry, weak additive direction,
constraint overlays, and session-local conditions) rather than evaluation IDs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.pipeline import PipelineDeps
from src.domain.enums import Capability
from src.nlu.direction import direction_of
from src.services.pipeline_bridge import reason

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


def _turn(deps: PipelineDeps, conversation_id: str, message: str):
    return reason(
        message=message,
        conversation_id=conversation_id,
        user_id="regression-user",
        role="owner",
        now=NOW,
        deps=deps,
    )


def _actions(result):
    return list(result.candidate_plan.actions) if result.candidate_plan else []


def test_strong_direction_beats_weak_additive_cue() -> None:
    assert direction_of("giảm thêm một nấc")[0] == "decrease"
    assert direction_of("hạ thêm 2 độ")[0] == "decrease"
    assert direction_of("đóng thêm một chút")[0] == "decrease"
    assert direction_of("làm mát thêm")[0] == "decrease"
    assert direction_of("tăng thêm một nấc")[0] == "increase"
    assert direction_of("mở thêm một chút")[0] == "increase"


def test_numeric_delta_uses_prior_confirmed_setpoint() -> None:
    deps = PipelineDeps()
    cid = "numeric-delta-from-ledger"

    assert _turn(deps, cid, "Bật TV phòng khách.").outcome == "candidate_plan"
    assert _turn(deps, cid, "Âm lượng 25 thôi.").outcome == "candidate_plan"

    result = _turn(deps, cid, "Giảm thêm 5.")
    actions = _actions(result)
    assert result.outcome == "candidate_plan"
    assert len(actions) == 1
    assert actions[0].device_id == "tv_phong_khach"
    assert actions[0].capability == Capability.VOLUME
    assert actions[0].action.value == "set"
    assert actions[0].params == {"percent": 20}


def test_group_continuation_keeps_preserved_light_unchanged() -> None:
    deps = PipelineDeps()
    cid = "group-continuation-preservation"

    assert _turn(deps, cid, "Cho phòng ngủ bố mẹ tối hơn.").outcome == "candidate_plan"
    assert _turn(deps, cid, "Đèn ngủ phải giữ nguyên.").outcome == "answer"

    result = _turn(deps, cid, "Giảm thêm chút.")
    actions = _actions(result)
    assert result.outcome == "candidate_plan"
    assert {action.device_id for action in actions} == {"den_ban_lam_viec"}
    assert {action.capability for action in actions} == {Capability.BRIGHTNESS}
    assert {action.action.value for action in actions} <= {"set", "decrease"}
    assert "avoid:den_ngu_bo_me" in result.explicit_constraints
    no_change = deps.ledger_store.load(cid).no_change
    assert no_change and no_change[-1].device_ids == ["den_ngu_bo_me"]


def test_room_carried_continuation_rehydrates_room_inventory() -> None:
    deps = PipelineDeps()
    cid = "room-inventory-carry"

    assert _turn(deps, cid, "Cho phòng ngủ bố mẹ mát hơn.").outcome == "candidate_plan"
    assert _turn(deps, cid, "Nhưng giữ máy lọc không khí bật.").outcome == "answer"

    result = _turn(deps, cid, "Thêm một chút nữa.")
    actions = _actions(result)
    assert result.outcome == "candidate_plan"
    assert {action.device_id for action in actions} == {"dieu_hoa_phong_bo_me"}
    assert {action.capability for action in actions} == {Capability.TEMPERATURE}
    assert {action.action.value for action in actions} <= {"set", "decrease", "turn_on"}
    assert "keep_on:may_loc_phong_bo_me" in result.explicit_constraints


def test_directional_followup_keeps_main_goal_not_constraint_referent() -> None:
    deps = PipelineDeps()
    cid = "constraint-is-not-main-goal"

    assert _turn(deps, cid, "Cho phòng khách bớt chói.").outcome == "candidate_plan"
    assert _turn(deps, cid, "TV vẫn phải xem rõ.").outcome == "answer"

    result = _turn(deps, cid, "Giảm thêm một chút.")
    actions = _actions(result)
    assert result.outcome == "candidate_plan"
    assert actions
    assert all(action.device_id != "tv_phong_khach" for action in actions)
    assert all(action.capability == Capability.BRIGHTNESS for action in actions)
    assert "avoid:tv_phong_khach" in result.explicit_constraints


def test_closing_followup_respects_numeric_floor() -> None:
    deps = PipelineDeps()
    cid = "curtain-bound-followup"

    assert _turn(deps, cid, "Đóng rèm phòng ngủ bố mẹ còn 80%.").outcome == "candidate_plan"
    _turn(deps, cid, "Không đóng thấp hơn 60%.")

    result = _turn(deps, cid, "Đóng thêm một chút.")
    actions = _actions(result)
    assert result.outcome == "candidate_plan"
    assert {action.device_id for action in actions} == {"rem_phong_bo_me"}
    assert {action.capability for action in actions} == {Capability.POSITION}
    assert all(float(action.params["percent"]) >= 60 for action in actions)


def test_session_condition_waits_then_activates_prior_target() -> None:
    deps = PipelineDeps()
    cid = "conditional-continuation"

    assert _turn(deps, cid, "Bật máy lọc không khí phòng khách mức 2.").outcome == "candidate_plan"

    pending = _turn(deps, cid, "Nếu vẫn nóng thì tăng lên.")
    assert pending.outcome == "answer"
    assert pending.candidate_plan is None

    result = _turn(deps, cid, "Vẫn nóng.")
    actions = _actions(result)
    assert result.outcome == "candidate_plan"
    assert {action.device_id for action in actions} == {"may_loc_phong_khach"}
    assert {action.capability for action in actions} == {Capability.FAN_SPEED}
    assert {action.action.value for action in actions} == {"increase"}
    assert all(action.device_id != "dieu_hoa_phong_khach" for action in actions)


def test_cooling_continuation_preserves_side_device_constraints() -> None:
    deps = PipelineDeps()
    cid = "cooling-with-preserved-side-devices"

    assert _turn(deps, cid, "Phòng ngủ bố mẹ nóng.").outcome == "candidate_plan"
    assert _turn(deps, cid, "Đừng mở cửa sổ.").outcome == "answer"
    assert _turn(deps, cid, "Và đừng tắt máy lọc không khí.").outcome == "answer"

    result = _turn(deps, cid, "Làm mát thêm.")
    actions = _actions(result)
    assert result.outcome == "candidate_plan"
    assert {action.device_id for action in actions} == {"dieu_hoa_phong_bo_me"}
    assert {action.capability for action in actions} == {Capability.TEMPERATURE}
    assert {action.action.value for action in actions} <= {"set", "decrease"}
    assert "keep_off:cua_so_phong_bo_me" in result.explicit_constraints
    assert "keep_on:may_loc_phong_bo_me" in result.explicit_constraints
