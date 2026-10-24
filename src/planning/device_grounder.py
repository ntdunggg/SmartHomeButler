"""Deterministic Device Selector Grounder (Prompt §7.9, §4.3).

Ánh xạ DeviceSelector (area, domain, labels, state, area_type) thành entity_id thực
từ device catalog hoặc registry. LLM tuyệt đối KHÔNG tự tạo device_id.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.core.interfaces import DeviceSelector, EnvironmentSnapshot
from src.domain.enums import RiskLevel, device_types_for_domain
from src.domain.error_codes import ErrorCode
from src.iot.registry import DEVICE_SPECS, DeviceSpec
from src.nlu.schemas import DeviceSelector as SchemaDeviceSelector


def ground_selector(
    selector: DeviceSelector | SchemaDeviceSelector | dict[str, Any],
    snapshot: EnvironmentSnapshot | list[DeviceSpec] | None = None,
    *,
    speaker_location: str | None = None,
) -> tuple[list[DeviceSpec], list[str]]:
    """Ground selector thành danh sách thiết bị thật từ catalog.

    Trả về (matched_devices, warnings_or_errors).
    """
    if isinstance(selector, dict):
        sel = DeviceSelector.model_validate(selector)
    elif isinstance(selector, SchemaDeviceSelector):
        sel = DeviceSelector(
            area=selector.area,
            area_type=selector.area_type,
            domain=selector.domain,
            labels=selector.labels or [],
            state=selector.state,
        )
    else:
        sel = selector

    # Thu thập catalog thiết bị
    specs: Sequence[DeviceSpec]
    if isinstance(snapshot, list):
        specs = snapshot
    else:
        specs = list(DEVICE_SPECS)

    area = sel.area
    if not area and sel.area_type == "speaker_location" and speaker_location:
        area = speaker_location

    domain = (sel.domain or "").lower()
    labels = [lbl.lower() for lbl in sel.labels]

    matched: list[DeviceSpec] = []
    for spec in specs:
        # 1. Kiểm tra an ninh (không cho routine tự chạm vào trừ khi tường minh)
        if spec.risk_level == RiskLevel.SECURITY:
            continue

        # 2. Lọc theo room/area
        if area and area.lower() not in (spec.room.lower(), spec.slug.lower()):
            continue
        if sel.area_type == "unused_common_area" and area and spec.room.lower() == area.lower():
            continue

        # 3. Lọc theo domain / device_type — qua BẢNG DÙNG CHUNG (§4), không tự dịch tại chỗ.
        # So khớp chuỗi con trước đây vừa thiếu vừa thừa: "media_player" không khớp gì, còn
        # domain CHÍNH XÁC "speaker" lại rơi vào nhánh alias nên khớp luôn cả TV.
        if domain:
            allowed = device_types_for_domain(domain)
            if not allowed or spec.device_type.value.lower() not in allowed:
                continue

        # 4. Lọc theo labels / semantic_roles / name
        if labels:
            spec_roles = [r.lower() for r in spec.semantic_roles]
            spec_name = spec.name.lower()
            if not any(lbl in spec_roles or lbl in spec_name or lbl in spec.slug for lbl in labels):
                continue

        matched.append(spec)

    warnings: list[str] = []
    if not matched:
        warnings.append(f"{ErrorCode.SELECTOR_NO_MATCH}: Không tìm thấy thiết bị phù hợp cho selector {sel.model_dump()}")

    return matched, warnings
