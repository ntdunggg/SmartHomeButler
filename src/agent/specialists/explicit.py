"""Fallback specialist cho lệnh thiết bị tường minh.

Mục tiêu suy diễn vẫn phải do specialist theo domain đề xuất. Riêng lệnh tường
minh đã có entity + action thì có thể ground trực tiếp từ catalog, kể cả khi
device type mới chưa có specialist tối ưu tiện nghi. Plan sau đó vẫn phải qua
deterministic validation và authorization/policy như mọi plan khác.
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, RuntimeContext
from src.agent.specialists.base import Specialist
from src.domain.enums import DeviceType


class ExplicitCommandAgent(Specialist):
    """Ground lệnh explicit cho mọi device type đã khai báo trong catalog."""

    agent_name = "explicit"
    device_types = frozenset(DeviceType)
    served_dimensions = frozenset()

    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,
        profile: list[dict] | None = None,
    ) -> DeviceProposal | None:
        # Chỉ là fallback cho explicit; không tự đề xuất từ mục tiêu suy diễn.
        return None


class CatalogPowerOffAgent(Specialist):
    """Fallback TẮT cho device type chưa có specialist tiện nghi riêng.

    Routine "ra ngoài"/"đi ngủ" sinh outcome `power=off` cho cả bình nóng lạnh, máy rửa
    bát... — những domain không nằm trong bản đồ chiều tiện nghi nào, nên không specialist
    nào nhận và outcome bị RƠI im lặng khỏi kế hoạch. Fallback này chỉ đọc catalog và chỉ
    biết đúng một luật `power_off_proposal` của base; thiết bị an ninh bị loại khỏi domain
    để giữ bất biến §38. Plan vẫn qua validator + authorization/policy như mọi plan khác.
    """

    agent_name = "catalog_power_off"
    device_types = frozenset(DeviceType) - frozenset({DeviceType.DOOR_LOCK, DeviceType.CAMERA})
    served_dimensions = frozenset()

    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,
        profile: list[dict] | None = None,
    ) -> DeviceProposal | None:
        return None


class CatalogPowerOnAgent(Specialist):
    """Fallback BẬT for catalog domains without a comfort specialist."""

    agent_name = "catalog_power_on"
    device_types = frozenset(DeviceType) - frozenset({DeviceType.DOOR_LOCK, DeviceType.CAMERA})
    served_dimensions = frozenset()

    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,
        profile: list[dict] | None = None,
    ) -> DeviceProposal | None:
        return None
