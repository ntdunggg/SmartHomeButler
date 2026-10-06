"""ACAgent (spec §36, §65) — thermal comfort.

Phục vụ chiều `temperature`: làm ấm/mát. Tầng understanding KHÔNG mặc định "turn on AC
heating" (spec §65) — ACAgent mới xét thiết bị/nhiệt độ hiện tại/preference/năng lượng.
Mục tiêu tuyệt đối ưu tiên preference distribution (spec §33) nếu không nêu số cụ thể.
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, ProposalAction, RuntimeContext
from src.agent.specialists.base import Specialist, _clamp_capability
from src.domain.enums import Capability, DeviceType


class ACAgent(Specialist):
    agent_name = "ac"
    device_types = frozenset({DeviceType.AIR_CONDITIONER, DeviceType.HEATER})
    served_dimensions = frozenset({"temperature"})

    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,
        profile: list[dict] | None = None,
    ) -> DeviceProposal | None:
        from src.agent.specialists.registry import memory_target

        devices = [
            d
            for d in self.candidate_devices(
                ctx,
                subgoal.room,
                subgoal.anchor_device_ids,
                selector_domain=subgoal.selector_domain,
                selector_is_strict=subgoal.selector_is_strict,
            )
            if d.device_id not in excluded
        ]
        if not devices:
            return None

        actions: list[ProposalAction] = []
        total_power = 0.0
        mem_used: list[str] = []
        rl_used_target: float | None = None

        # Explicit multi-state capability (e.g. humidity goal -> dry mode).
        # It is independent from a temperature setpoint and must not be collapsed
        # to the current temperature.
        if subgoal.target_state.get("hvac_mode") is not None:
            mode = str(subgoal.target_state["hvac_mode"])
            for d in devices:
                actions.append(
                    ProposalAction(
                        device_id=d.device_id,
                        capability="hvac_mode",
                        action="set",
                        target={"hvac_mode": mode},
                        reason_vi=subgoal.rationale or "Đặt chế độ điều hoà theo mục tiêu môi trường",
                    )
                )
                watts = self._power_of(d, ends_on=True)
                if watts is not None:
                    total_power += watts
            return self._proposal(
                objective="set HVAC mode",
                actions=actions,
                comfort=0.92,
                power_w=total_power,
                confidence=0.9,
                evidence=[f"hvac_mode={mode}"],
            )

        for d in devices:
            current = float(d.state.get("temperature", 24) or 24)
            is_on = str(d.state.get("power", "off")).lower() == "on"
            mem_target = memory_target(profile, d.device_id, "preferred_temperature")

            # 1. Explicit target wins absolutely
            if subgoal.target_state.get("temperature") is not None:
                target = float(subgoal.target_state["temperature"])

            # 2. Directional constraint: DECREASE ("làm mát", "hạ nhiệt", "nóng quá")
            elif subgoal.direction == "decrease" or subgoal.perceived_state in ("hot", "warm", "nóng", "oi"):
                pref_dec = (
                    self._preferred_temperature(preference, constraint=lambda t: t < current)
                    if is_on
                    else self._preferred_temperature(preference)
                )
                if pref_dec is not None and (not is_on or pref_dec < current):
                    target = pref_dec
                    rl_used_target = pref_dec
                elif mem_target is not None and (not is_on or mem_target < current):
                    target = _clamp_capability(mem_target, Capability.TEMPERATURE, d.device_type)
                    mem_used.append(f"memory:preferred_temperature={int(mem_target)}@{d.device_id}")
                else:
                    target = _clamp_capability(current - 2, Capability.TEMPERATURE, d.device_type)

            # 3. Directional constraint: INCREASE ("sưởi ấm", "tăng nhiệt", "lạnh quá")
            elif subgoal.direction == "increase" or subgoal.perceived_state in ("cold", "lạnh", "rét"):
                pref_inc = (
                    self._preferred_temperature(preference, constraint=lambda t: t > current)
                    if is_on
                    else self._preferred_temperature(preference)
                )
                if pref_inc is not None and (not is_on or pref_inc > current):
                    target = pref_inc
                    rl_used_target = pref_inc
                elif mem_target is not None and (not is_on or mem_target > current):
                    target = _clamp_capability(mem_target, Capability.TEMPERATURE, d.device_type)
                    mem_used.append(f"memory:preferred_temperature={int(mem_target)}@{d.device_id}")
                else:
                    target = _clamp_capability(current + 2, Capability.TEMPERATURE, d.device_type)

            # 4. No directional constraint (bật chung / chỉnh nhiệt độ không nêu hướng)
            else:
                pref_gen = self._preferred_temperature(preference)
                if pref_gen is not None:
                    target = pref_gen
                    rl_used_target = pref_gen
                elif mem_target is not None:
                    target = _clamp_capability(mem_target, Capability.TEMPERATURE, d.device_type)
                    mem_used.append(f"memory:preferred_temperature={int(mem_target)}@{d.device_id}")
                else:
                    target = current

            action = "turn_on" if not is_on else "set"
            actions.append(
                ProposalAction(
                    device_id=d.device_id,
                    capability="temperature",
                    action=action,
                    target={"temperature": target},
                    reason_vi=subgoal.rationale or "Điều chỉnh nhiệt độ theo mục tiêu thoải mái",
                )
            )
            watts = self._power_of(d, ends_on=True)
            if watts is not None:
                total_power += watts

        rl_evidence = []
        if rl_used_target is not None and preference and "temperature" in preference:
            d = preference["temperature"]
            rl_evidence.append(
                f"rl:temperature={int(rl_used_target)} confidence={d.confidence:.2f} state={d.context.get('state', '')}"
            )

        return self._proposal(
            objective="adjust thermal comfort",
            actions=actions,
            comfort=0.92,
            power_w=total_power,
            confidence=0.85,
            evidence=[f"ac_devices={len(actions)}", f"preference_target={rl_used_target}", *rl_evidence, *mem_used],
        )

    @staticmethod
    def _preferred_temperature(
        preference: dict[str, Any] | None,
        *,
        constraint: Any | None = None,
    ) -> float | None:
        if not preference:
            return None
        dist = preference.get("temperature")
        if dist is None or dist.confidence <= 0 or not dist.distribution:
            return None

        # Sắp xếp các action theo xác suất giảm dần
        sorted_actions = sorted(dist.distribution.items(), key=lambda item: item[1], reverse=True)
        if constraint is not None:
            for act_str, _prob in sorted_actions:
                try:
                    val = float(act_str)
                    if constraint(val):
                        return val
                except (ValueError, TypeError):
                    continue
            return None

        top = dist.top()
        return float(top[0]) if top else None
