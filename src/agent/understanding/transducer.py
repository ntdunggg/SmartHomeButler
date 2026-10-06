"""Context Transducer (spec §10-11) — phân tích trạng thái THÔNG TIN của request.

Transducer KHÔNG chọn plan cuối (spec §11). Nó phân tích các dạng mơ hồ/thiếu thông tin:

    semantic ambiguity · underspecification · reference ambiguity · goal ambiguity ·
    constraint ambiguity · context dependence

và trả một `semantic_analysis` (spec §10): `ambiguity_status` + `sufficiency_status` +
`missing_information`. `normal/incomplete/ambiguous` chỉ mô tả TRẠNG THÁI THÔNG TIN,
KHÔNG phải domain intent (spec §10) — một request có thể đồng thời ambiguous +
underspecified + context_resolvable.

Phân tích dựa trên tín hiệu TẤT ĐỊNH từ normalizer (spec §8: không map phrase→intent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent.schemas import SemanticGoal
from src.nlu.normalizer import Ambiguity, NormalizedUtterance


@dataclass(slots=True)
class SemanticAnalysis:
    """Trạng thái thông tin của request (spec §10-11)."""

    ambiguity_status: str = "resolved"  # resolved | ambiguous
    sufficiency_status: str = "sufficient"  # sufficient | insufficient
    ambiguity_sources: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    context_dependent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ambiguity_status": self.ambiguity_status,
            "sufficiency_status": self.sufficiency_status,
            "ambiguity_sources": list(self.ambiguity_sources),
            "missing_information": list(self.missing_information),
            "context_dependent": self.context_dependent,
        }


def _goal_needs_room(goal: SemanticGoal | None) -> bool:
    """Mục tiêu điều chỉnh theo cảm nhận (relative_change) chỉ có nghĩa với một phòng cụ thể.

    Đã đủ scope nếu MỌI outcome relative đã neo area/labels (selector) — khi đó không
    thiếu phòng (khớp bất biến over-act cũ: chỉ ENVIRONMENT_REQUEST trần mới hỏi phòng)."""
    if goal is None or goal.action_hint is not None:
        return False
    # Activity-to-home-room grounding is deterministic provenance written after
    # model authoring. It is already a resolved execution scope, unlike a bare
    # selector.area volunteered by the model.
    if goal.target_area and goal.target_area_source == "activity_home_room":
        return False
    rel_outcomes = [o for o in (goal.desired_outcomes or []) if getattr(o, "relative_change", None)]
    if not rel_outcomes:
        return False
    if all(o.selector.area or o.selector.labels for o in rel_outcomes):
        return False
    return True


def transduce(nu: NormalizedUtterance, goal: SemanticGoal | None) -> SemanticAnalysis:
    """Phân tích ambiguity/sufficiency của một request (spec §11)."""
    analysis = SemanticAnalysis()

    # Reference ambiguity: câu có tham chiếu ngầm ("cái đó", "phòng này") chưa giải.
    if nu.has_reference and not (goal and goal.references_resolved):
        analysis.ambiguity_sources.append("reference")
        analysis.context_dependent = True

    # Semantic ambiguity: CHỈ khi normalizer thấy nhiều cách hiểu CHƯA giải được
    # (Ambiguity.UNRESOLVED) VÀ goal chưa neo được thiết bị cụ thể. CLEAR/RESOLVABLE (đã
    # có phòng/ngữ cảnh phân giải) không tính — nếu không lệnh tường minh đã resolve vẫn
    # bị coi là mơ hồ (bug truthiness: mọi Ambiguity đều truthy).
    if nu.ambiguity == Ambiguity.UNRESOLVED and not (goal and goal.target_device_ids):
        analysis.ambiguity_sources.append("semantic")

    # Underspecification: mục tiêu cảm nhận nhưng thiếu phòng → thiếu thông tin thực thi.
    if _goal_needs_room(goal) and not nu.matched_rooms:
        analysis.missing_information.append("room")

    # Goal ambiguity: không có mục tiêu nào diễn giải được (goal None, không phải huỷ).
    if goal is None and not nu.has_cancellation:
        analysis.ambiguity_sources.append("goal")
        analysis.sufficiency_status = "insufficient"

    # Constraint ambiguity: có phủ định nhưng không rõ ràng buộc (hiếm; đánh dấu để theo dõi).
    if nu.has_negation and goal is not None and not goal.excluded_device_ids and not goal.negated:
        analysis.ambiguity_sources.append("constraint")

    if analysis.missing_information:
        analysis.sufficiency_status = "insufficient"
    if analysis.ambiguity_sources:
        analysis.ambiguity_status = "ambiguous"

    return analysis
