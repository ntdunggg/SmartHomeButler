"""LightingAgent (spec §36) — độ sáng / illumination.

Phục vụ chiều `illumination`: tăng/giảm độ sáng đèn. Ground tương đối → tuyệt đối bằng
trạng thái hiện tại + preference distribution (spec §33 nếu có). Đây là một trong nhiều
cách đạt "increase illumination" — ShutterAgent (mở rèm) là cách khác; Optimizer chọn
(spec §43).
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, ProposalAction, RuntimeContext
from src.agent.specialists.base import Specialist, _clamp_capability
from src.domain.action_registry import capability_bounds, relative_steps_for
from src.domain.enums import Capability, DeviceType


class LightingAgent(Specialist):
    agent_name = "lighting"
    device_types = frozenset({DeviceType.LIGHT})
    served_dimensions = frozenset({"illumination"})

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

        # `illumination` gồm HAI đại lượng: độ sáng và nhiệt màu. "ánh sáng ấm áp hơn"
        # là color_temp — ép nó thành brightness sẽ chỉnh sai đại lượng người dùng nêu.
        if self._wants_color_temp(subgoal):
            return self._propose_color_temp(subgoal, devices)

        actions: list[ProposalAction] = []
        total_power = 0.0
        mem_used: list[str] = []
        rl_used_target: float | None = None

        for d in devices:
            current = float(d.state.get("brightness", 0) or 0)
            is_on = str(d.state.get("power", "off")).lower() == "on"
            mem_target = memory_target(profile, d.device_id, "preferred_brightness")

            # A device that is off is already at minimum effective illumination.
            # Preserve it as a turn-off/no-op in the plan (for conversation trace
            # and stale planning snapshots), but never turn it on merely to set a
            # smaller non-zero brightness value.
            if not is_on and (
                subgoal.direction == "decrease"
                or subgoal.perceived_state in ("too_bright", "bright", "chói")
            ):
                actions.append(
                    ProposalAction(
                        device_id=d.device_id,
                        capability="brightness",
                        action="turn_off",
                        target={},
                        reason_vi=subgoal.rationale or "Giữ đèn tắt theo mục tiêu giảm sáng",
                    )
                )
                continue

            # 1. Explicit target wins absolutely
            if subgoal.target_state.get("brightness") is not None:
                target = float(subgoal.target_state["brightness"])

            # 2. Directional constraint: DECREASE ("giảm sáng", "tối hơn", "chói") -> Bắt buộc B < current khi đèn đang bật
            elif subgoal.direction == "decrease" or subgoal.perceived_state in ("too_bright", "bright", "chói"):
                pref_dec = self._preferred_brightness(preference, constraint=(lambda b: b < current) if is_on else None)
                if pref_dec is not None:
                    target = pref_dec
                    rl_used_target = pref_dec
                elif mem_target is not None and (not is_on or mem_target < current):
                    target = _clamp_capability(mem_target, Capability.BRIGHTNESS, d.device_type)
                    mem_used.append(f"memory:preferred_brightness={int(mem_target)}@{d.device_id}")
                else:
                    target = _clamp_capability(min(current - 30, 30), Capability.BRIGHTNESS, d.device_type) if is_on else 20

            # 3. Directional constraint: INCREASE ("tăng sáng", "sáng hơn", "tối quá") -> Bắt buộc B > current khi đèn đang bật
            elif subgoal.direction == "increase" or subgoal.perceived_state in ("too_dark", "dark", "tối", "thiếu sáng"):
                pref_inc = self._preferred_brightness(preference, constraint=(lambda b: b > current) if is_on else None)
                if pref_inc is not None:
                    target = pref_inc
                    rl_used_target = pref_inc
                elif mem_target is not None and (not is_on or mem_target > current):
                    target = _clamp_capability(mem_target, Capability.BRIGHTNESS, d.device_type)
                    mem_used.append(f"memory:preferred_brightness={int(mem_target)}@{d.device_id}")
                else:
                    target = _clamp_capability(max(current + 30, 70), Capability.BRIGHTNESS, d.device_type)

            # 4. No directional constraint
            else:
                pref_gen = self._preferred_brightness(preference)
                if pref_gen is not None:
                    target = pref_gen
                    rl_used_target = pref_gen
                elif mem_target is not None:
                    target = _clamp_capability(mem_target, Capability.BRIGHTNESS, d.device_type)
                    mem_used.append(f"memory:preferred_brightness={int(mem_target)}@{d.device_id}")
                else:
                    target = _clamp_capability(max(current + 30, 70), Capability.BRIGHTNESS, d.device_type)

            # No-op nếu đèn đã đúng mức và đang đúng power (§50 sẽ lọc lại trên live state).
            action = "turn_off" if target <= 0 else ("turn_on" if not is_on else "set")
            actions.append(
                ProposalAction(
                    device_id=d.device_id,
                    capability="brightness",
                    action=action,
                    target={"brightness": target},
                    reason_vi=subgoal.rationale or "Điều chỉnh độ sáng theo mục tiêu",
                )
            )
            watts = self._power_of(d, ends_on=target > 0)
            if watts is not None:
                total_power += watts

        comfort = 0.9 if subgoal.direction != "decrease" else 0.85
        rl_evidence = []
        if rl_used_target is not None and preference and "brightness" in preference:
            d = preference["brightness"]
            rl_evidence.append(
                f"rl:brightness={int(rl_used_target)} confidence={d.confidence:.2f} state={d.context.get('state', '')}"
            )

        return self._proposal(
            objective="adjust illumination via lights",
            actions=actions,
            comfort=comfort,
            power_w=total_power,
            confidence=0.85,
            evidence=[f"lighting_devices={len(actions)}", f"preference_target={rl_used_target}", *rl_evidence, *mem_used],
        )

    @staticmethod
    def _wants_color_temp(subgoal: Subgoal) -> bool:
        """Goal có nêu ĐÍCH DANH nhiệt màu không (capability hoặc trị tuyệt đối)?"""
        return (
            subgoal.capability == Capability.COLOR_TEMP.value
            or subgoal.target_state.get(Capability.COLOR_TEMP.value) is not None
        )

    def _propose_color_temp(self, subgoal: Subgoal, devices: list) -> DeviceProposal | None:
        """Chỉnh nhiệt màu. Ấm hơn = Kelvin THẤP hơn, nên `decrease` là ấm lên.

        Bước tương đối và biên lấy từ action registry (nguồn sự thật duy nhất), không
        đặt hằng số Kelvin riêng trong specialist.
        """
        cap = Capability.COLOR_TEMP
        bounds = capability_bounds(cap, DeviceType.LIGHT)
        steps = relative_steps_for(cap)
        step = steps[-1] if steps else 600.0
        default = float(sum(bounds) / 2) if bounds else 4000.0

        actions: list[ProposalAction] = []
        total_power = 0.0
        for d in devices:
            if cap not in d.capabilities:
                continue
            current = float(d.state.get(cap.value) or default)
            explicit = subgoal.target_state.get(cap.value)
            if explicit is not None:
                target = float(explicit)
            elif subgoal.direction == "decrease":
                target = current - step
            elif subgoal.direction == "increase":
                target = current + step
            else:
                target = current
            target = _clamp_capability(target, cap, d.device_type)
            actions.append(
                ProposalAction(
                    device_id=d.device_id,
                    capability=cap.value,
                    action="set",
                    target={cap.value: target},
                    reason_vi=subgoal.rationale or "Điều chỉnh nhiệt màu ánh sáng theo mục tiêu",
                )
            )
            watts = self._power_of(d, ends_on=True)
            if watts is not None:
                total_power += watts

        if not actions:
            return None
        return self._proposal(
            objective="adjust light colour temperature",
            actions=actions,
            comfort=0.9,
            power_w=total_power,
            confidence=0.85,
            evidence=[f"color_temp_devices={len(actions)}", f"step={int(step)}K"],
        )

    @staticmethod
    def _preferred_brightness(
        preference: dict[str, Any] | None,
        *,
        constraint: Any | None = None,
    ) -> float | None:
        if not preference:
            return None
        dist = preference.get("brightness")
        if dist is None or dist.confidence <= 0 or not dist.distribution:
            return None

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
