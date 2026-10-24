"""Schema domain cho tầng suy luận / lập kế hoạch của agent (Milestone 1).

Các schema ở đây là **proposal**, không phải command. Chúng mô tả agent *đề xuất*
làm gì; quyền quyết định thực thi thuộc về Safety Validator (tầng deterministic).
Không schema nào ở đây tự đi xuống thiết bị — bất biến S1.

Chưa gắn với node LangGraph nào: đây chỉ là định nghĩa dữ liệu, có kiểm chứng bằng
Pydantic ở biên giới giữa các tầng.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from src.domain.enums import (
    ActionType,
    Capability,
    ConfidenceStatus,
    RiskLevel,
    ValidationStatus,
)


class DeviceAction(BaseModel):
    """Một hành động đề xuất lên đúng một thiết bị.

    Ràng buộc bắt buộc: mỗi hành động phải chỉ đích danh ``device_id`` và
    ``capability`` — không có hành động "chung chung" hay fuzzy-match ở tầng này.
    ID thiết bị đến từ Registry, không phải chuỗi tự do (GOALS §12 R1).
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1, description="Slug/ID thiết bị lấy từ Registry")
    capability: Capability = Field(..., description="Khả năng thiết bị mà hành động này tác động")
    action: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.NORMAL
    reason_vi: str = ""

    @model_validator(mode="after")
    def _set_needs_params(self) -> DeviceAction:
        # "set" mà không có tham số thì vô nghĩa (đặt cái gì?).
        if self.action == ActionType.SET and not self.params:
            raise ValueError("Hành động 'set' phải kèm params (ví dụ nhiệt độ, độ sáng)")
        return self


class SemanticGoal(BaseModel):
    """Mục tiêu ngữ nghĩa rút ra từ câu nói người dùng — tầng suy luận đề xuất.

    Mô tả *người dùng muốn gì* (bật đèn, "hơi nóng" → làm mát) chứ chưa phải chuỗi
    lệnh cụ thể. ``confidence_status`` được suy ra từ ``confidence`` nếu không truyền.
    """

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(..., min_length=1, description="Loại ý định, ví dụ device.control")
    raw_utterance: str = Field(..., min_length=1, description="Câu gốc người dùng nói")
    confidence: float = Field(..., ge=0.0, le=1.0)
    target_area: str | None = None
    target_device_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Phủ định xử lý sớm ở L1 là nguồn lỗi lớn nhất.
    polarity: str = Field(default="affirmative", pattern="^(affirmative|negative)$")

    # ignore vì mypy chưa hiểu computed_field bọc property (false positive đã biết):
    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence_status(self) -> ConfidenceStatus:
        """Luôn suy ra từ điểm số, không cho tầng đề xuất tự khai.

        Trạng thái tin cậy là cổng định tuyến (đi thẳng / hỏi lại / bỏ). Nếu để nó
        là input tự do, tầng suy luận có thể khai CONFIDENT với score 0.2 và ép đi
        thẳng dưới ngưỡng — đúng thứ "không đoán gần đúng" cần tránh."""
        return ConfidenceStatus.from_score(self.confidence)


class ExecutionPlan(BaseModel):
    """Chuỗi hành động có thứ tự để đạt một mục tiêu.

    Là proposal cho tới khi Safety Validator đổi ``validation_status`` sang
    VALIDATED. ``requires_confirmation`` bật khi plan chạm nhiều thiết bị hoặc có
    hành động rủi ro (Human-in-the-loop, GOALS §4).
    """

    model_config = ConfigDict(extra="forbid")

    goal_intent: str = Field(..., min_length=1)
    actions: list[DeviceAction] = Field(..., min_length=1)
    validation_status: ValidationStatus = ValidationStatus.PENDING
    requires_confirmation: bool = False
    rejection_reason_vi: str = ""

    @property
    def is_executable(self) -> bool:
        """Chỉ chạy được khi đã qua validator (S1)."""
        return self.validation_status == ValidationStatus.VALIDATED

    @model_validator(mode="after")
    def _reject_needs_reason(self) -> ExecutionPlan:
        if self.validation_status == ValidationStatus.REJECTED and not self.rejection_reason_vi:
            raise ValueError("Kế hoạch bị từ chối phải kèm lý do (rejection_reason_vi)")
        return self


class ClarificationRequest(BaseModel):
    """Một câu hỏi làm rõ khi mục tiêu mơ hồ — tối đa 1 câu/lượt (GOALS F6)."""

    model_config = ConfigDict(extra="forbid")

    question_vi: str = Field(..., min_length=1, description="Câu hỏi tiếng Việt tự nhiên")
    reason: str = Field(default="", description="Vì sao phải hỏi: thiếu slot, nhiều ứng viên...")
    options: list[str] = Field(default_factory=list)
    related_intent: str = ""
    expected_answer_type: str = Field(
        default="text", pattern="^(text|number|boolean|device_ref)$"
    )
