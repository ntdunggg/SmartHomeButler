"""MediaAgent (spec §36) — loa/TV (âm lượng, phát nhạc).

Phục vụ chiều `media`: tăng/giảm âm lượng, bật nhạc nền. "Nhạc hơi to, giảm xuống chút"
(spec §20) → decrease volume, ground tương đối theo mức hiện tại.
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, ProposalAction, RuntimeContext
from src.agent.specialists.base import Specialist, _clamp_capability
from src.domain.enums import Capability, DeviceType


class MediaAgent(Specialist):
    agent_name = "media"
    device_types = frozenset({DeviceType.SPEAKER, DeviceType.TV})
    served_dimensions = frozenset({"media"})

    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,
        profile: list[dict] | None = None,  # noqa: ARG002 — nhận đồng nhất, chiều này chưa dùng memory prior
    ) -> DeviceProposal | None:
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
        for d in devices:
            current = float(d.state.get("volume", 30) or 30)
            is_on = str(d.state.get("power", "off")).lower() == "on"
            desired_power = str(subgoal.target_state.get("power", "")).lower()
            if desired_power in {"on", "off"}:
                actions.append(
                    ProposalAction(
                        device_id=d.device_id,
                        capability="on_off",
                        action="turn_on" if desired_power == "on" else "turn_off",
                        target={},
                        reason_vi=subgoal.rationale or "Chuẩn bị thiết bị media",
                    )
                )
                watts = self._power_of(d, ends_on=desired_power == "on")
                if watts is not None:
                    total_power += watts
                if desired_power == "off":
                    continue
            raw_volume = subgoal.target_state.get("volume")
            explicit_volume = (
                isinstance(raw_volume, int | float) and not isinstance(raw_volume, bool)
            )
            # Model-authored semantic states may use markers such as "unchanged".
            # They mean no numeric media adjustment and must never reach float().
            has_volume_goal = explicit_volume or subgoal.direction is not None
            if not has_volume_goal:
                continue
            # An inactive media source is already silent. Preserve a turn-off/no-op
            # for traceability, but never power it on to satisfy a relative
            # "quieter" outcome (the sleep regression was TV off -> on -> volume 0).
            target_volume = raw_volume
            already_silent = (
                subgoal.direction == "decrease"
                or (
                    isinstance(target_volume, int | float)
                    and not isinstance(target_volume, bool)
                    and float(target_volume) <= current
                )
            )
            if not is_on and desired_power != "on" and already_silent:
                actions.append(
                    ProposalAction(
                        device_id=d.device_id,
                        capability="on_off",
                        action="turn_off",
                        target={},
                        reason_vi=subgoal.rationale or "Giữ thiết bị media tắt để giảm tiếng ồn",
                    )
                )
                continue
            if isinstance(raw_volume, int | float) and not isinstance(raw_volume, bool):
                target = float(raw_volume)
            elif subgoal.direction == "decrease":
                target = _clamp_capability(current - 20, Capability.VOLUME, d.device_type)
            elif subgoal.direction == "increase":
                target = _clamp_capability(current + 20, Capability.VOLUME, d.device_type)
            else:
                target = current
            if not is_on and desired_power != "on":
                actions.append(
                    ProposalAction(
                        device_id=d.device_id,
                        capability="on_off",
                        action="turn_on",
                        target={},
                        reason_vi=subgoal.rationale or "Bật thiết bị media",
                    )
                )
            actions.append(
                ProposalAction(
                    device_id=d.device_id,
                    capability="volume",
                    action="set",
                    target={"volume": target},
                    reason_vi=subgoal.rationale or "Điều chỉnh âm lượng",
                )
            )
            if desired_power != "on":
                watts = self._power_of(d, ends_on=True)
                if watts is not None:
                    total_power += watts

        return self._proposal(
            objective="adjust media",
            actions=actions,
            comfort=0.85,
            power_w=total_power,
            confidence=0.8,
            evidence=[f"media_devices={len(actions)}"],
        )
