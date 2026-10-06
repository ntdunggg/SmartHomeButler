"""WebSocket realtime.

Đẩy ba loại sự kiện về frontend: thiết bị đổi trạng thái, agent có đề xuất mới,
và có yêu cầu chờ duyệt. Nhờ vậy khi một thành viên bật đèn thì màn hình của cả
nhà cùng đổi, không phải chờ poll.

Kết nối được phân theo hộ — client chỉ nhận sự kiện của chính hộ mình.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from src.core.errors import AuthenticationError
from src.core.security import decode_access_token
from src.db.session import session_scope
from src.domain.enums import Role
from src.domain.models import Approval, Device
from src.services.power_usage import estimated_power_sensor

logger = logging.getLogger(__name__)

router = APIRouter()

# Sau bao lâu không có tin nào từ client thì chủ động ping — client mất kết nối
# âm thầm (rớt wifi, sập app) sẽ lộ ra ngay khi gửi ping thất bại, thay vì treo
# ``receive_text()`` vô thời hạn và giữ một kết nối chết trong ConnectionManager.
PING_INTERVAL_SECONDS = 25


class ConnectionManager:
    """Quản lý các kết nối WebSocket đang mở, gom theo household_id.

    Nhớ thêm vai trò của mỗi kết nối để có thể đẩy một số sự kiện cho riêng chủ
    hộ (ví dụ popup chờ duyệt), không lộ cho mọi thành viên.
    """

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._role: dict[WebSocket, str] = {}
        self._user_id: dict[WebSocket, int] = {}

    async def connect(self, household_id: int, websocket: WebSocket, *, role: str = "", user_id: int = 0) -> None:
        await websocket.accept()
        self._connections[household_id].add(websocket)
        self._role[websocket] = role
        self._user_id[websocket] = user_id

    def disconnect(self, household_id: int, websocket: WebSocket) -> None:
        self._connections[household_id].discard(websocket)
        self._role.pop(websocket, None)
        self._user_id.pop(websocket, None)

    async def broadcast(
        self,
        household_id: int,
        message: dict,
        *,
        roles: set[str] | None = None,
        user_id: int | None = None,
        user_ids: set[int] | None = None,
    ) -> None:
        """Gửi tới client của hộ, có thể lọc theo vai trò hoặc một/nhiều người dùng.

        Client chết thì loại bỏ luôn.
        """
        dead = []
        for connection in list(self._connections.get(household_id, ())):
            if roles is not None and self._role.get(connection, "") not in roles:
                continue
            if user_id is not None and self._user_id.get(connection, 0) != user_id:
                continue
            if user_ids is not None and self._user_id.get(connection, 0) not in user_ids:
                continue
            try:
                await connection.send_json(message)
            except (WebSocketDisconnect, RuntimeError):
                dead.append(connection)
        for connection in dead:
            self.disconnect(household_id, connection)

    def count(self, household_id: int) -> int:
        return len(self._connections.get(household_id, ()))


manager = ConnectionManager()


async def on_device_state(slug: str, state: dict) -> None:
    """Handler đăng ký vào IoT bus — phát trạng thái mới cho đúng hộ sở hữu."""
    with session_scope() as session:
        household_ids = list(session.scalars(select(Device.household_id).where(Device.slug == slug)))
        power_sensors = {
            household_id: estimated_power_sensor(session, household_id=household_id)
            for household_id in household_ids
        }
    for household_id in household_ids:
        await manager.broadcast(household_id, {"type": "device_state", "slug": slug, "state": state})
        await manager.broadcast(household_id, {"type": "sensor_state", "sensors": [power_sensors[household_id]]})


async def on_sensor_state(household_id: int, sensors: list[dict]) -> None:
    """Handler đăng ký vào automation engine — đẩy giá trị cảm biến mới cho cả hộ.

    Nhờ vậy các chip cảm biến trên mọi trang đang mở cùng đổi một lúc, không lệch
    nhau do mỗi trang tự poll ở thời điểm khác nhau.
    """
    await manager.broadcast(household_id, {"type": "sensor_state", "sensors": sensors})


async def on_suggestion(suggestion) -> None:
    """Handler đăng ký vào automation engine.

    Đề xuất có ``user_id`` (sinh từ thói quen của một người) chỉ gửi tới các máy
    của đúng người đó; đề xuất chung cả hộ (không khí, thời tiết...) gửi cho mọi máy.
    """
    await manager.broadcast(
        suggestion.household_id,
        {"type": "suggestion", "suggestion": suggestion.to_dict()},
        user_id=getattr(suggestion, "user_id", None),
    )


def _approval_payload(approval: Approval, *, requester_name: str) -> dict:
    created = approval.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "id": approval.id,
        "command_text": approval.command_text,
        "reason_vi": approval.reason_vi,
        "steps": approval.steps or [],
        "required_role": approval.required_role,
        "requested_by": requester_name,
        "created_at": created.isoformat() if created is not None else None,
    }


async def notify_approval_request(household_id: int, approval: Approval, *, requester_name: str) -> None:
    """Đẩy popup 'có yêu cầu chờ duyệt' tới riêng các máy của chủ hộ."""
    await manager.broadcast(
        household_id,
        {"type": "approval_request", "approval": _approval_payload(approval, requester_name=requester_name)},
        roles={Role.OWNER.value},
    )


async def notify_conflict_override_request(
    household_id: int, approval: Approval, *, requester_name: str, message_vi: str
) -> None:
    """Đẩy WS riêng cho conflict override — FE phân biệt được với approval thường."""
    await manager.broadcast(
        household_id,
        {
            "type": "conflict_override_requested",
            "approval": _approval_payload(approval, requester_name=requester_name),
            "message_vi": message_vi,
        },
        roles={Role.OWNER.value},
    )


async def notify_child_lock_changed(household_id: int, enabled: bool) -> None:
    """Báo cả hộ khi khoá trẻ em bật/tắt để giao diện cập nhật ngay."""
    await manager.broadcast(household_id, {"type": "child_lock_changed", "enabled": enabled})


async def notify_action_log(household_id: int, log_payload: dict) -> None:
    """Đẩy MỘT dòng lịch sử mới cho cả hộ (live logs). ``log_payload`` = ActionLogOut đã JSON hoá."""
    await manager.broadcast(household_id, {"type": "action_log", "log": log_payload})


async def notify_approval_resolved(household_id: int, *, approval_id: int, status: str) -> None:
    """Báo cho cả hộ biết một yêu cầu đã được duyệt/từ chối để đóng popup, cập nhật UI."""
    await manager.broadcast(
        household_id,
        {"type": "approval_resolved", "approval_id": approval_id, "status": status},
    )


async def notify_agent_event(household_id: int, user_id: int, payload: dict) -> None:
    """Push private async-command progress/result only to the requesting user."""
    await manager.broadcast(household_id, payload, user_ids={user_id})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default="")) -> None:
    """Kết nối realtime. Token truyền qua query vì WebSocket không có header Authorization."""
    try:
        payload = decode_access_token(token)
    except AuthenticationError:
        await websocket.close(code=4401, reason="Token không hợp lệ")
        return

    household_id = int(payload.get("household_id", 0))
    user_id = int(payload.get("sub", 0))
    await manager.connect(household_id, websocket, role=str(payload.get("role", "")), user_id=user_id)
    await websocket.send_json({"type": "connected", "household_id": household_id})

    try:
        while True:
            # Không xử lý lệnh qua WebSocket; vòng lặp này chỉ để giữ kết nối
            # và nhận tín hiệu ngắt từ phía client. Hết PING_INTERVAL_SECONDS mà
            # client im lặng thì chủ động ping — gửi thất bại nghĩa là kết nối
            # đã chết, dọn luôn thay vì để treo mãi.
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=PING_INTERVAL_SECONDS)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(household_id, websocket)
    except Exception:
        logger.exception("WebSocket của hộ %s gặp lỗi", household_id)
        manager.disconnect(household_id, websocket)
