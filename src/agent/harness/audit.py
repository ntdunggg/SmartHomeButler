"""Audit Logger (spec §14, §53) — ghi lại mọi hành động vật lý.

Bản ghi đầy đủ dấu vết quyết định: input → semantic_goal → ledger → memory → preference
→ candidate_plans → selected_plan → policy_decision → execution_result. Đủ để tái dựng
"vì sao agent làm vậy" khi review/eval (spec §58).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agent.schemas import (
    AuditRecord,
    CandidateEnergyPlan,
    ExecutionResult,
    RequirementLedger,
    SemanticGoal,
)


def build_audit(
    *,
    conversation_id: str,
    user: str,
    user_input: str,
    semantic_goal: SemanticGoal | None,
    ledger: RequirementLedger | None,
    evidence_trace: list[dict[str, Any]] | None,
    memory_used: list[str] | None,
    profile_used: list[dict[str, Any]] | None = None,
    preference_used: dict[str, Any] | None,
    candidate_plans: list[CandidateEnergyPlan] | None,
    selected_plan: CandidateEnergyPlan | None,
    policy_decision: dict[str, Any] | None,
    execution_result: ExecutionResult | None,
    now: datetime | None = None,
) -> AuditRecord:
    """Dựng AuditRecord từ state cuối lượt (spec §53)."""
    return AuditRecord(
        timestamp=now or datetime.now(UTC),
        conversation_id=conversation_id,
        user=user,
        input=user_input,
        semantic_goal=semantic_goal.model_dump(mode="json") if semantic_goal else {},
        ledger_snapshot=ledger.model_dump(mode="json") if ledger else {},
        evidence_trace=list(evidence_trace or []),
        memory_used=list(memory_used or []),
        profile_used=list(profile_used or []),
        preference_used=dict(preference_used or {}),
        candidate_plans=[p.model_dump(mode="json") for p in (candidate_plans or [])],
        selected_plan=selected_plan.model_dump(mode="json") if selected_plan else {},
        policy_decision=dict(policy_decision or {}),
        execution_result=execution_result.model_dump(mode="json") if execution_result else {},
    )
