"""Focused contract tests for the category-aware Agent200 evaluator."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from eval.eval_agent200_v2 import (
    CATEGORY_PLAN,
    Metric,
    _fold_metric,
    _is_pass,
    _tool_scores,
    build_report,
    m_clarification,
    m_goal_plan_alignment,
    m_mixed_boundary,
    m_ood,
    m_state_answer,
    m_unsupported,
    score_case_deterministic,
    validate_cases,
)
from src.domain.enums import Capability
from src.iot import registry as runtime_registry
from src.services.pipeline_bridge import ReasoningResult


def _result(
    outcome: str,
    *,
    reply: str = "",
    actions: list[SimpleNamespace] | None = None,
    semantic_goal: SimpleNamespace | None = None,
    question: str | None = None,
) -> ReasoningResult:
    plan = SimpleNamespace(actions=actions) if actions is not None else None
    clarification = SimpleNamespace(question_vi=question) if question is not None else None
    return ReasoningResult(
        semantic_goal=semantic_goal,
        candidate_plan=plan,
        outcome=outcome,
        reply=reply,
        clarification=clarification,
    )


def _action(slug: str, capability: Capability | str) -> SimpleNamespace:
    return SimpleNamespace(device_id=slug, capability=capability)


def _call(slug: str, capability: str = "on_off", value: object = "on") -> dict:
    return {
        "name": "control_device",
        "args": {"device_slug": slug, "capability": capability, "value": value},
    }


def _case(category: str, **updates: object) -> dict:
    plan = CATEGORY_PLAN[category]
    case = {
        "case_id": f"case-{category}",
        "category": category,
        "user_input": "test",
        "reference_tool_calls": [],
        "tool_metrics_applicable": plan["tool_metrics_applicable"],
        "strict_order": plan["strict_order"],
    }
    case.update(updates)
    return case


def _goal(room: str | None, *, excluded: list[str] | None = None, no_change: list[str] | None = None):
    return SimpleNamespace(
        target_area=room,
        excluded_device_ids=excluded or [],
        no_change_device_ids=no_change or [],
    )


def test_category_matrix_covers_all_nine_categories_with_fixed_tool_contract() -> None:
    assert set(CATEGORY_PLAN) == {
        "single_control",
        "multi_step_ordered",
        "multi_device_unordered",
        "state_query",
        "goal_oriented",
        "clarification",
        "unsupported_in_domain",
        "topic_off_domain",
        "topic_mixed",
    }
    assert CATEGORY_PLAN["multi_step_ordered"]["strict_order"] is True
    assert CATEGORY_PLAN["multi_device_unordered"]["strict_order"] is False
    assert CATEGORY_PLAN["goal_oriented"]["tool_metrics_applicable"] is False
    assert CATEGORY_PLAN["topic_mixed"]["tool_metrics_applicable"] is True
    for plan in CATEGORY_PLAN.values():
        assert "agent_goal_accuracy" in plan["diagnostic"]
        assert "topic_adherence" in plan["diagnostic"]


def test_validate_cases_accepts_matrix_and_rejects_contract_drift() -> None:
    validate_cases([_case(category) for category in CATEGORY_PLAN])

    bad = _case("multi_step_ordered", strict_order=False)
    with pytest.raises(ValueError, match="strict_order"):
        validate_cases([bad])

    unknown = _case("single_control") | {"category": "new_category"}
    with pytest.raises(ValueError, match="category không hỗ trợ"):
        validate_cases([unknown])


def test_clarification_accuracy_and_target_slot() -> None:
    case = _case("clarification")
    result = _result("clarification", question="Bạn muốn bật đèn ngủ ở phòng nào?")
    accuracy, target = m_clarification(case, result, [])
    assert (accuracy.score, target.score) == (1.0, 1.0)

    acted, unscored_target = m_clarification(
        case,
        _result("candidate_plan", actions=[_action("den_ngu_phong_con", Capability.ON_OFF)]),
        [_call("den_ngu_phong_con")],
    )
    assert acted.score == 0.0
    assert acted.failure_mode == "auto_selected_ambiguous_target"
    assert unscored_target.score is None

    _, wrong_target = m_clarification(
        case,
        _result("clarification", question="Bạn muốn độ sáng bao nhiêu?"),
        [],
    )
    assert wrong_target.score == 0.0
    assert wrong_target.failure_mode == "wrong_clarification_slot"


def test_unsupported_handling_and_hallucination_rate() -> None:
    case = _case("unsupported_in_domain", reference_goal="Thiết bị không tồn tại")
    handling, rate = m_unsupported(case, _result("no_action", reply="Nhà mình không có thiết bị này."), [])
    assert handling.score == 1.0
    assert rate.score == 0.0
    assert _is_pass("hallucinated_device_rate", rate.score)

    handling, rate = m_unsupported(
        case,
        _result("candidate_plan", actions=[_action("may_pha_ca_phe", Capability.ON_OFF)]),
        [_call("may_pha_ca_phe")],
    )
    assert handling.score == 0.0
    assert rate.score == 1.0
    assert rate.failure_mode == "hallucinated_device"
    assert not _is_pass("hallucinated_device_rate", rate.score)


def test_out_of_domain_refusal_and_unexpected_tool_rate() -> None:
    case = _case("topic_off_domain")
    refusal, rate = m_ood(
        case,
        _result("no_action", reply="Mình chỉ hỗ trợ các yêu cầu nhà thông minh."),
        [],
    )
    assert (refusal.score, rate.score) == (1.0, 0.0)

    refusal, rate = m_ood(
        case,
        _result("candidate_plan", actions=[_action("tv_phong_khach", Capability.ON_OFF)]),
        [_call("tv_phong_khach")],
    )
    assert (refusal.score, rate.score) == (0.0, 1.0)
    assert refusal.failure_mode == rate.failure_mode == "off_domain_tool_call"


def test_mixed_domain_boundary_is_scored_separately() -> None:
    case = _case("topic_mixed")
    assert (
        m_mixed_boundary(
            case,
            _result("candidate_plan", reply="Mình không hỗ trợ phần ngoài phạm vi nhà thông minh.", actions=[]),
            [],
        ).score
        == 1.0
    )
    failed = m_mixed_boundary(case, _result("candidate_plan", reply="Mình sẽ tắt TV.", actions=[]), [])
    assert failed.score == 0.0
    assert failed.failure_mode == "missing_boundary_refusal"


def test_state_answer_uses_device_override_and_rejects_wrong_target() -> None:
    slug = "den_chum_phong_khach"
    case = _case(
        "state_query",
        user_input="Độ sáng đèn phòng khách là bao nhiêu?",
        reference_tool_calls=[{"name": "query_device_state", "args": {"device_slug": slug}}],
        initial_state_overrides={slug: {"power": "on", "brightness": 37}},
    )
    result = _result("answer", reply="Độ sáng hiện tại là 37%.")
    assert m_state_answer(case, result, [{"name": "query_device_state", "args": {"device_slug": slug}}]).score == 1.0

    wrong = m_state_answer(
        case,
        result,
        [{"name": "query_device_state", "args": {"device_slug": "den_ban_an"}}],
    )
    assert wrong.score == 0.0
    assert wrong.failure_mode == "wrong_state_target"


def test_state_answer_uses_runtime_sensor_registry() -> None:
    slug = "cam_bien_bui"
    sensor = runtime_registry.SENSOR_BY_SLUG[slug]
    value = int(sensor.value) if float(sensor.value).is_integer() else round(sensor.value, 1)
    case = _case(
        "state_query",
        user_input="PM2.5 ngoài trời hiện là bao nhiêu?",
        reference_tool_calls=[{"name": "query_sensor", "args": {"sensor_slug": slug}}],
    )
    result = _result("answer", reply=f"PM2.5 hiện là {value}{sensor.unit}.")
    calls = [{"name": "query_sensor", "args": {"sensor_slug": slug}}]
    assert m_state_answer(case, result, calls).score == 1.0


def test_state_answer_registry_contract_error_is_none_with_error() -> None:
    case = _case(
        "state_query",
        user_input="Thiết bị đang bật hay tắt?",
        reference_tool_calls=[{"name": "query_device_state", "args": {"device_slug": "khong_co"}}],
    )
    metric = m_state_answer(
        case,
        _result("answer", reply="Đang tắt."),
        [{"name": "query_device_state", "args": {"device_slug": "khong_co"}}],
    )
    assert metric.score is None
    assert metric.infra_error


def test_evaluator_reads_device_registry_at_scoring_time(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_slug = "runtime_only_device"
    runtime_spec = SimpleNamespace(
        initial_state={"power": "on"},
        room="Phòng runtime",
        capabilities={Capability.ON_OFF},
    )
    monkeypatch.setattr(
        runtime_registry,
        "DEVICE_BY_SLUG",
        {**runtime_registry.DEVICE_BY_SLUG, runtime_slug: runtime_spec},
    )
    case = _case(
        "state_query",
        user_input="Thiết bị runtime đang bật hay tắt?",
        reference_tool_calls=[{"name": "query_device_state", "args": {"device_slug": runtime_slug}}],
    )
    calls = [{"name": "query_device_state", "args": {"device_slug": runtime_slug}}]
    assert m_state_answer(case, _result("answer", reply="Thiết bị đang bật."), calls).score == 1.0


def test_goal_plan_alignment_accepts_runtime_grounding() -> None:
    slug = "den_chum_phong_khach"
    room = runtime_registry.DEVICE_BY_SLUG[slug].room
    case = _case("goal_oriented", user_input="Làm phòng này sáng hơn", context={"current_room": room})
    metric = m_goal_plan_alignment(
        case,
        _result(
            "candidate_plan",
            actions=[_action(slug, Capability.BRIGHTNESS)],
            semantic_goal=_goal(room),
        ),
        [],
    )
    assert metric.score == 1.0


@pytest.mark.parametrize(
    ("actions", "goal_factory", "failure_mode"),
    [
        ([_action("khong_co", Capability.ON_OFF)], lambda room, slug: _goal(room), "hallucinated_device"),
        (
            [_action("den_chum_phong_khach", Capability.TEMPERATURE)],
            lambda room, slug: _goal(room),
            "unsupported_capability",
        ),
        (
            [_action("den_chum_phong_khach", Capability.ON_OFF)],
            lambda room, slug: _goal(room, excluded=[slug]),
            "forbidden_device_touched",
        ),
        (
            [_action("den_chum_phong_khach", Capability.ON_OFF)],
            lambda room, slug: _goal(room, no_change=[slug]),
            "forbidden_device_touched",
        ),
    ],
)
def test_goal_plan_alignment_rejects_invalid_device_capability_and_constraints(
    actions: list[SimpleNamespace], goal_factory, failure_mode: str
) -> None:
    slug = "den_chum_phong_khach"
    room = runtime_registry.DEVICE_BY_SLUG[slug].room
    case = _case("goal_oriented", user_input="Làm phòng này dễ chịu", context={"current_room": room})
    metric = m_goal_plan_alignment(
        case,
        _result("candidate_plan", actions=actions, semantic_goal=goal_factory(room, slug)),
        [],
    )
    assert metric.score == 0.0
    assert metric.failure_mode == failure_mode


def test_goal_plan_alignment_rejects_wrong_room_and_missing_plan() -> None:
    slug = "den_chum_phong_khach"
    actual_room = runtime_registry.DEVICE_BY_SLUG[slug].room
    other_room = next(room for room in runtime_registry.ROOMS if room != actual_room)
    case = _case("goal_oriented", user_input="Làm phòng này sáng hơn", context={"current_room": other_room})
    wrong_room = m_goal_plan_alignment(
        case,
        _result("candidate_plan", actions=[_action(slug, Capability.ON_OFF)], semantic_goal=_goal(other_room)),
        [],
    )
    assert wrong_room.failure_mode == "wrong_room_grounding"

    missing = m_goal_plan_alignment(case, _result("no_action"), [])
    assert missing.score == 0.0
    assert missing.failure_mode == "plan_collapsed_to_no_action"


def test_goal_category_marks_end_state_unavailable_without_using_candidate_plan() -> None:
    slug = "den_chum_phong_khach"
    room = runtime_registry.DEVICE_BY_SLUG[slug].room
    metrics = score_case_deterministic(
        _case("goal_oriented", user_input="Làm phòng này sáng hơn", context={"current_room": room}),
        _result("candidate_plan", actions=[_action(slug, Capability.ON_OFF)], semantic_goal=_goal(room)),
        [],
    )
    assert metrics["goal_plan_alignment"].score == 1.0
    assert metrics["end_state_goal_accuracy"].score is None
    assert "unavailable_due_to_harness" in metrics["end_state_goal_accuracy"].detail


class _BrokenMetric:
    strict_order = False

    async def multi_turn_ascore(self, sample) -> float:  # noqa: ANN001
        raise TimeoutError("judge timeout")


def test_tool_infrastructure_error_is_none_not_zero() -> None:
    scores = asyncio.run(
        _tool_scores(object(), _case("single_control"), _BrokenMetric(), _BrokenMetric())
    )
    assert scores["tool_call_accuracy"].score is None
    assert "TimeoutError" in (scores["tool_call_accuracy"].infra_error or "")
    assert scores["tool_call_f1"].score is None


def test_repeat_fold_preserves_rate_failure_mode_and_ignores_none() -> None:
    folded = _fold_metric(
        [
            Metric(1.0, failure_mode="off_domain_tool_call"),
            Metric(None, infra_error="timeout"),
            Metric(1.0, failure_mode="off_domain_tool_call"),
        ]
    )
    assert folded.score == 1.0
    assert folded.failure_mode == "off_domain_tool_call"
    assert folded.infra_error == "timeout"


def test_report_separates_infrastructure_diagnostics_and_primary_failures() -> None:
    rows = [
        {
            "case_id": "SH-pass",
            "category": "single_control",
            "user_input": "Bật đèn",
            "tool_metrics_applicable": True,
            "metrics": {
                "tool_call_accuracy": Metric(1.0),
                "tool_call_f1": Metric(None, infra_error="timeout"),
                "agent_goal_accuracy": Metric(0.0, failure_mode="diagnostic_only"),
            },
        },
        {
            "case_id": "SH-ood",
            "category": "topic_off_domain",
            "user_input": "Viết email",
            "tool_metrics_applicable": False,
            "metrics": {
                "out_of_domain_refusal_accuracy": Metric(
                    0.0, "thiếu từ chối", failure_mode="missing_boundary_statement"
                ),
                "unexpected_tool_call_rate": Metric(
                    1.0, "gọi tool", failure_mode="off_domain_tool_call"
                ),
                "topic_adherence": Metric(0.0, failure_mode="diagnostic_only"),
            },
        },
    ]
    report = build_report(rows, meta={}, judged=True)

    assert report["primary_metrics"]["tool_execution"]["tool_call_accuracy"]["mean"] == 1.0
    assert report["primary_metrics"]["tool_execution"].get("tool_call_f1") is None
    assert report["infrastructure_errors"]["tool_call_f1"][0]["case_id"] == "SH-pass"
    assert {failure["case_id"] for failure in report["known_agent_failures"]} == {"SH-ood"}
    assert report["failure_analysis"]["affected_case_count"] == 1
    assert len(report["failure_analysis"]["by_case_id"]["SH-ood"]["failures"]) == 2
    assert report["diagnostic_metrics"]["agent_goal_accuracy"]["gating"] is False
    assert report["diagnostic_metrics"]["topic_adherence"]["gating"] is False
    assert report["diagnostic_metrics"]["end_state_goal_accuracy"]["status"] == "unavailable_due_to_harness"


def test_report_taxonomy_explains_tool_mismatch_from_case_trace() -> None:
    report = build_report(
        [
            {
                "case_id": "SH-tool",
                "category": "single_control",
                "user_input": "Bật đèn",
                "tool_metrics_applicable": True,
                "agent_tool_calls": [],
                "reference_tool_calls": [_call("den_chum_phong_khach")],
                "metrics": {
                    "tool_call_accuracy": Metric(0.0),
                    "tool_call_f1": Metric(0.0),
                },
            }
        ],
        meta={},
        judged=False,
    )
    assert report["failure_analysis"]["by_failure_mode"] == {"missing_tool_call": 2}
    failures = report["failure_analysis"]["by_case_id"]["SH-tool"]["failures"]
    assert all("expected=" in failure["detail"] for failure in failures)


def test_deterministic_report_has_required_top_level_contract() -> None:
    report = build_report(
        [
            {
                "case_id": "SH-goal",
                "category": "goal_oriented",
                "user_input": "Đi ngủ thôi",
                "tool_metrics_applicable": False,
                "metrics": {
                    "goal_plan_alignment": Metric(1.0),
                    "end_state_goal_accuracy": Metric(None, "unavailable_due_to_harness"),
                },
            }
        ],
        meta={"n_cases": 1},
        judged=False,
    )
    assert {
        "primary_metrics",
        "category_metrics",
        "failure_analysis",
        "known_agent_failures",
        "metric_applicability",
        "infrastructure_errors",
        "diagnostic_metrics",
    } <= report.keys()
    assert report["primary_metrics"]["end_to_end"] == {
        "end_state_goal_accuracy": None,
        "status": "unavailable_due_to_harness",
    }
    assert report["diagnostic_metrics"]["agent_goal_accuracy"]["status"] == "not_run_in_deterministic_baseline"
    assert report["diagnostic_metrics"]["topic_adherence"]["status"] == "not_run_in_deterministic_baseline"
