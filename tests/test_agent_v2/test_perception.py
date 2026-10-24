"""Layer 1 Perception — Power Load model (spec §8) + context building."""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.perception.context_builder import build_perception
from src.agent.perception.power_monitor import build_power_load, classify_power, estimate_baseline_watts
from src.agent.schemas import PowerMode


def test_power_classification_thresholds():
    assert classify_power(0) is PowerMode.NORMAL
    assert classify_power(3499) is PowerMode.NORMAL
    assert classify_power(3500) is PowerMode.MODERATE
    assert classify_power(4999) is PowerMode.MODERATE
    assert classify_power(5000) is PowerMode.CRITICAL


def test_power_load_carries_thresholds():
    pl = build_power_load(4200)
    assert pl.mode is PowerMode.MODERATE
    assert pl.moderate_threshold_w == 3500
    assert pl.critical_threshold_w == 5000


def test_estimate_baseline_ignores_off_and_unknown():
    # AC on (900W) + light on (12W); an unknown slug is ignored; off device ignored.
    watts = estimate_baseline_watts(
        {
            "dieu_hoa_phong_khach": {"power": "on"},
            "den_chum_phong_khach": {"power": "on"},
            "khong_ton_tai": {"power": "on"},
            "quat_tran_phong_khach": {"power": "off"},
        }
    )
    assert watts == 912.0


def test_build_perception_returns_context_and_power():
    nu, ctx, power = build_perception(
        "bật đèn phòng khách",
        now=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        speaker_location="Phòng khách",
        current_watts=1000,
    )
    assert power.mode is PowerMode.NORMAL
    assert "Phòng khách" in ctx.rooms
    assert ctx.devices  # có thiết bị liên quan


def test_context_marks_default_state_stale_when_no_snapshot():
    """QC-04/§7.4: chưa có snapshot → state/cảm biến là default, đánh dấu stale/unknown + power unknown."""
    _nu, ctx, power = build_perception("bật đèn phòng khách", speaker_location="Phòng khách")
    assert all(d.state_source == "default" for d in ctx.devices)
    assert all(s.is_stale and s.source == "default" and s.confidence is None for s in ctx.sensors)
    assert power.unknown is True


def test_context_marks_live_state_observed():
    """Có snapshot live → source='live', không stale, power không unknown."""
    _nu, ctx, power = build_perception(
        "bật đèn phòng khách",
        speaker_location="Phòng khách",
        live_device_states={"den_chum_phong_khach": {"power": "on", "brightness": 40}},
        live_sensors=[{"slug": "s1", "name": "nhiệt", "sensor_type": "temperature", "value": 26.0, "unit": "°C", "room": "Phòng khách"}],
    )
    lit = ctx.device("den_chum_phong_khach")
    assert lit is not None and lit.state_source == "live" and lit.state.get("brightness") == 40
    assert any(s.source == "live" and not s.is_stale and s.confidence == 1.0 for s in ctx.sensors)
    assert power.unknown is False


def test_occupancy_confidence_low_for_default_sensor():
    """QC-04: occupancy từ cảm biến default (chưa live) → confidence thấp, KHÔNG hardcode 1.0."""
    from src.agent.perception.sensor_adapter import occupancy_for_room
    _nu, ctx, _p = build_perception("có ai ở phòng khách không", speaker_location="Phòng khách")
    occ = occupancy_for_room(ctx.sensors, "Phòng khách")
    assert occ["confidence"] == 0.0  # cảm biến tĩnh → không tin như live


def test_outdoor_sensor_detected_by_slug_even_with_room_label():
    """Nhận diện cảm biến ngoài trời qua slug (không phải `sensor_id` — trường đó không tồn tại).

    Trước fix: đọc nhầm `sensor_id` → luôn rỗng → chỉ nhận diện qua phòng rỗng. Sau fix: slug
    chứa `ngoai_troi` được nhận diện dù có nhãn phòng, và KHÔNG trả nhầm cảm biến trong nhà."""
    from src.agent.perception.sensor_adapter import outdoor_sensor_by_type
    from src.agent.schemas import SensorReading

    indoor = SensorReading(slug="nhiet_phong_khach", name="Nhiệt trong nhà", sensor_type="temperature", value=26.0, room="Phòng khách")
    outdoor = SensorReading(slug="nhiet_ngoai_troi", name="Nhiệt ngoài trời", sensor_type="temperature", value=34.0, room="Phòng khách")
    got = outdoor_sensor_by_type([indoor, outdoor], "temperature")
    assert got is outdoor  # tìm đúng cảm biến ngoài trời theo slug, không lấy cảm biến trong nhà


def test_outdoor_sensor_detected_by_empty_room():
    """Cảm biến ngoài trời không gắn phòng (room rỗng) vẫn được nhận diện."""
    from src.agent.perception.sensor_adapter import outdoor_sensor_by_type
    from src.agent.schemas import SensorReading

    outdoor = SensorReading(slug="nhiet_1", name="Nhiệt", sensor_type="temperature", value=34.0, room="")
    assert outdoor_sensor_by_type([outdoor], "temperature") is outdoor
