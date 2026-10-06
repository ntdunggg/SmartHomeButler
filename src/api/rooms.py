"""Quản lý phòng trong hộ.

Mọi thành viên xem được danh sách phòng; thêm/sửa/xoá chỉ chủ hộ. Tên phòng được
giữ đồng bộ xuống ``Device.room`` (lưu tên) để phần đọc theo tên vẫn chạy như cũ.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from src.api.deps import CurrentUser, DbSession, OwnerUser
from src.domain.models import Device, Room, User
from src.models.schemas import RoomCreate, RoomOut, RoomUpdate

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _device_count(session: DbSession, room_id: int) -> int:
    return session.scalar(select(func.count()).select_from(Device).where(Device.room_id == room_id)) or 0


def _to_room_out(session: DbSession, room: Room) -> RoomOut:
    return RoomOut(
        id=room.id,
        name=room.name,
        sort_order=room.sort_order,
        device_count=_device_count(session, room.id),
    )


def _get_room(session: DbSession, room_id: int, owner: User) -> Room:
    room = session.get(Room, room_id)
    if room is None or room.household_id != owner.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy phòng.")
    return room


@router.get("", response_model=list[RoomOut])
async def list_rooms(user: CurrentUser, session: DbSession) -> list[RoomOut]:
    """Danh sách phòng của hộ (kể cả phòng rỗng), kèm số thiết bị mỗi phòng."""
    rooms = session.scalars(
        select(Room).where(Room.household_id == user.household_id).order_by(Room.sort_order, Room.name)
    )
    return [_to_room_out(session, r) for r in rooms]


@router.post("", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
async def create_room(payload: RoomCreate, owner: OwnerUser, session: DbSession) -> RoomOut:
    """Thêm một phòng mới (có thể để rỗng, thêm thiết bị sau)."""
    if session.scalar(
        select(Room).where(Room.household_id == owner.household_id, Room.name == payload.name)
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phòng trùng tên đã tồn tại.")
    room = Room(household_id=owner.household_id, name=payload.name, sort_order=payload.sort_order)
    session.add(room)
    session.commit()
    session.refresh(room)
    return _to_room_out(session, room)


@router.patch("/{room_id}", response_model=RoomOut)
async def update_room(room_id: int, payload: RoomUpdate, owner: OwnerUser, session: DbSession) -> RoomOut:
    """Đổi tên hoặc đổi thứ tự phòng. Đổi tên sẽ đồng bộ xuống các thiết bị trong phòng."""
    room = _get_room(session, room_id, owner)

    if payload.name is not None and payload.name != room.name:
        dup = session.scalar(
            select(Room).where(
                Room.household_id == owner.household_id, Room.name == payload.name, Room.id != room.id
            )
        )
        if dup is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phòng trùng tên đã tồn tại.")
        room.name = payload.name
        for device in session.scalars(select(Device).where(Device.room_id == room.id)):
            device.room = payload.name

    if payload.sort_order is not None:
        room.sort_order = payload.sort_order

    session.commit()
    session.refresh(room)
    return _to_room_out(session, room)


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(room_id: int, owner: OwnerUser, session: DbSession) -> None:
    """Xoá phòng; thiết bị và các gán phòng của user được đưa về chưa gán."""
    room = _get_room(session, room_id, owner)
    for device in session.scalars(select(Device).where(Device.room_id == room.id)):
        device.room_id = None
        device.room = ""
    for user in session.scalars(
        select(User).where(
            User.household_id == owner.household_id,
            (User.home_room_id == room.id) | (User.private_room_id == room.id),
        )
    ):
        if user.home_room_id == room.id:
            user.home_room_id = None
        if user.private_room_id == room.id:
            user.private_room_id = None
    session.delete(room)
    session.commit()
