"""Context-perturbation eval (spec §63) — LIVE-STATE perturbations manifest ở EXECUTION nên
kiểm qua run_turn (offline, tất định), khác các perturbation reason-level (room-swap/paraphrase/
multi-turn) chấm bằng goldenset live.

Bất biến kiểm ở đây: cùng một lệnh, khi trạng thái LIVE đổi thì hành vi thực thi đổi đúng cách:
- thiết bị đã ở đúng trạng thái → no-op (skip), KHÔNG gửi lệnh thừa (spec §50).
- thiết bị offline → validator loại TRƯỚC execute (spec §47, QC-05), không fail tận gateway.
- có phòng (focus) vs không phòng → act vs clarify (room-swap bất biến, §13).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, run_turn
from src.agent.preference.preference_store import PreferenceStore
from src.core.reasoning import FakeReasoningModel
from src.iot.registry import LIVING_ROOM


def _fresh_deps() -> PipelineDeps:
    """Deps ĐỘC LẬP (store riêng) — tránh rò rỉ ký ức/ledger chéo giữa các kịch bản perturbation."""
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), model_client=FakeReasoningModel(),
    )


@pytest.fixture
def deps():
    return _fresh_deps()


def _turn(deps, msg, conv="cp", **kw):
    base = {
        "conversation_id": conv, "user_id": "user_A", "user_message": msg,
        "now": datetime(2026, 8, 15, 20, 0, tzinfo=UTC),
    }
    base.update(kw)
    return run_turn(base, deps)


def test_perturb_noop_when_device_already_in_target_state(deps):
    """Live-state: đèn ĐÃ bật ở đúng độ sáng → reground bỏ no-op, không có action thực thi mới."""
    out = _turn(
        deps, "bật đèn phòng khách",
        live_device_states={"den_chum_phong_khach": {"power": "on", "brightness": 100}},
    )
    assert out["final_status"] in ("NO_OP", "SUCCESS")
    executed = out.get("executed") or []
    # Mọi action lên đèn đã-đúng-trạng-thái phải được đánh dấu skip/no-op, không gửi lệnh đổi.
    changed = [a for a in executed if a.get("entity_id") == "den_chum_phong_khach" and not a.get("skipped")]
    assert not changed


def test_perturb_offline_device_rejected_before_execute(deps):
    """Live-state: đèn OFFLINE trên context → validator loại (DEVICE_OFFLINE), không thực thi."""
    out = _turn(
        deps, "bật đèn phòng khách",
        live_device_states={"den_chum_phong_khach": {"power": "off", "online": False}},
    )
    # Không có action thực thi thành công lên thiết bị offline.
    executed = out.get("executed") or []
    ok = [a for a in executed if a.get("entity_id") == "den_chum_phong_khach" and a.get("status") == "EXECUTED"]
    assert not ok


def test_perturb_room_swap_act_vs_clarify():
    """Room-swap bất biến: cùng câu cảm nhận, có focus_room → hành động; không phòng → hỏi lại.

    Dùng deps ĐỘC LẬP cho hai nhánh: nếu dùng chung store, event của nhánh có-phòng (đã thực thi)
    sẽ giải phòng cho nhánh không-phòng (memory tier) và làm hỏng bất biến — chính là lý do phải
    scope ký ức theo hộ/hội thoại đúng cách."""
    with_room = _turn(_fresh_deps(), "trong phòng chói mắt quá", conv="r1", speaker_location="Phòng khách")
    assert with_room["final_status"] in ("SUCCESS", "NO_OP")
    no_room = _turn(_fresh_deps(), "trong phòng chói mắt quá", conv="r2")
    assert no_room["final_status"] == "clarification_required"


def test_episodic_room_does_not_cross_conversations_for_the_same_actor():
    """A relevant old event is long-term evidence, not permission to reuse its room silently."""
    deps = _fresh_deps()
    first = _turn(
        deps,
        "trong phòng chói mắt quá",
        conv="old-conversation",
        user_id="user_A",
        speaker_location=LIVING_ROOM,
    )
    assert first["final_status"] in ("SUCCESS", "NO_OP")
    assert deps.event_store.all_events(), "precondition: the old conversation must write an event"

    new_conversation = _turn(
        deps,
        "trong phòng chói mắt quá",
        conv="new-conversation",
        user_id="user_A",
    )

    assert new_conversation["final_status"] == "clarification_required"
    assert new_conversation["semantic_goal"].target_area is None


def test_episodic_room_does_not_cross_actor_boundary():
    """A household-shared store must never let Alice's event ground Bob's room."""
    deps = _fresh_deps()
    _turn(
        deps,
        "trong phòng chói mắt quá",
        conv="alice-conversation",
        user_id="alice",
        speaker_location=LIVING_ROOM,
    )

    bob = _turn(
        deps,
        "trong phòng chói mắt quá",
        conv="bob-conversation",
        user_id="bob",
    )

    assert bob["final_status"] == "clarification_required"
    assert bob["semantic_goal"].target_area is None


def test_relevant_episodic_room_still_resolves_inside_the_same_conversation():
    """Conversation scoping must retain the intended tier-5 resolution path."""
    deps = _fresh_deps()
    _turn(
        deps,
        "trong phòng chói mắt quá",
        conv="same-conversation",
        user_id="user_A",
        speaker_location=LIVING_ROOM,
    )
    # Remove the canonical room so only the event + source-turn provenance can
    # resolve the repeated request. EventStore and TurnStore deliberately remain.
    deps.ledger_store.reset("same-conversation")

    repeated = _turn(
        deps,
        "trong phòng chói mắt quá",
        conv="same-conversation",
        user_id="user_A",
    )

    assert repeated["sufficiency_decision"].value == "RESOLVE_CONTEXT"
    assert repeated["semantic_goal"].target_area == LIVING_ROOM


def test_unrelated_event_in_same_conversation_cannot_supply_room():
    """Conversation provenance is necessary but semantic relevance is still required."""
    deps = _fresh_deps()
    _turn(deps, "bật loa phòng khách", conv="same-but-unrelated", user_id="user_A")
    deps.ledger_store.reset("same-but-unrelated")

    unrelated = _turn(
        deps,
        "trong phòng chói mắt quá",
        conv="same-but-unrelated",
        user_id="user_A",
    )

    assert unrelated["final_status"] == "clarification_required"
    assert unrelated["semantic_goal"].target_area is None
