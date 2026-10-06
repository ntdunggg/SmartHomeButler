"""Tầng nhúng cho ký ức dài hạn (§23) — không gian vector, hiệu chỉnh, suy giảm duyên dáng.

Ranh giới kiến trúc được khẳng định ở đây: embedding chỉ phục vụ TRUY HỒI ký ức dài hạn.
Nó không đụng tới Requirement Ledger — trạng thái hội thoại vẫn có cấu trúc và tất định.
"""

from __future__ import annotations

import pytest

from src.agent.memory import embeddings as emb
from src.agent.memory.embeddings import (
    HASHING_SPACE,
    Embedding,
    HashingEmbeddingProvider,
    OpenAIEmbeddingProvider,
    cosine,
    get_provider,
    register_space_floor,
)


def test_offline_runs_use_the_deterministic_hashing_provider():
    """LLM_DISABLED (conftest) → không gọi mạng, và kết quả lặp lại được."""
    provider = get_provider()
    assert isinstance(provider, HashingEmbeddingProvider)
    assert provider.embed("bật đèn bếp") == provider.embed("bật đèn bếp")


def test_identical_text_is_maximally_similar():
    p = HashingEmbeddingProvider()
    assert cosine(p.embed("mở rèm phòng khách"), p.embed("mở rèm phòng khách")) == pytest.approx(1.0)


def test_vectors_from_different_spaces_never_compare():
    """Ký ức nhúng bằng hashing KHÔNG được so với truy vấn nhúng bằng API.

    Nếu so, điểm ra 0 một cách âm thầm và truy hồi ngữ nghĩa tắt mà không ai biết —
    nhãn không gian làm việc đó thành tường minh thay vì một lỗi im lặng.
    """
    a = Embedding(vector=(1.0, 0.0), space="space-a")
    b = Embedding(vector=(1.0, 0.0), space="space-b")
    assert cosine(a, b) == 0.0
    assert cosine(a, Embedding(vector=(1.0, 0.0), space="space-a")) == pytest.approx(1.0)


def test_empty_text_produces_a_non_comparable_vector():
    p = HashingEmbeddingProvider()
    assert cosine(p.embed(""), p.embed("bật đèn")) == 0.0


def test_similarity_floor_maps_unrelated_to_zero_and_keeps_related_high():
    """Không gian có nền cao (embedding thật) phải được quy về [0,1] quanh nền đó.

    Không có bước này, mọi ký ức đều vượt `min_score` của event_retriever và bộ lọc
    liên quan (§26) tắt âm thầm.
    """
    register_space_floor("floored-space", 0.30)
    at_floor = 0.30
    related = 0.78
    # Hai vector 1 chiều có tích vô hướng đúng bằng giá trị mong muốn.
    def _v(x: float) -> Embedding:
        return Embedding(vector=(x,), space="floored-space")

    assert cosine(_v(1.0), _v(at_floor)) == pytest.approx(0.0)
    assert cosine(_v(1.0), _v(related)) == pytest.approx((related - 0.30) / 0.70)
    assert cosine(_v(1.0), _v(0.2)) == 0.0  # dưới nền → không liên quan


def test_hashing_space_keeps_a_zero_floor_so_offline_scores_do_not_shift():
    p = HashingEmbeddingProvider()
    raw = sum(x * y for x, y in zip(p.embed("bật đèn bếp").vector, p.embed("bật đèn phòng").vector))
    assert cosine(p.embed("bật đèn bếp"), p.embed("bật đèn phòng")) == pytest.approx(raw)
    assert emb._SPACE_FLOOR[HASHING_SPACE] == 0.0


class _BrokenClient:
    class embeddings:  # noqa: N801 - bắt chước hình dạng SDK
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("embedding service down")


def test_api_failure_degrades_to_hashing_instead_of_breaking_the_turn(monkeypatch):
    provider = OpenAIEmbeddingProvider(api_key="k", model="text-embedding-3-small", dimensions=256)
    provider._client = _BrokenClient()
    provider._disabled = False

    result = provider.embed("trời nóng quá")

    assert result.space == HASHING_SPACE
    assert len(result.vector) == 64


def test_batch_embedding_reassembles_by_response_index():
    """`data` của API không đảm bảo thứ tự — ghép lại theo `index`, không theo vị trí."""

    class _Item:
        def __init__(self, index, embedding):
            self.index, self.embedding = index, embedding

    class _Client:
        class embeddings:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                n = len(kwargs["input"])
                # Trả ĐẢO NGƯỢC thứ tự để lộ lỗi nếu code ghép theo vị trí.
                items = [_Item(i, [1.0 if j == i else 0.0 for j in range(n)]) for i in range(n)]
                return type("R", (), {"data": list(reversed(items))})()

    provider = OpenAIEmbeddingProvider(api_key="k", model="text-embedding-3-small", dimensions=0)
    provider._client = _Client()
    provider._disabled = False

    out = provider.embed_many(["một", "hai", "ba"])

    assert [list(e.vector) for e in out] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    assert all(e.space == provider.space for e in out)


def test_repeated_text_is_embedded_once(monkeypatch):
    calls = {"n": 0}

    class _Client:
        class embeddings:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                calls["n"] += 1
                items = [type("I", (), {"index": i, "embedding": [1.0, 0.0]})() for i in range(len(kwargs["input"]))]
                return type("R", (), {"data": items})()

    provider = OpenAIEmbeddingProvider(api_key="k", model="text-embedding-3-small", dimensions=0)
    provider._client = _Client()
    provider._disabled = False

    provider.embed("cùng một câu")
    provider.embed("cùng một câu")

    assert calls["n"] == 1


def test_default_api_floor_matches_the_configured_one():
    """Nền mặc định của provider và của settings phải là MỘT — lệch nhau thì hiệu chỉnh
    ở production khác với hiệu chỉnh đã đo, một cách âm thầm."""
    from src.config import Settings

    assert Settings().embedding_similarity_floor == emb.DEFAULT_API_SIMILARITY_FLOOR
