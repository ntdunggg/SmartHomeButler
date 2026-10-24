"""Test Location Resolver: fusion tín hiệu → vị trí + độ tin cậy, và luật "hỏi khi mơ hồ".

Offline thuần, không DB/LLM.
"""

from __future__ import annotations

from src.iot.registry import KIDS_ROOM, KITCHEN, LIVING_ROOM
from src.services.location_resolver import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    build_signals,
    resolve_location,
)

_ROOMS = {LIVING_ROOM, KITCHEN, KIDS_ROOM}


def _resolve(*, capture=None, presence=None):
    return resolve_location(build_signals(capture_room=capture, presence_rooms=presence, valid_rooms=_ROOMS))


def test_capture_device_alone_is_confident() -> None:
    est = _resolve(capture=KITCHEN)
    assert est.is_confident and est.room == KITCHEN
    assert est.source == "capture_device"
    assert est.confidence >= DEFAULT_CONFIDENCE_THRESHOLD


def test_capture_and_presence_agree_boosts_confidence() -> None:
    est = _resolve(capture=KIDS_ROOM, presence=[KIDS_ROOM])
    assert est.room == KIDS_ROOM
    assert est.confidence == 1.0  # cùng phòng, cộng dồn rồi chặn trần


def test_single_presence_room_is_confident() -> None:
    est = _resolve(presence=[LIVING_ROOM])
    assert est.is_confident and est.room == LIVING_ROOM
    assert est.source == "presence_sensor"


def test_conflicting_signals_are_uncertain_and_ask() -> None:
    # Loa/mic nói phòng khách nhưng presence báo phòng con → mâu thuẫn → không đủ tin cậy.
    est = _resolve(capture=LIVING_ROOM, presence=[KIDS_ROOM])
    assert not est.is_confident and est.room is None
    assert est.confidence < DEFAULT_CONFIDENCE_THRESHOLD
    # Vẫn nêu được ứng viên để câu hỏi lại có gợi ý.
    assert {r for r, _ in est.candidates} == {LIVING_ROOM, KIDS_ROOM}


def test_presence_in_two_rooms_is_uncertain() -> None:
    est = _resolve(presence=[LIVING_ROOM, KIDS_ROOM])
    assert est.room is None
    assert est.confidence == 0.5


def test_no_signal_is_uncertain() -> None:
    est = resolve_location(build_signals(valid_rooms=_ROOMS))
    assert est.room is None and est.confidence == 0.0
    assert est.source == ""


def test_invalid_room_signal_is_dropped() -> None:
    est = _resolve(capture="Sao Hoả", presence=["Vịnh Hạ Long"])
    assert est.room is None and est.confidence == 0.0


def test_capture_beats_presence_when_capture_stronger() -> None:
    # Cùng phòng thì không xét; đây kiểm phòng thắng khi capture mạnh hơn presence phòng khác
    # nhưng chưa tới mức mâu thuẫn kéo xuống dưới ngưỡng? -> đây là ca mâu thuẫn, đã test ở trên.
    # Ca này: capture + presence CÙNG phòng khách, presence thêm phòng bếp (người khác).
    est = _resolve(capture=LIVING_ROOM, presence=[LIVING_ROOM, KITCHEN])
    assert est.room == LIVING_ROOM  # phòng khách: 0.7+0.6=1.3 áp đảo bếp 0.6


# --- Tích hợp gateway: resolver quyết định có bơm vị trí hay hỏi lại ---
async def _run_analyze(monkeypatch, req_kwargs, live_sensors=None):
    from src.services import nlu_gateway
    from src.services.nlu_gateway import NluRequest
    from src.services.pipeline_bridge import ReasoningResult

    captured: dict = {}

    def fake_reason(**kwargs):
        captured.update(kwargs)
        return ReasoningResult(semantic_goal=None, candidate_plan=None, outcome="no_action")

    monkeypatch.setattr(nlu_gateway.pipeline_bridge, "reason", fake_reason)
    resp = await nlu_gateway.analyze(NluRequest(**req_kwargs), live_sensors=live_sensors)
    return captured, resp


async def test_gateway_confident_location_is_injected(monkeypatch) -> None:
    captured, resp = await _run_analyze(monkeypatch, {"message": "nóng quá", "current_room": KIDS_ROOM})
    assert captured["speaker_location"] == KIDS_ROOM
    assert resp.location is not None and resp.location.resolved
    assert resp.location.room == KIDS_ROOM


async def test_gateway_uncertain_location_not_injected_so_agent_asks(monkeypatch) -> None:
    # Tín hiệu mâu thuẫn qua bảng giả lập → không đủ tin cậy → speaker_location None.
    captured, resp = await _run_analyze(
        monkeypatch, {"message": "nóng quá", "current_room": LIVING_ROOM, "presence_rooms": [KIDS_ROOM]}
    )
    assert captured["speaker_location"] is None, "chưa chắc thì KHÔNG áp vị trí, để pipeline hỏi lại"
    assert resp.location is not None and not resp.location.resolved
    assert set(resp.location.candidates) == {LIVING_ROOM, KIDS_ROOM}


async def test_gateway_presence_from_live_sensors_when_no_simulation(monkeypatch) -> None:
    # Không có bảng giả lập → resolver lấy presence từ cảm biến thật (live_sensors).
    live = [{"slug": "p", "name": "Hiện diện", "sensor_type": "presence", "value": 1.0, "unit": "", "room": KITCHEN}]
    captured, resp = await _run_analyze(monkeypatch, {"message": "nóng quá"}, live_sensors=live)
    assert captured["speaker_location"] == KITCHEN
    assert resp.location.source == "presence_sensor"
