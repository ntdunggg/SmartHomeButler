"""Pydantic schema cho request/response của API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, PlainSerializer, model_validator


def _serialize_as_utc(dt: datetime) -> str:
    """Xuất mốc thời gian ở ISO-8601 luôn kèm hậu tố UTC (+00:00).

    Dữ liệu được lưu theo UTC, nhưng SQLite trả về datetime không mang timezone.
    Nếu để nguyên, client tưởng đó là giờ địa phương và hiển thị lệch đúng bằng
    độ lệch múi giờ (VN nhanh hơn UTC 7 tiếng). Gắn UTC vào trước khi trả để chuỗi
    JSON tự mô tả rõ múi giờ, client chỉ việc đổi sang giờ máy người xem.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


# Dùng cho mọi trường thời gian trả ra API, thay cho ``datetime`` trần
UtcDateTime = Annotated[datetime, PlainSerializer(_serialize_as_utc, return_type=str)]


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    account_id: str | None = None
    full_name: str
    role: str
    household_id: int
    household_name: str
    home_room_id: int | None = None
    home_room_name: str = ""
    private_room_id: int | None = None
    private_room_name: str = ""


class LoginResponse(BaseModel):
    """Kết quả đăng nhập: access token để gọi API và refresh token để gia hạn phiên.

    Trả cả hai ngay lúc login để client không phải đăng nhập lại khi access token
    (sống ngắn) hết hạn — chỉ cần gọi ``/auth/refresh`` với refresh token.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshRequest(BaseModel):
    """Thân request cho ``/auth/refresh`` — chỉ cần refresh token đang giữ."""

    refresh_token: str = Field(..., min_length=1)


class RefreshResponse(BaseModel):
    """Cặp token mới sau khi gia hạn.

    Refresh token cũng được cấp lại (xoay vòng) để giảm rủi ro nếu token bị lộ.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# --------------------------------------------------------------------------
# Thiết bị
# --------------------------------------------------------------------------
class DeviceOut(BaseModel):
    id: int
    slug: str
    name: str
    room: str
    room_id: int | None = None
    device_type: str
    risk_level: str
    capabilities: list[str]
    state: dict[str, Any]
    online: bool
    # Quyền của người đang đăng nhập với chính thiết bị này
    can_control: bool
    requires_approval: bool
    child_locked: bool = False
    permission_note_vi: str


class SensorOut(BaseModel):
    slug: str
    name: str
    sensor_type: str
    value: float
    unit: str
    room: str = ""
    warning: bool | None = None
    mode: str | None = None
    estimated: bool = False
    unknown: bool = False


class DashboardOut(BaseModel):
    devices: list[DeviceOut]
    sensors: list[SensorOut]


class DemoSensorsUpdate(BaseModel):
    pm25: float | None = Field(default=None, ge=0, le=1000)
    temperature: float | None = Field(default=None, ge=-20, le=60)
    humidity: float | None = Field(default=None, ge=0, le=100)
    sunlight: float | None = Field(default=None, ge=0, le=100)
    rain: bool | None = None
    presence: bool | None = None
    presence_room: str | None = Field(default=None, max_length=64)


class DemoSensorsOut(BaseModel):
    sensors: list[SensorOut]


class EnergyUsageOut(BaseModel):
    """Số điện đã dùng (kWh) ước tính cho toàn nhà — hôm nay và tháng này."""

    today_kwh: float
    month_kwh: float
    estimated: bool = True


class EnergyBucketOut(BaseModel):
    """Một điểm trên đồ thị số điện: mốc bắt đầu, nhãn hiển thị, kWh của bucket."""

    start: str  # ISO date (YYYY-MM-DD) — đầu bucket theo giờ VN
    label: str  # nhãn ngắn để vẽ trục ("25/08" hoặc "08/2026")
    kwh: float


class EnergySeriesOut(BaseModel):
    """Chuỗi số điện toàn nhà theo ngày hoặc tháng — dữ liệu vẽ đồ thị đường."""

    granularity: str  # "day" | "month"
    buckets: list[EnergyBucketOut]
    total_kwh: float
    estimated: bool = True


class ControlRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)


class ControlResponse(BaseModel):
    ok: bool
    detail_vi: str
    state: dict[str, Any]
    latency_ms: int
    requires_approval: bool = False
    approval_id: int | None = None
    conflicts: list[ConflictOut] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------
class CommandRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="Câu lệnh tiếng Việt")
    conversation_id: str = Field(default="", max_length=64)
    speaker_location: str | None = Field(
        default=None,
        max_length=64,
        description="Phòng người nói đang đứng (client gửi) để agent hiểu theo ngữ cảnh vị trí",
    )


class PlanStepOut(BaseModel):
    device_slug: str = ""
    device_name: str = ""
    room: str = ""
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    reason_vi: str = ""
    risk_level: str = ""
    allowed: bool = True
    requires_approval: bool = False
    deny_reason_vi: str = ""
    status: str = ""
    detail_vi: str = ""
    reason_code: str = ""
    skipped: bool = False
    state_before: dict[str, Any] = Field(default_factory=dict)
    state_after: dict[str, Any] = Field(default_factory=dict)


class ConflictOut(BaseModel):
    type: str = ""
    severity: str = ""
    message_vi: str = ""
    device_slug: str = ""
    suggestion_vi: str = ""
    blocking: bool = False


class ApprovalOut(BaseModel):
    id: int
    conversation_id: str
    command_text: str
    reason_vi: str
    steps: list[dict[str, Any]]
    required_role: str
    status: str
    source: str = "chat"
    created_at: UtcDateTime
    expires_at: UtcDateTime | None = None


class CommandResponse(BaseModel):
    conversation_id: str
    response_vi: str
    intent: str
    nlu_source: str = Field(description="'rules' hoặc 'llm' — cho thấy khi nào bỏ qua được LLM")
    scene_name_vi: str = ""
    plan: list[PlanStepOut] = Field(default_factory=list)
    conflicts: list[ConflictOut] = Field(default_factory=list)
    latency_ms: int
    # Khi cần Human-in-the-loop, hai trường này được điền
    pending_approval: ApprovalOut | None = None
    execution_id: str | None = Field(
        default=None, description="ID của lần thực thi phục vụ feedback & RL credit assignment"
    )
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class CommandJobAccepted(BaseModel):
    job_id: str
    status: str = "queued"
    message_vi: str = "Đã nhận yêu cầu."


class CommandJobStatus(BaseModel):
    job_id: str
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    result: CommandResponse | None = None
    error_vi: str = ""


class ApprovalDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=255)


# --------------------------------------------------------------------------
# Đề xuất chủ động
# --------------------------------------------------------------------------
class SuggestionOut(BaseModel):
    id: str
    kind: str
    title_vi: str
    message_vi: str
    steps: list[dict[str, Any]]
    # Đề xuất nhắm tới riêng ai (None = chung cả hộ)
    user_id: int | None = None
    created_at: str


class SuggestionDecision(BaseModel):
    accepted: bool
    # Hoãn lại bao nhiêu phút (chỉ áp dụng cho đề xuất từ thói quen)
    snooze_minutes: int = Field(default=0, ge=0, le=720)


class MemoryFeedbackIn(BaseModel):
    """Phản hồi của người dùng cho một lượt đã được đề xuất (đóng vòng học).

    `episode_id` lấy từ `NluResponse.episode_id` của chính lượt đó."""

    episode_id: int
    # accepted | rejected | corrected | executed — validate ở service để danh sách hợp lệ
    # chỉ nằm một chỗ (src/services/memory_service.FEEDBACK_OUTCOMES).
    outcome: str
    note: str = ""


class FeedbackIn(BaseModel):
    """Payload cho endpoint POST /agent/feedback (FR-15)."""

    execution_id: str = Field(..., min_length=1, max_length=64)
    outcome: Literal["accepted", "helpful", "rejected", "unhelpful", "corrected", "no_correction"] = Field(
        ..., description="accepted | helpful | rejected | unhelpful | corrected | no_correction"
    )
    dimension: Literal["temperature", "brightness"] | None = Field(default=None, description="temperature | brightness")
    corrected_value: int | None = Field(default=None, description="Giá trị người dùng đã sửa đổi")

    @model_validator(mode="after")
    def validate_corrected(self) -> Self:
        if self.outcome == "corrected":
            if self.corrected_value is None:
                raise ValueError("Trường 'corrected_value' là bắt buộc khi outcome là 'corrected'.")
            if self.dimension == "temperature" and self.corrected_value not in range(20, 27):
                raise ValueError("Nhiệt độ hiệu chỉnh phải nằm trong khoảng 20-26°C.")
            if self.dimension == "brightness" and (
                self.corrected_value < 0 or self.corrected_value > 100 or self.corrected_value % 10 != 0
            ):
                raise ValueError("Độ sáng hiệu chỉnh phải từ 0-100% với bước nhảy 10%.")
        return self


class FeedbackOut(BaseModel):
    """Response cho endpoint POST /agent/feedback (FR-15)."""

    feedback_id: str
    duplicate: bool = False
    memory_status: str  # pending | processing | succeeded | failed | skipped
    rl_status: str  # pending | processing | succeeded | failed | skipped
    memory_attempt_count: int = 0
    rl_attempt_count: int = 0
    last_error: str | None = None


# --------------------------------------------------------------------------
# Lịch sử
# --------------------------------------------------------------------------
class ActionLogOut(BaseModel):
    id: int
    approval_id: int | None = None
    device_slug: str = ""
    device_name: str = ""
    username: str = ""
    command_text: str
    action: str
    params: dict[str, Any]
    status: str
    detail: str
    source: str
    latency_ms: int
    created_at: UtcDateTime


# --------------------------------------------------------------------------
# Quản lý thành viên (chỉ chủ hộ) + quyền truy cập theo thiết bị
# --------------------------------------------------------------------------
class MemberCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    full_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=6, max_length=128)
    # "owner" hoặc "member"; validate lại ở route để trả lỗi tiếng Việt rõ ràng
    role: str = Field(default="member")
    # Phòng mặc định dùng cho ngữ cảnh cá nhân.
    home_room_id: int | None = None
    # Phòng riêng (id phòng). Với thành viên nên có; chủ hộ thì không bắt buộc.
    private_room_id: int | None = None


class MemberUpdate(BaseModel):
    """Mọi trường tuỳ chọn — chỉ cập nhật những gì được gửi lên."""

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: str | None = None
    # Dùng sentinel để phân biệt "không gửi" với "gán về không có phòng riêng".
    home_room_id: int | None = Field(default=-1)
    private_room_id: int | None = Field(default=-1)


class MemberOut(BaseModel):
    id: int
    username: str
    account_id: str | None = None
    full_name: str
    role: str
    home_room_id: int | None = None
    home_room_name: str = ""
    private_room_id: int | None = None
    private_room_name: str = ""


class AccessRuleIn(BaseModel):
    device_slug: str = Field(..., min_length=1)
    # "accepted" | "alert" | "request" — validate ở route
    effect: str


class AccessRuleOut(BaseModel):
    device_slug: str
    device_name: str
    effect: str


class AccessRulesUpdate(BaseModel):
    """Thay toàn bộ danh sách quyền riêng của một thành viên."""

    rules: list[AccessRuleIn] = Field(default_factory=list)
    # Xác nhận cấp quyền "accepted"/"alert" cho thiết bị AN NINH (không cần duyệt mỗi
    # lần dùng). Ma sát chống bấm nhầm — chủ hộ vẫn toàn quyền, chỉ phải xác nhận.
    confirm_security: bool = False


class RoomAccessUpdate(BaseModel):
    """Cấp một trạng thái cho MỌI thiết bị trong một phòng (thao tác hàng loạt).

    Upsert: chỉ đụng thiết bị của phòng này, giữ nguyên quyền ở phòng khác.
    """

    # "accepted" | "alert" | "request" — validate ở route
    effect: str
    confirm_security: bool = False


class AccessDeviceOut(BaseModel):
    """Quyền đã giải quyết của một thành viên với một thiết bị (cho popup chỉnh quyền)."""

    device_slug: str
    device_name: str
    room: str = ""
    room_id: int | None = None
    risk_level: str = "normal"
    # Trạng thái chủ hộ đã cấp: "accepted" | "alert" | "request" | "none" (chưa cấp)
    effect: str
    allowed: bool
    requires_approval: bool
    # Thiết bị nằm trong phòng riêng của thành viên — chỉ là NGỮ CẢNH, không cấp quyền
    in_private_room: bool = False
    note_vi: str = ""


class MemberAccessOut(BaseModel):
    user_id: int
    private_room_id: int | None = None
    private_room_name: str = ""
    # Danh sách quyền đã giải quyết cho MỌI thiết bị trong hộ
    devices: list[AccessDeviceOut] = Field(default_factory=list)
    # Giữ lại danh sách luật riêng thô để tương thích phần cũ
    rules: list[AccessRuleOut] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Quản lý thói quen đã học
# --------------------------------------------------------------------------
class HabitOut(BaseModel):
    id: int
    user_id: int | None
    device_slug: str
    device_name: str = ""
    action: str
    hour: int
    minute: int = 0
    params: dict[str, Any]
    confidence: float
    occurrences: int
    description_vi: str
    enabled: bool
    source: str = "learned"


class HabitUpdate(BaseModel):
    """Bật/tắt và/hoặc chỉnh lịch một thói quen. Xoá dùng DELETE riêng.

    Gửi kèm ``device_slug``+``action``+``hour`` để đổi lịch (điều chỉnh thói quen); chỉ
    gửi ``enabled`` để bật/tắt."""

    enabled: bool | None = None
    device_slug: str | None = None
    action: str | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    params: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# Tự thêm thói quen (giao diện kiểu báo thức) + nhập hàng loạt từ JSON
# --------------------------------------------------------------------------
class HabitParamOption(BaseModel):
    """Mô tả ô nhập "mức độ" đi kèm một hành động, để frontend dựng widget tương ứng."""

    key: str
    kind: Literal["range", "choice", "bool", "color", "text"]
    label_vi: str = ""
    unit: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    default: Any = None
    choices: list[dict[str, str]] = Field(default_factory=list)  # [{value,label}]


class HabitActionOption(BaseModel):
    action: str
    label_vi: str
    capability: str
    param: HabitParamOption | None = None


class HabitDeviceOption(BaseModel):
    slug: str
    name: str
    room: str = ""
    device_type: str
    capabilities: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    actions: list[str] = Field(default_factory=list)  # tên các hành động hợp lệ cho thiết bị này


class HabitOptionsOut(BaseModel):
    """Nguồn dữ liệu cho modal thêm thói quen: thiết bị user điều khiển được + bảng hành động."""

    devices: list[HabitDeviceOption]
    actions: list[HabitActionOption]


class HabitDraftIn(BaseModel):
    device_slug: str
    action: str
    hour: int
    minute: int = 0
    params: dict[str, Any] = Field(default_factory=dict)


class HabitBatchIn(BaseModel):
    """Một (form tay) hoặc nhiều (nhập JSON) thói quen muốn thêm."""

    habits: list[HabitDraftIn]


class HabitCandidateOut(BaseModel):
    """Một ứng viên cho một khung (thiết bị + giờ) — có thể là thói quen đang có hoặc mới."""

    kind: Literal["existing", "incoming"]
    index: int | None = None  # vị trí trong danh sách gửi lên (chỉ 'incoming')
    habit_id: int | None = None  # id thói quen đang có (chỉ 'existing')
    source: str = "manual"
    action: str
    action_label_vi: str = ""
    hour: int
    minute: int = 0
    params: dict[str, Any] = Field(default_factory=dict)
    description_vi: str = ""
    confidence: float = 1.0
    enabled: bool = True
    owner_name: str = ""  # tên người sở hữu (dùng cho cross-user conflict)
    owner_role: str = ""  # role của người sở hữu


class HabitCrossUserWarning(BaseModel):
    """Cảnh báo xung đột thói quen với thành viên khác trong cùng hộ."""

    device_slug: str
    device_name: str = ""
    hour: int
    other_user_name: str
    other_user_role: str
    other_action: str
    other_action_label_vi: str = ""
    other_params: dict[str, Any] = Field(default_factory=dict)
    other_description_vi: str = ""
    message_vi: str


class HabitSlotOut(BaseModel):
    device_slug: str
    device_name: str = ""
    hour: int
    conflict: bool
    candidates: list[HabitCandidateOut]


class HabitInvalidOut(BaseModel):
    index: int
    device_slug: str = ""
    action: str = ""
    hour: int | None = None
    minute: int = 0
    reason_vi: str


class HabitPreviewOut(BaseModel):
    """Kết quả kiểm tra trước khi lưu: xung đột kiểu git + các mục không hợp lệ."""

    slots: list[HabitSlotOut]
    invalid: list[HabitInvalidOut]
    cross_user_warnings: list[HabitCrossUserWarning] = Field(default_factory=list)
    has_conflicts: bool
    has_invalid: bool
    total: int  # số thói quen nhận vào
    ready: int  # số khung sẽ được lưu nếu commit ngay (không còn xung đột)


class HabitCommitOut(BaseModel):
    saved: list[HabitOut]
    disabled: int = 0  # số thói quen cũ bị tắt vì bị thay thế


# --------------------------------------------------------------------------
# Đồng hồ mô phỏng (mirror đồng hồ nội bộ của frontend)
# --------------------------------------------------------------------------
class ClockState(BaseModel):
    """Đúng 3 trường FE lưu ở localStorage 'demoClock.v1'."""

    offset_ms: int = 0
    mode: Literal["live", "frozen"] = "live"
    frozen_ms: int = 0


class ClockOut(BaseModel):
    offset_ms: int
    mode: str
    frozen_ms: int
    now: datetime  # giờ mô phỏng hiện tại
    real_now: datetime  # giờ thực, để đối chiếu


class ScheduleSlotOut(BaseModel):
    """Một khung buổi trong lịch trình đã học của thành viên."""

    part: str  # morning | afternoon | evening | night
    hours: list[int] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)


class MemberProfileOut(BaseModel):
    """Hồ sơ hành vi + lịch trình theo buổi của một thành viên (chỉ đọc)."""

    user_id: int
    preferences: dict[str, float] = Field(default_factory=dict)
    routines: list[dict] = Field(default_factory=list)
    habits: list[dict] = Field(default_factory=list)
    schedule: list[ScheduleSlotOut] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Quản lý phòng & thiết bị (chỉ chủ hộ)
# --------------------------------------------------------------------------
class RoomCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    sort_order: int = Field(default=0, ge=0)


class RoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = Field(default=None, ge=0)


class RoomOut(BaseModel):
    id: int
    name: str
    sort_order: int
    device_count: int


class DeviceCreate(BaseModel):
    # Loại thiết bị chọn từ catalog (danh sách loại không đổi)
    device_type: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=120)
    room_id: int | None = None


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    room_id: int | None = None


class DeviceTypeOut(BaseModel):
    """Một loại thiết bị có thể thêm — dùng cho form 'thêm thiết bị' bên FE."""

    device_type: str
    default_name: str
    risk_level: str
    capabilities: list[str]


class NotificationOut(BaseModel):
    """Thông báo cho người dùng. ``notification_type``: sensitive_device_used,
    approval_requested/approved/rejected/expired, conflict_warning, child_lock_changed."""

    id: int
    notification_type: str = "sensitive_device_used"
    message_vi: str
    approval_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    read: bool
    created_at: str | None = None


class ChildLockOut(BaseModel):
    """Trạng thái khoá trẻ em của hộ."""

    enabled: bool
    enabled_at: str | None = None
    enabled_by_id: int | None = None


class ChildLockUpdate(BaseModel):
    enabled: bool
