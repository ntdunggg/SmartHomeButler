"""PurifierAgent (spec §36 OtherCapabilityAgent) — chất lượng không khí / luồng gió.

Phục vụ chiều `air_quality` (máy lọc không khí) và `air_flow` (quạt): bật thiết bị khi
người dùng thấy bí/ngột/bụi. Ground vào thiết bị thật trong phòng; KHÔNG bịa thiết bị.
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, ProposalAction, RuntimeContext
from src.agent.specialists.base import Specialist
from src.domain.enums import DeviceType


class PurifierAgent(Specialist):
    agent_name = "purifier"
    device_types = frozenset({DeviceType.AIR_PURIFIER, DeviceType.FAN})
    served_dimensions = frozenset({"air_quality", "air_flow"})

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
            actions.append(
                ProposalAction(
                    device_id=d.device_id,
                    capability="on_off",
                    action="turn_on",
                    target={},
                    reason_vi=subgoal.rationale or "Cải thiện chất lượng không khí",
                )
            )
            watts = self._power_of(d, ends_on=True)
            if watts is not None:
                total_power += watts
        return self._proposal(
            objective="improve air quality",
            actions=actions,
            comfort=0.85,
            power_w=total_power,
            confidence=0.8,
            evidence=[f"purifier_devices={len(actions)}"],
        )
