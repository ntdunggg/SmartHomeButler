"""Knowledge RAG (spec §27, P5) — tri thức "thiết bị/domain HOẠT ĐỘNG thế nào".

TÁCH khỏi Agent Memory (§27, P5): Memory trả lời *trước đây đã xảy ra gì*; Knowledge RAG
trả lời *thiết bị/domain vận hành ra sao* — manual, hướng dẫn an toàn, bảo dưỡng, dải giá
trị, cách dùng. Chỉ được gọi khi câu HỎI thật sự cần domain knowledge (`asks_knowledge`,
§27), không nằm trên đường điều khiển thiết bị.

Tri thức được SEED TẤT ĐỊNH từ device registry (capabilities/risk_level THẬT) + dải giá trị
của harness — KHÔNG bịa capability (§38). Truy hồi bằng embedding hashing nhẹ + trùng khớp
từ khoá (không phụ thuộc model ngoài; §23 cho phép vector embeddings, không bắt buộc Neo4j).
RAG KHÔNG map cụm từ → câu trả lời cứng (§71/§P8): nó truy hồi tài liệu theo ngữ nghĩa rồi
diễn đạt lại — thêm/bớt tài liệu là đổi tri thức, không phải thêm luật.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from src.domain.enums import Capability, RiskLevel
from src.iot.registry import DEVICE_SPECS, spec_for
from src.nlu.normalizer import NormalizedUtterance

_EMBED_DIM = 64


def _tokenize(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(t) > 1]


def _embed(text: str) -> tuple[float, ...]:
    """Bag-of-token hashing embedding (§23) — cùng không gian cho doc lẫn query, offline."""
    vec = [0.0] * _EMBED_DIM
    for token in _tokenize(text):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)  # noqa: S324 - không dùng cho bảo mật
        vec[h % _EMBED_DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return tuple(v / norm for v in vec)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


@dataclass(frozen=True, slots=True)
class KnowledgeDoc:
    """Một mẩu tri thức domain/thiết bị (§27). `topic` là metadata truy hồi, không phải taxonomy."""

    doc_id: str
    topic: str  # capability | safety | maintenance | usage | energy
    title: str
    text: str
    device_types: frozenset[str] = frozenset()
    keywords: frozenset[str] = frozenset()
    embedding: tuple[float, ...] = ()


class KnowledgeBase:
    """Kho tri thức + truy hồi ngữ nghĩa. Tách hẳn EventStore/PreferenceStore (P5)."""

    def __init__(self) -> None:
        self._docs: list[KnowledgeDoc] = []

    def add(self, doc: KnowledgeDoc) -> None:
        if not doc.embedding:
            doc = replace(doc, embedding=_embed(f"{doc.title} {doc.text} {' '.join(doc.keywords)}"))
        self._docs.append(doc)

    def __len__(self) -> int:
        return len(self._docs)

    def retrieve(
        self,
        query: str,
        *,
        device_types: frozenset[str] = frozenset(),
        k: int = 2,
    ) -> list[tuple[KnowledgeDoc, float]]:
        """Top-k doc liên quan. Câu nêu loại thiết bị → loại doc khác-loại; doc chung luôn xét.

        Doc chỉ ĐỦ ĐIỀU KIỆN khi có TÍN HIỆU thật: trùng ≥1 từ khoá nội dung HOẶC khớp loại thiết
        bị được hỏi — embedding hashing (dim nhỏ) dễ nhiễu va-chạm nên KHÔNG được một mình quyết
        (tránh bịa câu trả lời cho câu chẳng liên quan). Điểm xếp hạng = cosine + từ-khoá + đúng-loại."""
        q_emb = _embed(query)
        q_tokens = set(_tokenize(query))
        scored: list[tuple[KnowledgeDoc, float]] = []
        for d in self._docs:
            type_match = bool(device_types and (d.device_types & device_types))
            # Scope: doc gắn loại thiết bị mà câu KHÔNG hỏi loại đó → bỏ (doc chung device_types rỗng vẫn xét).
            if device_types and d.device_types and not type_match:
                continue
            doc_tokens = {t for kw in d.keywords for t in _tokenize(kw)}
            keyword_hits = len(q_tokens & doc_tokens)
            if keyword_hits == 0 and not type_match:
                continue  # không tín hiệu nội dung/loại → không nhận (embedding một mình quá nhiễu)
            score = _cosine(q_emb, d.embedding) + 0.2 * keyword_hits + (0.3 if type_match else 0.0)
            scored.append((d, score))
        scored.sort(key=lambda item: -item[1])
        return scored[:k]


# ---------------------------------------------------------------------------
# Seed tri thức TẤT ĐỊNH từ registry (grounded, không bịa — §38)
# ---------------------------------------------------------------------------
_TYPE_VI: dict[str, str] = {
    "light": "đèn", "air_conditioner": "điều hoà", "tv": "TV", "curtain": "rèm",
    "window": "cửa sổ", "door_lock": "khoá cửa", "camera": "camera",
    "water_heater": "bình nóng lạnh", "air_purifier": "máy lọc không khí",
    "vacuum": "robot hút bụi", "fan": "quạt", "speaker": "loa", "heater": "máy sưởi",
    "dishwasher": "máy rửa bát",
}
_CAP_VI: dict[str, str] = {
    Capability.ON_OFF.value: "bật/tắt", Capability.BRIGHTNESS.value: "chỉnh độ sáng 0–100%",
    Capability.TEMPERATURE.value: "đặt nhiệt độ 16–30°C", Capability.FAN_SPEED.value: "chỉnh tốc độ quạt",
    Capability.VOLUME.value: "chỉnh âm lượng 0–100%", Capability.POSITION.value: "chỉnh vị trí mở 0–100%",
    Capability.LOCK.value: "khoá/mở khoá",
}
# Bảo dưỡng theo loại — tri thức domain (manual), không phải luật câu lệnh.
_MAINTENANCE: dict[str, str] = {
    "air_purifier": "Máy lọc không khí nên vệ sinh lưới lọc thô mỗi 2–4 tuần và thay màng lọc HEPA khoảng 6–12 tháng tuỳ mức bụi.",
    "vacuum": "Robot hút bụi nên đổ hộp rác sau mỗi lần chạy và vệ sinh chổi cạnh, bánh xe hàng tuần.",
    "water_heater": "Bình nóng lạnh nên súc cặn và kiểm tra thanh magie định kỳ 6–12 tháng để dùng an toàn, bền hơn.",
    "air_conditioner": "Điều hoà nên vệ sinh lưới lọc mỗi 2–4 tuần và bảo dưỡng dàn lạnh mỗi 3–6 tháng.",
    "dishwasher": "Máy rửa bát nên vệ sinh bộ lọc đáy hàng tuần và chạy chu trình làm sạch định kỳ.",
}


def build_default_knowledge_base() -> KnowledgeBase:
    """Dựng KB mặc định từ registry: mỗi loại thiết bị một doc capability + doc an toàn/bảo dưỡng
    theo risk_level, cộng vài doc chung (chế độ điện, tiện nghi nhiệt). Grounded, không bịa."""
    kb = KnowledgeBase()
    seen_types: set[str] = set()
    for spec in DEVICE_SPECS:
        dt = spec.device_type.value
        if dt in seen_types:
            continue
        seen_types.add(dt)
        vi = _TYPE_VI.get(dt, dt)
        caps = ", ".join(_CAP_VI.get(c.value, c.value) for c in spec.capabilities)

        kb.add(KnowledgeDoc(
            doc_id=f"cap:{dt}", topic="capability",
            title=f"{vi} điều khiển được gì",
            text=f"{vi.capitalize()} hỗ trợ: {caps}.",
            device_types=frozenset({dt}),
            keywords=frozenset({"chức năng", "điều khiển", "dải"}),
        ))

        if spec.risk_level == RiskLevel.HIGH_POWER:
            kb.add(KnowledgeDoc(
                doc_id=f"safety:{dt}", topic="safety",
                title=f"An toàn khi dùng {vi}",
                text=(f"{vi.capitalize()} là thiết bị công suất cao: không nên bật khi không có người, "
                      f"và hệ thống sẽ ưu tiên giảm tải nhóm này khi điện năng ở mức CRITICAL."),
                device_types=frozenset({dt}),
                keywords=frozenset({"an toàn", "cảnh báo", "công suất", "điện", "nóng"}),
            ))
        if spec.risk_level == RiskLevel.SECURITY:
            kb.add(KnowledgeDoc(
                doc_id=f"security:{dt}", topic="safety",
                title=f"An ninh với {vi}",
                text=(f"{vi.capitalize()} thuộc nhóm an ninh: các thao tác nhạy cảm (mở khoá, tắt camera) "
                      f"cần người có quyền xác nhận và không bao giờ được tự động thực hiện."),
                device_types=frozenset({dt}),
                keywords=frozenset({"an toàn", "an ninh", "mở khoá", "quyền", "xác nhận"}),
            ))
        if dt in _MAINTENANCE:
            kb.add(KnowledgeDoc(
                doc_id=f"maint:{dt}", topic="maintenance",
                title=f"Bảo dưỡng {vi}",
                text=_MAINTENANCE[dt],
                device_types=frozenset({dt}),
                keywords=frozenset({"vệ sinh", "bảo dưỡng", "bao lâu", "lọc", "màng lọc", "cặn"}),
            ))

    kb.add(KnowledgeDoc(
        doc_id="energy:modes", topic="energy",
        title="Chế độ điện năng của nhà",
        text=("Hệ thống phân loại tải điện thành NORMAL, MODERATE và CRITICAL. Khi lên MODERATE/CRITICAL, "
              "planner ưu tiên tiết kiệm điện và có thể giảm/ngắt bớt thiết bị công suất cao."),
        keywords=frozenset({"điện", "tải", "tiết kiệm", "công suất", "chế độ", "critical"}),
    ))
    kb.add(KnowledgeDoc(
        doc_id="comfort:temperature", topic="usage",
        title="Dải nhiệt độ tiện nghi",
        text="Điều hoà đặt được trong khoảng 16–30°C; mức tiện nghi thường quanh 24–26°C tuỳ thời tiết và sở thích.",
        device_types=frozenset({"air_conditioner"}),
        keywords=frozenset({"nhiệt độ", "dải", "tiện nghi"}),
    ))
    return kb


# KB mặc định dùng chung trong tiến trình (service có thể inject KB riêng).
_DEFAULT_KB: KnowledgeBase | None = None


def get_default_knowledge_base() -> KnowledgeBase:
    global _DEFAULT_KB
    if _DEFAULT_KB is None:
        _DEFAULT_KB = build_default_knowledge_base()
    return _DEFAULT_KB


def _mentioned_device_types(nu: NormalizedUtterance) -> frozenset[str]:
    types: set[str] = set()
    for slug in nu.matched_device_ids:
        spec = spec_for(slug)
        if spec is not None:
            types.add(spec.device_type.value)
    for dt, vi in _TYPE_VI.items():
        if vi in nu.normalized:
            types.add(dt)
    return frozenset(types)


def answer_knowledge(nu: NormalizedUtterance, kb: KnowledgeBase | None) -> str | None:
    """Trả lời câu hỏi KIẾN THỨC từ KB (§27). Rỗng nếu không đủ tri thức phù hợp (→ fallback).

    Diễn đạt lại doc phù hợp nhất; ghép thêm doc phụ khác chủ đề nếu điểm gần (vd hỏi vừa an
    toàn vừa bảo dưỡng). Không bịa câu trả lời khi retrieval rỗng."""
    if kb is None or len(kb) == 0:
        return None
    hits = kb.retrieve(nu.normalized, device_types=_mentioned_device_types(nu), k=2)
    if not hits:
        return None
    top_doc, top_score = hits[0]
    parts = [top_doc.text]
    for doc, score in hits[1:]:
        # Ghép doc phụ khi bổ sung chủ đề KHÁC và đủ liên quan. 'capability' (chung nhất) chỉ
        # dùng khi nó là doc top — không kéo theo làm loãng câu trả lời an toàn/bảo dưỡng.
        if doc.topic not in (top_doc.topic, "capability") and score >= top_score * 0.7:
            parts.append(doc.text)
    return " ".join(parts)


__all__ = [
    "KnowledgeBase",
    "KnowledgeDoc",
    "answer_knowledge",
    "build_default_knowledge_base",
    "get_default_knowledge_base",
]
