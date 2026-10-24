"""Grounding thiết bị theo thứ tự bằng chứng — thay cho việc dò substring tên.

Nguồn chân lý là `semantic_roles` + `device_type` trong Registry, KHÔNG phải tên
người dùng đặt. Tên/alias chỉ được dùng như *legacy fallback* khi thiếu metadata và
chỉ khi còn đúng một ứng viên (kèm cờ để hạ confidence). Không bao giờ tự chọn khi
nhiều ứng viên ngang nhau — trả về danh sách để tầng trên hỏi lại.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.enums import DeviceType, RiskLevel
from src.iot.registry import DEVICE_BY_SLUG, DEVICE_SPECS, DeviceSpec


@dataclass(frozen=True, slots=True)
class Resolution:
    """Kết quả phân giải một thiết bị đơn.

    - `device` != None: đã chọn được duy nhất.
    - `device` == None và `candidates` > 1: nhập nhằng → tầng trên phải hỏi lại.
    """

    device: DeviceSpec | None
    candidates: tuple[DeviceSpec, ...] = field(default=())
    used_legacy_name: bool = False
    reason: str = ""


def devices_by(
    *,
    role: str | None = None,
    device_type: DeviceType | None = None,
    device_types: tuple[DeviceType, ...] = (),
    area: str | None = None,
    exclude_security: bool = True,
) -> list[DeviceSpec]:
    """Lọc thiết bị theo role/loại/phòng. Thiết bị an ninh bị loại mặc định (S2)."""
    types = set(device_types) | ({device_type} if device_type else set())
    out: list[DeviceSpec] = []
    for spec in DEVICE_SPECS:
        if exclude_security and spec.risk_level == RiskLevel.SECURITY:
            continue
        if area and spec.room != area:
            continue
        if types and spec.device_type not in types:
            continue
        if role and role not in spec.semantic_roles:
            continue
        out.append(spec)
    return out


def resolve_by_role(
    *,
    role: str,
    device_type: DeviceType,
    area: str | None = None,
    legacy_name_substrings: tuple[str, ...] = (),
) -> tuple[list[DeviceSpec], bool]:
    """Chọn thiết bị theo role. Trả (specs, used_legacy).

    Nếu không thiết bị nào khai role → rơi về so tên (legacy) NHƯNG chỉ nhận khi còn
    đúng một ứng viên; nhiều ứng viên legacy → trả rỗng (không đoán bừa)."""
    by_role = devices_by(role=role, area=area)
    if by_role:
        return by_role, False
    if legacy_name_substrings:
        legacy = [
            s
            for s in devices_by(device_type=device_type, area=area)
            if any(sub in s.name.lower() for sub in legacy_name_substrings)
        ]
        if len(legacy) == 1:
            return legacy, True
    return [], False


def resolve_single(
    *, device_type: DeviceType, area: str | None = None, explicit_ids: tuple[str, ...] = ()
) -> Resolution:
    """Chọn đúng MỘT thiết bị của một loại theo thứ tự bằng chứng (tổng quát hoá TV).

    Dùng cho ràng buộc cardinality="one": khi mục tiêu cần đúng một thiết bị mà có
    nhiều cái tương đương → trả candidates để tầng trên hỏi lại, KHÔNG chọn bừa."""
    explicit = [
        DEVICE_BY_SLUG[d]
        for d in explicit_ids
        if d in DEVICE_BY_SLUG and DEVICE_BY_SLUG[d].device_type == device_type
    ]
    if len(explicit) == 1:
        return Resolution(device=explicit[0])
    if len(explicit) > 1:
        return Resolution(device=None, candidates=tuple(explicit), reason="multiple_equivalent_devices")

    if area:
        area_ds = devices_by(device_type=device_type, area=area)
        if len(area_ds) == 1:
            return Resolution(device=area_ds[0])
        if len(area_ds) > 1:
            return Resolution(device=None, candidates=tuple(area_ds), reason="multiple_equivalent_devices")

    primary = devices_by(role="primary_entertainment", device_type=device_type) if device_type == DeviceType.TV else []
    if len(primary) == 1:
        return Resolution(device=primary[0])

    all_ds = devices_by(device_type=device_type)
    if len(all_ds) == 1:
        return Resolution(device=all_ds[0])
    if not all_ds:
        # 0 ứng viên (nhà không có thiết bị loại này) KHÁC hẳn "nhiều ứng viên ngang nhau" —
        # gộp chung sẽ khiến thông điệp hỏi lại phía trên hỏi sai bản chất ("bạn muốn cái
        # nào?" thay vì "nhà chưa có thiết bị này").
        return Resolution(device=None, candidates=(), reason="no_matching_devices")
    return Resolution(device=None, candidates=tuple(all_ds), reason="multiple_equivalent_devices")


def resolve_single_tv(*, explicit_ids: tuple[str, ...] = (), target_area: str | None = None) -> Resolution:
    """Chọn đúng một TV cho watch_movie theo thứ tự bằng chứng (Bước 4.3).

    1. TV được nhắc rõ (explicit_ids, gồm cả reference đã giải từ dialogue).
    2. TV trong phòng đích/hiện tại.
    3. TV duy nhất có role `primary_entertainment`.
    4. TV duy nhất còn lại.
    Còn ≥ 2 → nhập nhằng, reason = multiple_entertainment_devices.
    """
    explicit_tvs = [
        DEVICE_BY_SLUG[d] for d in explicit_ids if d in DEVICE_BY_SLUG and DEVICE_BY_SLUG[d].device_type == DeviceType.TV
    ]
    if len(explicit_tvs) == 1:
        return Resolution(device=explicit_tvs[0])
    if len(explicit_tvs) > 1:
        return Resolution(device=None, candidates=tuple(explicit_tvs), reason="multiple_entertainment_devices")

    if target_area:
        area_tvs = devices_by(device_type=DeviceType.TV, area=target_area)
        if len(area_tvs) == 1:
            return Resolution(device=area_tvs[0])
        if len(area_tvs) > 1:
            return Resolution(device=None, candidates=tuple(area_tvs), reason="multiple_entertainment_devices")

    primary = devices_by(role="primary_entertainment", device_type=DeviceType.TV)
    if len(primary) == 1:
        return Resolution(device=primary[0])

    all_tvs = devices_by(device_type=DeviceType.TV)
    if len(all_tvs) == 1:
        return Resolution(device=all_tvs[0])
    return Resolution(device=None, candidates=tuple(all_tvs), reason="multiple_entertainment_devices")
