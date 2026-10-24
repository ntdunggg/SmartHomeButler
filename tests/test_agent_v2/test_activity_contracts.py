"""Contract hoạt động chỉ VÁ THIẾU SÓT, không DỰNG một nếp.

`_complete_activity_contract` sinh ra để cứu một nếp phủ cả không gian mà model quên đúng một
khía cạnh (đón khách nhưng quên máy lọc không khí). Đo live 2026-08-31 cho thấy nó còn làm
thêm một việc KHÔNG được phép: với "dọn nhà giúp tôi" model soạn đúng một outcome
`vacuum power=on` — khớp y hệt reference — rồi contract thêm điều hoà + máy lọc + TV; với
"nghe nhạc ở phòng khách" nó thêm cả robot hút bụi. Cùng nhãn `activity_context="socializing"`
model gán cho cả hai loại câu, nên nhãn không phân biệt được; bằng chứng phân biệt nằm ở SỐ
điều kiện còn thiếu — vá một mục là sơ suất, thiếu gần hết checklist là mục tiêu khác.
"""

from src.agent.schemas import DesiredOutcome, SemanticGoal
from src.agent.understanding.goal_author import _complete_activity_contract, _repair_activity_outcomes
from src.core.interfaces import DeviceSelector
from src.nlu.ontology import UtteranceType


def _lighting() -> DesiredOutcome:
    return DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách", domain="light"),
        relative_change={"brightness": "increase_slight"},
        rationale="welcome lighting",
    )


def _cleaning() -> DesiredOutcome:
    return DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách", domain="vacuum"),
        target_state={"power": "on"},
        rationale="sàn sạch trước khi khách tới",
    )


def _cooling() -> DesiredOutcome:
    return DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách", domain="air_conditioner"),
        relative_change={"temperature": "decrease"},
        rationale="mát dễ chịu",
    )


def _air() -> DesiredOutcome:
    return DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách", domain="air_purifier"),
        target_state={"power": "on"},
        rationale="không khí sạch",
    )


def _media(*, power: bool = True) -> DesiredOutcome:
    return DesiredOutcome(
        selector=DeviceSelector(
            area="Phòng khách", domain="media_player", labels=["shared_entertainment"]
        ),
        target_state={"power": "on"} if power else {},
        cardinality="any",
        rationale="nguồn giải trí dùng chung",
    )


def _social_goal(*, polarity: str = "affirmative", outcomes: list[DesiredOutcome]) -> SemanticGoal:
    return SemanticGoal(
        intent="prepare a shared social space",
        raw_utterance="chuẩn bị khu vực chung cho mọi người ghé chơi",
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="prepare the shared room",
        activity_context="socializing",
        confidence=0.9,
        target_area="Phòng khách",
        polarity=polarity,
        desired_outcomes=outcomes,
    )


def test_socializing_contract_restores_missing_capability_outcome() -> None:
    """Nếp đã hình dung đủ, rơi đúng một mục (media) → contract vá lại. Đây là lý do nó tồn tại."""
    completed = _complete_activity_contract(
        _social_goal(outcomes=[_lighting(), _cleaning(), _cooling(), _air()])
    )
    domains = {outcome.selector.domain for outcome in completed.desired_outcomes}
    media = next(
        outcome
        for outcome in completed.desired_outcomes
        if "shared_entertainment" in outcome.selector.labels
    )

    assert {"vacuum", "air_conditioner", "air_purifier", "media_player"} <= domains
    assert media.cardinality == "any"
    assert media.target_state == {"power": "on"}


def test_activity_contract_does_not_override_a_negated_request() -> None:
    original = _social_goal(polarity="negative", outcomes=[_lighting(), _cleaning(), _cooling()])

    completed = _complete_activity_contract(original)

    assert completed.desired_outcomes == original.desired_outcomes


def test_selector_without_required_power_does_not_satisfy_contract() -> None:
    """Nêu media nhưng KHÔNG đòi nguồn phát đang bật vẫn tính là thiếu mục media."""
    goal = _social_goal(outcomes=[_cleaning(), _cooling(), _air(), _media(power=False)])

    completed = _complete_activity_contract(goal)
    active_media = [
        outcome
        for outcome in completed.desired_outcomes
        if "shared_entertainment" in outcome.selector.labels
        and outcome.target_state.get("power") == "on"
    ]

    assert len(active_media) == 1


def test_yeu_cau_don_muc_dich_khong_bi_no_thanh_nep_bon_buoc() -> None:
    """Hình dạng SH-126 "dọn nhà giúp tôi": một outcome vệ sinh là MỘT VIỆC, không phải nếp.

    Trước khi có cổng này, contract thêm điều hoà + máy lọc + TV và biến một kế hoạch khớp y
    hệt reference thành sai. Nhãn hoạt động vẫn là "socializing" — luật phải đọc hình dạng
    mục tiêu, không đọc nhãn.
    """
    goal = _social_goal(outcomes=[_cleaning()])

    completed = _complete_activity_contract(goal)

    assert completed.desired_outcomes == goal.desired_outcomes


def test_mot_nguon_nhac_khong_keo_theo_ca_nep_don_khach() -> None:
    """Hình dạng SH-143 "nghe nhạc ở phòng khách": mục media đã đủ, ba mục kia không được dựng."""
    goal = _social_goal(outcomes=[_media()])

    completed = _complete_activity_contract(goal)

    assert completed.desired_outcomes == goal.desired_outcomes


def test_thieu_hai_muc_van_la_muc_tieu_khac_chu_khong_phai_so_suat() -> None:
    """Ranh giới của luật: thiếu HAI mục thì contract phải đứng ngoài.

    Hai mục là quá nửa phần contract tự viết ra — lúc đó nó không còn vá một sơ suất mà đang
    thay mục tiêu người dùng nêu bằng một nếp không ai yêu cầu.
    """
    goal = _social_goal(outcomes=[_lighting(), _cleaning(), _cooling()])

    completed = _complete_activity_contract(goal)

    assert completed.desired_outcomes == goal.desired_outcomes


def test_sleep_activity_uses_an_unblocked_role_when_climate_must_be_preserved() -> None:
    climate = DesiredOutcome(
        selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="air_conditioner"),
        target_state={"power": "on"},
        relative_change={"temperature": "decrease_slight"},
        cardinality="one",
    )
    goal = SemanticGoal(
        intent="prepare sleep",
        raw_utterance="prepare sleep while preserving climate",
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="prepare sleep",
        activity_context="sleeping",
        confidence=0.9,
        target_area="Phòng ngủ bố mẹ",
        desired_outcomes=[climate],
    )

    repaired = _repair_activity_outcomes(
        goal,
        [climate],
        area="Phòng ngủ bố mẹ",
        preserved_ids={"dieu_hoa_phong_bo_me"},
    )

    assert len(repaired) == 2
    task_light = next(outcome for outcome in repaired if outcome.selector.domain == "light")
    media = next(outcome for outcome in repaired if outcome.selector.domain == "media_player")
    assert task_light.selector.labels == ["work_or_study_light"]
    assert task_light.target_state == {"power": "off"}
    assert media.target_state == {"power": "off"}


def _bathing_goal(*, outcomes: list[DesiredOutcome]) -> SemanticGoal:
    return SemanticGoal(
        intent="prepare hot water for a bath",
        raw_utterance="tôi chuẩn bị đi tắm",
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="chuẩn bị nước nóng để tắm",
        activity_context="bathing",
        confidence=0.9,
        target_area="Phòng ngủ bố mẹ",
        desired_outcomes=outcomes,
    )


def test_bathing_contract_restores_missing_water_heater_outcome() -> None:
    """LLM gán nhãn "bathing" nhưng quên outcome → contract vá thêm bình nóng lạnh power=on."""
    completed = _complete_activity_contract(_bathing_goal(outcomes=[]))

    heaters = [o for o in completed.desired_outcomes if o.selector.domain == "water_heater"]
    assert len(heaters) == 1
    assert heaters[0].target_state == {"power": "on"}
    assert heaters[0].cardinality == "one"


def test_bathing_contract_water_heater_outcome_carries_no_room() -> None:
    """Bình nóng lạnh là thiết bị đơn nhất: outcome do contract vá KHÔNG được ghim phòng người nói."""
    completed = _complete_activity_contract(_bathing_goal(outcomes=[]))

    heater = next(o for o in completed.desired_outcomes if o.selector.domain == "water_heater")
    assert heater.selector.area is None


def test_bathing_contract_stands_down_when_model_already_authored_the_heater() -> None:
    authored = DesiredOutcome(
        selector=DeviceSelector(area=None, domain="water_heater"),
        target_state={"power": "on"},
        cardinality="one",
        rationale="nước nóng để tắm",
    )
    goal = _bathing_goal(outcomes=[authored])

    completed = _complete_activity_contract(goal)

    assert completed.desired_outcomes == goal.desired_outcomes


def test_energy_saving_turns_observed_on_groups_into_power_off_outcomes() -> None:
    current_on = DesiredOutcome(
        selector=DeviceSelector(area="Phòng ngủ con"),
        perceived_state="on",
    )
    goal = SemanticGoal(
        intent="energy saving",
        raw_utterance="save energy in empty rooms",
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="energy saving",
        confidence=0.9,
        desired_outcomes=[current_on],
    )

    repaired = _repair_activity_outcomes(
        goal,
        [current_on],
        area=None,
        preserved_ids=set(),
    )

    assert repaired[0].target_state == {"power": "off"}
    assert repaired[0].cardinality == "all"


def _activity_goal(activity: str, outcomes: list[DesiredOutcome]) -> SemanticGoal:
    return SemanticGoal(
        intent=f"prepare {activity}",
        raw_utterance=f"prepare the room for {activity}",
        utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description=f"prepare the room for {activity}",
        activity_context=activity,
        confidence=0.9,
        target_area="Phòng ngủ con",
        desired_outcomes=outcomes,
    )


def test_focus_activity_keeps_task_light_and_removes_unrequested_comfort_devices() -> None:
    dim_light = DesiredOutcome(
        selector=DeviceSelector(area="Phòng ngủ con", domain="light"),
        perceived_state="too_bright",
        relative_change={"brightness": "decrease"},
    )
    purifier = DesiredOutcome(
        selector=DeviceSelector(area="Phòng ngủ con", domain="air_purifier"),
        perceived_state="noisy",
        relative_change={"fan_speed": "decrease"},
    )
    unsafe_media = DesiredOutcome(
        selector=DeviceSelector(area="Phòng ngủ con", domain="media_player"),
        target_state={"power": "on"},
    )

    repaired = _repair_activity_outcomes(
        _activity_goal("studying", [dim_light, purifier, unsafe_media]),
        [dim_light, purifier, unsafe_media],
        area="Phòng ngủ con",
        preserved_ids=set(),
    )

    assert [outcome.selector.domain for outcome in repaired] == ["light", "media_player"]
    assert repaired[0].selector.labels == ["work_or_study_light"]
    assert repaired[0].relative_change == {"brightness": "increase_slight"}
    assert repaired[1].target_state == {"power": "off"}


def test_quiet_activity_adds_task_light_and_media_off_without_overriding_reading_light() -> None:
    reading_light = DesiredOutcome(
        selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="light"),
        perceived_state="too_dark",
        relative_change={"brightness": "increase_slight"},
    )
    unsafe_media = DesiredOutcome(
        selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="media_player"),
        target_state={"power": "on"},
    )
    goal = _activity_goal("resting", [reading_light, unsafe_media]).model_copy(
        update={"target_area": "Phòng ngủ bố mẹ"}
    )

    repaired = _repair_activity_outcomes(
        goal,
        [reading_light, unsafe_media],
        area="Phòng ngủ bố mẹ",
        preserved_ids=set(),
    )

    lights = [outcome for outcome in repaired if outcome.selector.domain == "light"]
    media = [outcome for outcome in repaired if outcome.selector.domain == "media_player"]
    assert len(lights) == 1
    assert lights[0].relative_change == {"brightness": "increase_slight"}
    assert len(media) == 1
    assert media[0].target_state == {"power": "off"}


def test_specific_speaker_outcome_replaces_generic_shared_media() -> None:
    generic_media = _media()
    speaker = DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách", domain="speaker"),
        relative_change={"volume": "increase"},
    )
    goal = _activity_goal("socializing", [generic_media, speaker]).model_copy(
        update={"target_area": "Phòng khách"}
    )

    repaired = _repair_activity_outcomes(
        goal,
        [generic_media, speaker],
        area="Phòng khách",
        preserved_ids=set(),
    )

    assert [outcome.selector.domain for outcome in repaired] == ["speaker"]
    assert repaired[0].target_state == {"power": "on"}
    assert repaired[0].relative_change == {"volume": "increase"}


def test_malformed_lock_power_is_normalized_to_door_lock_state() -> None:
    malformed = DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách"),
        target_state={"power": "lock"},
        cardinality="one",
    )

    repaired = _repair_activity_outcomes(
        _activity_goal("leaving_home", [malformed]),
        [malformed],
        area="Phòng khách",
        preserved_ids=set(),
    )

    assert repaired[0].selector.domain == "door_lock"
    assert repaired[0].target_state == {"locked": True}


def test_preserved_domain_is_removed_when_another_outcome_can_satisfy_goal() -> None:
    tv = DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách", domain="tv"),
        target_state={"power": "on"},
    )
    curtain = DesiredOutcome(
        selector=DeviceSelector(area="Phòng khách", domain="curtain"),
        relative_change={"position": "decrease"},
    )
    goal = _activity_goal("watching_content", [tv, curtain]).model_copy(
        update={"target_area": "Phòng khách"}
    )

    repaired = _repair_activity_outcomes(
        goal,
        [tv, curtain],
        area="Phòng khách",
        preserved_ids={"rem_phong_khach"},
    )

    assert [outcome.selector.domain for outcome in repaired] == ["tv"]
