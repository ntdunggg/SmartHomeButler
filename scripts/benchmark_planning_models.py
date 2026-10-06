#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Benchmark planning model/effort combinations on non-executing golden scenarios.

Examples:
  python scripts/benchmark_planning_models.py --models gpt-5-mini --efforts low,medium
  python scripts/benchmark_planning_models.py --models gpt-5-mini,gpt-5.6-luna --repeats 3
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.pipeline import PipelineDeps, run_planning
from src.agent.state import AgentState
from src.config import get_settings
from src.nlu.model_client import build_nlu_model_client


SCENARIOS = (
    {
        "name": "movie",
        "message": "tôi muốn xem phim",
        "required_domains": set(),
        "required_device_kinds": {"tv_", "loa_", "den_"},
    },
    {
        "name": "guests",
        "message": "tối nay có khách, chuẩn bị phòng khách",
        "required_domains": set(),
        "required_device_kinds": {"robot_hut_bui", "dieu_hoa_", "may_loc_"},
        "exactly_one_device_kind_groups": ({"tv_", "loa_"},),
    },
    {
        "name": "arrival",
        "message": "tôi sắp về nhà",
        "required_domains": set(),
        "required_device_kinds": {"den_", "dieu_hoa_", "may_loc_"},
    },
)


def _state(message: str, run_id: str) -> AgentState:
    return {
        "conversation_id": run_id,
        "user_message": message,
        "user_id": "benchmark",
        "household_id": 0,
        "speaker_role": "owner",
        "now": datetime.now(UTC),
        "speaker_location": "Phòng khách",
        "speaker_home_room": "Phòng ngủ bố mẹ",
        "recent_dialogue": [],
        "live_device_states": None,
        "live_sensors": None,
    }


def _coverage(out: Mapping[str, Any], scenario: dict[str, Any]) -> tuple[float, list[str]]:
    goal = out.get("semantic_goal")
    domains = {
        outcome.selector.domain
        for outcome in (goal.desired_outcomes if goal is not None else [])
        if outcome.selector.domain
    }
    selected = out.get("selected_plan")
    device_ids = {action.device_id for action in (selected.actions if selected is not None else [])}
    checks = [domain in domains for domain in scenario["required_domains"]]
    checks.extend(any(kind in device_id for device_id in device_ids) for kind in scenario["required_device_kinds"])
    missing = [f"domain:{domain}" for domain in sorted(scenario["required_domains"] - domains)]
    missing.extend(
        f"device:{kind}" for kind in sorted(scenario["required_device_kinds"])
        if not any(kind in device_id for device_id in device_ids)
    )
    for group in scenario.get("exactly_one_device_kind_groups", ()):
        matched_kinds = sorted(kind for kind in group if any(kind in device_id for device_id in device_ids))
        checks.append(len(matched_kinds) == 1)
        if len(matched_kinds) != 1:
            missing.append(f"exactly_one_device:{'|'.join(sorted(group))};matched={','.join(matched_kinds)}")
    return (sum(checks) / len(checks) if checks else 1.0), missing


def run_candidate(model: str, effort: str, repeats: int, scenarios: tuple[dict[str, Any], ...] = SCENARIOS) -> dict[str, Any]:
    previous_model = os.environ.get("LLM_PLANNING_MODEL")
    previous_effort = os.environ.get("LLM_PLANNING_REASONING_EFFORT")
    os.environ["LLM_PLANNING_MODEL"] = model
    os.environ["LLM_PLANNING_REASONING_EFFORT"] = effort
    get_settings.cache_clear()
    samples: list[dict[str, Any]] = []
    try:
        for repeat in range(repeats):
            for scenario in scenarios:
                client = build_nlu_model_client()
                if client is None:
                    raise SystemExit("OPENAI_API_KEY/LLM config is missing; live benchmark cannot run")
                deps = PipelineDeps(model_client=client)
                started = time.perf_counter()
                out = run_planning(_state(scenario["message"], f"bench-{model}-{effort}-{repeat}-{scenario['name']}"), deps)
                latency_ms = (time.perf_counter() - started) * 1000
                coverage, missing = _coverage(out, scenario)
                samples.append(
                    {
                        "scenario": scenario["name"],
                        "latency_ms": round(latency_ms, 3),
                        "coverage": round(coverage, 4),
                        "missing": missing,
                        "clarified": bool(out.get("requires_clarification")),
                        "diagnostics": out.get("diagnostics") or {},
                    }
                )
    finally:
        if previous_model is None:
            os.environ.pop("LLM_PLANNING_MODEL", None)
        else:
            os.environ["LLM_PLANNING_MODEL"] = previous_model
        if previous_effort is None:
            os.environ.pop("LLM_PLANNING_REASONING_EFFORT", None)
        else:
            os.environ["LLM_PLANNING_REASONING_EFFORT"] = previous_effort
        get_settings.cache_clear()

    latencies = [sample["latency_ms"] for sample in samples]
    return {
        "model": model,
        "effort": effort,
        "samples": samples,
        "quality_pass_rate": sum(sample["coverage"] >= 0.95 and not sample["clarified"] for sample in samples) / len(samples),
        "latency_p50_ms": round(statistics.median(latencies), 3),
        "latency_p95_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="gpt-5-mini")
    parser.add_argument("--efforts", default="low,medium")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--scenarios", default="", help="Comma-separated scenario names; empty runs all")
    args = parser.parse_args()
    selected_names = {name.strip() for name in args.scenarios.split(",") if name.strip()}
    scenarios = tuple(s for s in SCENARIOS if not selected_names or s["name"] in selected_names)
    if not scenarios:
        raise SystemExit("No benchmark scenarios matched --scenarios")
    results = [
        run_candidate(model.strip(), effort.strip(), max(1, args.repeats), scenarios)
        for model in args.models.split(",") if model.strip()
        for effort in args.efforts.split(",") if effort.strip()
    ]
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
