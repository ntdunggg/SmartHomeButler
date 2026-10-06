"""Contract tests for the planner-only 200-case evaluator."""

from __future__ import annotations

from eval.eval_ragas_agent200 import (
    _METRIC_ORDER,
    _applicable,
    _fold_repeats,
    _live_sensors,
    _plan_signature,
    _reply_text,
    _report,
    run_case,
)
from src.domain.enums import ActionType, Capability
from src.nlu.schemas import CandidateAction, CandidatePlan
from src.services.pipeline_bridge import ReasoningResult


def test_metric_names_expose_planner_and_scope_semantics() -> None:
    assert _METRIC_ORDER == (
        "tool_call_accuracy",
        "tool_call_f1",
        "plan_semantic_accuracy",
        "scope_adherence",
    )


def test_metric_applicability_uses_existing_dataset_contract() -> None:
    case = {
        "tool_metrics_applicable": False,
        "goal_metric_applicable": True,
        "topic_metric_applicable": False,
    }
    assert _applicable(case, "plan_semantic_accuracy")
    assert not _applicable(case, "scope_adherence")
    assert not _applicable(case, "tool_call_accuracy")


def test_planner_reply_never_claims_execution() -> None:
    result = ReasoningResult(
        semantic_goal=None,
        candidate_plan=CandidatePlan(
            goal_summary="bật đèn",
            actions=[
                CandidateAction(
                    device_id="den_chum_phong_khach",
                    capability=Capability.ON_OFF,
                    action=ActionType.TURN_ON,
                )
            ],
        ),
        outcome="candidate_plan",
    )
    text = _reply_text(result)
    assert "chưa thực thi" in text
    assert "đã bật" not in text


def test_planner_reply_keeps_scope_refusal_beside_valid_plan() -> None:
    result = ReasoningResult(
        semantic_goal=None,
        candidate_plan=CandidatePlan(
            goal_summary="tắt TV",
            actions=[
                CandidateAction(
                    device_id="tv_phong_khach",
                    capability=Capability.ON_OFF,
                    action=ActionType.TURN_OFF,
                )
            ],
        ),
        outcome="candidate_plan",
        reply="Mình không thể thực hiện phần yêu cầu ngoài phạm vi nhà thông minh.",
    )
    text = _reply_text(result)
    assert "chưa thực thi" in text
    assert "ngoài phạm vi" in text


def test_report_uses_new_metric_names() -> None:
    rows = [
        {
            "category": "goal_oriented",
            "scores": {
                "tool_call_accuracy": None,
                "tool_call_f1": None,
                "plan_semantic_accuracy": 1.0,
                "scope_adherence": 0.5,
            },
        }
    ]
    report = _report(rows, judge_model="judge", offline=False)
    assert report["overall"]["plan_semantic_accuracy"] == 1.0
    assert report["overall"]["scope_adherence"] == 0.5
    assert "agent_goal_accuracy" not in report["overall"]
    assert "topic_adherence" not in report["overall"]


def test_sensor_overrides_are_applied_to_the_registry_snapshot() -> None:
    sensors = _live_sensors({"cam_bien_mua": 1.0})
    rain = next(sensor for sensor in sensors if sensor["slug"] == "cam_bien_mua")
    assert rain["value"] == 1.0


def test_dataset_current_room_reaches_the_pipeline_as_speaker_location(monkeypatch) -> None:
    captured = {}

    def fake_reason(**kwargs):
        captured.update(kwargs)
        return ReasoningResult(
            semantic_goal=None,
            candidate_plan=None,
            outcome="answer",
            reply="ok",
        )

    monkeypatch.setattr("eval.eval_ragas_agent200.reason", fake_reason)
    run_case(
        {
            "case_id": "context-contract",
            "user_input": "Đi ngủ thôi.",
            "context": {"current_room": "Phòng ngủ bố mẹ"},
        },
        model=object(),
    )
    assert captured["speaker_location"] == "Phòng ngủ bố mẹ"
    assert captured["live_sensors"] is None


def test_sensor_overrides_are_the_only_live_sensor_evidence(monkeypatch) -> None:
    captured = {}

    def fake_reason(**kwargs):
        captured.update(kwargs)
        return ReasoningResult(
            semantic_goal=None,
            candidate_plan=None,
            outcome="answer",
            reply="ok",
        )

    monkeypatch.setattr("eval.eval_ragas_agent200.reason", fake_reason)
    run_case(
        {
            "case_id": "sensor-contract",
            "user_input": "Mưa rồi.",
            "context": {"sensor_overrides": {"cam_bien_mua": 1.0}},
        },
        model=object(),
    )
    rain = next(sensor for sensor in captured["live_sensors"] if sensor["slug"] == "cam_bien_mua")
    assert rain["value"] == 1.0


# --- Kiểm soát nhiễu: cùng một quyết định phải cho cùng một chữ ký ---------------------
def _result(outcome: str = "candidate_plan") -> ReasoningResult:
    return ReasoningResult(semantic_goal=None, candidate_plan=None, outcome=outcome)


def _call(slug: str, capability: str, value) -> dict:  # noqa: ANN001
    return {"name": "control_device", "args": {"device_slug": slug, "capability": capability, "value": value}}


def test_plan_signature_ignores_call_order() -> None:
    """Đảo thứ tự hai hành động độc lập KHÔNG phải là một quyết định khác."""
    forward = [_call("den_chum_phong_khach", "on_off", "on"), _call("tv_phong_khach", "on_off", "off")]
    assert _plan_signature(_result(), forward) == _plan_signature(_result(), list(reversed(forward)))


def test_plan_signature_separates_a_different_device_or_value() -> None:
    base = [_call("den_chum_phong_khach", "brightness", 40)]
    assert _plan_signature(_result(), base) != _plan_signature(_result(), [_call("den_ban_an", "brightness", 40)])
    assert _plan_signature(_result(), base) != _plan_signature(_result(), [_call("den_chum_phong_khach", "brightness", 70)])
    assert _plan_signature(_result(), base) != _plan_signature(_result("clarification"), base)


def test_repeat_scores_fold_to_the_median_not_the_mean() -> None:
    """Hai lượt đúng, một lượt sai → 1.0. Trung bình sẽ ra 0.67, một điểm không lượt nào đạt."""
    folded = _fold_repeats(
        [
            {"plan_semantic_accuracy": 1.0, "scope_adherence": 1.0},
            {"plan_semantic_accuracy": 0.0, "scope_adherence": 0.5},
            {"plan_semantic_accuracy": 1.0, "scope_adherence": 1.0},
        ]
    )
    assert folded["plan_semantic_accuracy"] == 1.0
    assert folded["scope_adherence"] == 1.0


def test_unscorable_runs_do_not_count_as_zero() -> None:
    """None = KHÔNG chấm được (không áp dụng/lỗi judge). Trộn nó thành 0 biến một lỗi hạ tầng
    thành một "hồi quy chất lượng" giả."""
    folded = _fold_repeats(
        [
            {"plan_semantic_accuracy": 1.0},
            {"plan_semantic_accuracy": None, "plan_semantic_accuracy__error": "timeout"},
            {"plan_semantic_accuracy": 1.0},
        ]
    )
    assert folded["plan_semantic_accuracy"] == 1.0
    assert folded["plan_semantic_accuracy__error"] == "timeout"


def test_a_metric_no_run_could_score_stays_unscored() -> None:
    folded = _fold_repeats([{"plan_semantic_accuracy": None}, {"plan_semantic_accuracy": None}])
    assert folded["plan_semantic_accuracy"] is None


def test_report_records_the_noise_control_actually_used() -> None:
    rows = [
        {
            "case_id": "SH-001",
            "category": "goal_oriented",
            "plan_stability": 1.0,
            "scores": {"plan_semantic_accuracy": 1.0},
        },
        {
            "case_id": "SH-002",
            "category": "goal_oriented",
            "plan_stability": 2 / 3,
            "scores": {"plan_semantic_accuracy": 0.0},
        },
    ]
    report = _report(
        rows,
        judge_model="judge",
        offline=False,
        noise={"agent_temperature": 0.0, "repeat": 3, "score_aggregation": "median_low"},
    )
    assert report["noise_control"]["agent_temperature"] == 0.0
    assert report["noise_control"]["repeat"] == 3
    assert report["noise_control"]["unstable_case_ids"] == ["SH-002"]


def test_report_dem_do_rong_ke_hoach_de_thay_sinh_thua() -> None:
    """Judge mù trước sinh-thừa, nên báo cáo phải có một tín hiệu TẤT ĐỊNH cho nó.

    Đo live 2026-08-31: SH-126 "dọn nhà giúp tôi" bật thừa điều hoà + máy lọc + TV mà
    `plan_semantic_accuracy` vẫn 1.0 và `scope_adherence` vẫn 0.5. Nếu chỉ nhìn hai điểm đó
    thì một bản vá đúng trông như không có tác dụng.
    """
    rows = [
        {
            "case_id": "SH-126",
            "category": "goal_oriented",
            "agent_tool_calls": [
                {"name": "control_device", "args": {"device_slug": "robot_hut_bui"}},
                {"name": "control_device", "args": {"device_slug": "tv_phong_khach"}},
                {"name": "control_device", "args": {"device_slug": "tv_phong_khach"}},
            ],
            "scores": {},
        },
        {
            "case_id": "SH-130",
            "category": "goal_oriented",
            "agent_tool_calls": [{"name": "control_device", "args": {"device_slug": "den_chum_phong_khach"}}],
            "scores": {},
        },
    ]

    report = _report(rows, judge_model="judge", offline=True)

    # Hai lời gọi lên CÙNG một thiết bị vẫn là một thiết bị bị chạm: đếm theo TẬP, không theo
    # số lời gọi, để chỉ số không phồng lên chỉ vì kế hoạch tách bật + đặt mức thành hai bước.
    assert report["plan_breadth"]["devices_touched_total"] == 3
    assert report["plan_breadth"]["devices_per_plan_mean"] == 1.5
    assert report["plan_breadth"]["by_category"]["goal_oriented"] == 1.5
