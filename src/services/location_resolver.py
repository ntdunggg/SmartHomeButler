"""Location Resolver — tự suy phòng người dùng đang ở từ nhiều tín hiệu thật.

Ý tưởng: thay vì bắt người dùng chọn phòng thủ công, agent gộp các tín hiệu (loa/mic
theo phòng đã nghe thấy câu nói, cảm biến hiện diện theo phòng, và về sau là BLE) thành
MỘT ước lượng vị trí kèm ĐỘ TIN CẬY. Nếu đủ tin cậy → đưa thẳng vào context để LLM hiểu
"ở đây nóng quá". Nếu KHÔNG đủ tin cậy (thiếu tín hiệu / các tín hiệu mâu thuẫn) →
``room = None`` để pipeline hỏi lại người dùng (nhánh MISSING_AREA của validator).

Hàm ``resolve_location`` là THUẦN (dễ test). Việc thu thập tín hiệu từ DB/HTTP nằm ở
``build_signals`` + caller (service layer), tách khỏi logic fusion.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

# Trọng số mỗi loại tín hiệu (0..1) — "mức tin" của một nguồn khi nó chỉ về một phòng.
# Loa/mic nghe thấy câu nói là bằng chứng mạnh nhất (người phải ở gần thiết bị đó).
WEIGHT_CAPTURE_DEVICE = 0.7
WEIGHT_PRESENCE = 0.6
WEIGHT_BLE = 0.65  # dành sẵn cho nguồn BLE cắm sau

# Ngưỡng tin cậy tối thiểu để DÙNG vị trí mà không hỏi lại.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6


@dataclass(frozen=True, slots=True)
class LocationSignal:
    """Một tín hiệu vị trí thô: nguồn nào, chỉ về phòng nào, tin tới đâu."""

    room: str
    weight: float
    source: str  # "capture_device" | "presence_sensor" | "ble"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LocationEstimate:
    """Kết quả suy vị trí. ``room=None`` nghĩa là CHƯA đủ tin cậy — hãy hỏi lại."""

    room: str | None
    confidence: float
    source: str  # nguồn đóng góp nhiều nhất cho phòng thắng; "" nếu không có tín hiệu
    evidence: list[str] = field(default_factory=list)
    # Các phòng ứng viên đã xếp hạng (room, score) — dùng cho câu hỏi lại có gợi ý.
    candidates: list[tuple[str, float]] = field(default_factory=list)

    @property
    def is_confident(self) -> bool:
        return self.room is not None


def build_signals(
    *,
    capture_room: str | None = None,
    presence_rooms: list[str] | None = None,
    valid_rooms: set[str] | None = None,
) -> list[LocationSignal]:
    """Dựng danh sách tín hiệu từ dữ liệu đã thu thập.

    - ``capture_room``: phòng của loa/mic đã nghe thấy câu nói (client hint).
    - ``presence_rooms``: các phòng cảm biến hiện diện đang báo có người.
    - ``valid_rooms``: nếu truyền, chỉ giữ tín hiệu trỏ về phòng hợp lệ (chống rác)."""
    signals: list[LocationSignal] = []

    def _ok(room: str | None) -> bool:
        return bool(room) and (valid_rooms is None or room in valid_rooms)

    if capture_room and _ok(capture_room):
        signals.append(
            LocationSignal(room=capture_room, weight=WEIGHT_CAPTURE_DEVICE, source="capture_device",
                           detail="loa/mic nghe thấy câu nói ở phòng này")
        )
    for room in presence_rooms or []:
        if _ok(room):
            signals.append(
                LocationSignal(room=room, weight=WEIGHT_PRESENCE, source="presence_sensor",
                               detail="cảm biến hiện diện báo có người")
            )
    return signals


def resolve_location(
    signals: list[LocationSignal],
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> LocationEstimate:
    """Gộp các tín hiệu thành một ước lượng vị trí kèm độ tin cậy.

    Cách tính (đơn giản, giải thích được):
    - Cộng trọng số các tín hiệu theo từng phòng → điểm mỗi phòng.
    - Chỉ một phòng có tín hiệu: confidence = min(1, điểm) — không có gì cạnh tranh.
    - Nhiều phòng: confidence = điểm_nhất / (điểm_nhất + điểm_nhì) — tín hiệu mâu thuẫn
      kéo độ tin cậy xuống, phản ánh đúng sự mơ hồ.
    - confidence < threshold → ``room=None`` (hỏi lại), nhưng vẫn trả candidates."""
    if not signals:
        return LocationEstimate(room=None, confidence=0.0, source="", evidence=["Không có tín hiệu vị trí nào."])

    scores: dict[str, float] = defaultdict(float)
    top_source: dict[str, tuple[float, str]] = {}
    for s in signals:
        scores[s.room] += s.weight
        # Nhớ nguồn đóng góp mạnh nhất cho mỗi phòng để báo cáo source.
        if s.room not in top_source or s.weight > top_source[s.room][0]:
            top_source[s.room] = (s.weight, s.source)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_room, best_score = ranked[0]

    if len(ranked) == 1:
        confidence = min(1.0, best_score)
    else:
        second_score = ranked[1][1]
        confidence = best_score / (best_score + second_score)

    evidence = [
        f"{s.source}: {s.room} ({s.detail})" if s.detail else f"{s.source}: {s.room}"
        for s in signals
    ]

    if confidence < threshold:
        # Không đủ chắc — để pipeline hỏi lại, nhưng vẫn nêu ứng viên + lý do.
        reason = "tín hiệu mâu thuẫn" if len(ranked) > 1 else "tín hiệu quá yếu"
        return LocationEstimate(
            room=None,
            confidence=round(confidence, 3),
            source="",
            evidence=[*evidence, f"Chưa đủ tin cậy ({reason})."],
            candidates=[(r, round(sc, 3)) for r, sc in ranked],
        )

    return LocationEstimate(
        room=best_room,
        confidence=round(confidence, 3),
        source=top_source[best_room][1],
        evidence=evidence,
        candidates=[(r, round(sc, 3)) for r, sc in ranked],
    )
