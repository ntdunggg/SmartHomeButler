"""Ngoài phạm vi nhà thông minh (§4) — regression cho bug: câu không liên quan ("viết cho tôi
một bài thơ") bị agent hỏi lại "phòng nào ạ?" thay vì từ chối + định hướng lại.

Root cause (semantic_resolver.py + pipeline.py _understand): khi goal không actionable (không
device/outcome), pipeline luôn fallback hỏi "room" bất kể goal có thực sự cần phòng hay không.
Fix: `_is_out_of_domain` (pipeline.py) chặn TRƯỚC mọi nhánh clarify khi model đã tự gắn nhãn
UNKNOWN/SOCIAL_UTTERANCE cho một goal rỗng — dấu hiệu request không liên quan nhà thông minh,
không phải "thiếu phòng". `FakeReasoningModel` (offline) không mô phỏng nhãn này nên test ở đây
tiêm một stub model tối giản chỉ cho schema SemanticGoal, phần còn lại không cần LLM vì goal
rỗng không đi tới lập kế hoạch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, run_turn
from src.agent.preference.preference_store import PreferenceStore
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import SemanticGoal


class _OutOfDomainStubModel:
    """Model cố định trả một SemanticGoal ngoài-lệnh (như production LLM làm theo prompt
    src/nlu/prompting/semantic.py §"tự nhiên thấy nhớ nhà" → SOCIAL_UTTERANCE, outcomes rỗng)."""

    def __init__(self, utterance_type: UtteranceType) -> None:
        self._utterance_type = utterance_type

    def structured_generate_sync(self, prompt: str, output_schema: type, **kwargs: Any) -> Any:
        if output_schema is SemanticGoal:
            return SemanticGoal(
                raw_utterance=str(kwargs.get("context", {}).get("utterance", prompt)),
                goal_description="",
                utterance_type=self._utterance_type,
                confidence=0.9,
                desired_outcomes=[],
            )
        return output_schema.model_construct()

    async def structured_generate(self, prompt: str, output_schema: type, **kwargs: Any) -> Any:
        return self.structured_generate_sync(prompt, output_schema, **kwargs)


def _deps(model: Any) -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), model_client=model,
    )


def _turn(deps: PipelineDeps, msg: str) -> dict:
    return run_turn(
        {
            "conversation_id": "ood-1", "user_id": "user_A", "user_message": msg,
            "now": datetime(2026, 8, 22, 20, 0, tzinfo=UTC),
        },
        deps,
    )


@pytest.mark.parametrize("utype", [UtteranceType.SOCIAL_UTTERANCE, UtteranceType.UNKNOWN])
def test_out_of_domain_request_is_refused_not_asked_room(utype) -> None:
    out = _turn(_deps(_OutOfDomainStubModel(utype)), "viết cho tôi một bài thơ")
    assert out.get("final_status") == "answered"
    assert not out.get("requires_clarification")
    reply = out.get("reply", "")
    assert "phòng nào" not in reply.lower()
    assert "nhà thông minh" in reply.lower() or "thiết bị" in reply.lower()


def test_empty_environment_goal_still_asks_room_not_refused() -> None:
    """Không hồi quy: goal RỖNG nhưng domain-relevant (ENVIRONMENT_REQUEST, vd bình luận thời
    tiết) vẫn phải hỏi lại như cũ — chỉ UNKNOWN/SOCIAL_UTTERANCE mới bị từ chối."""
    out = _turn(_deps(_OutOfDomainStubModel(UtteranceType.ENVIRONMENT_REQUEST)), "hôm nay trời đẹp thật")
    assert out.get("requires_clarification") is True
    assert "nhà thông minh" not in (out.get("reply") or out.get("clarification_question") or "").lower()


def test_in_domain_command_unaffected_by_out_of_domain_gate() -> None:
    """Không hồi quy: lệnh tường minh vẫn chạy bình thường qua FakeReasoningModel (không đụng
    tới model client vì lệnh tất định không cần author LLM)."""
    out = _turn(_deps(FakeReasoningModel()), "bật đèn phòng khách")
    assert out.get("final_status") in ("SUCCESS", "NO_OP", "WAITING_FOR_USER_APPROVAL")
    assert not out.get("requires_clarification")


@pytest.mark.parametrize(
    "msg",
    [
        "thủ đô nước Pháp là gì",
        "ai là tổng thống Mỹ",
        "1 cộng 1 bằng mấy",
        "dịch câu này sang tiếng Anh",
    ],
)
def test_cau_hoi_ngoai_domain_bi_tu_choi_du_model_gan_nhan_khac(msg: str) -> None:
    """Hàng rào không được phụ thuộc vào việc model chọn đúng MỘT trong HAI nhãn.

    Đo thật §2026-08-30: "thủ đô nước Pháp là gì" ra INFORMATION_QUESTION (không phải UNKNOWN)
    nên thoát sạch hàng rào và rơi xuống "Bạn muốn mình làm ở Phòng khách, Phòng bếp, Phòng ngủ
    bố mẹ, hay Phòng ngủ con ạ?". Với CÂU HỎI có tín hiệu tất định thay cho nhãn: câu hỏi về nhà
    mình luôn neo vào thiết bị/phòng/cảm biến hoặc hỏi phạm vi cả nhà (`_asks_about_state`).
    """
    out = _turn(_deps(_OutOfDomainStubModel(UtteranceType.INFORMATION_QUESTION)), msg)

    assert out.get("final_status") == "answered"
    assert not out.get("requires_clarification")
    assert "phòng nào" not in (out.get("reply") or "").lower()
    assert "nhà thông minh" in (out.get("reply") or "").lower()


@pytest.mark.parametrize(
    "msg",
    [
        "trong nhà có bao nhiêu thiết bị đang bật",
        "còn thiết bị nào quên tắt không",
        "cả nhà có gì đang chạy không",
    ],
)
def test_cau_hoi_ve_nha_khong_bi_tu_choi_oan(msg: str) -> None:
    """Chiều ngược lại: câu hỏi NEO vào nhà (dù không nêu tên thiết bị) không được coi là
    ngoài domain — nó đọc được từ snapshot sống."""
    out = _turn(_deps(_OutOfDomainStubModel(UtteranceType.INFORMATION_QUESTION)), msg)

    assert "nhà thông minh thôi" not in (out.get("reply") or "").lower()


@pytest.mark.parametrize("utype", [UtteranceType.ENVIRONMENT_REQUEST, UtteranceType.ROUTINE_INTENT])
def test_yeu_cau_that_thieu_phong_van_duoc_hoi_lai(utype) -> None:
    """Luật mỏ neo CỐ Ý chỉ áp cho câu hỏi. "ngột ngạt quá" / "tôi sắp về nhà" cũng không neo
    vào thiết bị/phòng nào, nhưng là yêu cầu THẬT chỉ thiếu phòng — hỏi lại mới đúng, từ chối
    là sai. Đây là ranh giới dễ vỡ nhất của bản vá này."""
    out = _turn(_deps(_OutOfDomainStubModel(utype)), "ngột ngạt quá")

    assert "nhà thông minh thôi" not in (out.get("reply") or "").lower()
