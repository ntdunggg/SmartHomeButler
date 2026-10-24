"""Turn-intent + continuation/modification (spec §5, §17) — kế thừa mục tiêu đã ground.

Bao phủ: nhận diện modifier tổng quát (không phrase-cứng), phân loại 5 vai trò lượt, kế thừa
grounding (phòng/thiết bị/chiều) + áp modifier, cô lập ngữ cảnh khi đổi chủ đề, và KHÔNG hồi
quy đường capability-carry sẵn có ("giảm bớt độ sáng").
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, run_planning
from src.agent.preference.preference_store import PreferenceStore
from src.agent.schemas import MemoryEvent, RequirementLedger
from src.agent.understanding.turn_intent import TurnIntent, classify, detect_modifier
from src.core.reasoning import FakeReasoningModel
from src.nlu.normalizer import analyze
from src.services.pipeline_bridge import reason


def _excluded_from_constraints(ledger, device_id):
    hits = []
    for c in ledger.constraints:
        kind, _, slug = c.partition(":")
        if slug == device_id and kind in ("avoid", "keep_off"):
            hits.append(c)
    return hits

NOW = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)


# --- Unit: modifier detection (tổng quát, không phrase-cứng) ---------------------
@pytest.mark.parametrize(
    ("text", "kind", "direction", "value"),
    [
        ("mạnh hơn chút", "relative", "increase", None),      # so sánh <adj> hơn
        ("thêm một chút nữa", "relative", "increase", None),  # động từ tăng + tiếp tục
        ("bớt đi tí", "relative", "decrease", None),
        ("tối hơn", "relative", "decrease", None),
        ("mở lại một nửa", "fraction", "", 50),               # ½
        ("à 25 độ", "absolute", "", 25),                      # giá trị tuyệt đối
        ("không, 60 thôi", "absolute", "", 60),
        ("hạ xuống 42 độ", "absolute", "", 42),               # cue 'xuống' → set, không phải delta
    ],
)
def test_detect_modifier_generalizes(text, kind, direction, value):
    m = detect_modifier(analyze(text))
    assert m is not None and m.kind == kind and m.direction == direction and m.value == value


def test_capability_noun_not_flipped_by_polarity_adjective():
    """'độ sáng' (danh từ brightness) KHÔNG bị 'sáng' bắt thành tăng — hướng lấy từ động từ 'giảm'."""
    m = detect_modifier(analyze("giảm bớt độ sáng nữa đi"))
    assert m is not None and m.direction == "decrease"


def test_relative_delta_with_number_is_not_absolute():
    """'giảm 2 độ' (không cue set) = delta tương đối, KHÔNG phải set nhiệt độ = 2."""
    m = detect_modifier(analyze("giảm 2 độ"))
    assert m is not None and m.kind == "relative" and m.direction == "decrease"


def test_no_modifier_returns_none():
    assert detect_modifier(analyze("bật đèn phòng khách")) is None


# --- Unit: classify 5 vai trò ----------------------------------------------------
def _ledger_with(devices=None, room=None, outcomes=None) -> RequirementLedger:
    return RequirementLedger(
        conversation_id="c",
        current_goal={"raw_utterance": "mở rèm phòng khách", "goal_description": "mở rèm"},
        confirmed_facts={k: v for k, v in (("devices", devices), ("room", room)) if v},
        desired_outcomes=outcomes or [],
    )


def test_classify_continuation_when_grounded_prior_and_modifier():
    led = _ledger_with(devices=["rem_phong_khach"], room="Phòng khách")
    intent, mod = classify(analyze("thêm một chút nữa"), led)
    assert intent == TurnIntent.CONTINUATION and mod is not None


def test_classify_cancellation():
    intent, _ = classify(analyze("thôi khỏi"), _ledger_with(room="Phòng khách"))
    assert intent == TurnIntent.CANCELLATION


def test_classify_topic_switch_new_device_not_inherited():
    """Nêu thiết bị KHÁC mục tiêu trước → TOPIC_SWITCH (cô lập ngữ cảnh §73), không kế thừa."""
    led = _ledger_with(devices=["rem_phong_khach"], room="Phòng khách")
    intent, mod = classify(analyze("bật đèn phòng bếp"), led)
    assert intent == TurnIntent.TOPIC_SWITCH and mod is None


def test_classify_clarification_answer_room_only():
    led = _ledger_with(room="Phòng khách")
    intent, _ = classify(analyze("phòng ngủ con"), led, has_pending_clarification=True)
    assert intent == TurnIntent.CLARIFICATION_ANSWER


def test_environmental_request_in_new_room_is_topic_switch_not_slot_answer():
    led = _ledger_with(devices=["loa_phong_khach"], room="Phòng khách")
    intent, modifier = classify(analyze("Phòng bếp nóng quá"), led)
    assert intent == TurnIntent.TOPIC_SWITCH
    assert modifier is None


def test_classify_new_goal_when_no_prior():
    intent, _ = classify(analyze("mạnh hơn chút"), RequirementLedger(conversation_id="c"))
    assert intent == TurnIntent.NEW_GOAL


# --- Integration: end-to-end continuation qua pipeline (offline, tất định) --------
@pytest.fixture
def deps() -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=FakeReasoningModel(),
    )


def _run(deps, conv, msgs):
    r = None
    for m in msgs:
        r = reason(message=m, conversation_id=conv, now=NOW, deps=deps, model_client=deps.model_client)
    return r


def _plan(r):
    return {a.device_id: (getattr(a, "params", {}) or getattr(a, "target", {})) for a in (getattr(r.candidate_plan, "actions", []) or [])}


def test_relative_continuation_inherits_curtain(deps):
    r = _run(deps, "c1", ["mở rèm phòng khách", "thêm một chút nữa"])
    assert r.outcome == "candidate_plan" and "rem_phong_khach" in _plan(r)


def test_absolute_correction_pins_ac_and_sets_value(deps):
    r = _run(deps, "c2", ["đặt điều hoà phòng khách 24 độ", "à 25 độ"])
    assert r.outcome == "candidate_plan"
    assert _plan(r).get("dieu_hoa_phong_khach", {}).get("temperature") == 25


def test_value_correction_pins_light_not_curtain(deps):
    """'không, 60 thôi' sau 'tăng đèn ... 70%' → GHIM đúng ĐÈN ở 60, không đổ sang rèm cùng chiều sáng."""
    r = _run(deps, "c3", ["tăng đèn phòng khách lên 70%", "không, 60 thôi"])
    plan = _plan(r)
    assert "den_chum_phong_khach" in plan and plan["den_chum_phong_khach"].get("percent") == 60
    assert "rem_phong_khach" not in plan


def test_fraction_continuation_sets_half(deps):
    r = _run(deps, "c4", ["đóng rèm phòng khách", "mở lại một nửa"])
    assert _plan(r).get("rem_phong_khach", {}).get("percent") == 50


@pytest.mark.parametrize("follow_up", ["bật đèn", "bật lại đèn"])
def test_bare_light_command_reuses_confirmed_room_after_clarification(deps, follow_up):
    """Phòng user vừa chốt là scope thực thi của lượt sau, không bị guard lượt-đầu hỏi lại."""
    result = _run(deps, f"room-carry-{follow_up}", ["tắt đèn", "phòng khách", follow_up])

    assert result.outcome == "candidate_plan"
    assert [(a.device_id, a.action.value) for a in result.candidate_plan.actions] == [
        ("den_chum_phong_khach", "turn_on")
    ]


@pytest.mark.parametrize(
    "alternative_command",
    [
        "tắt đèn khác ở trong phòng bếp",
        "tắt đèn còn lại trong phòng bếp",
        "tat den khac trong phong bep",
    ],
)
def test_other_light_excludes_the_previously_grounded_light(deps, alternative_command):
    """Quan hệ 'khác' chọn phần còn lại trong cùng loại/phòng, không lặp target salient."""
    conversation_id = f"other-light-kitchen-{alternative_command}"
    result = _run(
        deps,
        conversation_id,
        ["tắt đèn bàn ăn phòng bếp", alternative_command],
    )

    assert result.outcome == "candidate_plan"
    assert [(a.device_id, a.action.value) for a in result.candidate_plan.actions] == [
        ("den_bep", "turn_off")
    ]
    assert "avoid:den_ban_an" not in deps.ledger_store.load(conversation_id).constraints


def test_other_light_clarifies_when_room_has_no_alternative(deps):
    """Phòng chỉ có một đèn: không được hiểu 'đèn khác' thành tác động lại chính đèn đó."""
    result = _run(
        deps,
        "other-light-none",
        ["tắt đèn", "phòng khách", "tắt đèn khác ở trong phòng khách"],
    )

    assert result.outcome == "clarification"
    assert result.candidate_plan is None


def test_open_door_clarification_keeps_intent_and_grounds_single_lock(deps):
    """Slot room phải hoàn thiện lệnh cửa đang treo, không trở thành goal độc lập rỗng."""
    conv = "open-door-room-slot"
    first = reason(
        message="mở cửa", conversation_id=conv, now=NOW,
        deps=deps, model_client=deps.model_client,
    )
    assert first.outcome == "clarification"
    assert "phòng" in first.reply.lower()

    pending = deps.ledger_store.load(conv)
    assert pending.pending_clarification.get("fields") == ["room"]
    assert "room" not in pending.confirmed_facts

    completed = reason(
        message="phòng khách", conversation_id=conv, now=NOW,
        deps=deps, model_client=deps.model_client,
    )
    assert completed.outcome == "candidate_plan"
    actions = completed.candidate_plan.actions
    assert [(a.device_id, a.action.value) for a in actions] == [("khoa_cua_chinh", "unlock")]


def test_clarification_slot_does_not_call_goal_author():
    class RejectSemanticAuthor(FakeReasoningModel):
        def structured_generate_sync(
            self, prompt, output_schema, *, system_prompt=None, context=None, temperature=0.0
        ):
            if getattr(output_schema, "__name__", "") == "SemanticGoal":
                raise AssertionError("slot answer must be resolved from the pending ledger")
            return super().structured_generate_sync(
                prompt, output_schema, system_prompt=system_prompt,
                context=context, temperature=temperature,
            )

    model = RejectSemanticAuthor()
    local_deps = PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=model,
    )

    result = _run(local_deps, "slot-no-author", ["mở cửa", "phòng khách"])
    assert result.outcome == "candidate_plan"
    assert [a.device_id for a in result.candidate_plan.actions] == ["khoa_cua_chinh"]


@pytest.mark.parametrize(
    ("command", "device_id", "action"),
    [
        ("mở cửa phòng khách", "khoa_cua_chinh", "unlock"),
        ("đóng cửa phòng ngủ bố mẹ", "khoa_cua_phong_bo_me", "lock"),
        ("mở cửa sổ phòng khách", "cua_so_phong_khach", "open"),
    ],
)
def test_explicit_open_close_grounds_by_device_capability(deps, command, device_id, action):
    result = reason(
        message=command, conversation_id=f"ground-{device_id}-{action}", now=NOW,
        deps=deps, model_client=deps.model_client,
    )

    assert result.outcome == "candidate_plan"
    assert [(a.device_id, a.action.value) for a in result.candidate_plan.actions] == [(device_id, action)]


def test_open_window_clarification_keeps_window_domain(deps):
    result = _run(deps, "open-window-room-slot", ["mở cửa sổ", "phòng khách"])

    assert result.outcome == "candidate_plan"
    assert [(a.device_id, a.action.value) for a in result.candidate_plan.actions] == [
        ("cua_so_phong_khach", "open")
    ]


def test_topic_switch_isolates_stale_context(deps):
    """Đổi chủ đề (bật đèn bếp) sau khi mở rèm phòng khách → KHÔNG kế thừa rèm."""
    r = _run(deps, "c5", ["mở rèm phòng khách", "bật đèn phòng bếp"])
    plan = _plan(r)
    assert any(d.startswith("den_") for d in plan) and "rem_phong_khach" not in plan


def test_topic_switch_clears_current_turn_ledger_and_memory_view(deps):
    reason(
        message="mở rèm phòng khách",
        conversation_id="topic-hard-boundary",
        user_id="user_A",
        now=NOW,
        deps=deps,
        model_client=deps.model_client,
    )
    deps.event_store.add(
        MemoryEvent(
            event_id="old-curtain-memory",
            actors=["user_A"],
            location=["Phòng khách"],
            summary="rèm phòng khách cũ",
        )
    )

    out = run_planning(
        {
            "conversation_id": "topic-hard-boundary",
            "user_id": "user_A",
            "user_message": "bật đèn phòng bếp",
            "speaker_role": "owner",
            "now": NOW,
        },
        deps,
    )
    assert out["turn_intent"] == TurnIntent.TOPIC_SWITCH.value
    assert out.get("memory_evidence") == []
    new_targets = out["semantic_goal"].target_device_ids
    assert new_targets and all(device_id.startswith("den_") for device_id in new_targets)
    assert "rem_phong_khach" not in new_targets
    assert deps.ledger_store.load("topic-hard-boundary").confirmed_facts["devices"] == new_targets


def test_numeric_bound_persists_and_clamps_later_continuation(deps):
    conv = "durable-bound"
    reason(
        message="đặt đèn phòng khách 60%",
        conversation_id=conv,
        now=NOW,
        deps=deps,
        model_client=deps.model_client,
    )
    reason(
        message="không quá 70%",
        conversation_id=conv,
        now=NOW,
        deps=deps,
        model_client=deps.model_client,
    )
    result = reason(
        message="tăng thêm nhiều nữa",
        conversation_id=conv,
        now=NOW,
        deps=deps,
        model_client=deps.model_client,
    )

    ledger = deps.ledger_store.load(conv)
    assert "bound:max:brightness:70:den_chum_phong_khach" in ledger.constraints
    assert result.outcome == "candidate_plan"
    assert result.candidate_plan.actions[0].params.get("brightness", result.candidate_plan.actions[0].params.get("percent")) <= 70


def test_modifier_without_prior_clarifies(deps):
    r = _run(deps, "c6", ["giảm bớt tí"])
    assert r.outcome == "clarification"


def test_continuation_room_not_overwritten_by_weaker_runtime_context(deps):
    """Bug A: phòng đã CHỐT trong ledger ở lượt tiếp nối không có phòng mới KHÔNG được bị
    runtime_context (speaker_location) xung đột đè lên. resolve_room's tier order hiện sai:
    runtime_context (tier2) chạy TRƯỚC requirement_ledger (tier3) trong context_resolver.py,
    và semantic_resolver._apply_room ghi đè vô điều kiện — kể cả khi build_continuation_goal
    đã ground đúng target_area từ ledger rồi."""
    r1 = reason(message="bật đèn bàn ăn phòng bếp", conversation_id="bugA1", now=NOW,
                deps=deps, model_client=deps.model_client)
    assert r1.outcome == "candidate_plan"
    led = deps.ledger_store.load("bugA1")
    assert led.confirmed_facts.get("room") == "Phòng bếp"
    assert led.confirmed_facts.get("devices") == ["den_ban_an"]

    r2 = reason(message="To hơn tí", conversation_id="bugA1", now=NOW,
                speaker_location="Phòng khách", deps=deps, model_client=deps.model_client)
    assert r2.semantic_goal is not None
    assert r2.semantic_goal.target_area == "Phòng bếp", (
        f"expected ledger-grounded room 'Phòng bếp' to stick, got {r2.semantic_goal.target_area!r}"
    )


def test_continuation_room_can_be_explicitly_corrected(deps):
    """Escape hatch: lượt tiếp nối NÊU RÕ phòng+thiết bị khác phải THẮNG (không được làm phòng
    dính cứng vô điều kiện — tránh overcorrect)."""
    reason(message="bật đèn bàn ăn phòng bếp", conversation_id="bugA2", now=NOW,
           deps=deps, model_client=deps.model_client)
    r2 = reason(message="đèn chùm phòng khách thì tăng lên", conversation_id="bugA2", now=NOW,
                speaker_location="Phòng khách", deps=deps, model_client=deps.model_client)
    assert r2.semantic_goal is not None
    assert r2.semantic_goal.target_area == "Phòng khách"
    assert r2.semantic_goal.target_device_ids == ["den_chum_phong_khach"]


def test_bare_continuation_preserves_anchor_device_not_whole_room(deps):
    """Bug B: 'To hơn tí' sau 'bật đèn bàn ăn phòng bếp' phải GHIM đúng den_ban_an (thiết bị đã
    ground lượt trước), KHÔNG lan sang den_bep (đèn khác cùng phòng, cùng loại). Manager's
    build_subgoals bỏ qua goal.target_device_ids ở nhánh open-ended -> Subgoal.explicit_device_id
    luôn None -> LightingAgent.propose lấy TẤT CẢ đèn trong phòng thay vì chỉ đèn đã ground."""
    r1 = reason(message="bật đèn bàn ăn phòng bếp", conversation_id="bugB1", now=NOW,
                deps=deps, model_client=deps.model_client)
    assert r1.outcome == "candidate_plan"
    led = deps.ledger_store.load("bugB1")
    assert led.confirmed_facts.get("devices") == ["den_ban_an"]
    assert led.confirmed_facts.get("room") == "Phòng bếp"

    r2 = reason(message="To hơn tí", conversation_id="bugB1", now=NOW,
                speaker_location="Phòng bếp", deps=deps, model_client=deps.model_client)
    assert r2.semantic_goal is not None
    assert r2.semantic_goal.target_device_ids == ["den_ban_an"]

    plan = _plan(r2)
    assert plan, f"expected a candidate plan, got outcome={r2.outcome!r} reply={r2.reply!r}"
    assert set(plan.keys()) == {"den_ban_an"}, (
        f"expected ONLY the turn-1-grounded device 'den_ban_an', got {set(plan.keys())!r} "
        f"(Phòng bếp also has 'den_bep' as a sibling light -- anchor device must not be dropped)"
    )


def test_group_goal_still_touches_all_matching_devices(deps):
    """Escape hatch / guardrail: 'phòng bếp hơi tối' KHÔNG nêu thiết bị cụ thể, mục tiêu nhóm
    hợp lệ (cardinality='all') -> phải touch CẢ HAI đèn phòng bếp. Coder's fix cho Bug B
    KHÔNG được thu hẹp trường hợp này về một thiết bị."""
    r = reason(message="phòng bếp hơi tối", conversation_id="bugB_group", now=NOW,
               deps=deps, model_client=deps.model_client)
    assert r.outcome == "candidate_plan"
    assert not r.semantic_goal.target_device_ids
    assert all(o.cardinality == "all" for o in r.semantic_goal.desired_outcomes)

    plan = _plan(r)
    assert set(plan.keys()) == {"den_bep", "den_ban_an"}, (
        f"expected group goal to touch BOTH kitchen lights, got {set(plan.keys())!r}"
    )


def test_named_capability_still_uses_understand_path(deps):
    """'giảm bớt độ sáng nữa đi' NÊU capability → understand ground, continuation KHÔNG đè (không hồi quy)."""
    reason(message="trong phòng chói mắt quá", conversation_id="c7", now=NOW,
           speaker_location="Phòng ngủ con", deps=deps, model_client=deps.model_client)
    r = reason(message="giảm bớt độ sáng nữa đi", conversation_id="c7", now=NOW,
               deps=deps, model_client=deps.model_client)
    # Vẫn ra kế hoạch điều chỉnh đèn (đường capability-carry cũ), không bị continuation làm hỏng.
    assert r.outcome == "candidate_plan" and any(d.startswith("den_") for d in _plan(r))


def test_negation_of_absent_device_does_not_self_conflict_continuation(deps):
    """LS-106 pattern: 'đèn bàn' does NOT exist in Phòng khách (only den_chum_phong_khach does,
    per src/iot/registry.py). The negation's alias-retry-by-room fallback resolves it to
    den_chum_phong_khach anyway, which then becomes BOTH the ledger's confirmed device anchor
    AND a keep_off-constrained device. A later bare positive continuation must not anchor on
    a device it simultaneously excludes."""
    conv = "ls106_repro"
    r1 = reason(message="Phòng khách tối quá", conversation_id=conv, now=NOW, deps=deps, model_client=deps.model_client)
    assert r1.outcome == "candidate_plan"
    led = deps.ledger_store.load(conv)
    assert led.confirmed_facts.get("room") == "Phòng khách"

    reason(message="Đừng bật đèn bàn", conversation_id=conv, now=NOW, deps=deps, model_client=deps.model_client)
    led = deps.ledger_store.load(conv)
    assert led.confirmed_facts.get("devices") == ["den_chum_phong_khach"], (
        f"expected alias-retry to resolve non-existent 'đèn bàn' to the only living-room light, "
        f"got {led.confirmed_facts.get('devices')!r}"
    )
    assert "keep_off:den_chum_phong_khach" in led.constraints

    r3 = reason(message="Tăng thêm một chút nữa", conversation_id=conv, now=NOW, deps=deps, model_client=deps.model_client)
    assert r3.semantic_goal is not None
    anchor_ids = list(r3.semantic_goal.target_device_ids)
    led3 = deps.ledger_store.load(conv)
    for device_id in anchor_ids:
        hits = _excluded_from_constraints(led3, device_id)
        assert not hits, (
            f"anchor device {device_id!r} is simultaneously excluded by constraint(s) {hits!r} -- "
            f"self-conflicting continuation: outcome={r3.outcome!r} reply={r3.reply!r}"
        )


class WrongDeviceGuessModel(FakeReasoningModel):
    """Simulates a live LLM's plausible-but-wrong device guess for author_goal().

    Stock FakeReasoningModel NEVER sets target_device_ids on SemanticGoal, so it cannot exercise
    the anchor-authority code path (manager.py Subgoal.anchor_device_ids) at all. This subclass
    overrides ONLY the SemanticGoal branch to mimic a real model returning a real-but-wrong device
    slug; every other schema call delegates to the base FakeReasoningModel unchanged."""

    def structured_generate_sync(self, prompt, output_schema, *, system_prompt=None, context=None, temperature=0.0):
        from src.nlu.schemas import SemanticGoal
        if output_schema is SemanticGoal:
            from src.core.interfaces import DeviceSelector
            from src.nlu.schemas import DesiredOutcome
            ctx = context or {}
            utt = str(ctx.get("utterance") or prompt or "").lower()
            if "tiếng" in utt or "âm lượng" in utt or "am luong" in utt:
                return SemanticGoal(
                    raw_utterance=ctx.get("utterance", utt) or utt,
                    goal_description=utt,
                    utterance_type="ENVIRONMENT_REQUEST",
                    confidence=0.9,
                    target_device_ids=["loa_phong_khach"],
                    desired_outcomes=[
                        DesiredOutcome(
                            selector=DeviceSelector(domain="speaker"),
                            target_state={}, perceived_state="",
                            relative_change={"volume": "decrease"}, cardinality="one",
                        )
                    ],
                    polarity="affirmative",
                )
        return super().structured_generate_sync(prompt, output_schema, system_prompt=system_prompt, context=context, temperature=temperature)


def test_llm_authored_device_guess_does_not_drop_deterministically_grounded_device():
    """LS-029 pattern: TV was deterministically grounded turn 1. A later capability-named
    phrase routes around continuation (_names_capability guard in pipeline.py) into
    author_goal(), whose LLM-proposed device (simulated: Loa) must not fully evict TV as a
    candidate -- both should remain plannable, not silently drop the deterministic grounding."""
    deps = PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=WrongDeviceGuessModel(),
    )
    conv = "ls029_repro"
    r1 = reason(message="Bật TV phòng khách", conversation_id=conv, now=NOW, deps=deps, model_client=deps.model_client)
    assert r1.outcome == "candidate_plan"
    led = deps.ledger_store.load(conv)
    assert led.confirmed_facts.get("devices") == ["tv_phong_khach"]

    r2 = reason(message="Nhỏ tiếng xuống", conversation_id=conv, now=NOW, deps=deps, model_client=deps.model_client)
    assert r2.semantic_goal is not None
    plan = _plan(r2)
    assert "tv_phong_khach" in plan or "tv_phong_khach" in (r2.semantic_goal.target_device_ids or []), (
        f"turn-1-grounded device 'tv_phong_khach' was fully dropped when the LLM's own guess "
        f"('loa_phong_khach') took hard-anchor authority -- goal.target_device_ids="
        f"{r2.semantic_goal.target_device_ids!r} plan={plan!r}"
    )
