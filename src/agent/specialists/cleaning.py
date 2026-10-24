"""Cleaning specialist — prepare a space through catalog-backed cleaning devices."""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, ProposalAction, RuntimeContext
from src.agent.specialists.base import Specialist
from src.domain.enums import DeviceType


class CleaningAgent(Specialist):
    agent_name = "cleaning"
    device_types = frozenset({DeviceType.VACUUM})
    served_dimensions = frozenset({"cleaning"})

    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,  # noqa: ARG002
        profile: list[dict] | None = None,  # noqa: ARG002
    ) -> DeviceProposal | None:
        devices = [
            device
            for device in self.candidate_devices(
                ctx,
                subgoal.room,
                subgoal.anchor_device_ids,
                selector_domain=subgoal.selector_domain,
                selector_is_strict=subgoal.selector_is_strict,
            )
            if device.device_id not in excluded
        ]
        if not devices:
            return None

        turn_off = str(subgoal.target_state.get("power", "on")).lower() == "off"
        action = "turn_off" if turn_off else "turn_on"
        actions = [
            ProposalAction(
                device_id=device.device_id,
                capability="on_off",
                action=action,
                target={},
                reason_vi=subgoal.rationale or "Chuẩn bị vệ sinh không gian",
            )
            for device in devices
        ]
        power_values = [self._power_of(device, ends_on=not turn_off) for device in devices]
        known_power = [power for power in power_values if power is not None]
        return self._proposal(
            objective="prepare cleanliness",
            actions=actions,
            comfort=0.85,
            power_w=sum(known_power) if len(known_power) == len(power_values) else None,
            confidence=0.85,
            evidence=[f"cleaning_devices={len(actions)}"],
        )
