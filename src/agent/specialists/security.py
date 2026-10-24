"""SecurityAgent (spec §36, §38, §49) — khoá cửa, camera.

Bất biến an ninh: mục tiêu SUY DIỄN chỉ được đề xuất hướng LÀM AN TOÀN HƠN (khoá), và
đề xuất đó vẫn phải đi qua Authorization/Policy như mọi action khác (spec §49, §P0/P1).
Mở khoá / tắt camera thì KHÔNG BAO GIỜ được suy diễn — chỉ lệnh tường minh mới làm được.
SecurityAgent KHÔNG execute (§38).
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, ProposalAction, RuntimeContext
from src.agent.specialists.base import Specialist
from src.domain.enums import DeviceType


class SecurityAgent(Specialist):
    agent_name = "security"
    device_types = frozenset({DeviceType.DOOR_LOCK, DeviceType.CAMERA})
    # Nhận chiều "security" để CHẶN có chủ đích thay vì để subgoal rơi vào fallback
    # catalog — không phải để tối ưu tiện nghi (§38: không phục vụ chiều comfort nào).
    served_dimensions = frozenset({"security"})
    allows_inferred_power_off = False  # "tắt" suy diễn không được chạm khoá/camera

    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,  # noqa: ARG002 — an ninh không theo preference/RL
        profile: list[dict] | None = None,  # noqa: ARG002 — an ninh không dùng memory prior
    ) -> DeviceProposal | None:
        """Chỉ đề xuất KHOÁ, và chỉ khi outcome nêu thẳng trạng thái khoá.

        Nếp sinh hoạt "cả nhà ra ngoài" author outcome `door_lock -> locked`; bỏ bước này
        khiến kịch bản thiếu đúng hành động người dùng mong đợi nhất. Đề xuất ở đây KHÔNG
        tự cho mình quyền thực thi: authorization/policy phía sau vẫn quyết định cần duyệt
        hay không, y như một lệnh khoá cửa tường minh.
        """
        if not self._wants_lock(subgoal):
            return None
        devices = [
            d
            for d in self.candidate_devices(
                ctx,
                subgoal.room,
                subgoal.anchor_device_ids,
                selector_domain=subgoal.selector_domain,
                selector_is_strict=subgoal.selector_is_strict,
            )
            if d.device_id not in excluded and d.device_type == DeviceType.DOOR_LOCK.value
        ]
        if not devices:
            return None
        actions = [
            ProposalAction(
                device_id=d.device_id,
                capability="lock",
                action="lock",
                target={},
                reason_vi=subgoal.rationale or "Khoá cửa theo mục tiêu an toàn khi rời nhà",
            )
            for d in devices
        ]
        return self._proposal(
            objective="secure the home",
            actions=actions,
            comfort=0.5,
            power_w=0.0,
            confidence=0.85,
            evidence=[f"lock_devices={len(actions)}"],
        )

    @staticmethod
    def _wants_lock(subgoal: Subgoal) -> bool:
        """Outcome có nêu thẳng trạng thái KHOÁ không (chấp nhận vài dạng model hay dùng)?

        Chỉ nhận hướng khoá; mọi dạng mở khoá đều trả False để suy diễn không bao giờ mở
        được cửa. Không suy từ perceived_state/rationale — chữ tự do không đủ tư cách mở
        đường tới thiết bị an ninh.
        """
        state = subgoal.target_state or {}
        if state.get("locked") is True:
            return True
        return str(state.get("lock", "")).lower() in {"locked", "lock"}

    # propose_explicit kế thừa từ base: lệnh tường minh vẫn khoá/mở bình thường.
