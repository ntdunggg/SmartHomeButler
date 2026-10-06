"""SQLAlchemy models.

Mọi bảng chứa dữ liệu người dùng đều có ``household_id`` — đây là ranh giới
phân tách dữ liệu theo hộ mà đề bài yêu cầu. Các trường cá nhân (họ tên, ghi chú
sở thích) được lưu ở dạng đã mã hoá, xem ``src/core/security.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.core import clock
from src.core.security import decrypt_personal, encrypt_personal
from src.domain.enums import (
    AccessEffect,
    ActionStatus,
    Capability,
    DeviceType,
    RiskLevel,
    Role,
)


def _utcnow() -> datetime:
    # Đi qua đồng hồ mô phỏng: khi FE đặt giờ, dấu thời gian log theo giờ đó;
    # khi chưa đặt (offset 0) bằng đúng datetime.now(UTC) như cũ.
    return clock.now()


class Base(DeclarativeBase):
    pass


class Household(Base):
    """Một hộ gia đình — ranh giới phân tách dữ liệu."""

    __tablename__ = "households"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Khoá trẻ em: khi bật, thành viên bị chặn thiết bị công suất lớn/an ninh dù đã được cấp.
    child_lock_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    child_lock_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Chủ hộ đã bật (id, không đặt FK để tránh nhập nhằng join households↔users).
    child_lock_enabled_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    users: Mapped[list[User]] = relationship(back_populates="household", cascade="all, delete-orphan")
    devices: Mapped[list[Device]] = relationship(back_populates="household", cascade="all, delete-orphan")


class User(Base):
    """Thành viên trong hộ. ``full_name`` lưu dạng mã hoá theo khoá của hộ."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    # Định danh công khai do backend sinh (thay cho việc lộ tuổi). Không nhạy cảm.
    account_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Dữ liệu cá nhân — không đọc trực tiếp, dùng property full_name
    full_name_encrypted: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[Role] = mapped_column(String(16), nullable=False)
    # Phòng mặc định/gắn bó của người dùng — dùng làm ngữ cảnh cá nhân khi câu nói
    # không nêu phòng cụ thể. Khác private_room_id (ownership/quyền phòng riêng).
    home_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Phòng riêng của thành viên. Thành viên được quyền với MỌI thiết bị trong phòng
    # riêng của mình; thiết bị phòng chung phải được chủ hộ cấp quyền (bảng access_rules).
    # Nhiều thành viên có thể chung một phòng riêng. Chủ hộ không cần (luôn có toàn quyền).
    private_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    household: Mapped[Household] = relationship(back_populates="users")

    @property
    def full_name(self) -> str:
        """Giải mã họ tên bằng khoá của hộ."""
        return decrypt_personal(self.full_name_encrypted, household_id=self.household_id)

    @full_name.setter
    def full_name(self, value: str) -> None:
        self.full_name_encrypted = encrypt_personal(value, household_id=self.household_id)



class Room(Base):
    """Một phòng trong hộ — quản lý được (thêm/sửa/xoá) lúc chạy.

    Tồn tại độc lập với thiết bị nên có thể tạo phòng rỗng. ``Device.room`` (chuỗi
    tên) vẫn được giữ đồng bộ với phòng để phần đọc theo tên cũ chạy được như trước.
    """

    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("household_id", "name", name="uq_rooms_household_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Device(Base):
    """Thiết bị thông minh. ``state`` là bản chụp trạng thái mới nhất từ IoT bus."""

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("household_id", "slug", name="uq_devices_household_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Tên phòng (giữ đồng bộ với Room.name) để tương thích phần đọc theo tên cũ
    room: Mapped[str] = mapped_column(String(64), nullable=False)
    # Xoá phòng thì thiết bị thành "chưa gán phòng" thay vì bị xoá theo
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True, index=True)
    device_type: Mapped[DeviceType] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(String(16), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    online: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    household: Mapped[Household] = relationship(back_populates="devices")

    def has_capability(self, capability: Capability) -> bool:
        return capability.value in (self.capabilities or [])


class Sensor(Base):
    """Cảm biến môi trường — đầu vào cho automation theo ngữ cảnh."""

    __tablename__ = "sensors"
    __table_args__ = (UniqueConstraint("household_id", "slug", name="uq_sensors_household_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sensor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(16), default="")
    # Rỗng nghĩa là cảm biến ngoài trời hoặc của cả nhà, không gắn với phòng nào
    room: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ActionLog(Base):
    """Log lịch sử hành động — deliverable bắt buộc của đề bài."""

    __tablename__ = "action_logs"
    # Truy vấn hay dùng: lịch sử/thói quen theo hộ, lọc theo người, sắp theo thời gian
    __table_args__ = (
        Index("ix_action_logs_hh_user_created", "household_id", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Liên kết dòng lịch sử với yêu cầu duyệt (nếu có) — để truy vết approval ↔ log.
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True)
    # Câu lệnh gốc người dùng nói, để đối chiếu khi review lịch sử
    command_text: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[ActionStatus] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    # Nguồn: "user" (bấm nút trực tiếp), "agent", "automation"
    source: Mapped[str] = mapped_column(String(16), default="agent")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class AuditEnvelope(Base):
    """Bản ghi kiểm toán §53 ĐẦY ĐỦ (decision trace) — persist để tái dựng "vì sao agent làm vậy"
    sau khi request kết thúc / restart (FR-14, spec §53).

    Khác ``ActionLog`` (chỉ action/status từng thiết bị): giữ nguyên envelope quyết định của cả lượt
    (semantic_goal, plan đề xuất, policy, kết quả thực thi) trong một JSON theo conversation/plan.
    """

    __tablename__ = "audit_envelopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    plan_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    input_text: Mapped[str] = mapped_column(Text, default="")
    # Toàn bộ decision trace §53: {semantic_goal, ledger?, memory?, preference?, plan, policy, execution}
    envelope: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class Approval(Base):
    """Yêu cầu Human-in-the-loop đang chờ hoặc đã xử lý."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    command_text: Mapped[str] = mapped_column(Text, default="")
    reason_vi: Mapped[str] = mapped_column(Text, default="")
    # Các bước cần duyệt, dạng [{device_slug, action, params, reason_vi}, ...]
    steps: Mapped[list] = mapped_column(JSON, default=list)
    required_role: Mapped[str] = mapped_column(String(16), default=Role.OWNER.value)
    status: Mapped[ActionStatus] = mapped_column(String(32), default=ActionStatus.PENDING_APPROVAL)
    episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Hết hạn tự động: request để quá hạn thì bị coi là EXPIRED khi đem ra duyệt.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Nguồn tạo yêu cầu: "chat" | "map" | "habit" | "automation" | "suggestion"
    source: Mapped[str] = mapped_column(String(16), default="chat")


class AccessRule(Base):
    """Quyền riêng của một thành viên với một thiết bị, ghi đè ma trận rủi ro.

    Chủ hộ đặt các luật này (tính năng quản lý thành viên). Mỗi cặp (thành viên,
    thiết bị) có tối đa một luật; không có luật nghĩa là áp dụng phân quyền mặc
    định theo mức rủi ro của thiết bị. Xem ``src/core/permissions.py``.
    """

    __tablename__ = "access_rules"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_access_rules_user_device"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    effect: Mapped[AccessEffect] = mapped_column(String(16), nullable=False)
    # Chủ hộ nào đặt luật này — để đối chiếu khi review
    set_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Preference(Base):
    """Sở thích của từng thành viên — đầu vào cho việc phát hiện xung đột."""

    __tablename__ = "preferences"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_preferences_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Ví dụ: "air_conditioner.temperature", "light.brightness"
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Habit(Base):
    """Thói quen agent học được từ ActionLog.

    Một thói quen = (thiết bị, hành động) lặp lại vào một khung giờ. ``confidence``
    là tỉ lệ ngày quan sát được hành vi đó, dùng để quyết định có nên chủ động đề xuất.
    """

    __tablename__ = "habits"
    __table_args__ = (
        UniqueConstraint("household_id", "user_id", "device_slug", "action", "hour", name="uq_habits_signature"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    device_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    description_vi: Mapped[str] = mapped_column(String(255), default="")
    # Phút trong giờ, để thói quen "tạo tay" tới đúng mốc như báo thức. Vòng học luôn
    # để 0 (nó gom theo giờ), nên learned habit giữ nguyên hành vi cũ. Xem due_habits.
    minute: Mapped[int] = mapped_column(Integer, default=0)
    # Nguồn gốc: "learned" (vòng học tự sinh từ ActionLog) hay "manual" (người dùng tự
    # thêm). Thói quen tay KHÔNG bị learn_habits/_decay_stale đụng tới — nếu không, một
    # thói quen chưa có bằng chứng lịch sử sẽ bị decay về 0 ngay vòng sau. Xem memory/habits.py.
    source: Mapped[str] = mapped_column(String(16), default="learned")
    # Chủ hộ/thành viên có thể tắt một thói quen học nhầm để nó ngừng sinh đề xuất
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Hoãn tới thời điểm này (người dùng nói "để 22h15 hẵng tắt")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class EpisodicMemory(Base):
    """Một TÌNH HUỐNG đã xảy ra: người dùng nói gì, agent hiểu ra sao, đề xuất gì, và
    người dùng phản ứng thế nào.

    Đây là bộ nhớ TẬP (episodic), không phải bộ nhớ quy tắc: mỗi dòng là một sự kiện có
    thật kèm thời điểm, KHÔNG phải một luật "câu X → kế hoạch Y". Tầng truy hồi đọc nó
    như BẰNG CHỨNG để agent hiểu ngữ cảnh, và tuyệt đối không được chép `proposed_actions`
    ra thi hành thẳng — mọi lượt vẫn phải đi qua Semantic Goal → Planner → Validator.

    `signals` giữ các khoá tất định để chấm điểm truy hồi (phòng, thiết bị, nhãn mục tiêu,
    khung giờ) — tách khỏi văn bản tự do để việc tìm lại không phụ thuộc cách diễn đạt.
    """

    __tablename__ = "episodic_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    utterance: Mapped[str] = mapped_column(Text, default="")
    goal_description: Mapped[str] = mapped_column(Text, default="")
    utterance_type: Mapped[str] = mapped_column(String(32), default="")
    # Nhãn tình huống do tầng ngữ nghĩa sinh ra (vd "có khách", "sắp về nhà") — dùng để
    # nhóm các lần lặp lại. KHÔNG phải khoá tra cứu kế hoạch.
    situation_label: Mapped[str] = mapped_column(String(120), default="", index=True)

    room: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    hour: Mapped[int] = mapped_column(Integer, default=0, index=True)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)

    proposed_actions: Mapped[list] = mapped_column(JSON, default=list)
    # accepted | rejected | corrected | executed | proposed
    outcome: Mapped[str] = mapped_column(String(24), default="proposed", index=True)
    correction_note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ResidentProfile(Base):
    """Hồ sơ dài hạn của một thành viên: một mệnh đề về thói quen/sở thích kèm BẰNG CHỨNG.

    Khác `Preference` (một con số do người dùng khai) và `Habit` (bộ ba thiết bị/hành
    động/giờ suy từ ActionLog): bảng này giữ mệnh đề ở mức NGỮ NGHĨA, đủ tổng quát để mô
    tả những thứ như "khi có khách thường muốn phòng khách sáng hơn".

    `evidence_count` + `confidence` là điều kiện để một quan sát được nâng thành thói quen
    dài hạn. Một lần xảy ra KHÔNG phải thói quen — đó là ranh giới chống việc bịa ra luật
    từ một mẫu duy nhất.
    """

    __tablename__ = "resident_profiles"
    __table_args__ = (
        UniqueConstraint("household_id", "user_id", "trait_key", name="uq_resident_profile_trait"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    # kind: preference | routine | constraint
    kind: Mapped[str] = mapped_column(String(24), default="preference", index=True)
    # Khoá ổn định của mệnh đề, vd "guests.living_room.brightness"
    trait_key: Mapped[str] = mapped_column(String(160), nullable=False)
    statement_vi: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[dict] = mapped_column(JSON, default=dict)

    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # id của các EpisodicMemory làm bằng chứng — để truy vết được vì sao agent tin điều này.
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)

    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Notification(Base):
    """Thông báo gửi cho chủ hộ khi thành viên dùng thiết bị ở trạng thái ALERT.

    ALERT = cho dùng ngay nhưng chủ hộ được biết. Khác với ``Approval`` (chặn chờ
    duyệt), thông báo này chỉ để chủ hộ nắm tình hình — hành động đã xảy ra rồi.
    Lưu lại (kèm ``read``) để chủ hộ offline lúc đó vẫn xem được sau.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    # Người nhận (chủ hộ) và người gây ra (thành viên)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    # Loại thông báo: approval_requested/approved/rejected/expired, sensitive_device_used,
    # conflict_warning, child_lock_changed. Mặc định giữ tương thích với ALERT cũ.
    notification_type: Mapped[str] = mapped_column(String(32), default="sensitive_device_used", index=True)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    message_vi: Mapped[str] = mapped_column(Text, default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class PendingSuggestion(Base):
    """Đề xuất automation/thói quen đang chờ người dùng xử lý (chấp nhận/hoãn/bỏ qua).

    Persist vào DB để không mất khi user offline hoặc server restart. Tự hết hạn
    sau ``expires_at`` — cleanup khi query hoặc qua scheduled job.
    """

    __tablename__ = "pending_suggestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    habit_id: Mapped[int | None] = mapped_column(ForeignKey("habits.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="habit")
    title_vi: Mapped[str] = mapped_column(Text, default="")
    message_vi: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PreferenceQValue(Base):
    """Q-table lưu trữ giá trị sở thích cho Contextual Bandit (FR-08).

    Phân tách theo hộ và thành viên. Tối ưu hoá truy vấn và cập nhật atomic.
    """

    __tablename__ = "preference_q_values"
    __table_args__ = (
        UniqueConstraint("household_id", "user_id", "dimension", "state_key", "action", name="uq_pref_q_values"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[int] = mapped_column(Integer, nullable=False)
    q_value: Mapped[float] = mapped_column(Float, default=0.0)
    update_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class LearningDecision(Base):
    """Ngữ cảnh ra quyết định và kết quả thực thi tại thời điểm thực tế (FR-15).

    Dùng để credit assignment chính xác khi user gửi feedback, tránh tái dựng từ sensor state hiện tại.
    """

    __tablename__ = "learning_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rl_state: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_context: Mapped[dict] = mapped_column(JSON, default=dict)
    executed_actions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class LearningFeedback(Base):
    """Biên lai tiếp nhận feedback từ production và trạng thái xử lý độc lập Memory / RL (FR-15).

    Idempotency theo (household_id, user_id, idempotency_key).
    """

    __tablename__ = "learning_feedbacks"
    __table_args__ = (
        UniqueConstraint("household_id", "user_id", "idempotency_key", name="uq_learning_feedbacks_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    execution_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    corrected_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    rl_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    memory_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    rl_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ConversationLedger(Base):
    """Requirement Ledger bền vững (spec §17) — trạng thái hội thoại CANONICAL.

    Ledger là bản ghi chính của mạch hội thoại (mục tiêu đang treo, câu hỏi làm rõ chưa
    được trả lời, ràng buộc người dùng nêu rõ, assumption đã bị bác, Salience Stack).
    Khi ledger chỉ sống trong RAM của một tiến trình, slot-fill đa lượt CHẾT ở lần restart
    hoặc khi request rơi vào uvicorn worker khác: lượt trả lời "phòng bếp" mất mốc
    `raw_utterance` nên rơi về hỏi lại, và các ràng buộc durable (§73 invariant #8/#9)
    biến mất — hệ thống có thể hồi sinh đúng assumption người dùng vừa bác.

    Bảng này lưu snapshot JSON của `src.agent.schemas.RequirementLedger` theo
    `conversation_id`. Snapshot (không phải event log) là đúng ngữ nghĩa: updater đã merge
    delta theo lượt, nên trạng thái sau-merge chính là thứ lượt sau cần đọc.
    """

    __tablename__ = "conversation_ledgers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Hộ gia đình sở hữu hội thoại — ranh giới phân tách dữ liệu như mọi bảng khác.
    # Nullable vì eval/harness offline chạy không gắn hộ.
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Snapshot RequirementLedger.model_dump(mode="json").
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    last_updated_turn: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True
    )
