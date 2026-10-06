"""Ontology tối giản cho tầng NLU của AI Engineer.

Kiến trúc **open-ended**: KHÔNG còn tập đóng `Intent` nào nữa. Mục tiêu người dùng
được LLM diễn đạt tự do (`SemanticGoal.goal_description` + `desired_outcomes` ở mức
capability), rồi planner tổng hợp kế hoạch trực tiếp từ catalog + trạng thái thật —
không ánh xạ intent→plan, không routine template.

File này chỉ còn giữ `UtteranceType`: một nhãn phân loại **thô** phục vụ *định tuyến
hội thoại* (đây là lệnh, câu hỏi, huỷ, hay sửa lời?). Nó KHÔNG bao giờ ánh xạ sang
một kế hoạch — chỉ quyết định nhánh xử lý trong graph.
"""

from __future__ import annotations

from enum import StrEnum


class UtteranceType(StrEnum):
    """Phân loại thô câu nói — chỉ dùng để định tuyến hội thoại, không ánh xạ plan."""

    DEVICE_COMMAND = "DEVICE_COMMAND"
    ROUTINE_INTENT = "ROUTINE_INTENT"
    ENVIRONMENT_REQUEST = "ENVIRONMENT_REQUEST"
    INFORMATION_QUESTION = "INFORMATION_QUESTION"
    PREFERENCE_STATEMENT = "PREFERENCE_STATEMENT"
    CONFIRMATION = "CONFIRMATION"
    REJECTION = "REJECTION"
    CORRECTION = "CORRECTION"
    CANCELLATION = "CANCELLATION"
    SOCIAL_UTTERANCE = "SOCIAL_UTTERANCE"
    UNKNOWN = "UNKNOWN"
