"""Plan Optimizer tất định (Prompt §7.10).

Loại bỏ no-op, xoá action trùng lặp, phát hiện xung đột, bảo tồn phủ định/ngoại trừ.
"""

from __future__ import annotations

from typing import Any

from src.domain.error_codes import ErrorCode
from src.nlu.schemas import ValidatedAction


def optimize_validated_actions(
    actions: list[ValidatedAction],
    *,
    device_states: dict[str, dict[str, Any]] | None = None,
    exclusions: list[dict[str, Any]] | list[str] | None = None,
) -> tuple[list[ValidatedAction], list[str], list[str]]:
    """Tối ưu hóa danh sách ValidatedAction.

    Trả về (optimized_actions, warnings, detected_conflicts).
    """
    device_states = device_states or {}
    exclusions = exclusions or []

    optimized: list[ValidatedAction] = []
    warnings: list[str] = []
    conflicts: list[str] = []

    seen_signatures: set[tuple[str, str, str]] = set()

    for act in actions:
        entity_id = act.entity_id

        # 1. Kiểm tra Exclusions (ngoại trừ) — Prompt §4.5
        is_excluded = False
        for ex in exclusions:
            if isinstance(ex, str) and ex.lower() in entity_id.lower():
                is_excluded = True
                break
            elif isinstance(ex, dict):
                ex_id = ex.get("entity_id") or ex.get("device_id")
                if ex_id and ex_id == entity_id:
                    is_excluded = True
                    break
        if is_excluded:
            warnings.append(f"Loại bỏ action trên '{entity_id}' do nằm trong danh sách ngoại trừ (exclusion)")
            continue

        # 2. Kiểm tra No-op (thiết bị đã ở đúng trạng thái mong muốn)
        current_state = device_states.get(entity_id, {})
        desired_power = act.desired_state.get("power") or act.desired_state.get("state")
        if desired_power:
            current_power = current_state.get("power") or current_state.get("state")
            if current_power and str(current_power).lower() == str(desired_power).lower():
                warnings.append(f"{ErrorCode.NO_OP_ACTION}: Thiết bị '{entity_id}' đã ở trạng thái {desired_power}, bỏ qua action")
                continue

        # 3. Phao phát hiện duplicate action
        sig = (entity_id, act.service, str(act.parameters))
        if sig in seen_signatures:
            warnings.append(f"{ErrorCode.DUPLICATE_ACTION}: Loại bỏ action trùng lặp trên '{entity_id}'")
            continue
        seen_signatures.add(sig)

        # 4. Phát hiện mâu thuẫn trong cùng plan (vd turn_on + turn_off cùng thiết bị)
        for prev in optimized:
            if prev.entity_id == entity_id:
                prev_p = prev.desired_state.get("power")
                curr_p = act.desired_state.get("power")
                if prev_p and curr_p and prev_p != curr_p:
                    conflicts.append(
                        f"{ErrorCode.CONFLICTING_ACTIONS}: Mâu thuẫn trạng thái trên '{entity_id}': {prev_p} vs {curr_p}"
                    )

        optimized.append(act)

    # 5. Bỏ "bật" THỪA: nếu cùng thiết bị đã có action ĐẶT GIÁ TRỊ khiến nó bật (set/tăng/giảm/
    # mở — desired power "on" kèm tham số), thì lệnh turn_on trần là dư. LLM hay kèm cả hai
    # ("đặt độ sáng 55" + "bật đèn") → hiện ra "bật" rồi báo no-op "đã bật sẵn" gây rối. Gộp
    # lại: giữ action đặt giá trị, bỏ turn_on. KHÔNG đụng trường hợp chỉ có mỗi turn_on.
    on_by_value = {
        a.entity_id for a in optimized if a.service != "turn_on" and a.desired_state.get("power") == "on"
    }
    if on_by_value:
        deduped: list[ValidatedAction] = []
        for a in optimized:
            if a.service == "turn_on" and a.entity_id in on_by_value:
                warnings.append(
                    f"{ErrorCode.DUPLICATE_ACTION}: Bỏ 'bật' thừa trên '{a.entity_id}' (đã có lệnh đặt giá trị làm bật)"
                )
                continue
            deduped.append(a)
        optimized = deduped

    return optimized, warnings, conflicts
