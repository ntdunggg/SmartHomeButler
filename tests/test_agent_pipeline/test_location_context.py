"""Test ngữ cảnh vị trí người nói (speaker_location) + snapshot live trong context.

Chấm theo KẾT QUẢ, offline (không gọi API). Bất biến kiểm: vị trí là OBSERVATION
(đo được) tách khỏi focus_room (ASSUMPTION); state/cảm biến LIVE đè lên registry tĩnh;
câu nêu phòng vẫn thắng vị trí người nói.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.iot.registry import KIDS_ROOM, LIVING_ROOM
from src.nlu.context import build_runtime_context
from src.nlu.normalizer import analyze
from src.nlu.prompts import build_reasoning_context
from src.nlu.schemas import ContextKind

_NOW = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)


def _ctx(utterance: str, **kw):
    return build_runtime_context(analyze(utterance, focus_room=kw.pop("focus_room", None)), now=_NOW, **kw)


def test_speaker_location_narrows_devices_when_no_room_in_utterance() -> None:
    ctx = _ctx("nóng quá", speaker_location=KIDS_ROOM)
    assert ctx.speaker_location == KIDS_ROOM
    assert ctx.devices, "phải có thiết bị của phòng người nói"
    assert {d.room for d in ctx.devices} == {KIDS_ROOM}


def test_speaker_location_is_observation_not_assumption() -> None:
    ctx = _ctx("nóng quá", speaker_location=KIDS_ROOM)
    loc_notes = [n for n in ctx.notes if "Người nói đang ở" in n.text]
    assert loc_notes and loc_notes[0].kind == ContextKind.OBSERVATION


def test_focus_room_without_location_is_assumption() -> None:
    # focus_room truyền THẲNG vào context (nu không nêu phòng) — như đường _finish của
    # dialogue: đây là suy đoán từ hội thoại, phải là ASSUMPTION, không phải OBSERVATION.
    ctx = build_runtime_context(analyze("nóng quá"), now=_NOW, focus_room=LIVING_ROOM)
    assumed = [n for n in ctx.notes if n.kind == ContextKind.ASSUMPTION and "giả định" in n.text]
    assert assumed, "focus_room không kèm vị trí đo được phải là ASSUMPTION"
    # Không phát sinh note OBSERVATION vị trí khi chỉ có focus_room.
    assert not any("Người nói đang ở" in n.text for n in ctx.notes)


def test_explicit_room_in_utterance_beats_speaker_location() -> None:
    # Người đứng phòng con nhưng nói rõ "phòng khách" → thiết bị phải là phòng khách.
    ctx = _ctx("bật đèn phòng khách", speaker_location=KIDS_ROOM)
    assert LIVING_ROOM in {d.room for d in ctx.devices}
    assert KIDS_ROOM not in {d.room for d in ctx.devices}


def test_invalid_speaker_location_ignored() -> None:
    ctx = _ctx("nóng quá", speaker_location="Sao Hoả")
    assert ctx.speaker_location is None


def test_live_device_state_overrides_registry_default() -> None:
    ctx = _ctx(
        "nóng quá",
        speaker_location=KIDS_ROOM,
        live_device_states={"dieu_hoa_phong_con": {"power": "on", "temperature": 18}},
    )
    ac = ctx.device("dieu_hoa_phong_con")
    assert ac is not None
    assert ac.state["power"] == "on" and ac.state["temperature"] == 18


def test_live_sensors_replace_static_and_render_prioritizes_speaker_room() -> None:
    ctx = _ctx(
        "nóng quá",
        speaker_location=KIDS_ROOM,
        live_sensors=[
            {"slug": "t_out", "name": "Nhiệt độ", "sensor_type": "temperature", "value": 20.0, "unit": "°C", "room": LIVING_ROOM},
            {"slug": "t_kid", "name": "Nhiệt độ", "sensor_type": "temperature", "value": 33.0, "unit": "°C", "room": KIDS_ROOM},
        ],
    )
    assert {s.slug for s in ctx.sensors} == {"t_out", "t_kid"}
    payload = build_reasoning_context(ctx, utterance="nóng quá")
    # Cảm biến phòng người nói phải đứng trước trong khối render.
    first_line = payload["sensors"].splitlines()[0]
    assert KIDS_ROOM in first_line
    assert payload["speaker_location"] == KIDS_ROOM


def test_semantic_authoring_context_can_omit_live_states_and_scope_sensors() -> None:
    ctx = _ctx(
        "tôi muốn xem phim",
        speaker_location=KIDS_ROOM,
        live_device_states={"dieu_hoa_phong_con": {"power": "on"}},
        live_sensors=[
            {"slug": "kid", "name": "Nhiệt độ", "sensor_type": "temperature", "value": 27, "room": KIDS_ROOM},
            {"slug": "living", "name": "Nhiệt độ", "sensor_type": "temperature", "value": 30, "room": LIVING_ROOM},
        ],
    )
    payload = build_reasoning_context(
        ctx,
        utterance="tôi muốn xem phim",
        scope_rooms={KIDS_ROOM},
        include_device_states=False,
        max_sensor_items=1,
    )
    assert "device_states" not in payload
    assert KIDS_ROOM in payload["sensors"]
    assert LIVING_ROOM not in payload["sensors"]
    assert len(payload["sensors"].splitlines()) == 1


async def test_gateway_maps_current_room_to_speaker_location_not_focus(monkeypatch) -> None:
    """Regression: current_room (vật lý) phải đi vào speaker_location, KHÔNG bị gộp vào
    focus_room như trước."""
    from src.services import nlu_gateway
    from src.services.nlu_gateway import NluRequest

    captured: dict = {}

    def fake_reason(**kwargs):
        captured.update(kwargs)
        from src.services.pipeline_bridge import ReasoningResult

        return ReasoningResult(semantic_goal=None, candidate_plan=None, outcome="no_action")

    monkeypatch.setattr(nlu_gateway.pipeline_bridge, "reason", fake_reason)

    await nlu_gateway.analyze(
        NluRequest(message="nóng quá", current_room=KIDS_ROOM),
        live_device_states={"dieu_hoa_phong_con": {"power": "on"}},
        live_sensors=[{"slug": "t", "name": "N", "sensor_type": "temperature", "value": 33.0, "unit": "°C", "room": KIDS_ROOM}],
    )

    assert captured["speaker_location"] == KIDS_ROOM
    assert captured["focus_room"] is None
    assert captured["live_device_states"] == {"dieu_hoa_phong_con": {"power": "on"}}
    assert captured["live_sensors"][0]["room"] == KIDS_ROOM
