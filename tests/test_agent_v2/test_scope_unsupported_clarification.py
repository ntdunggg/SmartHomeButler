"""Regressions for scope refusal, unsupported targets, and useful clarification."""

from __future__ import annotations

import pytest

from src.agent.pipeline import PipelineDeps
from src.agent.state_query import matched_sensor_types
from src.nlu.normalizer import analyze
from src.services.pipeline_bridge import reason


def _run(text: str):
    return reason(message=text, conversation_id=f"scope:{text}", deps=PipelineDeps())


def _run_at(text: str, room: str):
    return reason(
        message=text,
        conversation_id=f"scope:{room}:{text}",
        speaker_location=room,
        deps=PipelineDeps(),
    )


def test_bare_speaker_is_ambiguous_across_registry_rooms() -> None:
    result = _run("Bật loa.")
    assert result.outcome == "clarification"
    assert "Phòng khách" in result.reply
    assert "Phòng bếp" in result.reply


def test_duplicate_device_names_are_disambiguated_with_rooms() -> None:
    result = _run("Bật đèn ngủ.")
    assert result.outcome == "clarification"
    assert "Đèn ngủ hay Đèn ngủ" not in result.reply
    assert "Phòng ngủ bố mẹ" in result.reply
    assert "Phòng ngủ con" in result.reply


def test_deictic_room_without_live_location_is_clarified() -> None:
    result = _run("Cho phòng này mát hơn.")
    assert result.outcome == "clarification"
    assert result.candidate_plan is None


def test_current_room_wins_for_deictic_request() -> None:
    result = _run_at("Cho phòng này mát hơn.", "Phòng ngủ con")
    assert result.candidate_plan is not None
    assert {action.device_id for action in result.candidate_plan.actions} == {"dieu_hoa_phong_con"}


@pytest.mark.parametrize(
    "text, missing",
    [
        ("Bật máy pha cà phê.", "máy pha cà phê"),
        ("Khởi động máy giặt.", "máy giặt"),
        ("Mở cửa gara.", "cửa gara"),
        ("Bật đèn phòng tắm.", "phòng tắm"),
        ("Mở rèm phòng làm việc.", "phòng làm việc"),
        ("Bật máy hút mùi.", "máy hút mùi"),
        ("Mở lò vi sóng.", "lò vi sóng"),
        ("Bật đèn hành lang.", "hành lang"),
        ("Khởi động máy pha cafe giúp mình.", "máy pha cafe"),
    ],
)
def test_known_but_uninstalled_target_is_reported_not_clarified(text: str, missing: str) -> None:
    result = _run(text)
    assert result.outcome == "answer"
    assert "không có" in result.reply
    assert missing in result.reply.lower()


def test_room_complaint_is_not_mistaken_for_an_unavailable_area() -> None:
    result = _run("Phòng bí quá khó thở.")
    assert "không có phòng bí" not in result.reply.lower()


@pytest.mark.parametrize(
    "text",
    [
        "Soạn giúp tôi một email cảm ơn.",
        "Đánh giá phim này cho tôi.",
        "Sáng tác một bài thơ ngắn.",
    ],
)
def test_standalone_out_of_scope_request_is_clearly_refused(text: str) -> None:
    result = _run(text)
    assert result.outcome == "answer"
    assert result.candidate_plan is None
    assert "chỉ hỗ trợ" in result.reply
    assert "chưa giúp được" in result.reply


def test_mixed_request_keeps_home_plan_and_refuses_the_rest() -> None:
    result = _run("Tắt loa bếp rồi sáng tác một bài thơ.")
    assert result.outcome == "candidate_plan"
    assert [action.device_id for action in result.candidate_plan.actions] == ["loa_bep"]
    assert "ngoài phạm vi" in result.reply
    assert "không thể thực hiện" in result.reply


def test_in_scope_movie_setup_is_not_refused() -> None:
    result = _run("Tôi muốn xem phim ở phòng khách.")
    assert "ngoài phạm vi" not in result.reply


def test_purchase_word_does_not_read_the_rain_sensor() -> None:
    nu = analyze("Bitcoin có nên mua lúc này không?")
    assert "rain" not in matched_sensor_types(nu)
    result = _run(nu.raw)
    assert result.outcome == "answer"
    assert "chỉ hỗ trợ" in result.reply
    assert "mưa" not in result.reply


def test_unaccented_weather_phrase_still_reads_rain() -> None:
    assert "rain" in matched_sensor_types(analyze("Ngoai troi dang mua khong?"))


def test_real_capability_question_keeps_capability_answer() -> None:
    result = _run("Hệ thống có thể làm gì?")
    assert result.outcome == "answer"
    assert "điều khiển được" in result.reply


@pytest.mark.parametrize(
    "text,room,expected",
    [
        ("Con chuẩn bị học bài.", "Phòng ngủ con", {"den_ban_hoc"}),
        ("Tôi chuẩn bị tắm.", "Phòng khách", {"binh_nong_lanh"}),
        ("Tôi muốn nghe nhạc trong bếp.", "Phòng bếp", {"loa_bep"}),
        ("Cho phòng con yên tĩnh để học.", "Phòng ngủ con", {"den_ban_hoc"}),
    ],
)
def test_activity_goal_grounds_to_relevant_capability(
    text: str, room: str, expected: set[str]
) -> None:
    result = _run_at(text, room)
    assert result.candidate_plan is not None
    assert {action.device_id for action in result.candidate_plan.actions} == expected


def test_activity_exclusion_is_preserved_in_plan() -> None:
    result = _run_at(
        "Tôi sắp ngủ nhưng đừng tắt điều hòa.",
        "Phòng ngủ bố mẹ",
    )
    assert result.candidate_plan is not None
    assert result.semantic_goal is not None
    assert result.semantic_goal.excluded_device_ids == ["dieu_hoa_phong_bo_me"]
    assert "dieu_hoa_phong_bo_me" not in {
        action.device_id for action in result.candidate_plan.actions
    }


def test_no_change_device_is_not_touched() -> None:
    result = _run_at(
        "Tối nay xem TV, đừng đụng vào rèm.",
        "Phòng khách",
    )
    assert result.candidate_plan is not None
    assert result.semantic_goal is not None
    assert result.semantic_goal.no_change_device_ids == ["rem_phong_khach"]
    assert {action.device_id for action in result.candidate_plan.actions} == {"tv_phong_khach"}
