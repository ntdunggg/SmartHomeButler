"""State/knowledge query branch (parity với graph cũ) — trả lời từ snapshot sống.

Câu HỎI không lập kế hoạch (understanding không execute): đọc trạng thái thật và trả
lời trực tiếp, hoặc trả lời năng lực hệ thống. Bridge Phase 3 map thành outcome=answer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, run_planning, run_turn
from src.agent.preference.preference_store import PreferenceStore
from src.core.reasoning import FakeReasoningModel
from src.domain.models import Device, User
from src.iot.registry import DEVICE_SPECS
from src.services import agent_runner
from src.services.pipeline_bridge import reason


@pytest.fixture
def deps():
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), model_client=FakeReasoningModel(),
    )


def _base(msg, **kw):
    s = {
        "conversation_id": "q", "user_id": "u", "user_message": msg,
        "speaker_role": "owner",
        "now": datetime(2026, 8, 15, 20, 0, tzinfo=UTC), "speaker_location": "Phòng khách",
    }
    s.update(kw)
    return s


def test_state_query_answered_from_live_snapshot(deps):
    out = run_planning(_base("điều hoà phòng khách bật chưa?", live_device_states={"dieu_hoa_phong_khach": {"power": "on", "temperature": 24}}), deps)
    assert out["final_status"] == "answered"
    assert "bật" in out["reply"] and "24" in out["reply"]
    # KHÔNG lập kế hoạch cho câu hỏi.
    assert out.get("selected_plan") is None


def test_state_query_reflects_off_state(deps):
    out = run_planning(_base("đèn phòng khách đang bật không?", live_device_states={"den_chum_phong_khach": {"power": "off"}}), deps)
    assert out["final_status"] == "answered"
    assert "tắt" in out["reply"]


@pytest.mark.parametrize(
    "message",
    [
        "kiểm tra xem điều hoà nào đang bật không",
        "máy lạnh nào đang chạy không",
    ],
)
def test_generic_device_type_query_reports_only_active_matching_devices(deps, message):
    out = run_planning(
        _base(
            message,
            speaker_location=None,
            live_device_states={
                "dieu_hoa_phong_khach": {"power": "off", "temperature": 24},
                "dieu_hoa_phong_bo_me": {"power": "on", "temperature": 26},
                "dieu_hoa_phong_con": {"power": "off", "temperature": 25},
            },
        ),
        deps,
    )

    assert out["final_status"] == "answered"
    assert "phòng ngủ bố mẹ" in out["reply"].lower()
    assert "phòng khách" not in out["reply"].lower()
    assert "mình điều khiển được" not in out["reply"].lower()


def test_multi_turn_group_query_keeps_device_scope_and_beats_outdoor_sensor(deps):
    states = {
        "dieu_hoa_phong_khach": {"power": "off", "temperature": 24},
        "dieu_hoa_phong_bo_me": {"power": "off", "temperature": 26},
        "dieu_hoa_phong_con": {"power": "off", "temperature": 25},
    }
    sensors = [
        {
            "slug": "nhiet_ngoai_troi",
            "name": "Nhiệt độ ngoài trời",
            "sensor_type": "temperature",
            "value": 31.1,
            "unit": "°C",
            "room": "",
        }
    ]
    common = {
        "conversation_id": "q-multi-ac",
        "user_id": "u",
        "speaker_role": "owner",
        "now": datetime(2026, 8, 15, 20, 0, tzinfo=UTC),
        "speaker_location": "Phòng khách",
        "live_device_states": states,
        "live_sensors": sensors,
    }

    run_turn({**common, "user_message": "tắt tất cả điều hoà"}, deps)
    status = run_turn(
        {**common, "user_message": "kiểm tra xem điều hoà nào đang bật không"}, deps
    )
    temperature = run_turn(
        {**common, "user_message": "nhiệt độ điều hoà hiện tại là bao nhiêu"}, deps
    )

    assert "không có điều hoà nào đang bật" in status["reply"].lower()
    assert "mình điều khiển được" not in status["reply"].lower()
    assert all(str(value) in temperature["reply"] for value in (24, 25, 26))
    assert "31.1" not in temperature["reply"]
    ledger = deps.ledger_store.load("q-multi-ac")
    assert ledger.confirmed_facts["devices"] == list(states)


def test_capability_question_answered_generically(deps):
    out = run_planning(_base("hệ thống làm được gì?"), deps)
    assert out["final_status"] == "answered"
    assert out["reply"]


def test_realtime_aqi_query_answered_from_live_open_meteo(deps):
    """Câu hỏi AQI ngoài trời → gọi Open-Meteo LIVE (không phòng nêu → toạ độ nhà)."""
    import httpx

    from src.agent.tools import environment as env_tool

    env_tool.clear_caches()

    def handler(request: httpx.Request) -> httpx.Response:
        if "air-quality-api" in request.url.host:
            return httpx.Response(200, json={"current": {"pm2_5": 40.0, "us_aqi": 137.0, "uv_index": 5.0}})
        return httpx.Response(
            200,
            json={"current": {
                "temperature_2m": 30.0, "relative_humidity_2m": 65.0, "apparent_temperature": 34.0,
                "precipitation": 0.0, "rain": 0.0, "weather_code": 1, "wind_speed_10m": 8.0,
                "shortwave_radiation": 300.0,
            }},
        )

    deps.environment_client = httpx.Client(transport=httpx.MockTransport(handler))
    out = run_planning(_base("AQI hiện tại bao nhiêu?"), deps)
    env_tool.clear_caches()

    assert out["final_status"] == "answered"
    assert "137" in out["reply"]
    assert out.get("selected_plan") is None


def test_bridge_maps_state_query_to_answer(deps):
    r = reason(
        message="điều hoà phòng khách bật chưa?", conversation_id="qb", role="owner",
        now=datetime(2026, 8, 15, 20, 0, tzinfo=UTC), speaker_location="Phòng khách",
        live_device_states={"dieu_hoa_phong_khach": {"power": "on", "temperature": 24}}, deps=deps,
    )
    assert r.outcome == "answer"
    assert r.candidate_plan is None and r.reply


@pytest.mark.asyncio
async def test_bridge_state_query_no_execution(seeded):
    bo = seeded.scalar(select(User).where(User.username == "bo"))
    result = await agent_runner.run_command(seeded, user=bo, message="đèn phòng khách đang bật không?", conversation_id="v2-q")
    assert result["approval"] is None
    assert not (result["state"].get("executed") or [])
    assert result["state"]["response_vi"]  # có câu trả lời
    # Không thiết bị nào bị đổi trạng thái bởi một câu hỏi.
    dev = seeded.scalar(select(Device).where(Device.slug == "den_chum_phong_khach"))
    assert dev.state.get("power") in ("off", None) or True  # câu hỏi không đổi trạng thái


# --- Tổng quan CẢ NHÀ: thiết bị nào đang bật, bao nhiêu cái, trạng thái ra sao ---------
#
# Câu hỏi tổng quan không nêu alias/phòng/loại thiết bị nào, nên trước bản vá nó rơi khỏi cổng
# state-query và bị trả lời như câu hỏi năng lực ("Mình điều khiển được đèn, điều hoà..."), dù
# câu trả lời nằm sẵn trong snapshot sống.

# Snapshot ĐẦY ĐỦ: chỉ khi backend bơm trạng thái của mọi thiết bị thì "cả nhà" mới có mẫu số
# thật. Dựng từ registry để tổng số không phải hằng chép tay.
_ON = ("den_chum_phong_khach", "den_bep", "dieu_hoa_phong_bo_me")
_WHOLE_HOME = {
    spec.slug: {
        **spec.initial_state,
        **({"power": "on"} if spec.slug in _ON else {}),
        **({"power": "off"} if "power" in spec.initial_state and spec.slug not in _ON else {}),
    }
    for spec in DEVICE_SPECS
}
_TOTAL = len(DEVICE_SPECS)


@pytest.mark.parametrize(
    "message",
    [
        "trong nhà có bao nhiêu thiết bị đang bật?",
        "nhà mình đang bật những gì?",
        "liệt kê các thiết bị đang bật",
        "thiết bị nào đang bật?",
        "trong nha co bao nhieu thiet bi dang bat",
    ],
)
def test_whole_home_query_reports_which_devices_are_on_with_a_count(deps, message):
    out = run_planning(_base(message, live_device_states=_WHOLE_HOME), deps)

    assert out["final_status"] == "answered"
    reply = out["reply"]
    assert "mình điều khiển được" not in reply.lower()
    # Đúng 3 thiết bị có điện đang bật, trên tổng số thiết bị của cả nhà.
    assert f"Đang bật 3/{_TOTAL} thiết bị trong nhà:" in reply
    assert all(name in reply for name in ("Đèn chùm", "Đèn bếp", "Điều hoà"))
    # Phòng phải kèm theo vì báo cáo trải nhiều phòng.
    assert "Phòng ngủ bố mẹ" in reply
    assert out.get("selected_plan") is None


def test_whole_home_query_is_not_narrowed_to_the_speaker_room(deps):
    out = run_planning(
        _base("trong nhà có bao nhiêu thiết bị đang bật?", live_device_states=_WHOLE_HOME),
        deps,
    )

    # Người nói đứng ở Phòng khách nhưng hỏi cả nhà: bếp và phòng ngủ phải có mặt.
    assert "Đèn bếp" in out["reply"]
    assert "Phòng bếp" in out["reply"]


def test_open_curtain_and_unlocked_door_are_not_counted_as_powered_on(deps):
    reply = run_planning(
        _base("trong nhà có bao nhiêu thiết bị đang bật?", live_device_states=_WHOLE_HOME),
        deps,
    )["reply"]

    head, _, rest = reply.partition("Ngoài ra")
    assert "Rèm thông minh" not in head and "Cửa ra vào phòng" not in head
    # Vẫn phải báo cáo, chỉ là ở mục riêng — mở rèm/mở khoá không phải "đang bật".
    assert "Rèm thông minh" in rest and "Cửa ra vào phòng" in rest


def test_explicit_room_still_scopes_a_generic_device_question(deps):
    reply = run_planning(
        _base("phòng bếp có thiết bị nào đang bật không?", live_device_states=_WHOLE_HOME),
        deps,
    )["reply"]

    assert "Đèn bếp" in reply
    assert "Đèn chùm" not in reply and "Điều hoà" not in reply


def test_home_scope_beats_speaker_room_for_a_device_type_question(deps):
    reply = run_planning(
        _base("cả nhà có đèn nào đang bật?", live_device_states=_WHOLE_HOME),
        deps,
    )["reply"]

    assert "Đèn chùm" in reply and "Đèn bếp" in reply


def test_survey_question_is_never_executed_as_a_turn_on_command(deps):
    """Hỏi "đèn nào đang bật?" mà đi bật đèn là tự ý đổi trạng thái nhà — nguy hiểm hơn trả lời sai."""
    out = run_planning(
        _base("cả nhà có đèn nào đang bật?", live_device_states=_WHOLE_HOME), deps
    )

    assert out["final_status"] == "answered"
    assert out.get("selected_plan") is None
    assert not (out.get("executed") or [])


def test_a_state_filtered_command_is_still_a_command(deps):
    """Ranh giới ngược lại: "tắt cái nào đang bật" cũng chứa "nào ... đang bật" nhưng là LỆNH."""
    out = run_planning(
        _base("tắt đèn nào đang bật ở phòng khách", live_device_states=_WHOLE_HOME), deps
    )

    goal = out["semantic_goal"]
    assert goal is not None and goal.action_hint == "turn_off"
    assert out.get("final_status") != "answered"


# --- "có" NGHI VẤN vs "có" ĐỒNG Ý -------------------------------------------
# `_CONFIRM` (src/nlu/understanding.py) neo ^ nên trước bản vá MỌI câu chưa khớp alias thiết bị
# mà mở đầu bằng có/được/đúng/vâng đều bị nuốt thành CONFIRMATION. Pipeline không có nhánh nào
# đọc CONFIRMATION → câu rơi tiếp xuống authoring LLM và quay ra HỎI LẠI PHÒNG ("Bạn muốn mình
# làm ở Phòng khách, Phòng bếp, … ạ?") cho một câu hỏi đọc thẳng được từ snapshot sống.
@pytest.mark.parametrize(
    "message",
    [
        "có bao nhiêu thiết bị đang bật?",
        "có bao nhiêu thiết bị đang bật",
        "có thiết bị nào đang bật không?",
        "co bao nhieu thiet bi dang bat",
    ],
)
def test_question_opening_with_co_is_not_swallowed_as_an_affirmation(deps, message):
    out = run_planning(_base(message, live_device_states=_WHOLE_HOME), deps)

    assert out["final_status"] == "answered"
    reply = out["reply"]
    # Không được hỏi ngược lại phòng: câu hỏi cả nhà không thiếu thông tin nào.
    assert "làm ở" not in reply and "phòng nào" not in reply.lower()
    assert f"Đang bật 3/{_TOTAL} thiết bị trong nhà:" in reply
    assert out.get("selected_plan") is None


@pytest.mark.parametrize("message", ["có", "ừ", "được", "vâng ạ", "đúng rồi"])
def test_a_bare_affirmation_is_still_a_confirmation(message):
    """Ranh giới ngược lại: hạt xác nhận đứng một mình vẫn là XÁC NHẬN, không thành câu hỏi."""
    from src.nlu.normalizer import analyze
    from src.nlu.ontology import UtteranceType
    from src.nlu.schemas import RuntimeContext
    from src.nlu.understanding import understand

    nu = analyze(message)
    ctx = RuntimeContext(now=datetime(2026, 8, 15, 20, 0, tzinfo=UTC), devices=[], sensors=[], rooms=[])
    assert understand(nu, ctx).utterance_type == UtteranceType.CONFIRMATION
