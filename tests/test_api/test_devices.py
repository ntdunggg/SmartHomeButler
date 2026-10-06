"""Dashboard và điều khiển trực tiếp — nút bấm phải chịu cùng luật với agent."""

from __future__ import annotations

from httpx import AsyncClient


async def test_dashboard_du_thiet_bi_va_cam_bien(client: AsyncClient, login):
    headers = await login("bo")
    body = (await client.get("/api/v1/dashboard", headers=headers)).json()

    assert len(body["devices"]) >= 8, "đề bài yêu cầu tối thiểu 8 thiết bị"
    assert body["sensors"], "phải có cảm biến để automation theo ngữ cảnh"


async def test_dashboard_co_cam_bien_cong_suat_uoc_tinh(client: AsyncClient, login):
    headers = await login("bo")
    body = (await client.get("/api/v1/dashboard", headers=headers)).json()

    power = next(s for s in body["sensors"] if s["slug"] == "cam_bien_cong_suat")
    assert power["name"] == "Công suất ước tính"
    assert power["sensor_type"] == "power"
    assert power["unit"] == "kW"
    assert power["estimated"] is True
    assert power["unknown"] is False
    assert power["value"] == 0.01

    await client.post("/api/v1/devices/dieu_hoa_phong_khach/control", headers=headers, json={"action": "turn_on"})
    body_after = (await client.get("/api/v1/dashboard", headers=headers)).json()
    power_after = next(s for s in body_after["sensors"] if s["slug"] == "cam_bien_cong_suat")
    assert power_after["value"] == 0.91


async def test_dashboard_cho_biet_quyen_cua_tung_nguoi(client: AsyncClient, login):
    """Cùng một thiết bị, mỗi tài khoản thấy quyền khác nhau."""

    async def lock_view(username: str) -> dict:
        headers = await login(username)
        body = (await client.get("/api/v1/dashboard", headers=headers)).json()
        return next(d for d in body["devices"] if d["slug"] == "khoa_cua_chinh")

    # Bố VÀ mẹ đều là chủ hộ (quyền cao nhất): điều khiển được khoá cửa, thực thi ngay.
    for chu_ho in ("bo", "me"):
        view = await lock_view(chu_ho)
        assert view["can_control"] and not view["requires_approval"]

    # Con cái (thành viên) không điều khiển được thiết bị an ninh.
    for username in ("con_lon", "con_nho"):
        assert not (await lock_view(username))["can_control"]


async def test_thanh_vien_duoc_cap_accepted_chay_ngay(client: AsyncClient, login, grant_access):
    """Thành viên được chủ hộ cấp quyền 'accepted' cho một thiết bị → chạy ngay."""
    grant_access("con_nho", "den_ngu_con")  # chủ hộ cấp quyền accepted
    headers = await login("con_nho")
    response = await client.post("/api/v1/devices/den_ngu_con/control", headers=headers, json={"action": "turn_on"})
    assert response.status_code == 200

    body = response.json()
    assert body["ok"] and body["state"]["power"] == "on"
    assert not body["requires_approval"]
    assert body["latency_ms"] < 2000, "đề bài yêu cầu độ trễ dưới 2s"


async def test_thanh_vien_bi_chan_thiet_bi_chua_cap_quyen(client: AsyncClient, login):
    """Thiết bị chưa được chủ hộ cấp quyền → thành viên bị chặn (trắng quyền mặc định)."""
    headers = await login("con_nho")
    response = await client.post("/api/v1/devices/binh_nong_lanh/control", headers=headers, json={"action": "turn_on"})
    assert response.status_code == 403
    assert "chưa được chủ hộ cấp quyền" in response.json()["detail"].lower()


async def test_lenh_bi_chan_van_duoc_ghi_vao_lich_su(client: AsyncClient, login):
    """Việc bị từ chối cũng là thông tin cần audit."""
    headers = await login("con_nho")
    await client.post("/api/v1/devices/binh_nong_lanh/control", headers=headers, json={"action": "turn_on"})

    logs = (await client.get("/api/v1/history", headers=headers)).json()
    assert any(log["status"] == "denied" for log in logs)


async def test_thanh_vien_khong_dieu_khien_duoc_thiet_bi_an_ninh(client: AsyncClient, login):
    # Con cái (thành viên) vẫn bị chặn thẳng thiết bị an ninh.
    headers = await login("con_lon")
    response = await client.post("/api/v1/devices/khoa_cua_chinh/control", headers=headers, json={"action": "unlock"})
    assert response.status_code == 403


async def test_bo_me_bam_tay_len_thiet_bi_an_ninh_thuc_thi_ngay(client: AsyncClient, login):
    """Bố/mẹ = quyền cao nhất: bấm mở khoá cửa là thực thi NGAY, không qua HITL."""
    headers = await login("me")  # mẹ giờ cũng là chủ hộ
    response = await client.post("/api/v1/devices/khoa_cua_chinh/control", headers=headers, json={"action": "unlock"})
    assert response.status_code == 200

    body = response.json()
    assert body["ok"], "chủ hộ thực thi ngay"
    assert not body["requires_approval"]

    # Cửa đã mở khoá ngay.
    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    lock = next(d for d in dashboard["devices"] if d["slug"] == "khoa_cua_chinh")
    assert lock["state"]["locked"] is False


async def test_thiet_bi_khong_ton_tai(client: AsyncClient, login):
    headers = await login("bo")
    response = await client.post("/api/v1/devices/khong_co_that/control", headers=headers, json={"action": "turn_on"})
    assert response.status_code == 404


async def test_hanh_dong_thiet_bi_khong_ho_tro(client: AsyncClient, login):
    headers = await login("bo")
    response = await client.post(
        "/api/v1/devices/tv_phong_khach/control",
        headers=headers,
        json={"action": "set_temperature", "params": {"temperature": 20}},
    )
    assert response.status_code == 422


async def test_control_tu_dong_dong_cua_so_khi_mo_dieu_hoa(client: AsyncClient, login):
    """Auto-resolve: bật điều hoà khi cửa sổ cùng phòng đang mở → hệ thống TỰ đóng cửa sổ trước.

    Trước đây chỉ cảnh báo mâu thuẫn rồi vẫn để cửa mở; giờ tự gỡ nên không còn báo
    contradiction, và cửa sổ cùng phòng bị đóng lại."""
    headers = await login("bo")
    await client.post("/api/v1/devices/cua_so_phong_khach/control", headers=headers, json={"action": "open"})
    resp = await client.post("/api/v1/devices/dieu_hoa_phong_khach/control", headers=headers, json={"action": "turn_on"})
    assert resp.status_code == 200
    # Xung đột mâu thuẫn cửa-sổ đã được tự gỡ → không còn báo contradiction.
    types = [c["type"] for c in resp.json()["conflicts"]]
    assert "contradiction" not in types

    # Cửa sổ cùng phòng đã bị tự đóng (position = 0).
    dash = (await client.get("/api/v1/dashboard", headers=headers)).json()
    window = next(d for d in dash["devices"] if d["slug"] == "cua_so_phong_khach")
    assert window["state"].get("position", 0) == 0
