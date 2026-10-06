"""Specialist registry + coordinator (spec §36, §39).

Chạy tất cả specialist ĐÚNG DOMAIN của mình trên từng subgoal (spec §38: agent không
nhìn/gọi ngoài domain), gom thành các nhóm proposal thay thế để Aggregator dựng plan.
"""

from __future__ import annotations

from typing import Any

from src.agent.planning.manager import Subgoal
from src.agent.schemas import DeviceProposal, RuntimeContext
from src.agent.specialists.ac import ACAgent
from src.agent.specialists.base import Specialist
from src.agent.specialists.cleaning import CleaningAgent
from src.agent.specialists.explicit import CatalogPowerOffAgent, CatalogPowerOnAgent, ExplicitCommandAgent
from src.agent.specialists.lighting import LightingAgent
from src.agent.specialists.media import MediaAgent
from src.agent.specialists.purifier import PurifierAgent
from src.agent.specialists.security import SecurityAgent
from src.agent.specialists.shutter import ShutterAgent
from src.domain.enums import device_types_for_domain


def default_specialists() -> list[Specialist]:
    return [LightingAgent(), ACAgent(), ShutterAgent(), MediaAgent(), PurifierAgent(), CleaningAgent(), SecurityAgent()]


def memory_target(profile: list[dict] | None, device_id: str, relation: str) -> float | None:
    """Giá trị PRIOR từ Semantic/Profile Memory (§24) cho một (thiết bị, relation) — dùng khi RL
    chưa học (spec §35: Memory Evidence là input planning, TÁCH khỏi Preference Distribution — P5).

    `profile` = profile_evidence (list ProfileFact.model_dump). Trả None nếu không có fact khớp."""
    if not profile:
        return None
    for pf in profile:
        if pf.get("subject") == device_id and pf.get("fact") == relation:
            val = (pf.get("value") or {}).get("value")
            if isinstance(val, int | float) and not isinstance(val, bool):
                return float(val)
    return None


def _is_inferred_power_off(subgoal: Subgoal) -> bool:
    """Subgoal này là mục tiêu SUY DIỄN yêu cầu tắt thiết bị?

    Lệnh tường minh (đã có device + action) đi đường `propose_explicit` riêng, nên loại ra
    bằng `explicit_device_id` chứ không bằng tên chiều.
    """
    if subgoal.explicit_device_id or subgoal.explicit_action:
        return False
    return str(subgoal.target_state.get("power", "")).lower() == "off"


def _is_inferred_power_on(subgoal: Subgoal) -> bool:
    if subgoal.explicit_device_id or subgoal.explicit_action:
        return False
    return str(subgoal.target_state.get("power", "")).lower() == "on"


def proposals_for_subgoal(
    subgoal: Subgoal,
    ctx: RuntimeContext,
    *,
    specialists: list[Specialist] | None = None,
    excluded: frozenset[str] = frozenset(),
    preference: dict[str, Any] | None = None,
    profile: list[dict] | None = None,
) -> list[DeviceProposal]:
    """Các proposal THAY THẾ cho một subgoal (từ mọi specialist phù hợp)."""
    agents = specialists if specialists is not None else default_specialists()
    out: list[DeviceProposal] = []

    # Outcome suy diễn nêu thẳng `power=off` đi qua MỘT luật chung ở base thay vì phụ thuộc
    # việc từng specialist có nhớ đọc target_state hay không (lighting/ac/purifier từng quên
    # → "đi ngủ" bật đèn 100%). Cổng `handles()` theo chiều tiện nghi bị bỏ qua ở đây vì
    # "tắt" không thuộc chiều nào; quyền sở hữu domain vẫn do device_types quyết định.
    if _is_inferred_power_off(subgoal):
        for agent in agents:
            if not agent.allows_inferred_power_off:
                continue
            if subgoal.selector_domain and not (
                device_types_for_domain(subgoal.selector_domain) & {t.value for t in agent.device_types}
            ):
                continue
            prop = agent.power_off_proposal(subgoal, ctx, excluded=excluded)
            if prop is not None:
                out.append(prop)
        if not out:
            # Domain chưa có specialist tiện nghi (bình nóng lạnh, máy rửa bát...) vẫn phải tắt được.
            prop = CatalogPowerOffAgent().power_off_proposal(subgoal, ctx, excluded=excluded)
            if prop is not None:
                out.append(prop)
        return out

    for agent in agents:
        if subgoal.selector_domain and subgoal.selector_is_strict and subgoal.dimension != "explicit":
            # So khớp qua TỪ VỰNG DÙNG CHUNG chứ không bằng chuỗi thô: một domain họ như
            # "media_player" không bao giờ bằng "speaker"/"tv" nên MediaAgent từng bị loại
            # khỏi chính subgoal của mình, và mục tiêu nghe nhạc rơi về plan rỗng.
            owned_types = {device_type.value for device_type in agent.device_types}
            if not (device_types_for_domain(subgoal.selector_domain) & owned_types):
                continue
        if subgoal.dimension == "explicit":
            prop = agent.propose_explicit(subgoal, ctx, excluded=excluded)
        elif agent.handles(subgoal):
            prop = agent.propose(subgoal, ctx, excluded=excluded, preference=preference, profile=profile)
        else:
            prop = None
        if prop is not None:
            out.append(prop)
    # Lệnh tường minh đã ground entity/action không được biến thành plan rỗng
    # chỉ vì device type chưa có specialist tiện-nghi riêng (water heater,
    # dishwasher, vacuum, ...). Fallback vẫn chỉ đọc catalog; validator + policy ở sau.
    if subgoal.dimension == "explicit" and not out:
        prop = ExplicitCommandAgent().propose_explicit(subgoal, ctx, excluded=excluded)
        if prop is not None:
            out.append(prop)
    if not out and _is_inferred_power_on(subgoal):
        prop = CatalogPowerOnAgent().power_on_proposal(subgoal, ctx, excluded=excluded)
        if prop is not None:
            out.append(prop)
    return out


def gather_proposals(
    subgoals: list[Subgoal],
    ctx: RuntimeContext,
    *,
    specialists: list[Specialist] | None = None,
    excluded: frozenset[str] = frozenset(),
    preference: dict[str, Any] | None = None,
    profile: list[dict] | None = None,
) -> list[list[DeviceProposal]]:
    """Trả list[list[proposal]] — mỗi phần tử là nhóm proposal thay thế của một subgoal.

    `profile` (profile_evidence §24) là PRIOR memory cho target số của specialist (§35, QC-06)."""
    agents = specialists if specialists is not None else default_specialists()
    return [
        proposals_for_subgoal(sg, ctx, specialists=agents, excluded=excluded, preference=preference, profile=profile)
        for sg in subgoals
    ]
