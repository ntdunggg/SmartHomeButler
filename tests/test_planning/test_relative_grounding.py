"""Test tầng Semantic State: goal tương đối → hành động tuyệt đối bằng live context.

Chấm theo KẾT QUẢ, dùng FakeReasoningModel (không gọi API). Hai lớp:
- unit: `ground_relative_value` (mức/hướng/biên) + `ground_candidate_plan` (đổi INCREASE/
  DECREASE → SET đúng key, tôn trọng live state, giữ nguyên action không-số).
- pipeline: câu tiện nghi mơ hồ ra plan có SET giá trị cụ thể phụ thuộc cường độ.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.core.interfaces import DeviceSelector
from src.domain.enums import ActionType, Capability
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import (
    CandidateAction,
    CandidatePlan,
    DesiredOutcome,
    Device,
    RuntimeContext,
    SemanticGoal,
)
from src.planning.relative_grounding import (
    direction_of,
    env_notch,
    ground_candidate_plan,
    ground_relative_value,
    magnitude_of,
)

_NOW = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Unit: magnitude / direction parsing
# ---------------------------------------------------------------------------
def test_magnitude_slight_normal_large() -> None:
    assert magnitude_of("increase_slight") == "slight"
    assert magnitude_of("decrease") == "normal"
    assert magnitude_of("increase_large") == "large"
    assert magnitude_of("hơi lạnh") == "slight"


def test_direction_up_down_unknown() -> None:
    assert direction_of("increase_slight") == 1
    assert direction_of("decrease") == -1
    assert direction_of("something") == 0


# ---------------------------------------------------------------------------
# Unit: ground_relative_value (bước theo mức + chặn biên + param key)
# ---------------------------------------------------------------------------
def test_ground_value_temperature_steps() -> None:
    assert ground_relative_value(Capability.TEMPERATURE, +1, "slight", 26) == ("temperature", 27)
    assert ground_relative_value(Capability.TEMPERATURE, -1, "normal", 26) == ("temperature", 24)
    assert ground_relative_value(Capability.TEMPERATURE, +1, "large", 26) == ("temperature", 29)


def test_ground_value_clamped_to_bounds() -> None:
    # 70 + 50 (large) = 120 → chặn về 100
    assert ground_relative_value(Capability.BRIGHTNESS, +1, "large", 70) == ("percent", 100)
    # 16 - 2 = 14 → chặn về sàn 16
    assert ground_relative_value(Capability.TEMPERATURE, -1, "normal", 16) == ("temperature", 16)


def test_ground_value_param_key_per_capability() -> None:
    assert ground_relative_value(Capability.BRIGHTNESS, -1, "slight", 70)[0] == "percent"
    assert ground_relative_value(Capability.VOLUME, -1, "normal", 30)[0] == "percent"
    assert ground_relative_value(Capability.FAN_SPEED, +1, "normal", 1)[0] == "level"
    assert ground_relative_value(Capability.TEMPERATURE, +1, "normal", 26)[0] == "temperature"


def test_ground_value_none_direction_returns_none() -> None:
    assert ground_relative_value(Capability.TEMPERATURE, 0, "normal", 26) is None


# ---------------------------------------------------------------------------
# Unit: ground_candidate_plan
# ---------------------------------------------------------------------------
def _goal_with(rel: dict[str, str]) -> SemanticGoal:
    return SemanticGoal(
        intent="ấm hơn",
        raw_utterance="hơi lạnh",
        goal_description="ấm hơn",
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        confidence=0.9,
        desired_outcomes=[DesiredOutcome(selector=DeviceSelector(domain="air_conditioner"), relative_change=rel)],
    )


def _ctx_with_ac(temp: int) -> RuntimeContext:
    return RuntimeContext(
        now=_NOW,
        devices=[
            Device(
                device_id="dieu_hoa_phong_khach",
                name="Điều hoà",
                room="Phòng khách",
                device_type="air_conditioner",
                capabilities=[Capability.TEMPERATURE],
                state={"power": "on", "temperature": temp},
            )
        ],
        rooms=["Phòng khách"],
    )


def test_ground_plan_converts_increase_to_set_using_live_state() -> None:
    plan = CandidatePlan(
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        actions=[
            CandidateAction(
                device_id="dieu_hoa_phong_khach", capability=Capability.TEMPERATURE, action=ActionType.INCREASE
            )
        ],
    )
    # live temp = 20 (không phải initial_state 26) → slight +1 = 21
    grounded = ground_candidate_plan(plan, _goal_with({"temperature": "increase_slight"}), _ctx_with_ac(20))
    act = grounded.actions[0]
    assert act.action == ActionType.SET
    assert act.params == {"temperature": 21}


def test_ground_plan_leaves_nonnumeric_action_untouched() -> None:
    plan = CandidatePlan(
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        actions=[
            CandidateAction(device_id="may_loc_phong_khach", capability=Capability.ON_OFF, action=ActionType.TURN_ON)
        ],
    )
    grounded = ground_candidate_plan(plan, _goal_with({}), _ctx_with_ac(26))
    assert grounded.actions[0].action == ActionType.TURN_ON
    assert grounded.actions[0].params == {}


def test_ground_plan_keeps_relative_when_current_value_unknown() -> None:
    # ctx không chứa thiết bị & registry cũng không → giữ nguyên INCREASE (không đoán)
    plan = CandidatePlan(
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        actions=[
            CandidateAction(device_id="thiet_bi_ma", capability=Capability.TEMPERATURE, action=ActionType.INCREASE)
        ],
    )
    grounded = ground_candidate_plan(plan, _goal_with({"temperature": "increase"}), _ctx_with_ac(26))
    assert grounded.actions[0].action == ActionType.INCREASE


# ---------------------------------------------------------------------------
# Sensor fusion: env_notch ±1 nấc theo môi trường
# ---------------------------------------------------------------------------
def _ctx_with_sensor(sensor_type: str, value: float, room: str = "Phòng khách") -> RuntimeContext:
    from src.nlu.schemas import SensorReading

    return RuntimeContext(
        now=_NOW,
        devices=[
            Device(
                device_id="dieu_hoa_phong_khach", name="Điều hoà", room=room,
                device_type="air_conditioner", capabilities=[Capability.TEMPERATURE], state={"temperature": 26},
            )
        ],
        rooms=[room],
        sensors=[SensorReading(slug="s", name="s", sensor_type=sensor_type, value=value, room=room)],
    )


def test_env_notch_hot_room_amplifies_cooling() -> None:
    # Đang nóng (giảm nhiệt, sign<0) + phòng 33°C → +1 nấc
    ctx = _ctx_with_sensor("temperature", 33.0)
    assert env_notch(Capability.TEMPERATURE, -1, ctx, "dieu_hoa_phong_khach") == 1


def test_env_notch_cool_room_tempers_cooling() -> None:
    # Nói "nóng" nhưng phòng mới 23°C → hạ 1 nấc (làm dịu phản ứng thái quá)
    ctx = _ctx_with_sensor("temperature", 23.0)
    assert env_notch(Capability.TEMPERATURE, -1, ctx, "dieu_hoa_phong_khach") == -1


def test_env_notch_neutral_returns_zero() -> None:
    ctx = _ctx_with_sensor("temperature", 27.0)
    assert env_notch(Capability.TEMPERATURE, -1, ctx, "dieu_hoa_phong_khach") == 0


def test_env_notch_missing_sensor_returns_zero() -> None:
    ctx = _ctx_with_sensor("humidity", 80.0)  # không có nhiệt độ
    assert env_notch(Capability.TEMPERATURE, -1, ctx, "dieu_hoa_phong_khach") == 0


def test_env_notch_pm25_boosts_airflow() -> None:
    from src.nlu.schemas import SensorReading

    ctx = RuntimeContext(
        now=_NOW,
        devices=[
            Device(
                device_id="may_loc_phong_khach", name="Máy lọc", room="Phòng khách",
                device_type="air_purifier", capabilities=[Capability.FAN_SPEED], state={"fan_speed": 1},
            )
        ],
        rooms=["Phòng khách"],
        sensors=[SensorReading(slug="pm", name="PM2.5", sensor_type="pm25", value=70.0, room="Phòng khách")],
    )
    assert env_notch(Capability.FAN_SPEED, +1, ctx, "may_loc_phong_khach") == 1


def test_ground_plan_fan_speed_bumped_by_pm25() -> None:
    from src.nlu.schemas import SensorReading

    goal = _goal_with({"fan_speed": "increase_slight"})
    plan = CandidatePlan(
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        actions=[
            CandidateAction(
                device_id="may_loc_phong_khach", capability=Capability.FAN_SPEED, action=ActionType.INCREASE
            )
        ],
    )
    ctx = RuntimeContext(
        now=_NOW,
        devices=[
            Device(
                device_id="may_loc_phong_khach", name="Máy lọc", room="Phòng khách",
                device_type="air_purifier", capabilities=[Capability.FAN_SPEED], state={"fan_speed": 1},
            )
        ],
        rooms=["Phòng khách"],
        sensors=[SensorReading(slug="pm", name="PM2.5", sensor_type="pm25", value=70.0, room="Phòng khách")],
    )
    # slight (step 1) bị PM2.5 cao đẩy lên normal (step 1 cho fan_speed cũng =1) → 1+1=2 nấc
    grounded = ground_candidate_plan(plan, goal, ctx)
    act = grounded.actions[0]
    assert act.action == ActionType.SET
    assert act.params == {"level": 2}
