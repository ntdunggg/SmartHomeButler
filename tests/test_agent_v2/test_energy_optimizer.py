"""Energy Optimizer + Aggregator (spec §39-43) — cross-device + INFEASIBLE."""

from __future__ import annotations

from src.agent.planning.aggregator import aggregate
from src.agent.planning.energy_optimizer import optimize, score_plan
from src.agent.schemas import CandidateEnergyPlan, DeviceProposal, PowerLoad, PowerMode, ProposalAction


def _critical(current_watts: float) -> PowerLoad:
    return PowerLoad(current_watts=current_watts, mode=PowerMode.CRITICAL, critical_threshold_w=5000)


def _light_proposal() -> DeviceProposal:
    return DeviceProposal(
        agent="lighting",
        actions=[ProposalAction(device_id="den_chum_phong_khach", capability="brightness", action="turn_on", target={"brightness": 70})],
        estimated_comfort=0.9,
        estimated_power_w=12.0,
    )


def _curtain_proposal() -> DeviceProposal:
    return DeviceProposal(
        agent="shutter",
        actions=[ProposalAction(device_id="rem_phong_khach", capability="position", action="open", target={"position": 80})],
        estimated_comfort=0.9,
        estimated_power_w=0.0,
    )


def test_cross_device_prefers_zero_energy_when_comfort_ties():
    """Spec §43: cùng illumination, comfort ngang → ưu tiên mở rèm (0 W) hơn bật đèn."""
    plans = aggregate([[_light_proposal(), _curtain_proposal()]])
    selected, _ = optimize(plans, power_mode=PowerMode.NORMAL)
    assert selected is not None
    assert "shutter" in selected.source_agents


def test_critical_load_over_threshold_is_infeasible():
    """QC-08/§41: ở CRITICAL, tải dự phóng vượt ngưỡng → plan INFEASIBLE (dù là plan DUY NHẤT)."""
    heavy = CandidateEnergyPlan(plan_id="heavy", comfort=1.0, preference_match=1.0, estimated_power_w=3000)
    selected, scored = optimize([heavy], power_load=_critical(current_watts=3000))  # 3000+3000 > 5000
    assert selected is None
    assert scored[0].infeasible and "exceeds_critical" in scored[0].infeasible_reason


def test_critical_load_under_threshold_still_selectable():
    light = CandidateEnergyPlan(plan_id="light", comfort=0.9, preference_match=0.9, estimated_power_w=500)
    selected, _ = optimize([light], power_load=_critical(current_watts=1000))  # 1500 < 5000
    assert selected is not None and not selected.infeasible


def test_unknown_power_in_critical_is_infeasible():
    """QC-08/§42: công suất KHÔNG rõ trong CRITICAL bị loại (bảo thủ), không coi như 0 W miễn phí."""
    unknown = CandidateEnergyPlan(plan_id="u", comfort=1.0, preference_match=1.0, estimated_power_w=0.0, power_unknown=True)
    selected, scored = optimize([unknown], power_load=_critical(current_watts=100))
    assert selected is None and scored[0].infeasible_reason == "unknown_power_in_critical"


def test_aggregator_marks_power_unknown():
    """§42: proposal không biết công suất (None) → plan.power_unknown=True, không âm thầm thành 0."""
    prop = DeviceProposal(
        agent="ac",
        actions=[ProposalAction(device_id="dieu_hoa_phong_khach", capability="temperature", action="set", target={"temperature": 24})],
        estimated_comfort=0.9,
        estimated_power_w=None,
    )
    plans = aggregate([[prop]])
    assert plans and plans[0].power_unknown is True


def test_safety_violation_is_infeasible_not_penalty():
    """Spec §40: SafetyViolation → INFEASIBLE, không được chọn dù điểm khác cao."""
    unsafe = CandidateEnergyPlan(plan_id="unsafe", comfort=1.0, preference_match=1.0, infeasible=True)
    safe = CandidateEnergyPlan(plan_id="safe", comfort=0.7, preference_match=0.7, estimated_power_w=10)
    selected, scored = optimize([unsafe, safe], power_mode=PowerMode.NORMAL)
    assert selected is safe
    assert score_plan(unsafe) == float("-inf")


def test_contradictory_aggregated_actions_are_infeasible():
    """A model proposal cannot silently overwrite turn_off with turn_on for one device."""
    turn_off = DeviceProposal(
        agent="media",
        actions=[ProposalAction(device_id="tv_phong_bo_me", capability="on_off", action="turn_off")],
        estimated_comfort=0.9,
        estimated_power_w=0,
    )
    turn_on = DeviceProposal(
        agent="media",
        actions=[ProposalAction(device_id="tv_phong_bo_me", capability="on_off", action="turn_on")],
        estimated_comfort=0.9,
        estimated_power_w=100,
    )

    plans = aggregate([[turn_off], [turn_on]])
    selected, scored = optimize(plans)

    assert selected is None
    assert scored[0].infeasible is True
    assert scored[0].infeasible_reason == "contradictory_actions"


def test_critical_mode_weights_energy_more():
    """Spec §41: CRITICAL đẩy mạnh phạt năng lượng so với NORMAL."""
    heavy = CandidateEnergyPlan(plan_id="heavy", comfort=0.9, preference_match=0.5, estimated_power_w=2000)
    s_normal = score_plan(heavy, power_mode=PowerMode.NORMAL)
    s_critical = score_plan(heavy, power_mode=PowerMode.CRITICAL)
    assert s_critical < s_normal
