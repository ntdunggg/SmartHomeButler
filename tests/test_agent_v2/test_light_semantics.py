"""Ánh sáng vs nhiệt độ phòng — hai va chạm ngữ nghĩa cụ thể, một luật chung cho mỗi cái.

1. Bỏ dấu làm "mắt" trùng "mát" (nhiệt độ). Khớp accent-folded chỉ được phép ở đoạn
   người dùng THỰC SỰ gõ không dấu, nên "dịu mắt" không còn kích hoạt cue nhiệt độ.
2. `illumination` gồm cả độ sáng lẫn nhiệt màu. "ánh sáng ấm áp hơn" phải ra action
   color_temp trên ĐÈN, không phải chỉnh nhiệt độ phòng bằng điều hoà.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.pipeline import PipelineDeps
from src.agent.text import Pattern, TextView
from src.core.reasoning import FakeReasoningModel
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
_TEMPERATURE_CUE = Pattern(r"\b(nóng|nong|lạnh|lanh|mát|mat|ấm hơn|am hon)\b")


def _plan(message: str, conversation_id: str):
    model = FakeReasoningModel()
    result = reason(
        message=message,
        conversation_id=conversation_id,
        user_id="test:light-semantics",
        role="owner",
        now=_NOW,
        deps=PipelineDeps(model_client=model),
        model_client=model,
    )
    plan = result.candidate_plan
    return [
        (a.device_id, a.capability.value, a.action.value, dict(a.params))
        for a in (plan.actions if plan else [])
    ]


@pytest.mark.parametrize(
    ("text", "matches"),
    [
        ("cho phòng khách dịu mắt hơn", False),  # "mắt" ≠ "mát"
        ("mở tất cả đèn", False),  # cue nhiệt độ không dính vào câu này
        ("cho phòng mát hơn", True),  # có dấu, đúng từ
        ("cho phong mat hon", True),  # không dấu — fold vẫn phục vụ đúng đối tượng
    ],
)
def test_folded_match_only_applies_where_the_writer_dropped_diacritics(text: str, matches: bool):
    assert bool(_TEMPERATURE_CUE.search(TextView.of(text))) is matches


def test_textview_has_does_not_confuse_homographs_after_folding():
    view = TextView.of("mở tất cả đèn")
    assert view.has("tất cả")
    assert not view.has("tắt")


def test_warm_light_adjusts_colour_temperature_not_room_temperature():
    actions = _plan("Cho ánh sáng Phòng ngủ bố mẹ ấm áp hơn.", "warm-light")

    assert actions, "câu nêu rõ ánh sáng + hướng phải hành động được"
    assert {capability for _d, capability, _a, _p in actions} == {"color_temp"}
    assert all(device.startswith("den_") for device, *_ in actions)
    # Ấm hơn = Kelvin THẤP hơn mức hiện tại, và vẫn trong biên registry.
    for _device, _capability, _action, params in actions:
        assert 2200 <= params["color_temp"] < 4350


def test_cool_light_moves_colour_temperature_the_other_way():
    actions = _plan("Cho ánh sáng phòng khách trắng hơn.", "cool-light")

    assert actions
    assert {capability for _d, capability, _a, _p in actions} == {"color_temp"}
    for _device, _capability, _action, params in actions:
        assert 4350 < params["color_temp"] <= 6500


def test_room_warmth_without_a_lighting_noun_still_targets_the_air_conditioner():
    actions = _plan("Phòng ngủ bố mẹ lạnh quá.", "room-warmth")

    assert actions
    assert {capability for _d, capability, _a, _p in actions} == {"temperature"}
    assert all("dieu_hoa" in device for device, *_ in actions)
