"""Dev script: Fetch live weather & air quality data from Open-Meteo API
and map it directly to Smart Home sensor specs.

Toạ độ mặc định lấy từ Settings (ENVIRONMENT_LATITUDE/LONGITUDE) — KHÔNG hardcode
thành phố, để chỉ có một nguồn sự thật về vị trí nhà (xem src/agent/tools/environment.py).
"""

import json
import urllib.request


def _home_coords() -> tuple[float, float]:
    from src.config import get_settings

    s = get_settings()
    return s.environment_latitude, s.environment_longitude


def fetch_open_meteo_sensors(lat: float | None = None, lon: float | None = None) -> dict:
    if lat is None or lon is None:
        home_lat, home_lon = _home_coords()
        lat = home_lat if lat is None else lat
        lon = home_lon if lon is None else lon
    # 1. Fetch Weather data
    weather_url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,precipitation,rain,uv_index,wind_speed_10m,direct_normal_irradiance"
    )

    # 2. Fetch Air Quality data
    air_quality_url = (
        f"https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}"
        f"&current=pm2_5,us_aqi,european_aqi"
    )

    req_w = urllib.request.Request(weather_url, headers={"User-Agent": "SmartHome/1.0"})
    with urllib.request.urlopen(req_w, timeout=10) as resp:
        weather_data = json.loads(resp.read().decode("utf-8"))

    req_aq = urllib.request.Request(air_quality_url, headers={"User-Agent": "SmartHome/1.0"})
    with urllib.request.urlopen(req_aq, timeout=10) as resp:
        air_data = json.loads(resp.read().decode("utf-8"))

    curr_w = weather_data.get("current", {})
    curr_aq = air_data.get("current", {})

    # Map to Smart Home Sensor format
    sensors = [
        {
            "slug": "cam_bien_nhiet_do",
            "name": "Nhiệt độ ngoài trời",
            "sensor_type": "temperature",
            "value": round(curr_w.get("temperature_2m", 0.0), 1),
            "unit": "°C",
            "raw_api_key": "temperature_2m",
        },
        {
            "slug": "cam_bien_do_am",
            "name": "Độ ẩm ngoài trời",
            "sensor_type": "humidity",
            "value": round(curr_w.get("relative_humidity_2m", 0.0), 1),
            "unit": "%",
            "raw_api_key": "relative_humidity_2m",
        },
        {
            "slug": "cam_bien_mua",
            "name": "Cảm biến mưa",
            "sensor_type": "rain",
            "value": 1.0 if (curr_w.get("rain", 0) > 0 or curr_w.get("precipitation", 0) > 0) else 0.0,
            "unit": "",
            "display_text": "Đang mưa" if (curr_w.get("rain", 0) > 0 or curr_w.get("precipitation", 0) > 0) else "Không mưa",
            "raw_api_key": "rain / precipitation",
        },
        {
            "slug": "cam_bien_nang",
            "name": "Cường độ ánh nắng",
            "sensor_type": "sunlight",
            # Normalizing direct_normal_irradiance (0 - 1000 W/m²) into percentage (0 - 100%)
            "value": min(100.0, round((curr_w.get("direct_normal_irradiance", 0.0) / 1000.0) * 100, 1)),
            "unit": "%",
            "raw_api_key": "direct_normal_irradiance",
        },
        {
            "slug": "cam_bien_uv",
            "name": "Chỉ số UV",
            "sensor_type": "uv_index",
            "value": round(curr_w.get("uv_index", 0.0), 1),
            "unit": "UV",
            "raw_api_key": "uv_index",
        },
        {
            "slug": "cam_bien_gio",
            "name": "Tốc độ gió",
            "sensor_type": "wind_speed",
            "value": round(curr_w.get("wind_speed_10m", 0.0), 1),
            "unit": "km/h",
            "raw_api_key": "wind_speed_10m",
        },
        {
            "slug": "cam_bien_bui",
            "name": "PM2.5 ngoài trời",
            "sensor_type": "pm25",
            "value": round(curr_aq.get("pm2_5", 0.0), 1),
            "unit": "µg/m³",
            "raw_api_key": "pm2_5",
        },
        {
            "slug": "cam_bien_aqi",
            "name": "Chỉ số chất lượng không khí",
            "sensor_type": "aqi",
            "value": round(curr_aq.get("us_aqi", 0.0), 1),
            "unit": "AQI",
            "raw_api_key": "us_aqi",
        },
    ]

    return {
        "location": f"{lat:.4f}°N, {lon:.4f}°E",
        "timestamp": curr_w.get("time"),
        "sensors": sensors,
    }


if __name__ == "__main__":
    print("=" * 65)
    print("📡 Đang gửi request tới Open-Meteo API (toạ độ nhà từ Settings)...")
    print("=" * 65)

    result = fetch_open_meteo_sensors()

    print(f"📍 Địa điểm: {result['location']}")
    print(f"⏰ Thời gian cập nhật: {result['timestamp']}\n")
    print(f"{'SLUG':<20} | {'TÊN CẢM BIẾN':<28} | {'GIÁ TRỊ THẬT':<15} | {'RAW FIELD'}")
    print("-" * 85)

    for s in result["sensors"]:
        val_str = f"{s['value']} {s['unit']}".strip()
        if "display_text" in s:
            val_str = f"{s['value']} ({s['display_text']})"
        print(f"{s['slug']:<20} | {s['name']:<28} | {val_str:<15} | {s['raw_api_key']}")

    print("\n" + "=" * 65)
    print("📦 Dữ liệu JSON ánh xạ chuẩn vào format Backend / Dashboard:")
    print("=" * 65)
    print(json.dumps(result["sensors"], ensure_ascii=False, indent=2))
