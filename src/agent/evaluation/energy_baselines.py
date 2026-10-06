"""Energy baselines (spec §68) — so Energy Optimizer với các chiến lược nền.

Baselines (spec §68):
    - no optimization           → chọn plan đầu (không cân nhắc gì)
    - rule-based device-local   → luôn dùng thiết bị trực tiếp (đèn cho sáng), bỏ qua
                                   phương án chéo (mở rèm)
    - comfort-only planning     → chọn comfort cao nhất, phớt lờ năng lượng
    - fixed-scene planning       → luôn chọn plan gộp nhiều thiết bị nhất

Đo (spec §68): energy consumption, comfort, constraint violations, task success. Bất
biến (spec §68): KHÔNG chấp nhận energy saving nếu comfort/safety bị phá nghiêm trọng —
mọi baseline & optimizer đều LOẠI plan infeasible.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.planning.energy_optimizer import optimize
from src.agent.schemas import CandidateEnergyPlan, PowerMode

# Ngưỡng comfort tối thiểu để coi một plan là "task success" (spec §68).
_MIN_COMFORT = 0.6


@dataclass(frozen=True, slots=True)
class StrategyResult:
    strategy: str
    plan_id: str | None
    energy_w: float
    comfort: float
    constraint_penalty: float
    task_success: bool


def _feasible(plans: list[CandidateEnergyPlan]) -> list[CandidateEnergyPlan]:
    return [p for p in plans if not p.infeasible]


def _wrap(strategy: str, plan: CandidateEnergyPlan | None) -> StrategyResult:
    if plan is None:
        return StrategyResult(strategy, None, 0.0, 0.0, 0.0, False)
    return StrategyResult(
        strategy=strategy,
        plan_id=plan.plan_id,
        energy_w=plan.estimated_power_w,
        comfort=plan.comfort,
        constraint_penalty=plan.constraint_penalty,
        task_success=plan.comfort >= _MIN_COMFORT and plan.constraint_penalty == 0,
    )


def baseline_no_optimization(plans: list[CandidateEnergyPlan]) -> CandidateEnergyPlan | None:
    feas = _feasible(plans)
    return feas[0] if feas else None


def baseline_comfort_only(plans: list[CandidateEnergyPlan]) -> CandidateEnergyPlan | None:
    feas = _feasible(plans)
    return max(feas, key=lambda p: p.comfort) if feas else None


def baseline_rule_based(plans: list[CandidateEnergyPlan]) -> CandidateEnergyPlan | None:
    """Device-local: ưu tiên plan dùng thiết bị chủ động (đèn/AC/loa), bỏ qua rèm/cửa."""
    feas = _feasible(plans)
    if not feas:
        return None
    active = {"lighting", "ac", "media"}
    local = [p for p in feas if set(p.source_agents) & active]
    return (local or feas)[0]


def baseline_fixed_scene(plans: list[CandidateEnergyPlan]) -> CandidateEnergyPlan | None:
    """Fixed-scene: luôn chọn plan có NHIỀU hành động nhất (bật hết như một scene cố định)."""
    feas = _feasible(plans)
    return max(feas, key=lambda p: len(p.actions)) if feas else None


def compare_strategies(
    plans: list[CandidateEnergyPlan], *, power_mode: PowerMode | str = PowerMode.NORMAL
) -> dict[str, StrategyResult]:
    """So Energy Optimizer với 4 baseline trên cùng tập candidate plans (spec §68)."""
    selected, _ = optimize(plans, power_mode=power_mode, min_comfort=_MIN_COMFORT)
    return {
        "optimizer": _wrap("optimizer", selected),
        "no_optimization": _wrap("no_optimization", baseline_no_optimization(plans)),
        "comfort_only": _wrap("comfort_only", baseline_comfort_only(plans)),
        "rule_based": _wrap("rule_based", baseline_rule_based(plans)),
        "fixed_scene": _wrap("fixed_scene", baseline_fixed_scene(plans)),
    }


def energy_savings_vs(comparison: dict[str, StrategyResult], baseline: str) -> float:
    """% năng lượng optimizer tiết kiệm so với một baseline (spec §68 savings vs baseline)."""
    opt = comparison["optimizer"].energy_w
    base = comparison[baseline].energy_w
    if base <= 0:
        return 0.0
    return round(100.0 * (base - opt) / base, 1)
