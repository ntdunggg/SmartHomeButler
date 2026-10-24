"""Nạp cấu hình năng lượng/RL từ ``config/energy.yaml`` (spec §8, §34, §40).

Tách khỏi ``src.config.Settings`` (biến môi trường) vì đây là cấu hình *mô hình*
(ngưỡng power, trọng số optimizer, reward mapping) — thứ sản phẩm/nghiên cứu chỉnh,
KHÔNG hardcode trong agent logic (spec §8). Có default an toàn nếu thiếu file để
test chạy được ở mọi CWD.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# config/energy.yaml nằm ở gốc repo: file này là src/agent/config.py → lên 2 cấp.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "energy.yaml"

_DEFAULTS: dict[str, Any] = {
    "power_load": {"moderate_threshold_w": 3500, "critical_threshold_w": 5000},
    "optimizer_weights": {
        "NORMAL": {"comfort": 1.0, "preference": 0.8, "energy": 0.2, "constraint": 5.0},
        "MODERATE": {"comfort": 0.8, "preference": 0.6, "energy": 0.6, "constraint": 5.0},
        "CRITICAL": {"comfort": 0.5, "preference": 0.3, "energy": 1.5, "constraint": 5.0},
    },
    "energy_normalizer_w": 2000,
    "reward": {
        "explicit_accept": 1.0,
        "no_correction": 0.2,
        "slight_adjustment": -0.3,
        "explicit_reject": -1.0,
    },
    "rl": {"learning_rate": 0.3},
    "device_power_w": {},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


@lru_cache
def get_energy_config() -> dict[str, Any]:
    """Cấu hình năng lượng đã merge với default. Thiếu file → dùng default (không lỗi)."""
    if not _CONFIG_PATH.exists():
        return _DEFAULTS
    try:
        loaded = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return _DEFAULTS
    if not isinstance(loaded, dict):
        return _DEFAULTS
    return _deep_merge(_DEFAULTS, loaded)
