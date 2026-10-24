"""Embedding provider cho truy hồi ký ức dài hạn (spec §23).

VAI TRÒ, và ranh giới của nó
----------------------------
Embedding MỞ RỘNG ngữ cảnh dài hạn: nó cho phép truy hồi ký ức theo Ý NGHĨA thay vì theo
trùng token. Nó KHÔNG thay thế trạng thái hội thoại có cấu trúc — Requirement Ledger (§17)
vẫn là bản ghi canonical của mạch hội thoại, và mọi quyết định vẫn tất định. Ranh giới:

- Ledger (§17): mục tiêu đang treo, câu hỏi làm rõ, ràng buộc, assumption đã bác, salience.
  Có cấu trúc, tất định, bền vững trong DB. KHÔNG dùng embedding ở đây.
- EventStore/EventRetriever (§21–§26): ký ức dài hạn, xếp hạng theo LIÊN QUAN. Embedding
  chỉ đóng góp MỘT thành phần điểm, cạnh location/actor/recency; memory là BẰNG CHỨNG,
  không phải kế hoạch, và không override live state (invariant 3).

KHÔNG GIAN VECTOR
-----------------
Mỗi vector mang theo NHÃN không gian (`space`: tên model + số chiều). Cosine chỉ có nghĩa
GIỮA các vector cùng không gian: một event nhúng bằng hashing (dim 64) và một truy vấn
nhúng bằng text-embedding-3 (dim 256) không so sánh được, và nếu lặng lẽ trả 0 thì truy hồi
ngữ nghĩa TẮT ÂM THẦM — hỏng kiểu tệ nhất. Nhãn làm việc lệch không gian thành hữu hình,
và cho phép ký ức cũ (hashing) sống chung với ký ức mới cho tới khi được nhúng lại.

SUY GIẢM DUYÊN DÁNG
-------------------
Không key / LLM_DISABLED / API lỗi / timeout → rơi về hashing embedding tất định. Một lượt
hội thoại KHÔNG BAO GIỜ hỏng vì dịch vụ nhúng hỏng; nó chỉ mất phần truy hồi ngữ nghĩa.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agent.memory.embeddings")

# Số chiều vector hashing offline — giữ nguyên giá trị lịch sử để snapshot cũ đọc được.
HASHING_DIM = 64
HASHING_SPACE = f"hashing-{HASHING_DIM}"

# Nhúng cùng một câu nhiều lần trong một lượt là chuyện thường (extractor + retriever).
# Cache theo (space, text) để không trả tiền và độ trễ hai lần cho cùng một chuỗi.
_CACHE_MAX = 2048

# NGƯỠNG NỀN của mỗi không gian: cosine của HAI CÂU KHÔNG LIÊN QUAN.
#
# Hashing bag-of-token cho ~0 khi không trùng token, nên downstream (`min_score=0.15` ở
# event_retriever) được hiệu chỉnh quanh nền 0. Embedding thật KHÔNG như vậy: đo trên 45
# cặp câu nhà thông minh tiếng Việt KHÔNG liên quan với text-embedding-3-small (1024 chiều)
# cho mean=0.40, p75=0.45 — trong khi các cặp CÙNG NGHĨA cho mean=0.62. Nếu đưa cosine thô
# xuống, mọi ký ức đều vượt ngưỡng và bộ lọc liên quan (§26) TẮT ÂM THẦM: memory biến
# thành nhiễu, trái với "memory là bằng chứng" (invariant 3).
#
# Nên mỗi không gian khai báo nền của nó và `cosine` co giãn về [0,1] quanh nền đó. Nhờ vậy
# ngưỡng downstream giữ NGUYÊN ý nghĩa dù đổi model nhúng, và đường hashing không đổi một bit.
#
# Hai phân phối CHỒNG LẤN ở đuôi, nên nền được chọn bằng cách quét chính QUYẾT ĐỊNH ở hạ
# nguồn (`score >= min_score` với trọng số ngữ nghĩa 0.6) trên 8 cặp truy-vấn/ký-ức, không
# phải bằng một phân vị đẹp mắt. Số ký ức ĐÚNG giữ được / ký ức SAI lọt qua:
#     nền 0.00 → 8/8 đúng nhưng 54/56 sai   (bộ lọc coi như TẮT)
#     nền 0.30 → 6/8 đúng,       6/56 sai   ← chọn
#     nền 0.45 → 3/8 đúng,       0/56 sai   (giết quá nhiều ký ức đúng)
_SPACE_FLOOR: dict[str, float] = {HASHING_SPACE: 0.0}
# Nền mặc định cho họ text-embedding-3 (xem số đo ở trên).
DEFAULT_API_SIMILARITY_FLOOR = 0.30


def register_space_floor(space: str, floor: float) -> None:
    """Khai báo ngưỡng nền cho một không gian vector."""
    _SPACE_FLOOR[space] = max(0.0, min(0.99, floor))


@dataclass(frozen=True, slots=True)
class Embedding:
    """Vector kèm nhãn KHÔNG GIAN — cosine chỉ hợp lệ giữa hai vector cùng `space`."""

    vector: tuple[float, ...]
    space: str

    def __bool__(self) -> bool:
        return bool(self.vector)


def _tokenize(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(t) > 1]


def hashing_embedding(text: str) -> list[float]:
    """Bag-of-token hashing embedding — đường offline tất định, không phụ thuộc model ngoài."""
    vec = [0.0] * HASHING_DIM
    for token in _tokenize(text):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)  # noqa: S324 - không dùng cho bảo mật
        vec[h % HASHING_DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def _normalize(vector: list[float]) -> list[float]:
    norm = sum(v * v for v in vector) ** 0.5
    if not norm:
        return vector
    return [v / norm for v in vector]


class HashingEmbeddingProvider:
    """Provider offline: luôn có, luôn tất định, không gọi mạng."""

    space = HASHING_SPACE

    def embed(self, text: str) -> Embedding:
        return Embedding(vector=tuple(hashing_embedding(text)), space=self.space)

    def embed_many(self, texts: list[str]) -> list[Embedding]:
        return [self.embed(t) for t in texts]


class OpenAIEmbeddingProvider:
    """Provider gọi OpenAI Text-Embedding-3 (endpoint OpenAI-compatible qua ``base_url``).

    Dùng lại SDK ``openai`` đã có trong dependency, giống `nlu/model_client.py` — không
    thêm SDK thứ hai. Mọi lỗi (thiếu SDK, xác thực, timeout, transport) đều rơi về
    hashing: mất truy hồi ngữ nghĩa vẫn hơn mất cả lượt trả lời.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 10.0,
        dimensions: int = 0,
        similarity_floor: float = DEFAULT_API_SIMILARITY_FLOOR,
    ) -> None:
        self._model = model
        self._dimensions = dimensions if dimensions and dimensions > 0 else 0
        self._fallback = HashingEmbeddingProvider()
        self.space = f"{model}-{self._dimensions}" if self._dimensions else model
        register_space_floor(self.space, similarity_floor)
        self._lock = threading.Lock()
        self._cache: dict[str, Embedding] = {}
        # Sau một lần hỏng vì CẤU HÌNH (sai key/model), thử lại mỗi lượt chỉ tốn độ trễ.
        self._disabled = False
        self._client: Any | None
        try:
            from openai import OpenAI  # import cục bộ: không có key thì không cần SDK

            self._client = OpenAI(
                api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=1
            )
        except Exception:  # noqa: BLE001 - thiếu SDK/cấu hình hỏng → chạy tiếp offline
            logger.warning("openai SDK unavailable; embeddings fall back to hashing")
            self._client = None
            self._disabled = True

    def _cached(self, text: str) -> Embedding | None:
        with self._lock:
            return self._cache.get(text)

    def _store(self, text: str, emb: Embedding) -> None:
        with self._lock:
            if len(self._cache) >= _CACHE_MAX:
                self._cache.clear()
            self._cache[text] = emb

    def embed(self, text: str) -> Embedding:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[Embedding]:
        """Nhúng theo LÔ — một lời gọi API cho nhiều câu (rẻ và nhanh hơn gọi lẻ)."""
        if not texts:
            return []
        results: list[Embedding | None] = [None] * len(texts)
        pending: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            if not (text or "").strip():
                # Chuỗi rỗng không có ngữ nghĩa để nhúng; vector rỗng = "không so sánh".
                results[i] = Embedding(vector=(), space=self.space)
                continue
            hit = self._cached(text)
            if hit is not None:
                results[i] = hit
            else:
                pending.append((i, text))

        if pending and not self._disabled and self._client is not None:
            try:
                kwargs: dict = {"model": self._model, "input": [t for _, t in pending]}
                if self._dimensions:
                    kwargs["dimensions"] = self._dimensions
                response = self._client.embeddings.create(**kwargs)
                # `data` không được đảm bảo theo thứ tự; API trả `index` để ghép lại.
                by_index = {item.index: item.embedding for item in response.data}
                if len(by_index) != len(pending):
                    raise ValueError("embedding response size mismatch")
                for slot, (i, text) in enumerate(pending):
                    emb = Embedding(vector=tuple(_normalize(list(by_index[slot]))), space=self.space)
                    self._store(text, emb)
                    results[i] = emb
                pending = []
            except Exception:  # noqa: BLE001 - xem docstring lớp
                logger.warning("embedding request failed; falling back to hashing", exc_info=True)

        for i, text in pending:
            results[i] = self._fallback.embed(text)
        return [r if r is not None else self._fallback.embed(texts[i]) for i, r in enumerate(results)]


_PROVIDER: HashingEmbeddingProvider | OpenAIEmbeddingProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def get_provider() -> HashingEmbeddingProvider | OpenAIEmbeddingProvider:
    """Provider dùng chung, dựng theo settings (nhớ lại giữa các lượt để tái dùng cache/HTTP)."""
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is not None:
            return _PROVIDER
        from src.config import get_settings

        cfg = get_settings().embedding_config()
        if cfg is None:
            _PROVIDER = HashingEmbeddingProvider()
        else:
            _PROVIDER = OpenAIEmbeddingProvider(
                api_key=str(cfg["api_key"]),
                model=str(cfg["model"]),
                base_url=str(cfg["base_url"]) or None,
                timeout=float(cfg["timeout"]),
                dimensions=int(cfg["dimensions"]),
                similarity_floor=float(cfg["similarity_floor"]),
            )
        return _PROVIDER


def set_provider(provider: HashingEmbeddingProvider | OpenAIEmbeddingProvider | None) -> None:
    """Inject provider (test/eval). None = dựng lại từ settings ở lần dùng sau."""
    global _PROVIDER
    with _PROVIDER_LOCK:
        _PROVIDER = provider


def cosine(a: Embedding, b: Embedding) -> float:
    """Độ tương đồng ĐÃ HIỆU CHỈNH ∈ [0,1] giữa hai vector CÙNG không gian.

    Khác không gian → 0.0: không so sánh được, và im lặng cho điểm là cách hỏng tệ nhất.

    Cả hai vector đã chuẩn hoá đơn vị nên tích vô hướng CHÍNH LÀ cosine ∈ [-1, 1]. Kết quả
    được co giãn quanh NGƯỠNG NỀN của không gian (xem `_SPACE_FLOOR`) để "không liên quan"
    luôn quy về 0 dù model nhúng là gì — nhờ đó ngưỡng liên quan ở tầng trên không phải
    tinh chỉnh lại mỗi lần đổi model. Hashing có nền 0 nên đường offline không đổi.
    """
    if not a.vector or not b.vector or a.space != b.space or len(a.vector) != len(b.vector):
        return 0.0
    dot = sum(x * y for x, y in zip(a.vector, b.vector, strict=False))
    floor = _SPACE_FLOOR.get(a.space, 0.0)
    calibrated = (dot - floor) / (1.0 - floor) if floor < 1.0 else 0.0
    return max(0.0, min(1.0, calibrated))
