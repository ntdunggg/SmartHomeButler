"""Hỏi thời tiết / chất lượng không khí ngoài trời — nhận diện, định dạng, và gọi live."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from src.agent.pipeline import PipelineDeps
from src.agent.tools import environment as env_tool
from src.agent.weather_query import WeatherAsk, detect, format_answer
from src.nlu.normalizer import analyze
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

_WEATHER_HCMC = {
    "temperature_2m": 32.4,
    "relative_humidity_2m": 70.0,
    "apparent_temperature": 39.1,
    "precipitation": 0.0,
    "rain": 0.0,
    "weather_code": 2,
    "wind_speed_10m": 11.0,
    "shortwave_radiation": 540.0,
}
_WEATHER_HANOI = {**_WEATHER_HCMC, "temperature_2m": 24.0, "weather_code": 61, "rain": 1.4, "precipitation": 1.4}
_AIR_HCMC = {"pm2_5": 18.5, "us_aqi": 73.0, "uv_index": 7.0}
_AIR_HANOI = {"pm2_5": 96.0, "us_aqi": 168.0, "uv_index": 3.0}


def _make_client(*, weather=_WEATHER_HCMC, air=_AIR_HCMC, geo="found", fail=False) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if fail:
            return httpx.Response(503, json={})
        if "geocoding-api" in host:
            if geo == "notfound":
                return httpx.Response(200, json={"results": []})
            return httpx.Response(
                200,
                json={"results": [{"name": "Hà Nội", "country": "Việt Nam", "latitude": 21.03, "longitude": 105.85}]},
            )
        if "air-quality-api" in host:
            return httpx.Response(200, json={"current": air})
        return httpx.Response(200, json={"current": weather})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _clear_env_cache():
    env_tool.clear_caches()
    yield
    env_tool.clear_caches()


# --- detect() ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,dimension,city",
    [
        ("thời tiết hôm nay thế nào?", "summary", None),
        ("dự báo thời tiết ngày mai", "summary", None),
        ("trời có mưa không?", "rain", None),
        ("ngoài trời nóng không?", "temperature", None),
        ("chỉ số AQI bao nhiêu?", "aqi", None),
        ("chất lượng không khí ngoài trời ra sao?", "aqi", None),
        ("chỉ số UV hôm nay cao không?", "uv", None),
        ("thời tiết ở Hà Nội thế nào?", "summary", "Hà Nội"),
        ("AQI Đà Nẵng bao nhiêu?", "aqi", "Đà Nẵng"),
        ("thời tiết London thế nào?", "summary", "London"),
        ("thời tiết tại thành phố Huế hôm nay", "summary", "Huế"),
    ],
)
def test_detect_positive(text, dimension, city):
    ask = detect(analyze(text))
    assert ask is not None, text
    assert ask.dimension == dimension
    assert ask.city == city


@pytest.mark.parametrize(
    "text",
    [
        "nhiệt độ phòng khách bao nhiêu?",
        "bật đèn phòng khách",
        "trời nóng quá, bật điều hoà",
        "đừng nói thời tiết nữa",
        "mở rèm cho có nắng",
    ],
)
def test_detect_negative(text):
    assert detect(analyze(text)) is None


# --- format_answer() -------------------------------------------------------------------

def _snapshot(**kw) -> env_tool.EnvironmentSnapshot:
    base = dict(
        observed_at=_NOW, source="test", temperature_c=32.0, apparent_temperature_c=39.0,
        humidity_percent=70.0, precipitation_mm=0.0, rain_mm=0.0, weather_code=2,
        wind_speed_kmh=11.0, shortwave_radiation_wm2=540.0, pm25_ugm3=18.5, us_aqi=73.0, uv_index=7.0,
    )
    base.update(kw)
    return env_tool.EnvironmentSnapshot(**base)


def test_format_summary_mentions_place_and_key_dimensions():
    text = format_answer(_snapshot(), WeatherAsk("summary", "Hà Nội"), place_label="Hà Nội, Việt Nam")
    assert "Hà Nội, Việt Nam" in text
    assert "32°C" in text and "AQI 73" in text and "UV 7" in text


def test_format_rain_uses_snapshot_flag():
    wet = format_answer(_snapshot(rain_mm=1.4, weather_code=61), WeatherAsk("rain", None), place_label="")
    dry = format_answer(_snapshot(), WeatherAsk("rain", None), place_label="")
    assert "đang mưa" in wet
    assert "không mưa" in dry


def test_format_aqi_includes_band_label():
    text = format_answer(_snapshot(us_aqi=168.0, pm25_ugm3=96.0), WeatherAsk("aqi", None), place_label="")
    assert "AQI 168" in text and "xấu" in text


# --- end-to-end qua reason() ---------------------------------------------------------

def _ask(text: str, client: httpx.Client):
    return reason(message=text, conversation_id=f"wq-{text}", now=_NOW, deps=PipelineDeps(environment_client=client))


def test_home_location_weather_is_answered_live():
    r = _ask("thời tiết hôm nay thế nào?", _make_client())
    assert r.outcome == "answer"
    assert "32.4°C" in r.reply and "AQI 73" in r.reply


def test_named_city_is_geocoded_then_fetched():
    r = _ask("thời tiết ở Hà Nội thế nào?", _make_client(weather=_WEATHER_HANOI, air=_AIR_HANOI))
    assert r.outcome == "answer"
    assert "Hà Nội" in r.reply
    assert "24°C" in r.reply and "AQI 168" in r.reply


def test_unknown_city_is_reported_not_guessed():
    r = _ask("thời tiết ở Xyzville thế nào?", _make_client(geo="notfound"))
    assert r.outcome == "answer"
    assert "chưa tìm được địa điểm" in r.reply.lower() or "chưa tìm được" in r.reply


def test_forecast_phrasing_no_longer_asks_for_a_room():
    r = _ask("dự báo thời tiết ngày mai", _make_client())
    assert r.outcome == "answer"
    assert "phòng nào" not in r.reply


def test_provider_failure_without_city_falls_back_to_indoor_sensors():
    r = _ask("thời tiết hôm nay thế nào?", _make_client(fail=True))
    assert r.outcome == "answer"
    # Fallback đọc cảm biến ngoài trời trong snapshot (không crash, không hỏi phòng).
    assert "phòng nào" not in r.reply


def test_indoor_device_question_is_not_hijacked():
    r = _ask("điều hoà phòng khách đang bật không?", _make_client())
    assert r.outcome == "answer"
    assert "AQI" not in r.reply and "°C" not in r.reply.replace("26°C", "")
