"""Requirement Ledger (spec §17-18) — constraint persistence + rejected assumptions."""

from __future__ import annotations

from src.agent.cognitive.ledger import LedgerStore
from src.agent.cognitive.ledger_updater import (
    derive_constraints,
    numeric_bounds_for,
    update_ledger,
    violates_constraint,
)
from src.agent.schemas import SemanticGoal
from src.nlu.ontology import UtteranceType


def _goal(**kw) -> SemanticGoal:
    base = dict(intent="x", raw_utterance="x", utterance_type=UtteranceType.DEVICE_COMMAND, confidence=0.9)
    base.update(kw)
    return SemanticGoal(**base)


def test_excluded_device_becomes_constraint_and_rejected():
    g = _goal(
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        goal_description="làm mát",
        excluded_device_ids=["dieu_hoa_phong_khach"],
    )
    constraints, rejected = derive_constraints(g)
    assert "avoid:dieu_hoa_phong_khach" in constraints
    assert "use:dieu_hoa_phong_khach" in rejected


def test_compound_goal_preserves_room_dimensions_and_sub_actions():
    goal = _goal(
        raw_utterance="Mở rèm bếp rồi chỉnh đèn bàn ăn xuống 50%.",
        target_area="Phòng bếp",
        target_device_ids=["rem_bep", "den_ban_an"],
        action_hint="open",
        parameters={"percent": 50},
        target_actions={"rem_bep": "open", "den_ban_an": "set"},
        target_parameters={"den_ban_an": {"percent": 50}},
    )

    ledger = update_ledger(LedgerStore().load("compound-ledger"), goal, turn=1)

    assert ledger.current_goal["target_area"] == "Phòng bếp"
    assert ledger.current_goal["dimensions"] == ["brightness", "position"]
    assert ledger.current_goal["target_actions"] == {
        "rem_bep": "open",
        "den_ban_an": "set",
    }
    assert ledger.current_goal["target_parameters"] == {
        "den_ban_an": {"percent": 50},
    }
    assert ledger.current_goal["sub_actions"] == [
        {
            "device_id": "rem_bep",
            "action": "open",
            "parameters": {},
            "dimensions": ["position"],
        },
        {
            "device_id": "den_ban_an",
            "action": "set",
            "parameters": {"percent": 50},
            "dimensions": ["brightness"],
        },
    ]


def test_bound_is_first_class_and_remains_readable_through_compatibility_api():
    ledger = update_ledger(
        LedgerStore().load("typed-bound"),
        _goal(
            target_area="Phòng khách",
            target_device_ids=["den_chum_phong_khach"],
            explicit_constraints=["bound:max:brightness:70:den_chum_phong_khach"],
        ),
        turn=2,
    )

    assert [bound.model_dump() for bound in ledger.bounds] == [
        {
            "kind": "max",
            "dimension": "brightness",
            "value": 70.0,
            "scope": "den_chum_phong_khach",
            "turn": 2,
        }
    ]
    assert numeric_bounds_for(
        ledger,
        device_id="den_chum_phong_khach",
        capability="brightness",
    ) == (None, 70.0)
    assert "bound:max:brightness:70:den_chum_phong_khach" in ledger.constraints


def test_group_exclusion_is_first_class_with_original_anchor():
    original = update_ledger(
        LedgerStore().load("typed-group"),
        _goal(
            target_area="Phòng bếp",
            target_device_ids=["den_bep", "den_ban_an"],
            action_hint="turn_on",
        ),
        turn=1,
    )
    narrowed = update_ledger(
        original,
        _goal(
            target_area="Phòng bếp",
            target_device_ids=["den_bep"],
            excluded_device_ids=["den_ban_an"],
            action_hint="turn_on",
        ),
        turn=2,
    )

    state = narrowed.group_exclusions[-1]
    assert state.excluded_device_ids == ["den_ban_an"]
    assert state.anchor_device_ids == ["den_bep", "den_ban_an"]
    assert state.room == "Phòng bếp"
    assert violates_constraint(narrowed, device_id="den_ban_an", action="turn_on") == (
        "group_exclusion:den_ban_an"
    )


def test_no_change_is_first_class_and_not_an_actionable_target():
    ledger = update_ledger(
        LedgerStore().load("typed-no-change"),
        _goal(
            intent="preserve_device_state",
            target_area="Phòng ngủ bố mẹ",
            target_device_ids=["den_ngu_bo_me"],
            no_change_device_ids=["den_ngu_bo_me"],
            explicit_constraints=["avoid:den_ngu_bo_me"],
        ),
        turn=3,
    )

    assert ledger.no_change[-1].device_ids == ["den_ngu_bo_me"]
    assert ledger.no_change[-1].room == "Phòng ngủ bố mẹ"
    assert violates_constraint(ledger, device_id="den_ngu_bo_me", action="turn_off") == (
        "no_change:den_ngu_bo_me"
    )

    changed = update_ledger(
        ledger,
        _goal(
            action_hint="turn_on",
            target_area="Phòng ngủ bố mẹ",
            target_device_ids=["den_ngu_bo_me"],
            target_devices_deterministic=True,
        ),
        turn=4,
    )
    assert changed.no_change == []
    assert violates_constraint(changed, device_id="den_ngu_bo_me", action="turn_on") is None


def test_constraint_persists_across_turns():
    store = LedgerStore()
    led = update_ledger(
        store.load("c1"),
        _goal(goal_description="làm mát", excluded_device_ids=["dieu_hoa_phong_khach"]),
        turn=1,
        conversation_id="c1",
    )
    store.save(led)
    # Lượt 2 không liên quan — ràng buộc vẫn còn (spec §17.1, invariant #8).
    led2 = update_ledger(
        store.load("c1"),
        _goal(action_hint="turn_on", target_device_ids=["den_chum_phong_khach"]),
        turn=2,
    )
    assert "avoid:dieu_hoa_phong_khach" in led2.constraints


def test_violates_constraint_blocks_revival():
    led = update_ledger(
        LedgerStore().load("c1"),
        _goal(goal_description="làm mát", excluded_device_ids=["dieu_hoa_phong_khach"]),
        turn=1,
    )
    assert violates_constraint(led, device_id="dieu_hoa_phong_khach", action="turn_on") is not None
    assert violates_constraint(led, device_id="den_chum_phong_khach", action="turn_on") is None


def test_negated_off_action_becomes_keep_on():
    g = _goal(action_hint="turn_off", polarity="negative", target_device_ids=["dieu_hoa_phong_khach"])
    constraints, _ = derive_constraints(g)
    assert "keep_on:dieu_hoa_phong_khach" in constraints
    led = update_ledger(LedgerStore().load("c1"), g, turn=1)
    assert violates_constraint(led, device_id="dieu_hoa_phong_khach", action="turn_off") is not None


def test_cancellation_clears_goal_but_keeps_constraints():
    store = LedgerStore()
    led = update_ledger(
        store.load("c1"),
        _goal(goal_description="làm mát", excluded_device_ids=["dieu_hoa_phong_khach"]),
        turn=1,
    )
    led = update_ledger(led, _goal(is_cancellation=True), turn=2)
    assert led.current_goal == {}
    assert "avoid:dieu_hoa_phong_khach" in led.constraints


def test_pending_clarification_set_and_cleared_on_advance():
    """§9/§17: câu hỏi làm rõ được lưu TƯỜNG MINH khi clarify, XOÁ khi mục tiêu thực advance."""
    store = LedgerStore()
    g = _goal(action_hint="turn_on", raw_utterance="bật đèn")
    pending = {"fields": ["room"], "question": "Phòng nào?", "turn": 1, "raw_utterance": "bật đèn"}
    led = update_ledger(store.load("c1"), g, turn=1, missing_information=["room"], pending_clarification=pending)
    assert led.pending_clarification["fields"] == ["room"]
    # Lượt sau advance một mục tiêu thực (không truyền pending) → pending bị xoá.
    led2 = update_ledger(led, _goal(action_hint="turn_on", target_device_ids=["den_chum_phong_khach"]), turn=2)
    assert led2.pending_clarification == {}


def test_pending_field_is_not_promoted_to_confirmed_fact():
    """Một giá trị context tạm không được thành fact khi chính field đó vẫn đang được hỏi."""
    store = LedgerStore()
    pending = {"fields": ["room"], "question": "Phòng nào?", "turn": 1, "raw_utterance": "mở cửa"}
    led = update_ledger(
        store.load("c1"),
        _goal(action_hint="turn_on", raw_utterance="mở cửa", target_area="Phòng khách"),
        turn=1,
        missing_information=["room"],
        pending_clarification=pending,
    )

    assert "room" not in led.confirmed_facts
    assert not any(e.field == "room" for e in led.evidence)


def test_pending_clarification_cleared_on_cancellation():
    store = LedgerStore()
    pending = {"fields": ["room"], "question": "Phòng nào?", "turn": 1, "raw_utterance": "bật đèn"}
    led = update_ledger(store.load("c1"), _goal(action_hint="turn_on", raw_utterance="bật đèn"),
                        turn=1, missing_information=["room"], pending_clarification=pending)
    led = update_ledger(led, _goal(is_cancellation=True), turn=2)
    assert led.pending_clarification == {}


def test_missing_information_does_not_retract_a_fact_confirmed_earlier():
    """"Còn thiếu phòng" ở lượt sau KHÔNG được xoá phòng lượt trước đã chốt (§17.1).

    Im lặng không phải mâu thuẫn. Trước bản vá, nhánh `room_missing` pop luôn fact cũ,
    nên hội thoại từ lượt 3 trở đi mất chỗ đứng và rơi xuống hỏi lại "phòng nào?".
    """
    store = LedgerStore()
    led = update_ledger(
        store.load("c-facts"),
        _goal(utterance_type=UtteranceType.ENVIRONMENT_REQUEST, target_area="Phòng khách"),
        turn=1,
        conversation_id="c-facts",
    )
    assert led.confirmed_facts["room"] == "Phòng khách"
    store.save(led)

    # Lượt 2 nêu thiết bị, không nêu phòng, và tự báo còn thiếu phòng.
    led2 = update_ledger(
        store.load("c-facts"),
        _goal(action_hint="turn_on", target_device_ids=["den_chum_phong_khach"], negated=True),
        turn=2,
        missing_information=["room"],
    )
    assert led2.confirmed_facts["room"] == "Phòng khách"
    assert led2.confirmed_facts["devices"] == ["den_chum_phong_khach"]


def test_missing_information_still_blocks_promoting_this_turn_s_guess():
    """Phán đoán TẠM của chính lượt này vẫn không được lên fact khi hệ thống báo thiếu."""
    led = update_ledger(
        LedgerStore().load("c-guess"),
        _goal(utterance_type=UtteranceType.ENVIRONMENT_REQUEST, target_area="Phòng khách"),
        turn=1,
        missing_information=["room"],
    )
    assert "room" not in led.confirmed_facts


def test_naming_another_room_overwrites_the_confirmed_room():
    """Thu hồi fact là việc của MÂU THUẪN tường minh, và đường đó vẫn thông."""
    store = LedgerStore()
    led = update_ledger(
        store.load("c-move"),
        _goal(utterance_type=UtteranceType.ENVIRONMENT_REQUEST, target_area="Phòng khách"),
        turn=1,
        conversation_id="c-move",
    )
    store.save(led)
    led2 = update_ledger(
        store.load("c-move"),
        _goal(utterance_type=UtteranceType.ENVIRONMENT_REQUEST, target_area="Phòng bếp"),
        turn=2,
    )
    assert led2.confirmed_facts["room"] == "Phòng bếp"
