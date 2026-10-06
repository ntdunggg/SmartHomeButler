"""Clarification Agent (spec §15) — hỏi đúng, ngắn, information-gain cao.

Nguyên tắc (spec §15): targeted, ngắn, hỏi đúng missing information, ưu tiên câu hỏi có
information gain cao, TRÁNH hỏi những gì sensor/context đã trả lời được. Không dùng câu
mù "Bạn nói rõ hơn được không?" — mà đưa lựa chọn cụ thể ("Phòng khách hay phòng ngủ?").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.schemas import RuntimeContext
from src.iot.registry import ROOMS, spec_for
from src.nlu.normalizer import NormalizedUtterance

# Field thực-thi-quan-trọng, xếp theo information gain (thiết bị thu hẹp mạnh nhất trước).
_FIELD_PRIORITY = ("device", "device_id", "room", "area")


@dataclass(slots=True)
class Clarification:
    question: str
    target_field: str
    options: list[str] = field(default_factory=list)
    reason: str = ""


def _rooms_with_relevant_devices(ctx: RuntimeContext, nu: NormalizedUtterance | None = None) -> list[str]:
    """Phòng ĐÁNG hỏi cho câu này — theo thứ tự thông tin thu được nhiều nhất.

    1. Cụm phòng chung trong câu ("phòng ngủ") đã thu hẹp sẵn → hỏi đúng các phòng đó.
    2. Loại thiết bị người dùng NÊU → hỏi các phòng THỰC SỰ có loại đó, tra từ registry.
    3. Cuối cùng mới tới các phòng trong context.

    Bước 2 quan trọng vì `ctx.devices` KHÔNG phải tập ứng viên khi câu chưa nêu phòng:
    lúc đó context builder chỉ đưa một LÁT CẮT đầu catalog để giới hạn prompt. Hỏi từ lát
    cắt ấy sinh ra câu hỏi sai lệch — "Bật đèn" từng hỏi "Phòng khách hay Phòng bếp ạ?"
    dù đèn có ở cả bốn phòng, nên người dùng trả lời một phòng KHÔNG được liệt kê.
    """
    if nu is not None and nu.partial_rooms:
        return list(nu.partial_rooms)
    if nu is not None:
        # `mentioned_device_types` là nguồn sự thật DUY NHẤT cho "từ loại trần → DeviceType"
        # (§4: không dựng bản sao thứ hai của cùng một luật ngữ nghĩa). Nó bắt cả "đèn" trần,
        # thứ mà `matched_device_ids` cố ý để rỗng vì nhiều đèn trùng alias.
        from src.nlu.understanding import mentioned_device_types

        types = set(mentioned_device_types(nu))
        types.update(spec.device_type for spec in map(spec_for, nu.matched_device_ids) if spec)
        if types:
            rooms = [r for r in ROOMS if any(_has_type(r, t) for t in types)]
            if len(rooms) >= 2:
                return rooms
    rooms = [d.room for d in ctx.devices if d.room]
    seen: list[str] = []
    for r in rooms:
        if r not in seen:
            seen.append(r)
    return seen or list(ctx.rooms)


def _has_type(room: str, device_type) -> bool:
    from src.iot.registry import DEVICE_SPECS

    return any(spec.room == room and spec.device_type == device_type for spec in DEVICE_SPECS)


def _pick_field(still_missing: list[str]) -> str | None:
    for f in _FIELD_PRIORITY:
        if f in still_missing:
            return f
    return still_missing[0] if still_missing else None


def build_clarification(
    still_missing: list[str],
    *,
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
) -> Clarification | None:
    """Sinh MỘT câu hỏi làm rõ nhắm đúng field ưu tiên cao nhất (spec §15)."""
    field_name = _pick_field(still_missing)
    if field_name is None:
        return None

    if field_name in ("device", "device_id"):
        # Nhiều thiết bị khớp → hỏi CHỌN giữa chúng (info gain cao), không hỏi chung chung.
        specs = [spec for s in nu.matched_device_ids if (spec := spec_for(s)) is not None]
        if len(specs) >= 2:
            # Tên hiển thị thường trùng nhau giữa các phòng ("Đèn ngủ", "Rèm thông minh").
            # Một câu hỏi "Đèn ngủ hay Đèn ngủ?" không hề làm giảm ambiguity; room là thuộc
            # tính registry phân biệt ổn định, nên luôn kèm nó khi có nhiều ứng viên.
            opts = [f"{spec.name} ở {spec.room}" for spec in specs]
            listed = " hay ".join(opts) if len(opts) == 2 else ", ".join(opts[:-1]) + f", hay {opts[-1]}"
            return Clarification(
                question=f"Bạn muốn {listed} ạ?",
                target_field="device",
                options=opts,
                reason="multiple_matching_devices",
            )
        return Clarification(
            question="Bạn muốn điều khiển thiết bị nào ạ?", target_field="device", reason="device_unspecified"
        )

    # room / area
    rooms = _rooms_with_relevant_devices(ctx, nu)
    if 2 <= len(rooms) <= len(ROOMS):
        # Liệt kê bằng dấu phẩy khi >2 lựa chọn; "A hay B hay C hay D" đọc rất nặng.
        listed = " hay ".join(rooms) if len(rooms) == 2 else ", ".join(rooms[:-1]) + f", hay {rooms[-1]}"
        return Clarification(
            question=f"Bạn muốn mình làm ở {listed} ạ?",
            target_field="room",
            options=rooms,
            reason="room_unspecified_few_candidates",
        )
    return Clarification(
        question="Bạn muốn mình làm ở phòng nào ạ?",
        target_field="room",
        options=rooms,
        reason="room_unspecified",
    )
