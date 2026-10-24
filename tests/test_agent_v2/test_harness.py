"""Layer 5 Harness (spec §47-53) — validate, authorize, policy, refresh, execute."""

from __future__ import annotations

from src.agent.harness.authorization import authorize
from src.agent.harness.executor import execute_plan
from src.agent.harness.gateway import InMemoryGateway
from src.agent.harness.policy import decide
from src.agent.harness.refresh import refresh_state, reground, revalidate
from src.agent.harness.validator import validate_plan
from src.agent.perception.context_builder import build_perception
from src.agent.schemas import ProposalAction
from src.domain.enums import Role


def _ctx():
    _, ctx, _ = build_perception("x", speaker_location="Phòng khách")
    return ctx


def test_validator_rejects_hallucinated_device():
    acts = [ProposalAction(device_id="thiet_bi_ma", capability="on_off", action="turn_on")]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert not validated
    assert any(e.code == "DEVICE_NOT_FOUND" for e in errors)


def test_validator_rejects_unsupported_capability():
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="temperature", action="set", target={"temperature": 24})]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert not validated
    assert any(e.code == "CAPABILITY_NOT_SUPPORTED" for e in errors)


def test_validator_rejects_out_of_range_target():
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="brightness", action="set", target={"brightness": 500})]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert not validated
    assert any(e.code == "TARGET_OUT_OF_RANGE" for e in errors)


def test_validator_rejects_action_name_as_brightness_value():
    acts = [
        ProposalAction(
            device_id="den_chum_phong_khach",
            capability="brightness",
            action="turn_off",
            target={"brightness": "turn_off"},
        )
    ]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert not validated
    assert any(e.code == "PARAMETER_TYPE_INVALID" for e in errors)


def test_validator_uses_registry_fan_speed_scale() -> None:
    acts = [
        ProposalAction(
            device_id="dieu_hoa_phong_khach",
            capability="fan_speed",
            action="set",
            target={"fan_speed": 4},
        )
    ]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert not validated
    assert any(e.code == "TARGET_OUT_OF_RANGE" for e in errors)


def test_validator_rejects_hallucinated_action_verb():
    """§47 'action supported': verb ngoài tập đóng ActionType bị loại (chống ảo giác tên hành động)."""
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="on_off", action="teleport")]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert not validated
    assert any(e.code == "ACTION_NOT_SUPPORTED" for e in errors)


def test_validator_rejects_verb_capability_mismatch():
    """§47 'action supported': verb cấu trúc phải khớp capability — unlock cần capability lock."""
    # Đèn có on_off nhưng KHÔNG có capability lock → unlock không hợp lệ trên đèn.
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="on_off", action="unlock")]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert not validated
    assert any(e.code == "ACTION_NOT_SUPPORTED" for e in errors)


def test_validator_accepts_structural_verb_with_matching_capability():
    """open hợp lệ trên thiết bị có capability position (rèm)."""
    acts = [ProposalAction(device_id="rem_phong_khach", capability="position", action="open", target={"position": 80})]
    validated, errors, _ = validate_plan(acts, _ctx())
    assert validated and not errors


def test_validator_rejects_invalid_room():
    """§47 'room valid': thiết bị mang phòng không có trong danh mục phòng của ngữ cảnh → ROOM_INVALID.

    Không hardcode tên phòng: đọc phòng thật của thiết bị rồi dựng ctx.rooms KHÔNG chứa nó."""
    from src.iot.registry import DEVICE_BY_SLUG

    spec = DEVICE_BY_SLUG["den_chum_phong_khach"]
    ctx = _ctx().model_copy(update={"rooms": [f"__khong_phai_{spec.room}__"]})
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="on_off", action="turn_on")]
    validated, errors, _ = validate_plan(acts, ctx)
    assert not validated
    assert any(e.code == "ROOM_INVALID" for e in errors)


def test_validator_room_check_skips_when_room_catalog_unknown():
    """ctx.rooms rỗng (chưa biết danh mục phòng) → guard bỏ qua kiểm tra room, không chặn nhầm."""
    ctx = _ctx().model_copy(update={"rooms": []})
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="on_off", action="turn_on")]
    validated, errors, _ = validate_plan(acts, ctx)
    assert validated and not any(e.code == "ROOM_INVALID" for e in errors)


def test_inferred_goal_drops_security_action():
    acts = [ProposalAction(device_id="khoa_cua_chinh", capability="lock", action="unlock")]
    validated, _, dropped = validate_plan(acts, _ctx(), is_inferred_goal=True)
    assert not validated and "khoa_cua_chinh" in dropped


def test_inferred_goal_keeps_locking_but_forces_confirmation():
    """Nếp sinh hoạt "ra ngoài" ĐƯỢC đề xuất khoá cửa — nhưng luôn phải qua xác nhận."""
    acts = [ProposalAction(device_id="khoa_cua_chinh", capability="lock", action="lock")]
    validated, _, dropped = validate_plan(acts, _ctx(), is_inferred_goal=True)
    assert "khoa_cua_chinh" not in dropped
    assert [a.entity_id for a in validated] == ["khoa_cua_chinh"]
    assert validated[0].requires_confirmation is True


def test_explicit_lock_command_is_not_forced_into_confirmation_by_this_rule():
    """Lệnh tường minh giữ nguyên đường cũ: cờ do authorization/role quyết định, không do đây."""
    acts = [ProposalAction(device_id="khoa_cua_chinh", capability="lock", action="lock")]
    validated, _, _ = validate_plan(acts, _ctx(), is_inferred_goal=False)
    assert validated and validated[0].requires_confirmation is False


def test_owner_cannot_shortcut_a_confirmation_the_validator_demanded():
    """Vai trò cao chỉ được THÊM quyền, không được gỡ yêu cầu duyệt tầng tất định đã đặt."""
    acts = [ProposalAction(device_id="khoa_cua_chinh", capability="lock", action="lock")]
    validated, _, _ = validate_plan(acts, _ctx(), is_inferred_goal=True)
    auth = authorize(validated, role=Role.OWNER)
    assert auth.decision == "WAITING_FOR_USER_APPROVAL"
    assert [a.entity_id for a in auth.approval_actions] == ["khoa_cua_chinh"]


def test_child_high_power_blocked_rejects():
    acts = [ProposalAction(device_id="dieu_hoa_phong_khach", capability="temperature", action="turn_on", target={"temperature": 24})]
    validated, errors, _ = validate_plan(acts, _ctx())
    auth = authorize(validated, role=Role.MEMBER)
    decision = decide(hard_errors=errors, authorization=auth, has_actions=bool(validated))
    assert decision.decision == "REJECT"
    assert decision.priority_hit == "P1_authorization"


def test_offline_device_reports_failed():
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="brightness", action="turn_on", target={"brightness": 70})]
    validated, _, _ = validate_plan(acts, _ctx())
    gw = InMemoryGateway(offline=frozenset({"den_chum_phong_khach"}))
    result = execute_plan(gw, validated, plan_id="p")
    assert result.status == "FAILED"
    assert result.actions[0].reason == "DEVICE_OFFLINE"


def test_reground_drops_noop_on_live_state():
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="brightness", action="turn_on", target={"brightness": 70})]
    validated, _, _ = validate_plan(acts, _ctx())
    gw = InMemoryGateway(live_states={"den_chum_phong_khach": {"power": "on", "brightness": 70}})
    live = refresh_state(gw, ["den_chum_phong_khach"])
    kept, noops = reground(validated, live)
    assert not kept and len(noops) == 1


def test_revalidate_rejects_out_of_range_after_reground():
    """Spec §50, QC-03: bước revalidate sau reground loại action ngoài dải (không để fail tận gateway)."""
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="brightness", action="set", target={"brightness": 50})]
    validated, _, _ = validate_plan(acts, _ctx())
    assert validated
    validated[0].desired_state["brightness"] = 250  # mô phỏng invalid trên state mới sau refresh
    kept, rejected = revalidate(validated)
    assert not kept and len(rejected) == 1


def test_validator_rejects_offline_device_from_live_context():
    """QC-05/§47: thiết bị OFFLINE trên live context bị validator loại TRƯỚC execute, không fail tận gateway."""
    _, ctx, _ = build_perception(
        "bật đèn phòng khách", speaker_location="Phòng khách",
        live_device_states={"den_chum_phong_khach": {"power": "off", "online": False}},
    )
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="on_off", action="turn_on")]
    validated, errors, _ = validate_plan(acts, ctx)
    assert not validated
    assert any(e.code == "DEVICE_OFFLINE" for e in errors)


def test_revalidate_keeps_in_range():
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="brightness", action="set", target={"brightness": 60})]
    validated, _, _ = validate_plan(acts, _ctx())
    kept, rejected = revalidate(validated)
    assert len(kept) == 1 and not rejected


def test_owner_normal_device_proceeds():
    acts = [ProposalAction(device_id="den_chum_phong_khach", capability="brightness", action="turn_on", target={"brightness": 70})]
    validated, errors, _ = validate_plan(acts, _ctx())
    auth = authorize(validated, role=Role.OWNER)
    decision = decide(hard_errors=errors, authorization=auth, has_actions=bool(validated))
    assert decision.decision == "PROCEED"
