"""Plan Aggregator (spec §39) — merge proposals → candidate plans.

Trách nhiệm (spec §39): merge proposals, detect duplicates, detect conflicts, detect
cross-device dependencies, construct candidate plans.

Mỗi subgoal có thể có NHIỀU proposal thay thế (vd illumination: đèn vs rèm — spec §43).
Aggregator sinh các plan ứng viên bằng tích Descartes CÓ CHẶN giữa các lựa chọn, để
Energy Optimizer (spec §40) chấm điểm và chọn phương án rẻ nhất mà vẫn đủ comfort.
"""

from __future__ import annotations

from itertools import product

from src.agent.schemas import CandidateEnergyPlan, DeviceProposal, ProposalAction

# Chặn số plan ứng viên để không bùng nổ tổ hợp.
_MAX_PLANS = 8


def _merge_actions(proposals: list[DeviceProposal]) -> tuple[list[ProposalAction], list[str], bool]:
    """Gộp action của các proposal, khử trùng lặp, phát hiện xung đột trên cùng thiết bị.

    Trả (actions, source_agents, has_conflict). Trùng lặp (cùng device+capability+action)
    bị khử. Xung đột (cùng device, action mâu thuẫn bật↔tắt) → has_conflict=True.
    """
    seen: dict[tuple[str, str], ProposalAction] = {}
    agents: list[str] = []
    conflict = False
    _off = {"turn_off", "close"}
    _on = {"turn_on", "open", "set", "increase", "decrease"}
    device_dir: dict[str, str] = {}

    for prop in proposals:
        if prop.agent not in agents:
            agents.append(prop.agent)
        for a in prop.actions:
            key = (a.device_id, a.capability)
            if key in seen and seen[key].action == a.action and seen[key].target == a.target:
                continue  # duplicate
            # Xung đột hướng trên cùng thiết bị.
            direction = "on" if a.action in _on else ("off" if a.action in _off else "")
            prev = device_dir.get(a.device_id)
            if prev and direction and prev != direction:
                conflict = True
            if direction:
                device_dir[a.device_id] = direction
            seen[key] = a

    return list(seen.values()), agents, conflict


def aggregate(subgoal_proposals: list[list[DeviceProposal]], *, max_plans: int = _MAX_PLANS) -> list[CandidateEnergyPlan]:
    """Dựng candidate plans từ proposals theo từng subgoal (spec §39).

    `subgoal_proposals[i]` = các proposal THAY THẾ cho subgoal i. Subgoal không có
    proposal nào (specialist bó tay) bị bỏ qua khỏi tổ hợp.
    """
    option_lists = [opts for opts in subgoal_proposals if opts]
    if not option_lists:
        return []

    # Tích Descartes có chặn: mỗi plan chọn đúng một proposal cho mỗi subgoal.
    combos = list(product(*option_lists))
    if len(combos) > max_plans:
        combos = combos[:max_plans]

    plans: list[CandidateEnergyPlan] = []
    for idx, combo in enumerate(combos):
        proposals = list(combo)
        actions, agents, conflict = _merge_actions(proposals)
        comforts = [p.estimated_comfort for p in proposals if p.actions]
        comfort = min(comforts) if comforts else 0.0
        preference_match = (sum(p.estimated_comfort for p in proposals) / len(proposals)) if proposals else 0.0
        # Power: tổng phần ĐÃ biết; nếu có proposal unknown (§42) thì đánh dấu power_unknown
        # để optimizer xử lý bảo thủ — KHÔNG lặng lẽ coi unknown = 0 W (tối ưu như miễn phí).
        acting = [p for p in proposals if p.actions]
        power = sum((p.estimated_power_w or 0.0) for p in acting)
        power_unknown = any(p.estimated_power_w is None for p in acting)
        safety_violation = any(p.safety_violation for p in proposals)
        # A plan that asks the same device to end both on and off is not a
        # lower-quality option; it is impossible. Keeping it selectable allowed
        # the later action to overwrite the earlier one before validation.
        infeasible = safety_violation or conflict
        infeasible_reason = (
            "safety_violation" if safety_violation else ("contradictory_actions" if conflict else "")
        )
        plans.append(
            CandidateEnergyPlan(
                plan_id=f"plan_{idx+1}",
                actions=actions,
                source_agents=agents,
                comfort=comfort,
                preference_match=preference_match,
                estimated_power_w=power,
                power_unknown=power_unknown,
                constraint_penalty=1.0 if conflict else 0.0,
                infeasible=infeasible,
                infeasible_reason=infeasible_reason,
            )
        )
    return plans
