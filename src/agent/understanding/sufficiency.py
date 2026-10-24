"""Semantic Sufficiency Gate (spec §13) — PROCEED / RESOLVE_CONTEXT / CLARIFY / ABSTAIN.

KHÔNG chỉ dựa vào confidence (spec §13). Xét đồng thời:
    - missing execution-critical information?
    - multiple materially different interpretations?
    - available evidence (context resolver giải được không)?
    - risk level?
    - constraint ambiguity?

Quyết định là ĐẦU VÀO cho routing của pipeline: PROCEED (đủ), RESOLVE_CONTEXT (thiếu
nhưng context giải được — áp resolution rồi đi tiếp), CLARIFY (phải hỏi user), ABSTAIN
(không có gì để diễn giải / rủi ro quá cao mà bằng chứng trống).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.schemas import SufficiencyDecision
from src.agent.understanding.transducer import SemanticAnalysis


@dataclass(slots=True)
class SufficiencyResult:
    decision: SufficiencyDecision
    reason: str = ""
    # Field đã giải được nhờ context (áp vào goal khi RESOLVE_CONTEXT).
    resolvable_fields: tuple[str, ...] = ()


def decide(
    analysis: SemanticAnalysis,
    *,
    has_goal: bool,
    resolved_fields: tuple[str, ...] = (),
    still_missing: tuple[str, ...] = (),
    empty_utterance: bool = False,
    high_risk: bool = False,
) -> SufficiencyResult:
    """Quyết định cổng sufficiency (spec §13)."""
    # ABSTAIN: không có nội dung ngữ nghĩa để diễn giải (câu rỗng/nhiễu).
    if empty_utterance:
        return SufficiencyResult(SufficiencyDecision.ABSTAIN, reason="empty_or_non_semantic_utterance")

    # Không diễn giải được mục tiêu nào → phải hỏi user (không đoán bừa).
    if not has_goal:
        return SufficiencyResult(SufficiencyDecision.CLARIFY, reason="no_interpretable_goal")

    # Còn field thực-thi-quan-trọng chưa giải được từ bất kỳ nguồn nào → CLARIFY.
    if still_missing:
        return SufficiencyResult(
            SufficiencyDecision.CLARIFY,
            reason="missing:" + ",".join(still_missing),
            resolvable_fields=resolved_fields,
        )

    # Thiếu thông tin NHƯNG context resolver đã giải được → áp resolution rồi đi tiếp.
    if resolved_fields:
        return SufficiencyResult(
            SufficiencyDecision.RESOLVE_CONTEXT,
            reason="resolved:" + ",".join(resolved_fields),
            resolvable_fields=resolved_fields,
        )

    # Nhiều cách hiểu vật chất khác nhau mà chưa nguồn nào phân giải + rủi ro cao → CLARIFY.
    material = {"reference", "semantic"} & set(analysis.ambiguity_sources)
    if material and high_risk:
        return SufficiencyResult(SufficiencyDecision.CLARIFY, reason="ambiguous_high_risk")

    return SufficiencyResult(SufficiencyDecision.PROCEED, reason="sufficient")
