"""ShutterAgent (spec §36, §43) — rèm/cửa sổ (openness), và ĐÓNG GÓP illumination.

Mở rèm là cách gần-như-0-W để tăng ánh sáng khi có daylight (spec §43): ShutterAgent
đề xuất mở rèm cho `illumination`, Optimizer so với LightingAgent và ưu tiên phương án
rẻ hơn nếu đủ comfort.
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, ProposalAction, RuntimeContext, SensorReading
from src.agent.specialists.base import Specialist, _clamp_capability
from src.domain.enums import Capability, DeviceType


class ShutterAgent(Specialist):
    agent_name = "shutter"
    device_types = frozenset({DeviceType.CURTAIN, DeviceType.WINDOW})
    served_dimensions = frozenset({"openness", "illumination"})

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

        # Đóng góp illumination chỉ hợp lý khi có ánh sáng ngoài trời (daylight/nắng).
        if subgoal.dimension == "illumination":
            if subgoal.direction == "decrease":
                # With strong daylight, the shutter removes the external glare at
                # its source and should outrank dimming electric lights.
                daylight = self._daylight_value(ctx.sensors)
                target_pos, action, comfort = 0.0, "close", (0.95 if daylight >= 70 else 0.7)
            elif not self._has_daylight(ctx.sensors):
                return None  # ban đêm mở rèm không giúp sáng → không đề xuất
            else:
                # Ban ngày mở rèm ĐẠT comfort chiếu sáng ngang bật đèn → comfort tie, để năng
                # lượng phá hoà: rèm ~0 W thắng đèn (spec §43 "Nếu B đáp ứng comfort, ưu tiên B").
                target_pos, action, comfort = 80.0, "open", 0.9
        else:  # openness trực tiếp
            raw_position = subgoal.target_state.get("position", 100)
            if isinstance(raw_position, str) and raw_position.strip().lower() in {"closed", "close", "off"}:
                target_position = 0.0
            elif isinstance(raw_position, str) and raw_position.strip().lower() in {"open", "opened", "on"}:
                target_position = 80.0
            else:
                try:
                    target_position = float(raw_position)
                except (TypeError, ValueError):
                    target_position = 80.0
            if subgoal.direction == "decrease" or target_position <= 0:
                target_pos, action, comfort = 0.0, "close", 0.85
            else:
                target_pos, action, comfort = _clamp_capability(target_position, Capability.POSITION), "open", 0.85

        actions = [
            ProposalAction(
                device_id=d.device_id,
                capability="position",
                action=action,
                target={"position": target_pos},
                reason_vi=subgoal.rationale or "Điều chỉnh rèm",
            )
            for d in devices
        ]
        return self._proposal(
            objective="adjust openness / natural light",
            actions=actions,
            comfort=comfort,
            power_w=0.0,  # rèm ~0 W (spec §43)
            confidence=0.8,
            evidence=[f"shutter_devices={len(actions)}"],
        )

    @staticmethod
    def _has_daylight(sensors: list[SensorReading]) -> bool:
        return ShutterAgent._daylight_value(sensors) > 20

    @staticmethod
    def _daylight_value(sensors: list[SensorReading]) -> float:
        for s in sensors:
            if s.sensor_type == "sunlight":
                return float(s.value)
        return 0.0
