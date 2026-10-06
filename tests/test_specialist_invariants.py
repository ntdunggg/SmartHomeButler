"""Test Specialist Directional Invariants (Finding 3) — Preference cannot invert user directional intent."""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.perception.context_builder import build_perception
from src.agent.pipeline import PipelineDeps
from src.agent.planning.manager import Subgoal
from src.agent.schemas import PreferenceDistribution
from src.agent.specialists.ac import ACAgent
from src.agent.specialists.lighting import LightingAgent
from src.agent.specialists.media import MediaAgent
from src.services.pipeline_bridge import reason


def _ac_ctx(current_temp: float = 24.0):
    _nu, ctx, _p = build_perception("làm mát phòng", speaker_location="Phòng ngủ bố mẹ")
    # Đặt nhiệt độ thiết bị hiện tại
    for d in ctx.devices:
        if d.device_type == "air_conditioner":
            d.state["temperature"] = current_temp
            d.state["power"] = "on"
    return ctx


def _light_ctx(current_brightness: float = 70.0):
    _nu, ctx, _p = build_perception("tăng sáng phòng khách", speaker_location="Phòng khách")
    for d in ctx.devices:
        if d.device_type == "light":
            d.state["brightness"] = current_brightness
            d.state["power"] = "on"
    return ctx


def test_cooling_invariant_never_heats_room():
    """Current=24°C, user='làm mát phòng' (decrease), preference=26°C -> target PHẢI < 24°C (không bao giờ chọn 26°C)."""
    ctx = _ac_ctx(current_temp=24.0)
    sub = Subgoal(dimension="temperature", direction="decrease", room="Phòng ngủ bố mẹ")

    # Giả lập preference đã học phân phối đỉnh tại 26°C (xác suất 0.9) và 22°C (xác suất 0.1)
    pref = {
        "temperature": PreferenceDistribution(
            dimension="temperature",
            distribution={"26": 0.9, "22": 0.1},
            confidence=0.85,
        )
    }

    agent = ACAgent()
    prop = agent.propose(sub, ctx, preference=pref)

    assert prop is not None
    action = prop.actions[0]
    target_temp = action.target["temperature"]

    # Bắt buộc target < 24. Vì 22°C thoả mãn và nằm trong preference nên agent chọn 22°C thay vì 26°C.
    assert target_temp < 24.0
    assert target_temp == 22.0


def test_warming_invariant_never_cools_room():
    """Current=24°C, user='sưởi ấm phòng' (increase), preference=20°C -> target PHẢI > 24°C (không bao giờ chọn 20°C)."""
    ctx = _ac_ctx(current_temp=24.0)
    sub = Subgoal(dimension="temperature", direction="increase", room="Phòng ngủ bố mẹ")

    # Preference đỉnh tại 20°C (0.9) và 26°C (0.1)
    pref = {
        "temperature": PreferenceDistribution(
            dimension="temperature",
            distribution={"20": 0.9, "26": 0.1},
            confidence=0.85,
        )
    }

    agent = ACAgent()
    prop = agent.propose(sub, ctx, preference=pref)

    assert prop is not None
    action = prop.actions[0]
    target_temp = action.target["temperature"]

    # Bắt buộc target > 24. Agent chọn 26°C thay vì 20°C.
    assert target_temp > 24.0
    assert target_temp == 26.0


def test_brightening_invariant_never_dims_lights():
    """Current=70%, user='tăng sáng' (increase), preference=30% -> target PHẢI > 70% (không bao giờ chọn 30%)."""
    ctx = _light_ctx(current_brightness=70.0)
    sub = Subgoal(dimension="illumination", direction="increase", room="Phòng khách")

    # Preference đỉnh tại 30% (0.9) và 90% (0.1)
    pref = {
        "brightness": PreferenceDistribution(
            dimension="brightness",
            distribution={"30": 0.9, "90": 0.1},
            confidence=0.85,
        )
    }

    agent = LightingAgent()
    prop = agent.propose(sub, ctx, preference=pref)

    assert prop is not None
    action = prop.actions[0]
    target_brightness = action.target["brightness"]

    # Bắt buộc target > 70.
    assert target_brightness > 70.0
    assert target_brightness == 90.0


def test_dimming_invariant_never_brightens_lights():
    """Current=70%, user='giảm sáng' (decrease), preference=100% -> target PHẢI < 70% (không bao giờ chọn 100%)."""
    ctx = _light_ctx(current_brightness=70.0)
    sub = Subgoal(dimension="illumination", direction="decrease", room="Phòng khách")

    # Preference đỉnh tại 100% (0.9) và 40% (0.1)
    pref = {
        "brightness": PreferenceDistribution(
            dimension="brightness",
            distribution={"100": 0.9, "40": 0.1},
            confidence=0.85,
        )
    }

    agent = LightingAgent()
    prop = agent.propose(sub, ctx, preference=pref)

    assert prop is not None
    action = prop.actions[0]
    target_brightness = action.target["brightness"]

    # Bắt buộc target < 70.
    assert target_brightness < 70.0
    assert target_brightness == 40.0


def test_dimming_does_not_turn_on_an_inactive_light():
    """An off light is already maximally dim; preserve only a turn-off/no-op."""
    ctx = _light_ctx(current_brightness=0.0)
    for device in ctx.devices:
        if device.device_type == "light":
            device.state["power"] = "off"
    sub = Subgoal(dimension="illumination", direction="decrease", room="Phòng khách")

    proposal = LightingAgent().propose(sub, ctx)
    assert proposal is not None
    assert proposal.actions
    assert all(action.action == "turn_off" for action in proposal.actions)


def test_value_less_temperature_direction_uses_ac_specialist():
    """A directional AC command without a setpoint must be grounded to temperature."""
    result = reason(
        message="làm mát phòng bố mẹ",
        conversation_id="directional-temperature-specialist",
        user_id="test:directional-temperature",
        role="owner",
        now=datetime(2026, 8, 14, 22, 0, tzinfo=UTC),
        speaker_location="Phòng ngủ bố mẹ",
        live_device_states={"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}},
        live_sensors=[],
        deps=PipelineDeps(),
    )

    assert result.outcome == "candidate_plan"
    action = result.candidate_plan.actions[0]
    assert action.device_id == "dieu_hoa_phong_bo_me"
    assert action.capability.value == "temperature"
    assert action.action.value == "set"
    assert action.params == {"temperature": 22.0}


def test_low_brightness_decrease_has_explicit_zero_to_off_contract():
    """Decreasing a lit low-level light to zero remains an intentional turn-off."""
    ctx = _light_ctx(current_brightness=20.0)
    sub = Subgoal(dimension="illumination", direction="decrease", room="Phòng khách")

    proposal = LightingAgent().propose(sub, ctx)

    assert proposal is not None and proposal.actions
    action = proposal.actions[0]
    assert action.action == "turn_off"
    assert action.target == {"brightness": 0}


def test_quieter_does_not_turn_on_an_inactive_media_source():
    """An off TV/speaker is already silent; preserve only a turn-off/no-op."""
    _nu, ctx, _p = build_perception("yên tĩnh hơn", speaker_location="Phòng khách")
    for device in ctx.devices:
        if device.device_type in {"speaker", "tv"}:
            device.state["power"] = "off"
    sub = Subgoal(dimension="media", direction="decrease", room="Phòng khách")

    proposal = MediaAgent().propose(sub, ctx)
    assert proposal is not None
    assert proposal.actions
    assert all(action.action == "turn_off" for action in proposal.actions)


def test_non_numeric_unchanged_volume_is_a_safe_noop():
    """A model marker such as ``unchanged`` must not reach float()."""
    _nu, ctx, _p = build_perception("giữ nguyên âm lượng", speaker_location="Phòng khách")
    sub = Subgoal(
        dimension="media",
        direction=None,
        room="Phòng khách",
        target_state={"volume": "unchanged"},
    )

    assert MediaAgent().propose(sub, ctx) is None


def test_explicit_curtain_open_maps_to_position_not_on_off():
    """Verb device-type-aware: 'mở/đóng' trên rèm (position, không on_off) → open/close·position.

    Trước fix: turn_on → capability on_off → validator loại (rèm không có on_off) → no_goal."""
    from src.agent.specialists.shutter import ShutterAgent

    _nu, ctx, _p = build_perception("mở rèm phòng khách")
    sub = Subgoal(dimension="explicit", room="Phòng khách", explicit_device_id="rem_phong_khach", explicit_action="turn_on")
    prop = ShutterAgent().propose_explicit(sub, ctx)
    assert prop is not None and prop.actions
    act = prop.actions[0]
    assert act.action == "open" and act.capability == "position"

    sub_close = Subgoal(dimension="explicit", room="Phòng khách", explicit_device_id="rem_phong_khach", explicit_action="turn_off")
    prop_close = ShutterAgent().propose_explicit(sub_close, ctx)
    assert prop_close.actions[0].action == "close" and prop_close.actions[0].capability == "position"


def test_explicit_light_on_stays_on_off():
    """Thiết bị CÓ on_off (đèn) giữ nguyên turn_on·on_off — không dịch nhầm sang open."""
    from src.agent.specialists.lighting import LightingAgent

    _nu, ctx, _p = build_perception("bật đèn phòng khách")
    sub = Subgoal(dimension="explicit", room="Phòng khách", explicit_device_id="den_chum_phong_khach", explicit_action="turn_on")
    prop = LightingAgent().propose_explicit(sub, ctx)
    assert prop is not None
    assert prop.actions[0].action == "turn_on" and prop.actions[0].capability == "on_off"
