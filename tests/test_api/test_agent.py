"""Luồng agent end-to-end qua API: tổng hợp kế hoạch, thực thi, HITL, hỏi lại, phân quyền.

Kiến trúc open-ended: KHÔNG còn scene/intent đóng. Lệnh NL đi qua CÙNG cổng quyền
``resolve_access`` như nút bấm: chủ hộ theo ma trận rủi ro; thành viên theo quyền
per-device (accepted/alert/request), chưa cấp thì bị chặn. HITL của thành viên xảy ra
khi chủ hộ cấp trạng thái ``request`` cho thiết bị đó."""

from __future__ import annotations

from httpx import AsyncClient

from src.domain.enums import AccessEffect

_AC = "dieu_hoa_phong_khach"


async def send(client: AsyncClient, headers: dict, message: str, conversation_id: str = "") -> dict:
    response = await client.post(
        "/api/v1/agent/command",
        headers=headers,
        json={"message": message, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_lenh_don_le_chay_ngay(client: AsyncClient, login):
    headers = await login("bo")
    body = await send(client, headers, "bật đèn phòng khách")
    assert body["nlu_source"] == "rules", "offline: lệnh tường minh không cần LLM"
    assert body["latency_ms"] < 2000
    assert "Đèn chùm" in body["response_vi"]
    assert not body["pending_approval"]
    assert "pipeline_total_ms" in body["diagnostics"]


async def test_async_command_returns_immediately_and_can_be_polled(client: AsyncClient, login):
    import asyncio

    headers = await login("bo")
    accepted = await client.post(
        "/api/v1/agent/command/async",
        headers=headers,
        json={"message": "bật đèn phòng khách", "conversation_id": "async-command"},
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]

    body = None
    for _ in range(30):
        polled = await client.get(f"/api/v1/agent/command/jobs/{job_id}", headers=headers)
        assert polled.status_code == 200
        body = polled.json()
        if body["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.01)

    assert body is not None and body["status"] == "completed"
    assert body["result"]["conversation_id"] == "async-command"
    assert body["result"]["plan"]


async def test_bo_me_cong_suat_lon_chay_ngay_khong_can_duyet(client: AsyncClient, login):
    """Bố/mẹ = quyền cao nhất: lệnh công suất lớn thực thi NGAY, không qua HITL."""
    for chu_ho in ("bo", "me"):
        headers = await login(chu_ho)
        body = await send(client, headers, "đặt điều hoà phòng khách 24 độ")
        assert not body["pending_approval"], f"{chu_ho} là chủ hộ nên không cần duyệt"

        dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
        ac = next(d for d in dashboard["devices"] if d["slug"] == "dieu_hoa_phong_khach")
        assert ac["state"]["power"] == "on"
        assert ac["state"]["temperature"] == 24

        # Trả về trạng thái cũ cho vòng lặp kế tiếp.
        await send(client, headers, "tắt điều hoà phòng khách")


async def test_thanh_vien_duoc_cap_request_dung_lai_cho_duyet_roi_chay_tiep(client: AsyncClient, login, grant_access):
    """HITL: thành viên được cấp 'request' → lệnh phải chờ chủ hộ duyệt → đổi trạng thái."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    body = await send(client, con_lon, "đặt điều hoà phòng khách 24 độ")
    approval = body["pending_approval"]
    assert approval, "được cấp 'request' nên phải xin xác nhận"

    dashboard = (await client.get("/api/v1/dashboard", headers=con_lon)).json()
    ac = next(d for d in dashboard["devices"] if d["slug"] == "dieu_hoa_phong_khach")
    assert ac["state"]["power"] == "off"

    # Chủ hộ (bố) duyệt.
    bo = await login("bo")
    resumed = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    assert resumed.status_code == 200

    dashboard = (await client.get("/api/v1/dashboard", headers=con_lon)).json()
    ac = next(d for d in dashboard["devices"] if d["slug"] == "dieu_hoa_phong_khach")
    assert ac["state"]["power"] == "on"
    assert ac["state"]["temperature"] == 24


async def test_duyet_lai_reground_tren_live_state(client: AsyncClient, login, grant_access):
    """QC-02/§50/FR-13: state đổi trong lúc CHỜ DUYỆT → resume refresh/reground, không execute mù.

    Thành viên (cấp REQUEST) xin đặt điều hoà 24°C (chờ duyệt). Trong lúc chờ, chủ hộ tự đặt đúng 24°C.
    Khi duyệt approval cũ, harness thống nhất phải nhận ra đã đúng trạng thái (no-op), không
    dispatch lại lệnh trên state cũ đã lưu."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    approval = (await send(client, con_lon, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    assert approval

    # Trong lúc chờ duyệt: chủ hộ đặt điều hoà đúng mục tiêu pending (state đã thoả).
    bo = await login("bo")
    await send(client, bo, "đặt điều hoà phòng khách 24 độ")

    # Duyệt approval CŨ → phải re-ground trên live: đã đúng trạng thái → giữ nguyên, không lỗi.
    resumed = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    assert resumed.status_code == 200
    assert "Giữ nguyên" in resumed.json()["response_vi"]

    dashboard = (await client.get("/api/v1/dashboard", headers=con_lon)).json()
    ac = next(d for d in dashboard["devices"] if d["slug"] == "dieu_hoa_phong_khach")
    assert ac["state"]["temperature"] == 24 and ac["state"]["power"] == "on"


async def test_tu_choi_thi_khong_thuc_hien(client: AsyncClient, login, grant_access):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    approval = (await send(client, con_lon, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    assert approval
    bo = await login("bo")
    resumed = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": False})
    assert resumed.status_code == 200
    assert "từ chối" in resumed.json()["response_vi"].lower()

    dashboard = (await client.get("/api/v1/dashboard", headers=con_lon)).json()
    ac = next(d for d in dashboard["devices"] if d["slug"] == "dieu_hoa_phong_khach")
    assert ac["state"]["power"] == "off", "chưa duyệt thì không được bật"


async def test_khong_duyet_duoc_hai_lan(client: AsyncClient, login, grant_access):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    approval = (await send(client, con_lon, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    bo = await login("bo")
    await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    # Duyệt lần hai: idempotent → 200 no-op (không lỗi 409), báo đã xử lý.
    lan_hai = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    assert lan_hai.status_code == 200
    assert "đã được xử lý" in lan_hai.json()["response_vi"].lower()


async def test_thanh_vien_khong_tu_duyet_duoc_lenh_cua_minh(client: AsyncClient, login, grant_access):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    # Thành viên được cấp 'request' → lệnh cần chủ hộ duyệt.
    approval = (await send(client, con_lon, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    assert approval
    # Thành viên không tự duyệt được lệnh của mình.
    response = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=con_lon, json={"approved": True})
    assert response.status_code == 403


async def test_xac_nhan_bang_loi_trong_chat_chu_ho_gat_dau(client: AsyncClient, login, grant_access):
    """Việc đang chờ duyệt được GẬT ĐẦU bằng lời ở lượt sau — bố nói 'đồng ý' → chạy ngay."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    body = await send(client, con_lon, "đặt điều hoà phòng khách 24 độ")
    approval = body["pending_approval"]
    assert approval, "được cấp 'request' phải xin duyệt"
    conv = body["conversation_id"]

    # Bố (chủ hộ) gật đầu bằng LỜI trong cùng hội thoại → thực thi.
    bo = await login("bo")
    resumed = await send(client, bo, "đồng ý", conversation_id=conv)
    assert not resumed["pending_approval"]

    dashboard = (await client.get("/api/v1/dashboard", headers=con_lon)).json()
    ac = next(d for d in dashboard["devices"] if d["slug"] == "dieu_hoa_phong_khach")
    assert ac["state"]["temperature"] == 24, "gật đầu bằng lời đã thực thi lệnh chờ"


async def test_tu_choi_bang_loi_trong_chat(client: AsyncClient, login, grant_access):
    """'Thôi' ở lượt sau → huỷ việc đang chờ; người YÊU CẦU tự huỷ được (không phá gì)."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    body = await send(client, con_lon, "đặt điều hoà phòng khách 24 độ")
    conv = body["conversation_id"]
    assert body["pending_approval"]

    reply = await send(client, con_lon, "thôi", conversation_id=conv)
    assert not reply["pending_approval"]
    assert "từ chối" in reply["response_vi"].lower() or "không thực hiện" in reply["response_vi"].lower()

    dashboard = (await client.get("/api/v1/dashboard", headers=con_lon)).json()
    ac = next(d for d in dashboard["devices"] if d["slug"] == "dieu_hoa_phong_khach")
    assert ac["state"]["power"] != "on", "đã từ chối nên không thực thi"


async def test_gat_dau_bang_loi_khong_du_quyen_thi_van_cho(client: AsyncClient, login, grant_access):
    """Thành viên tự nói 'đồng ý' cho lệnh của mình → KHÔNG đủ quyền duyệt, việc vẫn treo."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    body = await send(client, con_lon, "đặt điều hoà phòng khách 24 độ")
    conv = body["conversation_id"]
    assert body["pending_approval"]

    reply = await send(client, con_lon, "đồng ý", conversation_id=conv)
    assert reply["pending_approval"], "chưa đủ quyền → giữ chờ"
    assert "bố/mẹ" in reply["response_vi"].lower() or "chưa đủ quyền" in reply["response_vi"].lower()


async def test_lenh_moi_khong_bi_nham_thanh_gat_dau(client: AsyncClient, login, grant_access):
    """Khi có việc đang chờ, câu MANG lệnh mới ('bật đèn…') KHÔNG bị coi là gật đầu."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con_lon = await login("con_lon")
    body = await send(client, con_lon, "đặt điều hoà phòng khách 24 độ")
    conv = body["conversation_id"]
    assert body["pending_approval"]

    # Lệnh thường mới → xử lý riêng, việc chờ vẫn còn.
    other = await send(client, con_lon, "bật đèn phòng khách", conversation_id=conv)
    assert "đèn" in other["response_vi"].lower()


async def test_thanh_vien_chua_cap_quyen_bi_chan_qua_chat(client: AsyncClient, login):
    headers = await login("con_nho")
    body = await send(client, headers, "bật bình nóng lạnh")
    assert not body["pending_approval"], "chưa cấp quyền → chặn thẳng, không phải xin duyệt"
    assert "chưa được chủ hộ cấp quyền" in body["response_vi"].lower()
    assert body["plan"][0]["allowed"] is False


async def test_chu_ho_dieu_khien_an_ninh_qua_chat_chay_ngay(client: AsyncClient, login):
    """Bố/mẹ (chủ hộ, quyền cao nhất) mở khoá cửa qua chat — thực thi NGAY, không HITL."""
    headers = await login("bo")
    body = await send(client, headers, "mở cửa chính")
    assert not body["pending_approval"]

    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    lock = next(d for d in dashboard["devices"] if d["slug"] == "khoa_cua_chinh")
    assert lock["state"]["locked"] is False, "chủ hộ mở khoá được ngay qua chat"


async def test_mo_cua_hoi_phong_roi_mo_khoa_va_noop_ro_nghia(client: AsyncClient, login):
    """Regression: slot phòng hoàn tất đúng lệnh và response phân biệt mở khóa/mở cửa."""
    headers = await login("bo")
    conversation_id = "open-door-room-regression"

    first = await send(client, headers, "mở cửa", conversation_id=conversation_id)
    assert not first["plan"]
    # Câu hỏi phải nhắm vào PHÒNG và nêu được lựa chọn người dùng sắp trả lời. Trước đây
    # test khoá vào đúng chữ "phòng nào"; câu hỏi nay liệt kê các phòng THỰC SỰ có khoá cửa
    # (info gain cao hơn, spec §15), nên khẳng định theo hành vi thay vì theo câu chữ.
    question = first["response_vi"].lower()
    assert "phòng" in question
    assert "phòng nào" in question or "phòng khách" in question

    second = await send(client, headers, "phòng khách", conversation_id=conversation_id)
    assert [(step["device_slug"], step["action"]) for step in second["plan"]] == [
        ("khoa_cua_chinh", "unlock")
    ]
    assert "mở khóa" in second["response_vi"].lower()
    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    lock = next(device for device in dashboard["devices"] if device["slug"] == "khoa_cua_chinh")
    assert lock["state"] == {"locked": False}

    repeated = await send(client, headers, "mở cửa phòng khách", conversation_id=conversation_id)
    assert repeated["plan"][0]["skipped"] is True
    assert "đã mở khóa" in repeated["response_vi"].lower()


async def test_con_bi_chan_thiet_bi_an_ninh_qua_chat(client: AsyncClient, login):
    """Con (thành viên) KHÔNG điều khiển được thiết bị an ninh — bị phân quyền chặn."""
    headers = await login("con_lon")
    body = await send(client, headers, "mở cửa chính")
    assert not body["pending_approval"]
    assert "chưa được chủ hộ cấp quyền" in body["response_vi"].lower()

    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    lock = next(d for d in dashboard["devices"] if d["slug"] == "khoa_cua_chinh")
    assert lock["state"]["locked"] is True, "con bị chặn nên cửa vẫn khoá"


async def test_lenh_mo_ho_thi_agent_hoi_lai(client: AsyncClient, login):
    """Lệnh loại-thiết-bị không có phòng phải hỏi, rồi slot-fill ở lượt kế tiếp.

    Cảm biến hiện diện chỉ là ngữ cảnh môi trường, không phải bằng chứng người dùng đã
    chọn phạm vi thực thi. Vì vậy ``tắt đèn`` không được tự chọn phòng khách.
    """
    headers = await login("bo")
    conversation_id = "clarify-bare-light-command"

    # Dựng đúng tình huống: mọi đèn đều đang bật.
    prepared = await send(client, headers, "bật tất cả đèn", "prepare-all-lights-on")
    assert prepared["plan"]

    body = await send(client, headers, "tắt đèn", conversation_id)
    assert "phòng" in body["response_vi"].lower() and "?" in body["response_vi"]
    assert body["plan"] == []
    assert not body["pending_approval"]

    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    lights = [d for d in dashboard["devices"] if d["device_type"] == "light"]
    assert lights and all(d["state"]["power"] == "on" for d in lights), "chưa trả lời thì chưa được tắt đèn"

    completed = await send(client, headers, "phòng khách", conversation_id)
    assert completed["plan"]
    assert {step["room"] for step in completed["plan"]} == {"Phòng khách"}

    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    living_light = next(d for d in dashboard["devices"] if d["slug"] == "den_chum_phong_khach")
    other_lights = [d for d in dashboard["devices"] if d["device_type"] == "light" and d["slug"] != living_light["slug"]]
    assert living_light["state"]["power"] == "off"
    assert all(d["state"]["power"] == "on" for d in other_lights)


async def test_lenh_mo_ho_alias_van_hoi_lai_du_biet_vi_tri(client: AsyncClient, login):
    """"đèn ngủ" khớp THẲNG 2 thiết bị theo alias (2 phòng ngủ) — mơ hồ ở tầng alias, không
    phải "thiếu phòng" — nên biết speaker_location=Phòng khách (không có đèn ngủ nào ở đó)
    cũng KHÔNG được đoán bừa; vẫn phải hỏi lại."""
    headers = await login("bo")
    body = await send(client, headers, "tắt đèn ngủ")
    assert "Đèn ngủ" in body["response_vi"] and "?" in body["response_vi"], "phải hỏi lại, không tự chọn 1 trong 2"
    assert not body["pending_approval"]


async def test_vi_tri_tu_suy_ra_thi_khong_hoi_lai(client: AsyncClient, login):
    """Location Resolver: presence (seed) báo phòng khách có người → 'ở đây tối quá' tự
    hiểu là phòng khách và lập kế hoạch NGAY, không bắt người dùng chọn phòng.

    Ban ngày optimizer có thể chọn mở rèm/cửa sổ thay vì bật đèn; invariant cần
    kiểm là mọi action đều ground vào đúng phòng được suy ra."""
    headers = await login("bo")
    body = await send(client, headers, "ở đây tối quá")

    assert not body["pending_approval"]
    assert body["plan"], "đã tự suy được phòng thì phải ra kế hoạch, không hỏi lại"
    # Hành động rơi vào thiết bị phòng khách (theo presence).
    from src.iot.registry import DEVICE_BY_SLUG, LIVING_ROOM

    slugs = [s["device_slug"] for s in body["plan"] if s.get("device_slug")]
    assert slugs and all(DEVICE_BY_SLUG[s].room == LIVING_ROOM for s in slugs)


async def test_cau_khong_lien_quan_duoc_tra_loi_lich_su(client: AsyncClient, login):
    headers = await login("bo")
    body = await send(client, headers, "hôm nay trời đẹp nhỉ")
    assert body["response_vi"], "không hiểu vẫn phải nói gì đó, không được im lặng"
    assert not body["pending_approval"]


async def test_bo_qua_buoc_thua_khi_thiet_bi_da_dung_trang_thai(client: AsyncClient, login):
    headers = await login("bo")
    await send(client, headers, "bật đèn phòng khách")
    body = await send(client, headers, "bật đèn phòng khách")
    assert body["plan"][0]["skipped"] is True


async def test_moi_hanh_dong_deu_vao_lich_su(client: AsyncClient, login):
    headers = await login("bo")
    await send(client, headers, "bật đèn phòng khách")
    logs = (await client.get("/api/v1/history", headers=headers)).json()
    assert logs
    entry = logs[0]
    assert entry["source"] == "agent"
    assert entry["username"] == "Trần Văn Bố"
    assert entry["command_text"] == "bật đèn phòng khách"
