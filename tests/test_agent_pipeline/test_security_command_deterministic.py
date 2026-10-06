"""C (§an ninh): lệnh AN NINH (khoá/camera cửa) phải được quyết TẤT ĐỊNH bởi rule tier —
KHÔNG phụ thuộc LLM — và kế hoạch phải yêu cầu xác nhận HITL trước khi thực thi.

Bối cảnh: đường an ninh không được lệ thuộc vào LLM (có dao động giữa các lần chạy + tốn
3–10s). `understand()` gọi KHÔNG kèm model_client ⇒ nếu nó tự dựng được goal thì quyết định
an ninh là tất định. HITL (requires_confirmation) là chốt chặn thực thi mù (đối chiếu
core/permissions.py: thiết bị SECURITY luôn requires_approval).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.nlu.normalizer import analyze
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import RuntimeContext
from src.nlu.understanding import understand
from src.planning.plan_validator import synthesize_direct_plan

_ROOMS = ["Phòng khách", "Phòng bếp", "Phòng ngủ bố mẹ", "Phòng ngủ con"]


def _ctx() -> RuntimeContext:
    return RuntimeContext(now=datetime(2026, 8, 5, tzinfo=UTC), rooms=_ROOMS)


@pytest.mark.parametrize(
    "utt,expected_dev",
    [
        ("mở khoá cửa chính", "khoa_cua_chinh"),
        ("khoá cửa chính lại", "khoa_cua_chinh"),
        ("tắt camera cửa chính", "camera_cua_chinh"),
    ],
)
def test_security_command_decided_deterministically(utt: str, expected_dev: str) -> None:
    """Rule tier tự bắt lệnh an ninh (không truyền model_client) → không lệ thuộc LLM."""
    u = understand(analyze(utt), _ctx())  # KHÔNG có model_client
    assert u.goal is not None, "rule tier phải tự quyết lệnh an ninh, không nhờ LLM"
    assert u.utterance_type == UtteranceType.DEVICE_COMMAND
    assert expected_dev in u.goal.target_device_ids


@pytest.mark.parametrize("utt", ["mở khoá cửa chính", "khoá cửa chính lại", "tắt camera cửa chính"])
def test_security_plan_requires_hitl_confirmation(utt: str) -> None:
    """Kế hoạch trên thiết bị an ninh PHẢI requires_confirmation → không thực thi mù."""
    u = understand(analyze(utt), _ctx())
    assert u.goal is not None
    plan = synthesize_direct_plan(u.goal)
    assert plan.actions, "phải có hành động cụ thể trên thiết bị an ninh"
    assert plan.requires_confirmation is True, "lệnh an ninh phải qua HITL trước khi thực thi"
