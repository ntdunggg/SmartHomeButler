"""Thư viện prompt, tách theo TRÁCH NHIỆM và ghép lại khi dùng.

Ba tầng, mỗi tầng một lý do để thay đổi:

1. `principles` — hiến pháp: ranh giới thẩm quyền, trung thực dữ liệu, fail-closed.
   Sửa ở đây là đổi bất biến an toàn của cả hệ thống.
2. `persona`     — cách trợ lý nghĩ và nói. Chỉnh theo gu sản phẩm, không đụng an toàn.
3. vai trò       — nhiệm vụ kỹ thuật của từng node (`understanding`, `semantic`, `planner`…).

System prompt thật của một node = principles + persona + vai trò, ghép bởi `compose()`.
Trước đây bảy prompt phẳng mỗi cái tự lặp lại phần nguyên tắc, nên chúng trôi lệch nhau —
ví dụ prompt phê duyệt tự cho mình quyền quyết định trong khi prompt khác nói ngược lại.

Các hằng số `*_SYSTEM_PROMPT` xuất ra ở đây đã ghép sẵn, dùng trực tiếp được ở graph node.
"""

from __future__ import annotations

from src.nlu.prompting.persona import ASSISTANT_PERSONA
from src.nlu.prompting.planner import (
    APPROVAL_ADVISORY_ROLE,
    CANDIDATE_PLAN_ROLE,
    CLARIFICATION_ROLE,
    NEXT_ACTION_DECISION_ROLE,
    PLAN_CRITIQUE_ROLE,
    PLAN_REPAIR_ROLE,
)
from src.nlu.prompting.principles import AGENT_PRINCIPLES
from src.nlu.prompting.semantic import SEMANTIC_GOAL_ROLE
from src.nlu.prompting.understanding import UNDERSTANDING_ROLE


def compose(role_prompt: str, *, persona: bool = True) -> str:
    """Ghép system prompt cho một node: nguyên tắc chung → persona → nhiệm vụ vai trò.

    `persona=False` cho các node thuần kỹ thuật (sửa kế hoạch, cố vấn rủi ro) — giọng điệu
    không liên quan tới việc chúng làm, và mỗi dòng thừa là token trả tiền ở mọi lượt gọi."""
    parts = [AGENT_PRINCIPLES] + ([ASSISTANT_PERSONA] if persona else []) + [role_prompt]
    return "\n\n".join(parts)


# --- System prompt đã ghép, dùng trực tiếp ở graph node --------------------------------
REQUEST_UNDERSTANDING_SYSTEM_PROMPT = compose(UNDERSTANDING_ROLE)
SEMANTIC_GOAL_SYSTEM_PROMPT = compose(SEMANTIC_GOAL_ROLE)
CANDIDATE_PLAN_SYSTEM_PROMPT = compose(CANDIDATE_PLAN_ROLE)
CLARIFICATION_SYSTEM_PROMPT = compose(CLARIFICATION_ROLE)
NEXT_ACTION_DECISION_SYSTEM_PROMPT = compose(NEXT_ACTION_DECISION_ROLE)

# Node kỹ thuật: bỏ persona (không sinh chữ cho người đọc).
PLAN_CRITIQUE_SYSTEM_PROMPT = compose(PLAN_CRITIQUE_ROLE, persona=False)
PLAN_REPAIR_SYSTEM_PROMPT = compose(PLAN_REPAIR_ROLE, persona=False)
APPROVAL_RECOMMENDATION_SYSTEM_PROMPT = compose(APPROVAL_ADVISORY_ROLE, persona=False)

__all__ = [
    "AGENT_PRINCIPLES",
    "APPROVAL_RECOMMENDATION_SYSTEM_PROMPT",
    "ASSISTANT_PERSONA",
    "CANDIDATE_PLAN_SYSTEM_PROMPT",
    "CLARIFICATION_SYSTEM_PROMPT",
    "NEXT_ACTION_DECISION_SYSTEM_PROMPT",
    "PLAN_CRITIQUE_SYSTEM_PROMPT",
    "PLAN_REPAIR_SYSTEM_PROMPT",
    "REQUEST_UNDERSTANDING_SYSTEM_PROMPT",
    "SEMANTIC_GOAL_SYSTEM_PROMPT",
    "compose",
]
