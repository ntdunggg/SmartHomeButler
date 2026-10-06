"""Energy Optimizer (spec §40-43) — chấm điểm & chọn plan.

Score (spec §40):
    Score = wComfort·Comfort + wPreference·PreferenceMatch − wEnergy·Energy − wConstraint·ConstraintPenalty

Bất biến (spec §40): SafetyViolation KHÔNG chỉ là penalty — plan phải INFEASIBLE và bị
loại hẳn khỏi lựa chọn. Trọng số theo power mode (spec §41): CRITICAL đẩy mạnh phạt năng
lượng. Ví dụ §43: cùng "increase illumination", ưu tiên mở rèm (~0 W) hơn bật đèn (14 W).
"""

from __future__ import annotations

from src.agent.config import get_energy_config
from src.agent.schemas import CandidateEnergyPlan, PowerLoad, PowerMode


def _mode_str(mode: PowerMode | str) -> str:
    return mode.value if isinstance(mode, PowerMode) else str(mode)


def apply_load_constraint(plan: CandidateEnergyPlan, power_load: PowerLoad) -> None:
    """Ràng buộc TẢI ĐIỆN cứng ở CRITICAL (spec §41): tải dự phóng vượt ngưỡng → plan INFEASIBLE
    (không chỉ trừ điểm — §40). Plan KHÔNG biết công suất trong CRITICAL cũng bị loại (bảo thủ,
    §42: không đoán unknown = an toàn). NORMAL/MODERATE giữ mềm (chỉ nặng trọng số năng lượng)."""
    if _mode_str(power_load.mode) != PowerMode.CRITICAL.value or plan.infeasible:
        return
    if plan.power_unknown:
        plan.infeasible = True
        plan.infeasible_reason = "unknown_power_in_critical"
        return
    # The house may already be above the threshold. A no-increase or reducing
    # action does not worsen that state and must remain available (for example,
    # dimming an on light or turning a device off).
    if plan.estimated_power_w <= 0:
        return
    projected = power_load.current_watts + plan.estimated_power_w
    if projected > power_load.critical_threshold_w:
        plan.infeasible = True
        plan.infeasible_reason = (
            f"projected_load_{int(projected)}w_exceeds_critical_{int(power_load.critical_threshold_w)}w"
        )


def score_plan(plan: CandidateEnergyPlan, *, power_mode: PowerMode | str = PowerMode.NORMAL) -> float:
    """Tính điểm một plan theo trọng số của power mode. Infeasible → -inf."""
    if plan.infeasible:
        return float("-inf")
    cfg = get_energy_config()
    mode = power_mode.value if isinstance(power_mode, PowerMode) else str(power_mode)
    weights = cfg["optimizer_weights"].get(mode, cfg["optimizer_weights"]["NORMAL"])
    normalizer = float(cfg.get("energy_normalizer_w", 2000)) or 1.0

    energy_term = plan.estimated_power_w / normalizer
    score = (
        weights["comfort"] * plan.comfort
        + weights["preference"] * plan.preference_match
        - weights["energy"] * energy_term
        - weights["constraint"] * plan.constraint_penalty
    )
    return round(score, 6)


def optimize(
    plans: list[CandidateEnergyPlan],
    *,
    power_mode: PowerMode | str = PowerMode.NORMAL,
    power_load: PowerLoad | None = None,
    min_comfort: float = 0.6,
) -> tuple[CandidateEnergyPlan | None, list[CandidateEnergyPlan]]:
    """Chấm điểm mọi plan, chọn plan điểm cao nhất trong số FEASIBLE (spec §40).

    Trả (selected, scored_plans). `power_load` (nếu có): áp ràng buộc tải cứng ở CRITICAL
    (§41) TRƯỚC khi chấm — plan vượt ngưỡng/không rõ công suất thành infeasible. `min_comfort`:
    plan feasible nhưng comfort quá thấp vẫn được chấm nhưng ưu tiên thấp (§68).
    """
    mode = power_load.mode if power_load is not None else power_mode
    scored: list[CandidateEnergyPlan] = []
    for plan in plans:
        if power_load is not None:
            apply_load_constraint(plan, power_load)
        s = score_plan(plan, power_mode=mode)
        plan.score = s if s != float("-inf") else -1e9
        scored.append(plan)

    feasible = [p for p in scored if not p.infeasible]
    # Ưu tiên plan đạt ngưỡng comfort; nếu không có, lấy plan feasible điểm cao nhất.
    adequate = [p for p in feasible if p.comfort >= min_comfort]
    pool = adequate or feasible
    selected = max(pool, key=lambda p: p.score) if pool else None
    scored.sort(key=lambda p: p.score, reverse=True)
    return selected, scored
