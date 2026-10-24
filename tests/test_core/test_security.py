"""Băm mật khẩu, JWT và mã hoá dữ liệu cá nhân phân theo hộ."""

from __future__ import annotations

import pytest

from src.core.errors import AuthenticationError
from src.core.security import (
    create_access_token,
    decode_access_token,
    decrypt_personal,
    encrypt_personal,
    hash_password,
    verify_password,
)


def test_mat_khau_khong_luu_dang_ro():
    stored = hash_password("demo1234")
    assert "demo1234" not in stored
    assert verify_password("demo1234", stored)
    assert not verify_password("sai-mat-khau", stored)


def test_moi_lan_bam_ra_salt_khac_nhau():
    """Hai người dùng cùng mật khẩu phải cho hash khác nhau."""
    assert hash_password("demo1234") != hash_password("demo1234")


@pytest.mark.parametrize("broken", ["", "khong-dung-dinh-dang", "pbkdf2$abc$def", "md5$1$aa$bb"])
def test_hash_hong_khong_lam_sap_ma_tra_ve_false(broken):
    assert not verify_password("demo1234", broken)


def test_jwt_giu_nguyen_thong_tin_dinh_danh():
    token = create_access_token(user_id=7, household_id=3, role="owner")
    payload = decode_access_token(token)
    assert payload["sub"] == "7"
    assert payload["household_id"] == 3
    assert payload["role"] == "owner"


def test_jwt_gia_mao_bi_tu_choi():
    with pytest.raises(AuthenticationError):
        decode_access_token("day.khong.phai.token")


def test_ma_hoa_du_lieu_ca_nhan_giai_ma_lai_dung():
    encrypted = encrypt_personal("Trần Văn Bố", household_id=1)
    assert "Trần" not in encrypted
    assert decrypt_personal(encrypted, household_id=1) == "Trần Văn Bố"


def test_khoa_cua_ho_nay_khong_doc_duoc_du_lieu_ho_khac():
    """Đây chính là yêu cầu 'phân theo hộ' của đề bài."""
    encrypted = encrypt_personal("Trần Văn Bố", household_id=1)
    assert decrypt_personal(encrypted, household_id=2) == ""


def test_chuoi_rong_va_du_lieu_hong_khong_raise():
    assert encrypt_personal("", household_id=1) == ""
    assert decrypt_personal("", household_id=1) == ""
    assert decrypt_personal("rac-khong-giai-ma-duoc", household_id=1) == ""
