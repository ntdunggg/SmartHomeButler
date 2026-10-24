"""Bộ lọc TRẠNG THÁI THỰC — rút gọn danh sách thiết bị mơ hồ theo trạng thái sống + hướng hành động.

Bài toán (LC-004 / "Tắt quạt" khi có 3 quạt): khi một hành động phân giải ra NHIỀU thiết bị cùng
loại, để nguyên danh sách khiến tầng sau (LLM/Specialists) dễ ĐOÁN BỪA hoặc phản hồi thừa (premature
assumptions / answer bloat — 2505.06120). Giải pháp TẤT ĐỊNH: dùng trạng thái thật từ registry/snapshot
để thu hẹp:

- Hành động TẮT / GIẢM (turn_off, decrease, close): chỉ giữ thiết bị đang HOẠT ĐỘNG (bật/mở) — chỉ
  cái đó mới tắt/giảm được.
- Hành động BẬT / MỞ (turn_on, open): chỉ giữ thiết bị đang TẮT (off/đóng) — chỉ cái đó mới cần bật.
- Nếu sau lọc còn ĐÚNG MỘT thiết bị → rút gọn về nó (đích rõ ràng, không cần hỏi).
- BIÊN: nếu tất cả cùng trạng thái (lọc ra 0 hoặc vẫn >1) → GIỮ NGUYÊN danh sách gốc, để tầng sau
  / câu hỏi clarification quyết định (không tự ý chọn).

Thuần, tất định, không LLM. Trạng thái "hoạt động" đồng bộ với `state_query._is_active` (power on,
hoặc rèm/cửa đang mở, hoặc khoá đang mở).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("context.state_filter")

# Hành động ĐÒI thiết bị đang HOẠT ĐỘNG (chỉ tác động được lên cái đang bật/mở).
_NEEDS_ACTIVE = frozenset({"turn_off", "decrease", "close"})
# Hành động ĐÒI thiết bị đang TẮT (chỉ cần bật/mở cái đang off/đóng).
_NEEDS_INACTIVE = frozenset({"turn_on", "open"})


def is_active(state: dict[str, Any] | None) -> bool:
    """Thiết bị có đang HOẠT ĐỘNG không (bật / rèm-cửa mở / khoá mở)? (đồng bộ state_query._is_active)."""
    state = state or {}
    if state.get("power") == "on":
        return True
    if state.get("locked") is False:
        return True
    pos = state.get("position")
    if isinstance(pos, int | float) and pos > 0:
        return True
    if isinstance(pos, str) and pos.strip().lower() in {"open", "opened", "mở", "mo"}:
        return True
    return False


def reduce_by_state(
    device_ids: list[str], *, action_hint: str | None, live_states: dict[str, dict[str, Any]],
) -> list[str]:
    """Rút gọn danh sách thiết bị theo trạng thái thật + hướng hành động (xem docstring module).

    Trả về danh sách MỚI: rút về 1 nếu lọc còn đúng một; else GIỮ NGUYÊN danh sách gốc (biên)."""
    if len(device_ids) <= 1:
        return list(device_ids)
    if action_hint in _NEEDS_ACTIVE:
        want_active = True
    elif action_hint in _NEEDS_INACTIVE:
        want_active = False
    else:
        return list(device_ids)  # set/tăng/khoá... không suy được trạng thái đích → không lọc
    filtered = [d for d in device_ids if is_active(live_states.get(d)) == want_active]
    if len(filtered) == 1:
        logger.debug("state_filter reduce %s --%s--> %s", device_ids, action_hint, filtered)
        return filtered
    return list(device_ids)  # 0 hoặc >1 (tất cả cùng trạng thái) → giữ nguyên, để clarify quyết
