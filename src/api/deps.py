"""Dependency dùng chung cho các route.

``get_current_user`` là cửa duy nhất để lấy danh tính người dùng, và mọi truy vấn
dữ liệu phải lọc theo ``current_user.household_id`` lấy từ đây. Nhờ vậy việc phân
tách dữ liệu theo hộ được ép ở tầng hạ tầng, không phụ thuộc vào việc từng route
có nhớ thêm điều kiện lọc hay không.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.core.errors import AuthenticationError
from src.core.security import decode_access_token
from src.db.session import get_db
from src.domain.enums import Role
from src.domain.models import User

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


async def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bạn cần đăng nhập để dùng chức năng này.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tài khoản không còn tồn tại.")
    # Token phải khớp hộ ghi trong DB — chặn token cũ sau khi tài khoản đổi hộ
    if user.household_id != payload.get("household_id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không còn hợp lệ.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_owner(user: CurrentUser) -> User:
    """Chỉ cho chủ hộ đi qua — dùng cho các route quản trị (thành viên, phân quyền)."""
    if Role(user.role) is not Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ chủ hộ mới dùng được chức năng này.",
        )
    return user


OwnerUser = Annotated[User, Depends(get_owner)]
