"""Knowledge RAG (spec §27, P5) — tri thức domain/thiết bị, tách khỏi Memory + Preference.

Kiểm: KB seed grounded từ registry; truy hồi đúng chủ đề; không bịa khi thiếu tri thức;
pipeline định tuyến câu hỏi kiến thức sang RAG mà KHÔNG nuốt lệnh/câu hỏi trạng thái.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.knowledge.rag import (
    KnowledgeBase,
    answer_knowledge,
    build_default_knowledge_base,
)
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.perception.context_builder import build_perception
from src.agent.pipeline import PipelineDeps, run_planning
from src.agent.preference.preference_store import PreferenceStore
from src.core.reasoning import FakeReasoningModel

_NOW = datetime(2026, 8, 15, 20, 0, tzinfo=UTC)


def _nu(text: str):
    nu, _ctx, _p = build_perception(text, now=_NOW)
    return nu


@pytest.fixture
def kb() -> KnowledgeBase:
    return build_default_knowledge_base()


def test_kb_seeded_from_registry_is_grounded(kb):
    """KB dựng từ registry: có doc cho các loại thiết bị thật, không rỗng (§27/§38)."""
    assert len(kb) > 5
    # Doc capability của điều hoà phải nêu đúng dải nhiệt độ domain, không bịa.
    hits = kb.retrieve("điều hoà đặt được bao nhiêu độ", device_types=frozenset({"air_conditioner"}))
    assert hits and "16–30" in " ".join(d.text for d, _ in hits)


def test_retrieves_maintenance_topic(kb):
    ans = answer_knowledge(_nu("máy lọc không khí bao lâu vệ sinh một lần"), kb)
    assert ans is not None and ("vệ sinh" in ans or "màng lọc" in ans)


def test_retrieves_security_topic(kb):
    ans = answer_knowledge(_nu("mở khoá cửa có an toàn không"), kb)
    assert ans is not None and ("an ninh" in ans or "xác nhận" in ans)


def test_scopes_by_mentioned_device_type(kb):
    """Hỏi về bình nóng lạnh → không trả tri thức của máy lọc (scope theo loại thiết bị)."""
    ans = answer_knowledge(_nu("bình nóng lạnh cần vệ sinh gì"), kb)
    assert ans is not None and "cặn" in ans and "màng lọc" not in ans


def test_no_knowledge_returns_none(kb):
    """Không có tri thức phù hợp → None (không bịa; tầng trên fallback câu chung)."""
    assert answer_knowledge(_nu("mạng wifi nhà mình băng thông thế nào"), kb) is None


def test_empty_kb_returns_none():
    assert answer_knowledge(_nu("điều hoà vệ sinh thế nào"), KnowledgeBase()) is None


def _deps() -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), model_client=FakeReasoningModel(),
    )


def _run(msg: str):
    return run_planning(
        {
            "conversation_id": "kb", "user_id": "u", "user_message": msg,
            "speaker_location": "Phòng khách",
        },
        _deps(),
    )


def test_pipeline_routes_knowledge_question_to_rag():
    out = _run("máy lọc không khí bao lâu vệ sinh một lần")
    assert out["final_status"] == "answered" and out["route_kind"] == "answer"
    assert "vệ sinh" in out["reply"]


def test_pipeline_does_not_hijack_commands():
    """Lệnh điều khiển (goal ≠ None) KHÔNG bị RAG nuốt — guard `u.goal is None` bảo vệ lệnh."""
    out = _run("bật điều hoà phòng khách")
    assert out.get("route_kind") != "answer"
    assert out.get("semantic_goal") is not None


def test_pipeline_state_query_not_sent_to_rag():
    """Câu hỏi TRẠNG THÁI đọc snapshot sống, không đi RAG (P5: memory/state ≠ knowledge)."""
    out = _run("điều hoà phòng khách đang bật không")
    assert out["final_status"] == "answered"
    assert "đang" in out["reply"]  # mô tả trạng thái, không phải tri thức domain
