"""Đăng nhập và thông tin tài khoản — hai vai trò: chủ hộ và thành viên."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession
from src.core.errors import AuthenticationError
from src.core.security import create_access_token, create_refresh_token, decode_refresh_token, verify_password
from src.domain.models import Household, Room, User
from src.models.schemas import LoginRequest, LoginResponse, RefreshRequest, RefreshResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _room_name(session: DbSession, room_id: int | None) -> str:
    if room_id is None:
        return ""
    room = session.get(Room, room_id)
    return room.name if room else ""


def to_user_out(session: DbSession, user: User, household: Household) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        account_id=user.account_id,
        full_name=user.full_name or user.username,
        role=str(user.role),
        household_id=user.household_id,
        household_name=household.name,
        home_room_id=user.home_room_id,
        home_room_name=_room_name(session, user.home_room_id),
        private_room_id=user.private_room_id,
        private_room_name=_room_name(session, user.private_room_id),
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: DbSession) -> LoginResponse:
    user = session.scalar(select(User).where(User.username == payload.username))
    # So sánh mật khẩu kể cả khi không tìm thấy tài khoản để tránh lộ username nào tồn tại
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai tên đăng nhập hoặc mật khẩu.",
        )

    household = session.get(Household, user.household_id)
    if household is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Hộ gia đình không tồn tại.")

    token = create_access_token(user_id=user.id, household_id=user.household_id, role=str(user.role))
    refresh_token = create_refresh_token(user_id=user.id, household_id=user.household_id)
    return LoginResponse(access_token=token, refresh_token=refresh_token, user=to_user_out(session, user, household))


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(payload: RefreshRequest, session: DbSession) -> RefreshResponse:
    """Đổi refresh token lấy access token mới mà không cần đăng nhập lại."""
    try:
        claims = decode_refresh_token(payload.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    user = session.get(User, int(claims["sub"]))
    if user is None or user.household_id != claims.get("household_id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không còn hợp lệ.")

    access_token = create_access_token(user_id=user.id, household_id=user.household_id, role=str(user.role))
    new_refresh_token = create_refresh_token(user_id=user.id, household_id=user.household_id)
    return RefreshResponse(access_token=access_token, refresh_token=new_refresh_token)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser, session: DbSession) -> UserOut:
    household = session.get(Household, user.household_id)
    if household is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hộ gia đình không tồn tại.")
    return to_user_out(session, user, household)
