"""Evaluation harness (spec §59-68) — energy baselines + anti-overfitting families."""

from __future__ import annotations

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.evaluation.energy_baselines import (
    baseline_comfort_only,
    compare_strategies,
    energy_savings_vs,
)
from src.agent.evaluation.families import (
    default_contexts,
    default_families,
    hard_gate_report,
    run_case,
    run_families,
)
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.agent.schemas import CandidateEnergyPlan, PowerMode
from src.core.reasoning import FakeReasoningModel


@pytest.fixture
def deps():
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), model_client=FakeReasoningModel(),
    )


# --- Energy baselines (§68) ---------------------------------------------
def _plans():
    light = CandidateEnergyPlan(plan_id="light", actions=[], source_agents=["lighting"], comfort=0.9, preference_match=0.9, estimated_power_w=12)
    curtain = CandidateEnergyPlan(plan_id="curtain", actions=[], source_agents=["shutter"], comfort=0.9, preference_match=0.9, estimated_power_w=0)
    return [light, curtain]


def test_optimizer_saves_energy_vs_comfort_only():
    plans = _plans()
    comparison = compare_strategies(plans, power_mode=PowerMode.NORMAL)
    # Optimizer chọn rèm (0 W); comfort-only có thể chọn đèn (12 W, comfort ngang) → optimizer ≤.
    assert comparison["optimizer"].energy_w <= comparison["comfort_only"].energy_w
    assert comparison["optimizer"].task_success


def test_energy_savings_positive_vs_fixed_scene():
    heavy = CandidateEnergyPlan(plan_id="heavy", source_agents=["lighting", "ac"], comfort=0.9, preference_match=0.8, estimated_power_w=900, actions=[])
    cheap = CandidateEnergyPlan(plan_id="cheap", source_agents=["shutter"], comfort=0.8, preference_match=0.8, estimated_power_w=0, actions=[])
    comparison = compare_strategies([cheap, heavy], power_mode=PowerMode.CRITICAL)
    assert energy_savings_vs(comparison, "fixed_scene") >= 0.0


def test_baseline_comfort_only_ignores_energy():
    plans = _plans()
    # Cả hai comfort 0.9 → comfort_only lấy 1 trong 2; nếu đổi đèn comfort cao hơn → lấy đèn.
    plans[0].comfort = 0.95  # đèn comfort cao hơn
    assert baseline_comfort_only(plans).plan_id == "light"


# --- Anti-overfitting families (§59-63) ---------------------------------
def test_families_pass_hard_gates(deps):
    """Spec §62: hallucinated_device=0, unsupported_capability=0 trên mọi family/context."""
    outcomes = run_families(deps)
    report = hard_gate_report(outcomes)
    assert report["hallucinated_device"] == 0
    assert report["unsupported_capability"] == 0
    assert report["task_success_rate"] >= 0.8


def test_context_perturbation_illumination(deps):
    """Spec §60: illumination-increase → rèm ban ngày, đèn ban đêm (plan khác nhau)."""
    fam = next(f for f in default_families() if f.name == "illumination_increase")
    contexts = {c.name: c for c in default_contexts()}
    day = run_case(fam, contexts["daylight"], deps)
    night = run_case(fam, contexts["night"], deps)
    assert "shutter" in day.selected_agents
    assert "lighting" in night.selected_agents


def test_every_family_served_by_expected_agent(deps):
    outcomes = run_families(deps)
    # Mỗi family phải được phục vụ bởi agent kỳ vọng ở ít nhất một context.
    by_family: dict[str, bool] = {}
    for o in outcomes:
        by_family[o.family] = by_family.get(o.family, False) or o.served_expected
    assert all(by_family.values()), by_family
