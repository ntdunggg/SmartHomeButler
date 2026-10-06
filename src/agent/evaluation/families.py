"""Anti-overfitting evaluation (spec §59-63) — semantic families + context perturbation.

Đánh giá theo SEMANTIC FAMILY, không theo exact string (spec §59). Mỗi family cung cấp
một SemanticGoal ĐẠI DIỆN (thứ tầng understanding sẽ author cho mọi paraphrase cùng
họ) rồi chạy qua planning + harness dưới NHIỀU context (§60 perturbation) để kiểm:

- Hard gates §62: hallucinated_device = 0, unsupported_capability = 0.
- Context perturbation §60: cùng goal, context khác → plan khác (không keyword-rule).
- Task success: có plan feasible phục vụ đúng chiều tiện nghi.

Đây là eval cho tầng TẤT ĐỊNH (planning/harness) — phần generalization ngôn ngữ của
understanding do goldenset LLM đo riêng (spec §71 không đưa goldenset vào prompt).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.agent.pipeline import PipelineDeps, run_turn
from src.agent.schemas import DesiredOutcome, SemanticGoal
from src.agent.state import AgentState
from src.core.interfaces import DeviceSelector
from src.iot.registry import DEVICE_BY_SLUG, spec_for
from src.nlu.ontology import UtteranceType


@dataclass(slots=True)
class ContextSpec:
    """Một biến thể ngữ cảnh để perturbation (spec §60)."""

    name: str
    live_device_states: dict[str, dict] = field(default_factory=dict)
    live_sensors: list[dict] = field(default_factory=list)
    now: datetime = field(default_factory=lambda: datetime(2026, 8, 15, 20, 0, tzinfo=UTC))
    speaker_location: str = "Phòng khách"


@dataclass(slots=True)
class SemanticFamily:
    """Một họ ngữ nghĩa: goal đại diện + kỳ vọng chiều tiện nghi phục vụ."""

    name: str
    make_goal: Callable[[], SemanticGoal]
    expected_agents_any: frozenset[str]  # plan phải dùng ÍT NHẤT một trong các agent này


@dataclass(slots=True)
class CaseOutcome:
    family: str
    context: str
    final_status: str
    selected_agents: tuple[str, ...]
    hallucinated_devices: int
    unsupported_capabilities: int
    served_expected: bool


def _env_goal(perceived: str, capability: str, direction: str, *, area: str | None = None) -> SemanticGoal:
    return SemanticGoal(
        intent=perceived,
        raw_utterance=perceived,
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        goal_description=perceived,
        confidence=0.85,
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area=area) if area else DeviceSelector(),
                perceived_state=perceived,
                relative_change={capability: direction},
            )
        ],
    )


def default_families() -> list[SemanticFamily]:
    """Các họ ngữ nghĩa đại diện cho test families của spec (§63)."""
    room = "Phòng khách"
    return [
        SemanticFamily("illumination_increase", lambda: _env_goal("too_dark", "brightness", "increase", area=room), frozenset({"lighting", "shutter"})),
        SemanticFamily("illumination_decrease", lambda: _env_goal("too_bright", "brightness", "decrease", area=room), frozenset({"lighting", "shutter"})),
        SemanticFamily("thermal_warmer", lambda: _env_goal("cold", "temperature", "increase", area=room), frozenset({"ac"})),
        SemanticFamily("thermal_cooler", lambda: _env_goal("hot", "temperature", "decrease", area=room), frozenset({"ac"})),
        SemanticFamily("media_quieter", lambda: _env_goal("noisy", "volume", "decrease", area=room), frozenset({"media"})),
    ]


def default_contexts() -> list[ContextSpec]:
    """Perturbations: ban ngày (có nắng) vs ban đêm (spec §60)."""
    daylight = [{"slug": "cam_bien_nang", "name": "nắng", "sensor_type": "sunlight", "value": 80.0, "unit": "%", "room": ""}]
    night = [{"slug": "cam_bien_nang", "name": "nắng", "sensor_type": "sunlight", "value": 0.0, "unit": "%", "room": ""}]
    off_states = {
        "den_chum_phong_khach": {"power": "off", "brightness": 0},
        "rem_phong_khach": {"power": "off", "position": 0},
        "dieu_hoa_phong_khach": {"power": "off", "temperature": 26},
        "loa_phong_khach": {"power": "on", "volume": 60},
    }
    return [
        ContextSpec("daylight", live_device_states=off_states, live_sensors=daylight, now=datetime(2026, 8, 15, 10, 0, tzinfo=UTC)),
        ContextSpec("night", live_device_states=off_states, live_sensors=night, now=datetime(2026, 8, 15, 22, 0, tzinfo=UTC)),
    ]


def _count_gate_violations(state: AgentState) -> tuple[int, int]:
    """Đếm hallucinated device + unsupported capability trong plan được chọn (spec §62)."""
    selected = state.get("selected_plan")
    hallucinated = 0
    unsupported = 0
    if selected is not None:
        for a in selected.actions:
            spec = spec_for(a.device_id)
            if spec is None or a.device_id not in DEVICE_BY_SLUG:
                hallucinated += 1
                continue
            try:
                from src.domain.enums import Capability

                if Capability(a.capability) not in spec.capabilities:
                    unsupported += 1
            except ValueError:
                unsupported += 1
    return hallucinated, unsupported


def run_case(family: SemanticFamily, ctx: ContextSpec, deps: PipelineDeps) -> CaseOutcome:
    state: AgentState = {
        "conversation_id": f"{family.name}-{ctx.name}",
        "user_id": "user_A",
        "user_message": family.make_goal().raw_utterance,
        "speaker_role": "owner",
        "now": ctx.now,
        "speaker_location": ctx.speaker_location,
        "semantic_goal": family.make_goal(),
        "live_device_states": ctx.live_device_states,
        "live_sensors": ctx.live_sensors,
    }
    out = run_turn(state, deps)
    selected = out.get("selected_plan")
    agents = tuple(selected.source_agents) if selected else ()
    hallucinated, unsupported = _count_gate_violations(out)
    served = bool(set(agents) & family.expected_agents_any)
    return CaseOutcome(
        family=family.name,
        context=ctx.name,
        final_status=out.get("final_status", ""),
        selected_agents=agents,
        hallucinated_devices=hallucinated,
        unsupported_capabilities=unsupported,
        served_expected=served,
    )


def run_families(deps: PipelineDeps, *, families=None, contexts=None) -> list[CaseOutcome]:
    families = families or default_families()
    contexts = contexts or default_contexts()
    return [run_case(f, c, deps) for f in families for c in contexts]


def hard_gate_report(outcomes: list[CaseOutcome]) -> dict:
    """Tổng hợp hard acceptance gates (spec §62)."""
    return {
        "cases": len(outcomes),
        "hallucinated_device": sum(o.hallucinated_devices for o in outcomes),
        "unsupported_capability": sum(o.unsupported_capabilities for o in outcomes),
        "task_success_rate": round(sum(o.served_expected for o in outcomes) / max(1, len(outcomes)), 3),
    }
