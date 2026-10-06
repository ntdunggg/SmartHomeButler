"""Đăng nhập, token và ranh giới dữ liệu giữa các hộ."""

from __future__ import annotations

from httpx import AsyncClient


async def test_dang_nhap_thanh_cong_tra_ve_token_va_ho_so(client: AsyncClient):
    response = await client.post("/api/v1/auth/login", json={"username": "bo", "password": "demo1234"})
    assert response.status_code == 200

    body = response.json()
    assert body["access_token"]
    assert body["user"]["role"] == "owner"
    # Họ tên lưu mã hoá nhưng API phải trả về bản đã giải mã
    assert body["user"]["full_name"] == "Trần Văn Bố"


async def test_sai_mat_khau_bi_tu_choi(client: AsyncClient):
    response = await client.post("/api/v1/auth/login", json={"username": "bo", "password": "sai"})
    assert response.status_code == 401


async def test_tai_khoan_khong_ton_tai_bao_loi_giong_sai_mat_khau(client: AsyncClient):
    """Không được để lộ username nào có thật trong hệ thống."""
    khong_co = await client.post("/api/v1/auth/login", json={"username": "ma", "password": "demo1234"})
    sai_pass = await client.post("/api/v1/auth/login", json={"username": "bo", "password": "sai"})
    assert khong_co.status_code == sai_pass.status_code == 401
    assert khong_co.json()["detail"] == sai_pass.json()["detail"]


async def test_khong_co_token_thi_khong_vao_duoc_dashboard(client: AsyncClient):
    assert (await client.get("/api/v1/dashboard")).status_code == 401


async def test_token_gia_mao_bi_tu_choi(client: AsyncClient):
    response = await client.get("/api/v1/dashboard", headers={"Authorization": "Bearer khong-phai-token"})
    assert response.status_code == 401


async def test_me_tra_dung_nguoi_dang_dang_nhap(client: AsyncClient, login):
    headers = await login("con_nho")
    body = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert body["username"] == "con_nho"
    assert body["role"] == "member"
    assert body["private_room_name"] == "Phòng ngủ con"


async def test_moi_vai_tro_deu_dang_nhap_duoc(client: AsyncClient, login):
    # Bố và mẹ đều là chủ hộ (owner); con cái là thành viên (member).
    for username, role in [("bo", "owner"), ("me", "owner"), ("con_lon", "member"), ("con_nho", "member")]:
        headers = await login(username)
        body = (await client.get("/api/v1/auth/me", headers=headers)).json()
        assert body["role"] == role


async def test_me_tra_dung_phong_rieng_khop_voi_trang_quan_ly(client: AsyncClient, login):
    """/auth/me và /members phải thống nhất private_room — trước đây /auth/me luôn trả rỗng."""
    member_headers = await login("con_lon")
    me_body = (await client.get("/api/v1/auth/me", headers=member_headers)).json()
    assert me_body["private_room_id"] is not None
    assert me_body["private_room_name"] == "Phòng ngủ con"

    owner_headers = await login("bo")
    members = (await client.get("/api/v1/members", headers=owner_headers)).json()
    con_lon = next(m for m in members if m["username"] == "con_lon")

    assert me_body["private_room_id"] == con_lon["private_room_id"]
    assert me_body["private_room_name"] == con_lon["private_room_name"]
    assert me_body["home_room_id"] == con_lon["home_room_id"]
    assert me_body["home_room_name"] == con_lon["home_room_name"] == "Phòng ngủ con"
