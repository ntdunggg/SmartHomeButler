"""Chỉ số rò-rỉ ngữ cảnh của live-semantic eval phải đo đúng thứ nó gọi tên.

Bản cũ là `device_ok` đảo dấu: mọi thiết bị ngoài allowed-set đều bị gọi là "stale", nên
cổng phát hành mang tên rò-rỉ thực chất đang chấm độ khớp LỚP thiết bị. Tách đôi:

  - RÒ RỈ = chạm ra ngoài phòng mong đợi, hoặc chạm lại đích của một lượt đã bị thay thế.
  - LỆCH LỚP = đúng phòng, chưa từng là đích lượt trước, chỉ chọn sai loại thiết bị.
"""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.eval_live_semantic import run_case
from src.core.reasoning import FakeReasoningModel

_NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)


def _case(*turns: str, room: str, target: str, adjustment: str) -> dict:
    return {
        "id": "synthetic",
        "category": "negation_cancel_correction",
        "messages": [{"role": "user", "content": t} for t in turns],
        "expected": {
            "decision": "PROCEED", "room": room, "target": target,
            "adjustment": adjustment, "constraints": [],
            "should_use_context": False, "memory_expectation": "NONE",
        },
    }


def test_excluded_device_does_not_leak_from_the_superseded_turn():
    """"Loa thì thôi" keeps the TV goal and removes the superseded speaker target."""
    evaluation = run_case(
        _case("Bật TV và loa phòng khách.", "Loa thì thôi.",
              room="phòng khách", target="tv", adjustment="turn_on"),
        FakeReasoningModel(), now=_NOW,
    )

    assert evaluation.exception is None
    assert evaluation.outcome == "candidate_plan"
    assert evaluation.stale_target_leakage == 0
    assert evaluation.device_class_mismatch == 0


def test_a_device_never_targeted_before_is_a_class_mismatch_not_leakage():
    """Chọn sai LOẠI thiết bị trong ĐÚNG phòng là chất lượng planner, không phải rò ngữ cảnh."""
    evaluation = run_case(
        _case("Đóng rèm phòng ngủ.", "Nhưng chừa một khe nhỏ.", "Thêm chút ánh sáng tự nhiên.",
              room="phòng ngủ", target="rèm", adjustment="increase_opening"),
        FakeReasoningModel(), now=_NOW,
    )

    assert evaluation.exception is None
    assert evaluation.device_class_mismatch == 1
    assert evaluation.stale_target_leakage == 0


def test_a_single_turn_case_can_never_be_scored_as_carry_over():
    """Không có lượt trước thì không có gì để 'sót lại' — chỉ còn tiêu chí sai phòng."""
    evaluation = run_case(
        _case("Bật đèn phòng bếp.", room="phòng bếp", target="đèn", adjustment="turn_on"),
        FakeReasoningModel(), now=_NOW,
    )

    assert evaluation.exception is None
    assert evaluation.stale_target_leakage == 0
