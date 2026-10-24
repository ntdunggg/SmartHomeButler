"""Câu hỏi XÁC NHẬN đuôi "đúng không?/phải không?" phân loại là hỏi trạng thái, không phải lệnh.

Chốt fix TS-006: đuôi nghi vấn chứa "không"/"chưa" không được đẩy câu sang turn_off/cancel; thiết
bị nêu trong câu phải vào Salience Stack (kind=query) để "bật lên"/"tắt nó" lượt sau bám đúng.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.pipeline import PipelineDeps
from src.agent.text import TextView
from src.context.salience import SalienceStack
from src.nlu.normalizer import analyze
from src.nlu.ontology import UtteranceType
from src.nlu.understanding import _classify, _extract_params
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    [
        "Máy rửa bát đang tắt đúng không?",
        "Điều hoà bật rồi phải không?",
        "Đèn còn sáng đúng chứ?",
        "Cửa khoá chưa nhỉ?",
    ],
)
def test_duoi_xac_nhan_la_cau_hoi_khong_phai_lenh(text):
    nu = analyze(text)
    view = TextView(raw=nu.normalized, folded=nu.folded)
    utype, action_hint, _ = _classify(nu, view, _extract_params(view))
    assert utype == UtteranceType.INFORMATION_QUESTION
    assert action_hint is None


@pytest.mark.parametrize(
    "text",
    ["Tắt đèn phòng khách.", "Bật điều hoà phòng con.", "Giảm âm lượng loa xuống 20."],
)
def test_lenh_that_van_la_lenh(text):
    nu = analyze(text)
    view = TextView(raw=nu.normalized, folded=nu.folded)
    utype, action_hint, _ = _classify(nu, view, _extract_params(view))
    assert utype == UtteranceType.DEVICE_COMMAND
    assert action_hint is not None


def test_ts006_cau_hoi_xac_nhan_day_thiet_bi_vao_salience_va_bat_lai():
    """End-to-end: hỏi "máy rửa bát … đúng không?" đẩy máy rửa bát vào stack → "Bật lên" bám đúng."""
    deps = PipelineDeps()
    reason(message="Bật đèn bếp.", conversation_id="ts6", now=_NOW, deps=deps)
    r_q = reason(message="Máy rửa bát đang tắt đúng không?", conversation_id="ts6", now=_NOW, deps=deps)
    assert r_q.outcome == "answer"  # là câu hỏi, không thực thi
    stack = SalienceStack.from_list(deps.ledger_store.load("ts6").salience)
    assert stack.top().device_id == "may_rua_bat" and stack.top().kind == "query"
    r = reason(message="Bật lên.", conversation_id="ts6", now=_NOW, deps=deps)
    assert r.outcome == "candidate_plan"
    devices = [a.device_id for a in r.candidate_plan.actions]
    assert "may_rua_bat" in devices and "den_bep" not in devices
