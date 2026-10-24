"""Exception nghiệp vụ — API layer map các lỗi này sang HTTP status tương ứng."""

from __future__ import annotations


class SmartHomeError(Exception):
    """Lỗi gốc của hệ thống."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(SmartHomeError):
    status_code = 401


class PermissionDeniedError(SmartHomeError):
    """Người dùng không đủ quyền theo vai trò hoặc nhóm tuổi."""

    status_code = 403

    def __init__(self, message: str, *, reason_vi: str = "") -> None:
        super().__init__(message)
        # Câu giải thích tiếng Việt hiển thị thẳng cho người dùng cuối
        self.reason_vi = reason_vi or message


class NotFoundError(SmartHomeError):
    status_code = 404


class DeviceError(SmartHomeError):
    """Thiết bị từ chối lệnh hoặc không phản hồi."""

    status_code = 422
