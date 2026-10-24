"""Regression E2E cho Context-Transducer: ordinal, deferred, bare reactivation, anaphora."""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.pipeline import PipelineDeps
from src.services.pipeline_bridge import reason

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


def _run(messages: list[str]):
    deps = PipelineDeps()
    result = None
    for message in messages:
        result = reason(message=message, conversation_id="ctx-regression", now=NOW, deps=deps)
    return result


def _actions(result):
    return {
        action.device_id: (action.capability.value, action.action.value, dict(action.params))
        for action in (result.candidate_plan.actions if result.candidate_plan else [])
    }


def test_ordinal_resolves_against_ordered_previous_group():
    result = _run([
        "Bật điều hoà phòng khách và TV phòng khách.",
        "Tắt cái thứ hai.",
    ])
    assert result.outcome == "candidate_plan"
    assert set(_actions(result)) == {"tv_phong_khach"}


def test_query_after_dismissal_becomes_new_salience_anchor():
    result = _run([
        "Bật loa phòng khách.",
        "Thôi chuyện cái loa để sau. Camera cửa chính đang thế nào?",
        "Tắt nó.",
    ])
    assert result.outcome == "candidate_plan"
    assert set(_actions(result)) == {"camera_cua_chinh"}


def test_bare_unlock_reactivation_uses_salient_lock():
    result = _run([
        "Khóa cửa phòng bố mẹ lại.",
        "Mở khóa lại đi.",
    ])
    assert result.outcome == "candidate_plan"
    assert _actions(result)["khoa_cua_phong_bo_me"][:2] == ("lock", "unlock")


def test_deferred_description_resolves_deferred_device_not_latest_command():
    result = _run([
        "Máy rửa bát đang tắt đúng không?",
        "Để máy rửa bát lát nữa.",
        "Tắt đèn bếp.",
        "Giờ bật cái máy mình vừa nói để lát nữa ấy.",
    ])
    assert result.outcome == "candidate_plan"
    assert set(_actions(result)) == {"may_rua_bat"}


def test_cancelling_a_unique_deferred_task_removes_its_stale_anchor():
    deps = PipelineDeps()
    cid = "cancel-unique-deferred"

    assert reason(
        message="Để máy rửa bát lát nữa.", conversation_id=cid, now=NOW, deps=deps
    ).outcome == "answer"

    cancelled = reason(
        message="Thôi, bỏ việc đó đi.", conversation_id=cid, now=NOW, deps=deps
    )
    assert cancelled.outcome == "answer"
    assert not deps.ledger_store.load(cid).salience

    reactivation = reason(
        message="Giờ bật cái máy mình vừa nói để lát nữa ấy.", conversation_id=cid, now=NOW, deps=deps
    )
    assert reactivation.outcome == "clarification"
    assert not _actions(reactivation)


def test_cancelling_an_ambiguous_deferred_task_keeps_all_anchors():
    deps = PipelineDeps()
    cid = "cancel-ambiguous-deferred"
    for message in ("Để máy rửa bát lát nữa.", "Để máy lọc không khí phòng khách lát nữa."):
        assert reason(message=message, conversation_id=cid, now=NOW, deps=deps).outcome == "answer"

    cancelled = reason(
        message="Không cần việc đó nữa.", conversation_id=cid, now=NOW, deps=deps
    )
    assert cancelled.outcome == "answer"
    assert cancelled.reply == "Bạn muốn bỏ việc đã hoãn nào ạ?"
    assert {
        entry["device_id"] for entry in deps.ledger_store.load(cid).salience if entry["kind"] == "deferred"
    } == {"may_rua_bat", "may_loc_phong_khach"}


def test_regular_cancellation_does_not_consume_an_unrelated_deferred_task():
    deps = PipelineDeps()
    cid = "regular-cancellation-keeps-deferred"
    assert reason(
        message="Để máy rửa bát lát nữa.", conversation_id=cid, now=NOW, deps=deps
    ).outcome == "answer"
    assert reason(
        message="Bật TV phòng khách.", conversation_id=cid, now=NOW, deps=deps
    ).outcome == "candidate_plan"

    cancelled = reason(message="Bỏ đi.", conversation_id=cid, now=NOW, deps=deps)
    assert cancelled.outcome == "cancelled"
    assert any(
        entry["device_id"] == "may_rua_bat" and entry["kind"] == "deferred"
        for entry in deps.ledger_store.load(cid).salience
    )


def test_absolute_fan_level_is_set_on_referenced_purifier():
    result = _run([
        "Bật máy lọc phòng con.",
        "Tăng quạt của nó lên mức 3.",
    ])
    assert result.outcome == "candidate_plan"
    assert _actions(result)["may_loc_phong_con"] == ("fan_speed", "set", {"level": 3})


def test_numeric_anaphora_skips_device_just_turned_off():
    result = _run([
        "Bật TV phòng khách.",
        "Bật loa phòng khách.",
        "TV âm lượng 20.",
        "Loa âm lượng 35.",
        "Tắt TV đi.",
        "Giảm nó xuống 25.",
    ])
    assert result.outcome == "candidate_plan"
    assert _actions(result)["loa_phong_khach"] == ("volume", "set", {"percent": 25})
    assert "tv_phong_khach" not in _actions(result)
