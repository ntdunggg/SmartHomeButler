"""Layer 2 understanding (spec §11-15) — transducer, resolver, sufficiency, clarify."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.perception.context_builder import build_perception
from src.agent.pipeline import PipelineDeps, run_turn
from src.agent.preference.preference_store import PreferenceStore
from src.agent.schemas import DesiredOutcome, RequirementLedger, SemanticGoal, SufficiencyDecision
from src.agent.understanding.clarification import build_clarification
from src.agent.understanding.semantic_resolver import resolve_semantics
from src.agent.understanding.sufficiency import decide
from src.agent.understanding.transducer import transduce
from src.core.interfaces import DeviceSelector
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType


def _env_goal(area: str | None = None) -> SemanticGoal:
    return SemanticGoal(
        intent="tăng sáng",
        raw_utterance="tối quá",
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        goal_description="increase illumination",
        confidence=0.8,
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area=area) if area else DeviceSelector(),
                perceived_state="too_dark",
                relative_change={"brightness": "increase"},
            )
        ],
    )


def test_transducer_flags_missing_room_for_bare_env_goal():
    nu, ctx, _ = build_perception("tối quá")
    analysis = transduce(nu, _env_goal())
    assert "room" in analysis.missing_information
    assert analysis.sufficiency_status == "insufficient"


def test_transducer_no_ambiguity_when_goal_resolved():
    nu, ctx, _ = build_perception("bật điều hoà phòng khách", speaker_location="Phòng khách")
    goal = SemanticGoal(
        intent="bật", raw_utterance="bật điều hoà phòng khách", utterance_type=UtteranceType.DEVICE_COMMAND,
        confidence=0.95, action_hint="turn_on", target_device_ids=["dieu_hoa_phong_khach"], target_area="Phòng khách",
    )
    analysis = transduce(nu, goal)
    assert analysis.ambiguity_sources == []


def test_sufficiency_four_way():
    from src.agent.understanding.transducer import SemanticAnalysis

    a = SemanticAnalysis()
    assert decide(a, has_goal=True).decision is SufficiencyDecision.PROCEED
    assert decide(a, has_goal=False).decision is SufficiencyDecision.CLARIFY
    assert decide(a, has_goal=True, empty_utterance=True).decision is SufficiencyDecision.ABSTAIN
    assert decide(a, has_goal=True, resolved_fields=("room",)).decision is SufficiencyDecision.RESOLVE_CONTEXT
    assert decide(a, has_goal=True, still_missing=("room",)).decision is SufficiencyDecision.CLARIFY


def test_clarification_offers_targeted_options():
    nu, ctx, _ = build_perception("tối quá")
    clar = build_clarification(["room"], nu=nu, ctx=ctx)
    assert clar is not None and clar.target_field == "room"
    assert clar.options  # có lựa chọn cụ thể, không hỏi chung chung
    assert "phòng nào" in clar.question or "hay" in clar.question


def test_resolver_resolve_context_when_speaker_location_known():
    nu, ctx, _ = build_perception("tối quá", speaker_location="Phòng ngủ bố mẹ")
    outcome = resolve_semantics(nu, ctx, _env_goal())
    assert outcome.decision is SufficiencyDecision.RESOLVE_CONTEXT
    assert outcome.goal.target_area == "Phòng ngủ bố mẹ"
    assert outcome.evidence_trace  # có dấu vết bằng chứng (spec §14)


def test_resolver_clarify_when_no_room_anywhere():
    nu, ctx, _ = build_perception("tối quá")
    outcome = resolve_semantics(nu, ctx, _env_goal())
    assert outcome.decision is SufficiencyDecision.CLARIFY
    assert outcome.clarification is not None


def test_resolver_requires_deterministic_room_ignoring_llm_area():
    """§P2 code decides: mục tiêu open-ended có target_area do LLM tự đặt NHƯNG không có phòng tất
    định (không nêu phòng/không focus/speaker) → vẫn CLARIFY, không tin phòng LLM đoán."""
    nu, ctx, _ = build_perception("làm cho phòng dễ chịu hơn")  # ctx không có phòng nào
    # Goal open-ended (action_hint=None) đã có selector.area + target_area do "LLM" đặt sẵn.
    goal = _env_goal(area="Phòng khách").model_copy(update={"target_area": "Phòng khách"})
    outcome = resolve_semantics(nu, ctx, goal)
    assert outcome.decision is SufficiencyDecision.CLARIFY


def test_resolver_proceeds_open_ended_with_focus_room():
    """Mục tiêu open-ended CÓ phòng tất định (focus_room) → RESOLVE_CONTEXT/PROCEED, không hỏi lại."""
    nu, ctx, _ = build_perception("làm cho phòng dễ chịu hơn", focus_room="Phòng khách")
    goal = _env_goal(area="Phòng khách").model_copy(update={"target_area": "Phòng khách"})
    outcome = resolve_semantics(nu, ctx, goal)
    assert outcome.decision in (SufficiencyDecision.PROCEED, SufficiencyDecision.RESOLVE_CONTEXT)


def test_resolver_runtime_location_overrides_model_guessed_area():
    """KI-04: local inferred goal phải ground từ location evidence, không tin area model đoán."""
    nu, ctx, _ = build_perception("trong phòng chói mắt quá", speaker_location="Phòng ngủ con")
    guessed = _env_goal(area="Phòng khách").model_copy(update={"target_area": "Phòng khách"})

    outcome = resolve_semantics(nu, ctx, guessed)

    assert outcome.decision is SufficiencyDecision.RESOLVE_CONTEXT
    assert outcome.goal.target_area == "Phòng ngủ con"
    assert outcome.evidence_trace[0]["source"] == "runtime_context"


@pytest.fixture
def deps():
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), model_client=FakeReasoningModel(),
    )


def test_pipeline_clarifies_bare_env_goal(deps):
    """'tối quá' không phòng/vị trí → hỏi lại phòng (spec §P3, §15), KHÔNG đoán bừa."""
    out = run_turn(
        {
            "conversation_id": "c", "user_id": "user_A", "user_message": "tối quá",
            "now": datetime(2026, 8, 14, 20, 0, tzinfo=UTC), "semantic_goal": _env_goal(),
        },
        deps,
    )
    assert out["final_status"] == "clarification_required"
    assert "phòng" in out["reply"].lower()


def test_pipeline_resolves_from_ledger(deps):
    """Phòng đã chốt lượt trước (ledger) → resolve được, không hỏi lại (spec §14 tier 3)."""
    deps.ledger_store.save(RequirementLedger(conversation_id="c", confirmed_facts={"room": "Phòng khách"}, last_updated_turn=1))
    out = run_turn(
        {
            "conversation_id": "c", "user_id": "user_A", "user_message": "tối quá",
            "now": datetime(2026, 8, 14, 10, 0, tzinfo=UTC), "semantic_goal": _env_goal(),
            "live_sensors": [{"slug": "cam_bien_nang", "name": "nắng", "sensor_type": "sunlight", "value": 80.0, "unit": "%", "room": ""}],
        },
        deps,
    )
    assert out["final_status"] in ("SUCCESS", "NO_OP")
    assert out["selected_plan"] is not None


def test_pipeline_binds_anaphora_from_ledger(deps):
    """A1 đa lượt (spec §14 tier-3, §17): "tắt nó đi" bind vào thiết bị đã chốt lượt trước.

    Không truyền last_device_id qua state — nó phải suy từ ledger.confirmed_facts (parity
    với dialogue.py cũ). Trước fix, câu có đại từ 'nó' → clarify vì không đích để bind."""
    # Lượt 1: lệnh tường minh chốt đúng một thiết bị vào ledger.
    run_turn(
        {
            "conversation_id": "ana", "user_id": "user_A", "user_message": "bật đèn phòng khách",
            "now": datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        },
        deps,
    )
    assert deps.ledger_store.load("ana").confirmed_facts.get("devices") == ["den_chum_phong_khach"]
    # Lượt 2: đại từ "nó" phải bind vào den_chum_phong_khach (không hỏi lại).
    out = run_turn(
        {
            "conversation_id": "ana", "user_id": "user_A", "user_message": "tắt nó đi",
            "now": datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        },
        deps,
    )
    assert out["final_status"] in ("SUCCESS", "NO_OP")
    assert out["semantic_goal"].target_device_ids == ["den_chum_phong_khach"]


def test_pipeline_does_not_bind_anaphora_when_ledger_ambiguous(deps):
    """Ledger chốt >1 thiết bị → 'nó' KHÔNG đoán bừa một cái (spec §P3), hỏi lại."""
    deps.ledger_store.save(
        RequirementLedger(
            conversation_id="amb",
            confirmed_facts={"devices": ["den_chum_phong_khach", "den_bep"]},
            last_updated_turn=1,
        )
    )
    out = run_turn(
        {
            "conversation_id": "amb", "user_id": "user_A", "user_message": "tắt nó đi",
            "now": datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        },
        deps,
    )
    assert out["final_status"] == "clarification_required"


def _turn(conv, msg, deps, **kw):
    base = {
        "conversation_id": conv, "user_id": "user_A", "user_message": msg,
        "now": datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
    }
    base.update(kw)
    return run_turn(base, deps)


def test_pipeline_clarifies_bare_imperative_without_context(deps):
    """Lệnh điều khiển TƯỜNG MINH (có động từ) nhưng không thiết bị/phòng/ngữ cảnh hội thoại →
    CLARIFY, KHÔNG để LLM ground bừa thiết bị fallback (§P2). "khoá lại" có động từ lock, không đích."""
    out = _turn("bimp", "khoá lại", deps)
    assert out["final_status"] == "clarification_required"


def test_pipeline_slot_fills_room_after_clarify(deps):
    """A2 slot-fill (§17): 'tắt đèn' → hỏi phòng; lượt sau 'phòng khách' hoàn tất 'tắt đèn phòng khách'.

    Pending goal được lưu vào ledger khi clarify (raw_utterance='tắt đèn') để lượt sau ghép lại."""
    # Even a measured/inferred current location must not silently scope a bare
    # device-type command. The user explicitly chooses the room on turn two.
    c = _turn("sf", "tắt đèn", deps, speaker_location="Phòng khách")
    assert c["final_status"] == "clarification_required"
    assert "phòng" in c["reply"].lower()
    assert deps.ledger_store.load("sf").current_goal.get("raw_utterance") == "tắt đèn"
    out = _turn("sf", "phòng khách", deps, now=datetime(2026, 8, 14, 20, 1, tzinfo=UTC))
    assert out["final_status"] in ("SUCCESS", "NO_OP")
    assert "den_chum_phong_khach" in out["semantic_goal"].target_device_ids


def test_pipeline_refines_room_of_prior_env_goal(deps):
    """A2 refine-room (§5, §17): 'à ở phòng ngủ con mà' chạy lại mục tiêu cảm nhận lượt trước ở phòng MỚI.

    Chiều tiện nghi (giảm sáng) được GIỮ (tái dựng từ ledger), chỉ đổi phòng → không đụng đèn phòng khách."""
    t1 = _turn("rf", "trong phòng chói mắt quá", deps, speaker_location="Phòng khách")
    assert t1["selected_plan"] is not None
    out = _turn("rf", "à ở phòng ngủ con mà", deps, now=datetime(2026, 8, 14, 20, 1, tzinfo=UTC))
    devs = [a.device_id for a in out["selected_plan"].actions] if out.get("selected_plan") else []
    assert "den_ngu_con" in devs and "den_chum_phong_khach" not in devs


def test_pipeline_carries_room_for_bare_adjust(deps):
    """A2 room-carry (§14 tier-3): 'giảm bớt độ sáng nữa đi' nối tiếp lượt trước → ground đúng phòng cũ."""
    _turn("ca", "trong phòng chói mắt quá", deps, speaker_location="Phòng ngủ con")
    out = _turn("ca", "giảm bớt độ sáng nữa đi", deps, now=datetime(2026, 8, 14, 20, 1, tzinfo=UTC))
    devs = [a.device_id for a in out["selected_plan"].actions] if out.get("selected_plan") else []
    assert "den_ngu_con" in devs
