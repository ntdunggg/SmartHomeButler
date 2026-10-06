"""Refresh → Re-ground → Revalidate (spec §50) — không execute trên stale state.

    Plan created → refresh live state → re-ground → revalidate → execute

Nếu một action trở thành no-op hoặc invalid trên trạng thái MỚI: remove/reject/replan
(spec §50). Đây là bất biến §73 #7 (live state refreshed before execution) và #3
(memory không override live state).
"""

from __future__ import annotations

from src.agent.harness.gateway import DeviceGateway
from src.agent.schemas import ValidatedAction
from src.domain.action_registry import validate_state_values


def refresh_state(gateway: DeviceGateway, entity_ids: list[str]) -> dict[str, dict]:
    """Đọc lại trạng thái sống của các thiết bị trong plan (spec §50)."""
    return {slug: gateway.get_state(slug) for slug in dict.fromkeys(entity_ids)}


def _is_noop(action: ValidatedAction, live: dict) -> bool:
    """Action đã được thoả trên trạng thái sống? (spec §50 → loại no-op)."""
    if not live:
        return False
    for key, want in action.desired_state.items():
        have = live.get(key)
        if key == "power":
            if str(have).lower() != str(want).lower():
                return False
        elif have != want:
            return False
    return True


def reground(
    actions: list[ValidatedAction],
    live_states: dict[str, dict],
) -> tuple[list[ValidatedAction], list[ValidatedAction]]:
    """Trả (kept, dropped_noops). Gắn `previous_state` từ live để executor/audit dùng."""
    kept: list[ValidatedAction] = []
    dropped: list[ValidatedAction] = []
    for a in actions:
        live = live_states.get(a.entity_id, {})
        a.previous_state = dict(live)
        if _is_noop(a, live):
            dropped.append(a)
        else:
            kept.append(a)
    return kept, dropped


def revalidate(actions: list[ValidatedAction]) -> tuple[list[ValidatedAction], list[ValidatedAction]]:
    """Revalidate SAU refresh/reground (spec §50, invariant §73.6): giữ bất biến device-limit
    (dải giá trị §47) ngay trước execute — action ngoài dải bị LOẠI, không để fail tận gateway.

    Dùng chung ActionSpecRegistry với deterministic validator. Trả (kept, rejected)."""
    kept: list[ValidatedAction] = []
    rejected: list[ValidatedAction] = []
    for a in actions:
        issue = validate_state_values(a.desired_state or {}, device_type=a.domain)
        (rejected if issue is not None else kept).append(a)
    return kept, rejected
