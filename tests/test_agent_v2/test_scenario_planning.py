"""Open-ended activities are authored as complete, multi-domain plans."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.agent.perception.context_builder import build_perception
from src.agent.pipeline import PipelineDeps, run_planning
from src.agent.schemas import DesiredOutcome, SemanticGoal
from src.agent.understanding import goal_author
from src.core.interfaces import DeviceSelector
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType


def _state(message: str, goal: SemanticGoal) -> dict:
    return {
        "conversation_id": f"scenario-{message}",
        "user_id": "user_A",
        "user_message": message,
        "speaker_role": "owner",
        "speaker_location": "Phòng khách",
        "now": datetime(2026, 8, 20, 20, 0, tzinfo=UTC),
        "semantic_goal": goal,
        "live_device_states": {
            "den_chum_phong_khach": {"power": "on", "brightness": 90},
            "tv_phong_khach": {"power": "off", "volume": 25},
            "loa_phong_khach": {"power": "off", "volume": 30},
            "robot_hut_bui": {"power": "off", "battery": 92},
        },
    }


def _goal(message: str, outcomes: list[DesiredOutcome]) -> SemanticGoal:
    return SemanticGoal(
        intent="prepare an activity",
        raw_utterance=message,
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="prepare the living room for the requested activity",
        confidence=0.92,
        target_area="Phòng khách",
        desired_outcomes=outcomes,
    )


def _action_keys(result: dict) -> set[tuple[str, str, str]]:
    selected = result["selected_plan"]
    assert selected is not None
    return {(action.device_id, action.capability, action.action) for action in selected.actions}


def test_content_activity_plan_coordinates_display_audio_and_lighting():
    """Planner consumes semantic outcomes; it does not recognize a hard-coded phrase."""
    message = "cho tôi một không gian phù hợp để thưởng thức nội dung dài"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="tv"),
                target_state={"power": "on"},
                rationale="display ready",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="speaker"),
                target_state={"power": "on", "volume": 35},
                rationale="comfortable audio",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="light"),
                relative_change={"brightness": "decrease"},
                rationale="reduce glare",
            ),
        ],
    )

    result = run_planning(_state(message, goal), PipelineDeps(model_client=FakeReasoningModel()))
    actions = _action_keys(result)

    assert ("tv_phong_khach", "on_off", "turn_on") in actions
    assert ("loa_phong_khach", "on_off", "turn_on") in actions
    assert ("loa_phong_khach", "volume", "set") in actions
    assert any(device == "den_chum_phong_khach" and capability == "brightness" for device, capability, _ in actions)


def test_social_space_plan_coordinates_cleaning_media_and_lighting():
    message = "hãy chuẩn bị khu vực chung cho một buổi gặp gỡ"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="vacuum"),
                target_state={"power": "on"},
                rationale="clean the shared space",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="speaker"),
                target_state={"power": "on", "volume": 30},
                rationale="background audio",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="tv"),
                target_state={"power": "on"},
                rationale="ambient display",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="light"),
                target_state={"power": "on"},
                rationale="welcoming light",
            ),
        ],
    )

    result = run_planning(_state(message, goal), PipelineDeps(model_client=FakeReasoningModel()))
    actions = _action_keys(result)
    acted_devices = {device for device, _, _ in actions}

    assert {
        "robot_hut_bui",
        "loa_phong_khach",
        "tv_phong_khach",
        "den_chum_phong_khach",
    } <= acted_devices


@pytest.mark.parametrize("media_domain", ["media_player", "shared_entertainment"])
def test_social_hosting_uses_clean_air_cooling_and_one_entertainment_source(media_domain: str):
    """Activity semantics use roles/cardinality, not a phrase-to-device scene."""
    message = "chuẩn bị không gian chung để mọi người ghé chơi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="vacuum"),
                target_state={"power": "on"},
                rationale="clean shared floor",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="air_conditioner"),
                relative_change={"temperature": "decrease_slight"},
                rationale="comfortable cool temperature",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="air_purifier"),
                target_state={"power": "on"},
                rationale="fresh shared air",
            ),
            DesiredOutcome(
                selector=DeviceSelector(
                    area="Phòng khách",
                    domain=media_domain,
                    labels=["shared_entertainment"],
                ),
                target_state={"power": "on"},
                cardinality="any",
                rationale="one shared entertainment source",
            ),
        ],
    )

    result = run_planning(_state(message, goal), PipelineDeps(model_client=FakeReasoningModel()))
    actions = _action_keys(result)
    acted_devices = {device for device, _, _ in actions}
    media_devices = acted_devices & {"tv_phong_khach", "loa_phong_khach"}

    assert {"robot_hut_bui", "dieu_hoa_phong_khach", "may_loc_phong_khach"} <= acted_devices
    assert len(media_devices) == 1


def test_relative_change_with_exact_selector_is_handled_by_specialist():
    message = "làm thiết bị làm mát cụ thể này dịu hơn một chút"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(
                    device_id="dieu_hoa_phong_khach",
                    area="Phòng khách",
                    domain="air_conditioner",
                ),
                relative_change={"temperature": "decrease_slight"},
                rationale="cool the exact target",
            )
        ],
    )

    result = run_planning(_state(message, goal), PipelineDeps(model_client=FakeReasoningModel()))
    actions = _action_keys(result)

    assert any(
        device == "dieu_hoa_phong_khach" and capability == "temperature"
        for device, capability, _action in actions
    )


def test_critical_existing_load_does_not_double_count_devices_already_on():
    message = "prepare the shared space without adding electrical load"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="vacuum"),
                target_state={"power": "on"},
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="speaker"),
                target_state={"power": "on"},
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="tv"),
                target_state={"power": "on"},
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"].update(
        {
            "tv_phong_khach": {"power": "on", "volume": 25},
            "loa_phong_khach": {"power": "on", "volume": 30},
            "robot_hut_bui": {"power": "on", "battery": 92},
            "dieu_hoa_phong_khach": {"power": "on", "temperature": 23},
            "binh_nong_lanh": {"power": "on", "temperature": 45},
            "may_rua_bat": {"power": "on", "program": "normal"},
            "dieu_hoa_phong_bo_me": {"power": "on", "temperature": 25},
            "dieu_hoa_phong_con": {"power": "on", "temperature": 26},
        }
    )

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))

    assert result["power_load"].mode == "CRITICAL"
    assert result["selected_plan"] is not None
    assert result["selected_plan"].estimated_power_w == 0


def test_critical_routine_keeps_load_neutral_steps_and_drops_positive_load_step():
    message = "prepare the shared space under critical load"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="vacuum"),
                target_state={"power": "on"},
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="light"),
                relative_change={"brightness": "increase"},
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"].update(
        {
            "robot_hut_bui": {"power": "off", "battery": 92},
            "dieu_hoa_phong_khach": {"power": "on", "temperature": 23},
            "binh_nong_lanh": {"power": "on", "temperature": 45},
            "may_rua_bat": {"power": "on", "program": "normal"},
            "dieu_hoa_phong_bo_me": {"power": "on", "temperature": 25},
            "dieu_hoa_phong_con": {"power": "on", "temperature": 26},
        }
    )

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))

    assert result["power_load"].mode == "CRITICAL"
    selected = result["selected_plan"]
    assert selected is not None
    assert selected.plan_id.startswith("load_neutral_")
    assert any(action.device_id == "den_chum_phong_khach" for action in selected.actions)
    assert all(action.device_id != "robot_hut_bui" for action in selected.actions)


def test_goal_author_uses_dedicated_planning_reasoning_effort(monkeypatch):
    class RecordingModel:
        def __init__(self):
            self.planning_profiles: list[tuple[str, str, float]] = []

        @contextmanager
        def use_planning_profile(self, planning_model: str, effort: str, timeout: float):
            self.planning_profiles.append((planning_model, effort, timeout))
            yield

        def structured_generate_sync(self, prompt, schema, **kwargs):  # noqa: ANN001, ARG002
            return SemanticGoal(
                intent="prepare activity",
                raw_utterance="prepare",
                utterance_type=UtteranceType.ROUTINE_INTENT,
                goal_description="prepare an activity",
                confidence=0.9,
                desired_outcomes=[
                    DesiredOutcome(
                        selector=DeviceSelector(domain="tv"),
                        target_state={"power": "on"},
                    )
                ],
            )

    monkeypatch.setattr(
        goal_author,
        "get_settings",
        lambda: SimpleNamespace(
            llm_planning_model="planning-model",
            llm_planning_reasoning_effort="high",
            llm_planning_timeout_seconds=75.0,
        ),
    )
    normalized, context, _ = build_perception(
        "chuẩn bị hoạt động",
        speaker_location="Phòng khách",
    )
    model = RecordingModel()

    authored = goal_author.author_goal(
        model,
        normalized,
        context,
        utterance="chuẩn bị hoạt động",
    )

    assert authored is not None
    assert model.planning_profiles == [("planning-model", "high", 75.0)]


def test_semantic_goal_cache_skips_second_llm_call_but_refinalizes_live_scope(monkeypatch):
    from src.agent.understanding.semantic_cache import SemanticGoalCache

    class CountingModel:
        def __init__(self):
            self.calls = 0

        def structured_generate_sync(self, prompt, schema, **kwargs):  # noqa: ANN001, ARG002
            self.calls += 1
            return SemanticGoal(
                intent="watch content",
                raw_utterance="cached-source",
                utterance_type=UtteranceType.ROUTINE_INTENT,
                goal_description="prepare media",
                confidence=0.9,
                desired_outcomes=[
                    DesiredOutcome(selector=DeviceSelector(domain="tv"), target_state={"power": "on"})
                ],
            )

    monkeypatch.setattr(
        goal_author,
        "get_settings",
        lambda: SimpleNamespace(
            llm_planning_model="planning-model",
            llm_planning_reasoning_effort="medium",
            llm_planning_timeout_seconds=40.0,
        ),
    )
    normalized, context, _ = build_perception("tôi muốn xem phim", speaker_location="Phòng khách")
    model = CountingModel()
    cache = SemanticGoalCache(ttl_seconds=60, max_entries=8)

    first = goal_author.author_goal(model, normalized, context, utterance="tôi muốn xem phim", semantic_cache=cache)
    second = goal_author.author_goal(model, normalized, context, utterance="tôi muốn xem phim", semantic_cache=cache)

    assert first is not None and second is not None
    assert model.calls == 1
    assert second.raw_utterance == "tôi muốn xem phim"
    assert second.target_area == "Phòng khách"
    assert cache.stats()["hits"] == 1


def test_sleep_activity_uses_home_room_over_anonymous_presence_and_keeps_glare_relative(monkeypatch):
    class SleepContextModel:
        def structured_generate_sync(self, prompt, schema, **kwargs):  # noqa: ANN001, ARG002
            return SemanticGoal(
                intent="environment.adjust",
                raw_utterance="ôi đang ngủ mà đèn chói quá",
                utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
                goal_description="reduce glare while sleeping",
                activity_context="sleeping",
                confidence=0.9,
                desired_outcomes=[
                    DesiredOutcome(
                        selector=DeviceSelector(domain="light"),
                        target_state={"power": "off", "brightness": 0},
                        relative_change={"brightness": "decrease"},
                        perceived_state="too_bright",
                    )
                ],
            )

    monkeypatch.setattr(
        goal_author,
        "get_settings",
        lambda: SimpleNamespace(
            llm_planning_model="planning-model",
            llm_planning_reasoning_effort="low",
            llm_planning_timeout_seconds=40.0,
        ),
    )
    normalized, context, _ = build_perception(
        "ôi đang ngủ mà đèn chói quá",
        speaker_location="Phòng khách",
        speaker_location_source="presence_sensor",
        speaker_location_confidence=0.6,
        speaker_home_room="Phòng ngủ bố mẹ",
    )

    authored = goal_author.author_goal(
        SleepContextModel(),
        normalized,
        context,
        utterance="ôi đang ngủ mà đèn chói quá",
    )

    assert authored is not None
    assert authored.activity_context == "sleeping"
    assert authored.target_area == "Phòng ngủ bố mẹ"
    assert authored.target_area_source == "activity_home_room"
    assert authored.desired_outcomes[0].relative_change == {"brightness": "decrease"}
    assert authored.desired_outcomes[0].target_state == {}


def test_identity_bound_capture_location_wins_over_sleep_home_room(monkeypatch):
    class SleepContextModel:
        def structured_generate_sync(self, prompt, schema, **kwargs):  # noqa: ANN001, ARG002
            return SemanticGoal(
                intent="environment.adjust",
                raw_utterance="đang ngủ và thấy chói",
                utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
                activity_context="sleeping",
                confidence=0.9,
                desired_outcomes=[
                    DesiredOutcome(
                        selector=DeviceSelector(domain="light"),
                        relative_change={"brightness": "decrease"},
                    )
                ],
            )

    monkeypatch.setattr(
        goal_author,
        "get_settings",
        lambda: SimpleNamespace(
            llm_planning_model="planning-model",
            llm_planning_reasoning_effort="low",
            llm_planning_timeout_seconds=40.0,
        ),
    )
    normalized, context, _ = build_perception(
        "đang ngủ và thấy chói",
        speaker_location="Phòng khách",
        speaker_location_source="capture_device",
        speaker_location_confidence=0.9,
        speaker_home_room="Phòng ngủ bố mẹ",
    )

    authored = goal_author.author_goal(
        SleepContextModel(), normalized, context, utterance="đang ngủ và thấy chói"
    )

    assert authored is not None
    assert authored.target_area == "Phòng khách"
    assert authored.target_area_source == "speaker_capture"


def test_sleep_glare_plan_only_adjusts_home_bedroom_lights():
    message = "ôi đang ngủ mà đèn chói quá"
    goal = SemanticGoal(
        intent="environment.adjust",
        raw_utterance=message,
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        goal_description="reduce glare while sleeping",
        activity_context="sleeping",
        target_area="Phòng ngủ bố mẹ",
        target_area_source="activity_home_room",
        confidence=0.9,
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(domain="light"),
                relative_change={"brightness": "decrease"},
                perceived_state="too_bright",
            )
        ],
    )
    state = {
        "conversation_id": "sleep-home-room",
        "user_id": "user_A",
        "user_message": message,
        "speaker_role": "owner",
        "speaker_location": "Phòng khách",
        "speaker_location_source": "presence_sensor",
        "speaker_location_confidence": 0.6,
        "speaker_home_room": "Phòng ngủ bố mẹ",
        "now": datetime(2026, 8, 20, 23, 0, tzinfo=UTC),
        "semantic_goal": goal,
        "live_device_states": {
            "den_chum_phong_khach": {"power": "on", "brightness": 90},
            "den_ngu_bo_me": {"power": "on", "brightness": 55},
            "den_ban_lam_viec": {"power": "off", "brightness": 0},
        },
    }

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    actions = result["selected_plan"].actions

    assert actions
    assert all(action.device_id != "den_chum_phong_khach" for action in actions)
    bedroom = next(action for action in actions if action.device_id == "den_ngu_bo_me")
    assert 0 < bedroom.target["brightness"] < 55


def test_sleep_routine_keeps_media_off_and_curtain_selector_does_not_close_window():
    """Regression from the production envelope for 'tôi buồn ngủ rồi'."""
    message = "tôi buồn ngủ rồi"
    goal = SemanticGoal(
        intent="prepare sleep",
        raw_utterance=message,
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="Chuẩn bị phòng ngủ bố mẹ để đi ngủ",
        activity_context="sleeping",
        target_area="Phòng ngủ bố mẹ",
        confidence=0.9,
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="light"),
                relative_change={"brightness": "decrease_large"},
                cardinality="all",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="tv"),
                target_state={"power": "off"},
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="speaker"),
                relative_change={"volume": "decrease_large"},
                cardinality="any",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="curtain"),
                target_state={"position": "closed"},
                cardinality="all",
            ),
        ],
    )
    state = _state(message, goal)
    state["speaker_location"] = "Phòng ngủ bố mẹ"
    state["live_device_states"].update(
        {
            "den_ngu_bo_me": {"power": "on", "brightness": 47},
            "den_ban_lam_viec": {"power": "on", "brightness": 47},
            "tv_phong_bo_me": {"power": "off", "volume": 20},
            "cua_so_phong_bo_me": {"position": 0},
            "rem_phong_bo_me": {"position": 100},
        }
    )

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    selected = result["selected_plan"]
    assert selected is not None
    actions = selected.actions

    assert any(action.device_id == "tv_phong_bo_me" and action.action == "turn_off" for action in actions)
    assert not any(action.device_id == "tv_phong_bo_me" and action.action == "turn_on" for action in actions)
    assert any(action.device_id == "rem_phong_bo_me" for action in actions)
    assert not any(action.device_id == "cua_so_phong_bo_me" for action in actions)


def test_sleep_activity_hard_guard_drops_model_authored_media_activation(monkeypatch):
    class UnsafeSleepModel:
        def structured_generate_sync(self, prompt, schema, **kwargs):  # noqa: ANN001, ARG002
            return SemanticGoal(
                intent="prepare sleep",
                raw_utterance="placeholder",
                utterance_type=UtteranceType.ROUTINE_INTENT,
                goal_description="prepare a quiet bedroom",
                activity_context="sleeping",
                confidence=0.9,
                desired_outcomes=[
                    DesiredOutcome(
                        selector=DeviceSelector(domain="tv"),
                        target_state={"power": "on"},
                    ),
                    DesiredOutcome(
                        selector=DeviceSelector(domain="speaker"),
                        target_state={"power": "on", "volume": 30},
                    ),
                    DesiredOutcome(
                        selector=DeviceSelector(domain="tv"),
                        target_state={"power": "off"},
                    ),
                ],
            )

    monkeypatch.setattr(
        goal_author,
        "get_settings",
        lambda: SimpleNamespace(
            llm_planning_model="planning-model",
            llm_planning_reasoning_effort="low",
            llm_planning_timeout_seconds=40.0,
        ),
    )
    normalized, context, _ = build_perception(
        "tôi buồn ngủ rồi",
        speaker_home_room="Phòng ngủ bố mẹ",
    )

    authored = goal_author.author_goal(
        UnsafeSleepModel(), normalized, context, utterance="tôi buồn ngủ rồi"
    )

    assert authored is not None
    media = [outcome for outcome in authored.desired_outcomes if outcome.selector.domain in {"tv", "speaker"}]
    assert len(media) == 1
    assert media[0].selector.domain == "tv"
    assert media[0].target_state == {"power": "off"}


def test_bathing_routine_turns_on_the_single_water_heater_from_another_room() -> None:
    """"Đi tắm" nói từ phòng ngủ vẫn bật được bình nóng lạnh (thiết bị đơn nhất ở Phòng khách).

    Trước fix: outcome water_heater bị `_room_of_selector` ghim về `target_area` (phòng ngủ),
    bộ lọc phòng của specialist loại sạch ứng viên → outcome rơi mất, plan quay ra chỉnh đèn.
    """
    message = "tôi chuẩn bị đi tắm"
    goal = SemanticGoal(
        intent="prepare hot water for a bath",
        raw_utterance=message,
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="chuẩn bị nước nóng để tắm",
        activity_context="bathing",
        confidence=0.9,
        target_area="Phòng ngủ bố mẹ",
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area=None, domain="water_heater"),
                target_state={"power": "on"},
                cardinality="one",
                rationale="nước nóng để tắm",
            )
        ],
    )
    state = _state(message, goal)
    state["speaker_location"] = "Phòng ngủ bố mẹ"
    state["live_device_states"]["binh_nong_lanh"] = {"power": "off"}

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    actions = _action_keys(result)

    assert ("binh_nong_lanh", "on_off", "turn_on") in actions
    assert not any(cap == "brightness" for _device, cap, _action in actions)
    assert not any(device.startswith("may_loc") for device, _cap, _action in actions)


def test_shutter_specialist_accepts_semantic_string_position() -> None:
    from src.agent.planning.manager import Subgoal
    from src.agent.specialists.shutter import ShutterAgent

    _, context, _ = build_perception("chuẩn bị xem phim", speaker_location="Phòng khách")
    subgoal = Subgoal(
        dimension="openness",
        target_state={"position": "closed"},
        direction=None,
        room="Phòng khách",
        rationale="Giảm chói",
    )
    proposal = ShutterAgent().propose(subgoal, context)
    assert proposal is not None
    assert proposal.actions
    assert all(action.action == "close" for action in proposal.actions)


def test_routine_power_off_outcome_turns_devices_off_across_domains() -> None:
    """Outcome suy diễn `power=off` phải TẮT, kể cả ở domain phục vụ chiều tiện nghi.

    Hồi quy: lighting/ac/purifier không đọc `target_state['power']` nên "đi ngủ"/"ra ngoài"
    rơi vào nhánh "không có ràng buộc chiều" và dựng ngược lại — đèn sáng 100%, điều hoà
    26°C. Luật "tắt" nay nằm một chỗ ở Specialist base.
    """
    message = "chuẩn bị không gian để nghỉ ngơi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="light"),
                target_state={"power": "off"},
                rationale="tắt đèn không cần thiết",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="air_conditioner"),
                target_state={"power": "off"},
                rationale="tắt điều hoà",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="air_purifier"),
                target_state={"power": "off"},
                rationale="tắt máy lọc",
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"].update({
        "dieu_hoa_phong_khach": {"power": "on", "temperature": 24},
        "may_loc_phong_khach": {"power": "on"},
    })

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    actions = _action_keys(result)

    assert ("den_chum_phong_khach", "on_off", "turn_off") in actions
    assert ("dieu_hoa_phong_khach", "on_off", "turn_off") in actions
    assert ("may_loc_phong_khach", "on_off", "turn_off") in actions
    # Không được dựng hành động làm thiết bị SÁNG/ẤM lên khi mục tiêu là tắt.
    assert not any(action == "set" for _, _, action in actions)
    assert not any(action == "turn_on" for _, _, action in actions)


def test_inferred_power_off_reaches_domains_without_a_comfort_specialist() -> None:
    """Bình nóng lạnh không thuộc chiều tiện nghi nào — outcome tắt nó từng bị rơi im lặng."""
    message = "cả nhà chuẩn bị rời đi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="water_heater"),
                target_state={"power": "off"},
                rationale="tắt bình nóng lạnh khi rời nhà",
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"]["binh_nong_lanh"] = {"power": "on"}

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    assert ("binh_nong_lanh", "on_off", "turn_off") in _action_keys(result)


def test_inferred_power_off_never_touches_security_devices() -> None:
    """"Tắt" suy diễn không được chạm khoá/camera — chỉ outcome KHOÁ tường minh mới tới."""
    message = "cả nhà chuẩn bị rời đi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="door_lock"),
                target_state={"power": "off"},
                rationale="mục tiêu suy diễn chạm an ninh",
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"]["khoa_cua_chinh"] = {"lock": "unlocked"}

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    selected = result["selected_plan"]
    acted = {action.device_id for action in selected.actions} if selected else set()
    assert "khoa_cua_chinh" not in acted
    assert "camera_cua_chinh" not in acted


def test_departure_routine_proposes_locking_the_door() -> None:
    """Quyết định sản phẩm: routine rời nhà ĐƯỢC đề xuất khoá cửa (vẫn qua authorization/policy)."""
    message = "cả nhà chuẩn bị rời đi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="door_lock"),
                target_state={"lock": "locked"},
                rationale="khoá cửa khi rời nhà",
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"]["khoa_cua_chinh"] = {"lock": "unlocked"}

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    assert ("khoa_cua_chinh", "lock", "lock") in _action_keys(result)


def test_inferred_goal_can_never_unlock_a_door() -> None:
    """Hướng NGƯỢC lại phải luôn bị chặn: suy diễn không bao giờ mở được khoá."""
    message = "chuẩn bị đón khách tới chơi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="door_lock"),
                target_state={"lock": "unlocked"},
                rationale="mở sẵn cửa cho khách",
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"]["khoa_cua_chinh"] = {"lock": "locked"}

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    selected = result["selected_plan"]
    acted = {action.device_id for action in selected.actions} if selected else set()
    assert "khoa_cua_chinh" not in acted


def test_departure_shutdown_reaches_every_room_not_just_the_speaker_room() -> None:
    """Outcome tắt cả nhóm, không nêu phòng → quét toàn nhà, không thu về phòng người nói."""
    message = "cả nhà chuẩn bị rời đi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(domain="light"),
                target_state={"power": "off"},
                cardinality="all",
                rationale="tắt mọi đèn khi rời nhà",
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"].update({
        "den_ngu_bo_me": {"power": "on", "brightness": 60},
        "den_bep": {"power": "on", "brightness": 80},
    })

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    acted = {device for device, _, _ in _action_keys(result)}
    assert {"den_chum_phong_khach", "den_ngu_bo_me", "den_bep"} <= acted


def test_activity_routine_that_turns_devices_on_stays_in_the_speaker_room() -> None:
    """Nới phạm vi CHỈ cho hướng tắt: routine bật vẫn giới hạn ở phòng sinh hoạt."""
    message = "chuẩn bị đón khách tới chơi"
    goal = _goal(
        message,
        [
            DesiredOutcome(
                selector=DeviceSelector(domain="light"),
                target_state={"power": "on"},
                cardinality="all",
                rationale="bật đèn đón khách",
            ),
        ],
    )
    state = _state(message, goal)
    state["live_device_states"].update({
        "den_ngu_bo_me": {"power": "off", "brightness": 0},
        "den_ngu_con": {"power": "off", "brightness": 0},
    })

    result = run_planning(state, PipelineDeps(model_client=FakeReasoningModel()))
    acted = {device for device, _, _ in _action_keys(result)}
    assert "den_ngu_bo_me" not in acted
    assert "den_ngu_con" not in acted
