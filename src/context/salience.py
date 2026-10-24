"""Salience Stack — nguồn sự thật DUY NHẤT cho "thiết bị nổi bật" xuyên các lượt hội thoại.

Thay các đường `last_device_id` rải rác (§14 tier-3) bằng MỘT ngăn xếp tất định: mỗi lượt tác
động/hỏi tới thiết bị nào thì ĐẨY thiết bị đó lên đỉnh. Lượt sau giải đại từ ("bật nó lên", "tắt
đi") = lấy thực thể nổi bật GẦN NHẤT TƯƠNG THÍCH capability của câu (điều chỉnh nhiệt độ → chỉ lấy
điều hoà, bỏ qua đèn). Đây là hạ tầng cho Context-Transducer (xem docs/BLUEPRINT_CONTEXT_TRANSDUCER).

Thiết kế: in-memory, TẤT ĐỊNH, dễ debug (log mỗi push/evict). Không I/O, không LLM. Ngăn xếp được
tuần tự hoá vào RequirementLedger (`salience`) để bền qua các lượt cùng conversation.

Bất biến:
- Đẩy trùng device_id = LÀM MỚI độ nổi bật (gỡ bản cũ, thêm bản mới ở đỉnh) — recency phản ánh
  đúng lần nhắc gần nhất.
- Eviction: giữ tối đa `max_depth` phần tử thường mới nhất; DECAY loại phần tử thường quá
  `max_age` lượt. Mốc `deferred` do người dùng chủ động hẹn quay lại sống đến khi được gọi
  lại hoặc huỷ tường minh, không phai theo cửa sổ đại từ tám lượt.
- `most_recent(capability=...)` duyệt từ ĐỈNH xuống, trả thực thể đầu tiên hỗ trợ capability đó.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.iot.registry import spec_for

logger = logging.getLogger("context.salience")

# Giới hạn độ sâu ngăn xếp thường và tuổi tối đa trước khi một thực thể thường "phai".
# Deferred branch là cam kết hội thoại tường minh nên được bảo toàn riêng.
DEFAULT_MAX_DEPTH = 5
DEFAULT_MAX_AGE = 8


def capabilities_of(device_id: str) -> frozenset[str]:
    """Tập capability (value string) của thiết bị theo registry, hoặc rỗng nếu không có spec."""
    spec = spec_for(device_id)
    if spec is None:
        return frozenset()
    return frozenset(c.value for c in spec.capabilities)


@dataclass(frozen=True, slots=True)
class SalienceEntry:
    """Một thực thể nổi bật: thiết bị + phòng + capability + lượt được nhắc + vai trò lượt."""

    device_id: str
    room: str | None
    capabilities: frozenset[str]
    turn: int
    kind: str = "command"  # command | query | group | deferred
    # Hành động gần nhất trên thiết bị. Dùng để loại một thực thể vừa bị
    # tắt/đóng khỏi anaphora điều chỉnh số ("giảm nó" nên bám loa còn bật,
    # không bám TV vừa tắt). Rỗng cho query/deferred và dữ liệu ledger cũ.
    action: str = ""

    def supports(self, capability: str | None) -> bool:
        """Thực thể này hỗ trợ capability yêu cầu? (None = không lọc, nhận mọi thực thể)."""
        return capability is None or capability in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "room": self.room,
            "capabilities": sorted(self.capabilities),
            "turn": self.turn,
            "kind": self.kind,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SalienceEntry:
        return cls(
            device_id=str(data["device_id"]),
            room=data.get("room"),
            capabilities=frozenset(data.get("capabilities") or ()),
            turn=int(data.get("turn", 0)),
            kind=str(data.get("kind", "command")),
            action=str(data.get("action", "")),
        )


@dataclass(slots=True)
class SalienceStack:
    """Ngăn xếp thực thể nổi bật (cũ → mới; đỉnh = phần tử cuối)."""

    max_depth: int = DEFAULT_MAX_DEPTH
    max_age: int | None = DEFAULT_MAX_AGE
    _entries: list[SalienceEntry] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self._entries)

    def push(
        self, device_id: str, *, turn: int, room: str | None = None,
        kind: str = "command", action: str = "", capabilities: frozenset[str] | None = None,
    ) -> SalienceEntry:
        """Đẩy MỘT thiết bị lên đỉnh (làm mới nếu đã có), rồi decay/evict. Trả entry vừa đẩy."""
        caps = capabilities if capabilities is not None else capabilities_of(device_id)
        entry = SalienceEntry(
            device_id=device_id, room=room, capabilities=caps, turn=turn, kind=kind, action=action,
        )
        # Làm mới độ nổi bật: gỡ bản cũ cùng device_id trước khi thêm ở đỉnh.
        self._entries = [e for e in self._entries if e.device_id != device_id]
        self._entries.append(entry)
        self._evict(turn)
        logger.debug("salience.push %s turn=%d kind=%s caps=%s depth=%d",
                     device_id, turn, kind, sorted(caps), len(self._entries))
        return entry

    def push_many(self, device_ids: list[str], *, turn: int, room: str | None = None,
                  kind: str = "command", action: str = "") -> None:
        """Đẩy nhiều thiết bị (giữ THỨ TỰ nêu: phần tử cuối danh sách thành đỉnh)."""
        for device_id in device_ids:
            self.push(device_id, turn=turn, room=room, kind=kind, action=action)

    def ordinal(self, index: int) -> SalienceEntry | None:
        """Phần tử `index` (0-based; âm từ cuối) của nhóm gần nhất.

        Mỗi thiết bị của lệnh nhóm được đẩy cùng `turn`, `kind=group`; danh
        sách nội bộ giữ thứ tự nêu cũ→mới. Chỉ đọc group-turn mới nhất, không
        trộn các nhóm xa nhau."""
        group_turns = [entry.turn for entry in self._entries if entry.kind == "group"]
        if not group_turns:
            return None
        latest = max(group_turns)
        group = [entry for entry in self._entries if entry.kind == "group" and entry.turn == latest]
        try:
            return group[index]
        except IndexError:
            return None

    def _evict(self, now_turn: int) -> None:
        """DECAY (bỏ phần tử quá cũ) rồi EVICT theo độ sâu (giữ `max_depth` mới nhất)."""
        if self.max_age is not None:
            kept = [
                entry
                for entry in self._entries
                if entry.kind == "deferred" or now_turn - entry.turn <= self.max_age
            ]
            if len(kept) != len(self._entries):
                logger.debug("salience.decay dropped=%d (age>%d)", len(self._entries) - len(kept), self.max_age)
            self._entries = kept
        regular = [entry for entry in self._entries if entry.kind != "deferred"]
        deferred = [entry for entry in self._entries if entry.kind == "deferred"]
        if len(regular) > self.max_depth:
            logger.debug("salience.evict over_depth dropped=%d", len(regular) - self.max_depth)
            regular = regular[-self.max_depth:]
        keep_ids = {id(entry) for entry in [*regular, *deferred]}
        self._entries = [entry for entry in self._entries if id(entry) in keep_ids]

    def top(self) -> SalienceEntry | None:
        """Thực thể nổi bật nhất (đỉnh), hoặc None nếu rỗng."""
        return self._entries[-1] if self._entries else None

    def most_recent(
        self, *, capability: str | None = None, predicate: Callable[[SalienceEntry], bool] | None = None,
    ) -> SalienceEntry | None:
        """Thực thể GẦN NHẤT hỗ trợ `capability` (và thoả `predicate` nếu có), duyệt từ đỉnh xuống."""
        for entry in reversed(self._entries):
            if entry.supports(capability) and (predicate is None or predicate(entry)):
                return entry
        return None

    def remove(self, device_id: str) -> None:
        """Gỡ một thiết bị khỏi ngăn (vd "thôi chuyện cái loa để sau" — dismissal)."""
        self._entries = [e for e in self._entries if e.device_id != device_id]

    def entries(self, *, newest_first: bool = True) -> list[SalienceEntry]:
        """Bản sao danh sách entry (mặc định mới → cũ)."""
        return list(reversed(self._entries)) if newest_first else list(self._entries)

    def clear(self) -> None:
        self._entries = []

    def to_list(self) -> list[dict[str, Any]]:
        """Tuần tự hoá (cũ → mới) để lưu vào ledger."""
        return [e.to_dict() for e in self._entries]

    @classmethod
    def from_list(
        cls, data: list[dict[str, Any]] | None, *, max_depth: int = DEFAULT_MAX_DEPTH,
        max_age: int | None = DEFAULT_MAX_AGE,
    ) -> SalienceStack:
        """Dựng lại ngăn xếp từ dữ liệu ledger (cũ → mới)."""
        stack = cls(max_depth=max_depth, max_age=max_age)
        stack._entries = [SalienceEntry.from_dict(d) for d in (data or [])]
        return stack
