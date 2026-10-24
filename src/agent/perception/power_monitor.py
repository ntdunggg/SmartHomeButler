"""Power Load monitor (spec §8) — chuẩn hoá watt thành NORMAL/MODERATE/CRITICAL.

Ngưỡng đọc từ ``config/energy.yaml`` (spec §8: "Không hardcode trực tiếp trong agent
logic"). Đây là Layer 1 — chỉ *quan sát* tải điện, không quyết định gì.
"""

from __future__ import annotations

from src.agent.config import get_energy_config
from src.agent.schemas import PowerLoad, PowerMode


def classify_power(current_watts: float) -> PowerMode:
    """Phân loại tải điện hiện tại theo ngưỡng config (spec §8)."""
    cfg = get_energy_config()["power_load"]
    moderate = float(cfg["moderate_threshold_w"])
    critical = float(cfg["critical_threshold_w"])
    if current_watts >= critical:
        return PowerMode.CRITICAL
    if current_watts >= moderate:
        return PowerMode.MODERATE
    return PowerMode.NORMAL


def build_power_load(current_watts: float, *, unknown: bool = False) -> PowerLoad:
    """Dựng snapshot PowerLoad từ watt đo được (spec §7.3 `power`). `unknown=True` khi CHƯA có
    cơ sở đo tải (spec §7.4: không trình bày mặc định như quan sát thật)."""
    cfg = get_energy_config()["power_load"]
    return PowerLoad(
        current_watts=max(0.0, float(current_watts)),
        mode=classify_power(current_watts),
        moderate_threshold_w=float(cfg["moderate_threshold_w"]),
        critical_threshold_w=float(cfg["critical_threshold_w"]),
        unknown=unknown,
    )


def estimate_baseline_watts(live_device_states: dict[str, dict] | None) -> float:
    """Ước lượng tải điện hiện tại từ trạng thái thiết bị đang BẬT (spec §42).

    Dùng bảng `device_power_w` trong config. Thiết bị không rõ công suất → bỏ qua
    (unknown, KHÔNG bịa). Thiết bị `power == "off"` không tính.
    """
    if not live_device_states:
        return 0.0
    from src.iot.registry import spec_for

    power_table = get_energy_config().get("device_power_w", {})
    total = 0.0
    for slug, state in live_device_states.items():
        if not isinstance(state, dict):
            continue
        if str(state.get("power", "")).lower() == "off":
            continue
        spec = spec_for(slug)
        if spec is None:
            continue
        watts = power_table.get(spec.device_type.value)
        if watts is None:
            continue
        total += float(watts)
    return total
