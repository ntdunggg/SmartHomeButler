"""Xác thực, băm mật khẩu và mã hoá dữ liệu cá nhân.

Đề bài yêu cầu "mã hoá dữ liệu cá nhân, phân theo hộ". Ở đây mỗi hộ có một khoá
Fernet riêng, dẫn xuất từ ``ENCRYPTION_MASTER_KEY`` bằng HKDF với ``household_id``
làm salt — rò rỉ dữ liệu của một hộ không giải mã được hộ khác, mà vẫn chỉ cần
quản lý một khoá gốc duy nhất.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.config import get_settings
from src.core.errors import AuthenticationError

_PBKDF2_ROUNDS = 200_000
_HASH_NAME = "sha256"


# --------------------------------------------------------------------------
# Mật khẩu
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Băm mật khẩu bằng PBKDF2-HMAC-SHA256 (thư viện chuẩn, không cần build C)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """So sánh mật khẩu ở thời gian hằng số để tránh timing attack."""
    try:
        scheme, rounds_raw, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    try:
        rounds = int(rounds_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode(), salt, rounds)
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------
def create_access_token(*, user_id: int, household_id: int, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "household_id": household_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token không hợp lệ.") from exc
    # Refresh token không được dùng thay access token ở bất kỳ route nào khác /auth/refresh
    if payload.get("type") == "refresh":
        raise AuthenticationError("Token không hợp lệ.")
    return payload


def create_refresh_token(*, user_id: int, household_id: int) -> str:
    """Refresh token sống lâu, chỉ dùng để đổi lấy access token mới qua /auth/refresh."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "household_id": household_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_refresh_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_refresh_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token không hợp lệ.") from exc
    if payload.get("type") != "refresh":
        raise AuthenticationError("Token này không phải refresh token.")
    return payload


# --------------------------------------------------------------------------
# Mã hoá dữ liệu cá nhân, phân tách theo hộ
# --------------------------------------------------------------------------
@lru_cache(maxsize=256)
def _household_cipher(household_id: int) -> Fernet:
    """Dẫn xuất khoá Fernet riêng cho từng hộ từ khoá gốc."""
    settings = get_settings()
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=f"household:{household_id}".encode(),
        info=b"smarthome-personal-data",
    ).derive(settings.encryption_master_key.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_personal(value: str, *, household_id: int) -> str:
    """Mã hoá một trường dữ liệu cá nhân bằng khoá của hộ."""
    if not value:
        return ""
    return _household_cipher(household_id).encrypt(value.encode()).decode()


def decrypt_personal(token: str, *, household_id: int) -> str:
    """Giải mã trường dữ liệu cá nhân.

    Trả về chuỗi rỗng nếu dữ liệu không giải mã được bằng khoá của hộ này —
    tức là dữ liệu thuộc hộ khác hoặc đã hỏng. Không raise để một bản ghi lỗi
    không làm sập cả trang dashboard.
    """
    if not token:
        return ""
    try:
        return _household_cipher(household_id).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
