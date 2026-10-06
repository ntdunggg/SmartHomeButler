"""FR-16 generalization (spec §16, §59-61) — LLM author open-ended goal + goldenset.

Pipeline mới AUTHOR mục tiêu open-ended cho câu MỚI LẠ (không inject goal) rồi lập kế
hoạch — chạy offline với FakeReasoningModel (author từ cue tiếng Việt). Số goldenset
thật cần model live; ở đây kiểm WIRING + regression floor offline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.evaluation.goldenset_runner import run_goldenset
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, run_turn
from src.agent.preference.preference_store import PreferenceStore
from src.core.reasoning import FakeReasoningModel

GOLDENSET = "src/evaluation/goldensets/goldenset_ambiguity.jsonl"


def _deps():
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), model_client=FakeReasoningModel(),
    )


def _run(msg, **kw):
    state = {
        "conversation_id": msg, "user_id": "u", "user_message": msg,
        "now": datetime(2026, 8, 15, 20, 0, tzinfo=UTC), "speaker_location": "Phòng khách",
    }
    state.update(kw)
    return run_turn(state, _deps())


@pytest.mark.parametrize(
    "utterance,expected_agent",
    [
        ("tối quá", "shutter"),      # illumination (rèm ban ngày mặc định có nắng)
        ("lạnh quá", "ac"),          # thermal warmer
        ("nóng nực quá", "ac"),      # thermal cooler
        ("phòng bí quá khó thở", "purifier"),  # air quality
    ],
)
def test_unseen_phrase_authors_goal_and_plans(utterance, expected_agent):
    """KHÔNG inject goal — LLM(Fake) author từ câu, pipeline lập kế hoạch đúng chiều."""
    out = _run(utterance, live_device_states={"den_chum_phong_khach": {"power": "off", "brightness": 0}, "dieu_hoa_phong_khach": {"power": "off", "temperature": 26}})
    sel = out.get("selected_plan")
    assert out["final_status"] in ("SUCCESS", "NO_OP")
    assert sel is not None and expected_agent in sel.source_agents


def test_explicit_command_still_deterministic():
    out = _run("bật đèn phòng khách")
    assert out["final_status"] == "SUCCESS"
    assert "lighting" in out["selected_plan"].source_agents


def test_ambiguous_command_clarifies_not_noop():
    """"tắt đèn" (không rõ đèn nào) → hỏi lại, KHÔNG sinh kế hoạch rỗng (FR-02/FR-04)."""
    out = _run("tắt đèn", speaker_location=None)
    assert out["final_status"] == "clarification_required"


def test_goldenset_regression_floor():
    """Goldenset ambiguity offline (FakeReasoningModel) — floor regression, không phải số live."""
    report = run_goldenset(GOLDENSET, deps=_deps())
    # Offline ~0.87; đặt floor an toàn để bắt hồi quy lớn (số THẬT đo bằng model live).
    assert report.accuracy >= 0.8, report.summary()
    # Nhánh mơ hồ phải rất cao (ambiguous → clarify).
    passed, total = report.by_category["cau_mo_ho"]
    assert passed / total >= 0.9
