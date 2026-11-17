"""Test nghiệm thu cho các use case demo (docs/kich-ban-kiem-thu.md).

Mỗi use case chạy qua API thật trên thiết bị giả lập, đúng như người dùng dùng phần mềm.
Use case đang hỏng vì một lỗi trong sổ lỗi (docs/findings/README.md) được đánh dấu
``xfail(strict=True)`` kèm mã lỗi: bộ test vẫn xanh, nhưng khi lỗi được sửa thì test
chuyển XPASS và báo đỏ, buộc người sửa gỡ dấu và cập nhật trạng thái use case.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.config import get_settings
from src.core import clock
from src.domain.enums import AccessEffect, SuggestionKind
from src.domain.models import Device, Habit
from src.services.automation import AutomationEngine, registry
from tests.helpers_habits import DEMO_DEVICE_SLUG, freeze_vn, ids, log_evidence, vn
from tests.test_services.test_environment_automation import _snapshot, _StaticEnvironmentTool


def _blocked_by(code: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"Lỗi {code} trong docs/findings/README.md: {why}")


async def _send(client: AsyncClient, headers: dict, message: str, *, cid: str = "", location: str | None = None) -> dict:
    body: dict = {"message": message, "conversation_id": cid}
    if location:
        body["speaker_location"] = location
    resp = await client.post("/api/v1/agent/command", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _state(client: AsyncClient, headers: dict, slug: str) -> dict:
    dash = (await client.get("/api/v1/dashboard", headers=headers)).json()
    return next(d for d in dash["devices"] if d["slug"] == slug)["state"]


# --- TC-01 ------------------------------------------------------------------------
@pytest.mark.parametrize("message", ["Bật đèn phòng khách.", "bat den phong khach"])
async def test_tc01_lenh_ro_rang(client, login, message):
    bo = await login("bo")
    await _send(client, bo, message)
    assert (await _state(client, bo, "den_chum_phong_khach"))["power"] == "on"


# --- TC-02 ------------------------------------------------------------------------
@_blocked_by("11", "“nóng quá” ở phòng bố mẹ lại chỉnh điều hoà phòng khách")
async def test_tc02_cau_mo_ho_theo_vi_tri(client, login):
    bo = await login("bo")
    before_living = await _state(client, bo, "dieu_hoa_phong_khach")
    await _send(client, bo, "Nóng quá.", location="Phòng ngủ bố mẹ")
    parents = await _state(client, bo, "dieu_hoa_phong_bo_me")
    assert parents["power"] == "on"
    assert parents["temperature"] <= 26
    assert await _state(client, bo, "dieu_hoa_phong_khach") == before_living


# --- TC-03 ------------------------------------------------------------------------
async def test_tc03_hoi_lai_khi_thieu_phong(client, login):
    bo = await login("bo")
    await _send(client, bo, "Bật đèn phòng khách.")
    first = await _send(client, bo, "Tắt đèn.", cid="uc03")
    assert first["plan"] == []
    assert "Phòng khách" in first["response_vi"]
    assert (await _state(client, bo, "den_chum_phong_khach"))["power"] == "on"
    await _send(client, bo, "Phòng khách.", cid="uc03")
    assert (await _state(client, bo, "den_chum_phong_khach"))["power"] == "off"


# --- TC-04 ------------------------------------------------------------------------
@_blocked_by("01", "“giảm thêm chút” từ 80% về 30% thay vì 65% (ADR-0007)")
async def test_tc04_hoi_thoai_nhieu_luot(client, login):
    bo = await login("bo")
    await _send(client, bo, "Đặt đèn chùm phòng khách 80%.", cid="uc04")
    await _send(client, bo, "Giảm thêm chút.", cid="uc04")
    assert (await _state(client, bo, "den_chum_phong_khach"))["brightness"] == 65


# --- TC-05 ------------------------------------------------------------------------
async def test_tc05_thanh_vien_mo_khoa_cho_duyet(client, login, grant_access):
    grant_access("con_lon", "khoa_cua_chinh", effect=AccessEffect.REQUEST)
    con = await login("con_lon")
    resp = await _send(client, con, "Mở khoá cửa chính.")
    approval = resp["pending_approval"]
    assert approval
    bo = await login("bo")
    assert (await _state(client, bo, "khoa_cua_chinh"))["locked"] is True

    decided = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    assert decided.status_code == 200, decided.text
    assert (await _state(client, bo, "khoa_cua_chinh"))["locked"] is False


# --- TC-06 ------------------------------------------------------------------------
async def test_tc06_khoa_tre_em(client, login, grant_access):
    grant_access("con_nho", "binh_nong_lanh", effect=AccessEffect.ACCEPTED)
    bo = await login("bo")
    resp = await client.put("/api/v1/household/child-lock", headers=bo, json={"enabled": True})
    assert resp.status_code == 200, resp.text

    con = await login("con_nho")
    await _send(client, con, "Bật bình nóng lạnh.")
    assert (await _state(client, bo, "binh_nong_lanh"))["power"] == "off"


# --- TC-07 ------------------------------------------------------------------------
@_blocked_by("12", "chưa phát hiện xung đột sở thích giữa thành viên (bước ⑤)")
async def test_tc07_xung_dot_so_thich(client, login):
    # Seed: bố thích 26°C, mẹ thích 24°C. Mẹ bật điều hoà phòng chung.
    me = await login("me")
    resp = await _send(client, me, "Bật điều hoà phòng bố mẹ.", location="Phòng ngủ bố mẹ")
    bo = await login("bo")
    state = await _state(client, bo, "dieu_hoa_phong_bo_me")
    assert state["power"] == "on"
    noticed = bool(resp["conflicts"]) or "bố" in resp["response_vi"].lower()
    assert noticed, resp["response_vi"]
    assert state["temperature"] == 25  # dung hoà giữa 24 và 26


# --- TC-08 ------------------------------------------------------------------------
@pytest.fixture
def _automation_isolated(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT_REALTIME_ENABLED", "false")
    get_settings.cache_clear()
    registry.clear()
    yield
    registry.clear()
    clock.reset()
    get_settings.cache_clear()


async def test_tc08_thoi_quen_hang_ngay(client, login, seeded, _automation_isolated):
    hh, user, device = ids(seeded)
    for day in (1, 3, 5, 7):  # bố bật máy lạnh phòng khách lúc 22:30 trong 4 ngày
        log_evidence(seeded, hh=hh, user=user, device=device, action="turn_on", when=vn(day))
    seeded.commit()
    freeze_vn(9)

    await AutomationEngine().run_once()
    bo = await login("bo")
    listed = (await client.get("/api/v1/agent/suggestions", headers=bo)).json()
    confirms = [s for s in listed if s["kind"] == SuggestionKind.HABIT_CONFIRM]
    assert len(confirms) == 1, listed

    resp = await client.post(f"/api/v1/agent/suggestions/{confirms[0]['id']}", headers=bo, json={"accepted": True})
    assert resp.status_code == 200, resp.text
    seeded.expire_all()
    habit = seeded.scalar(select(Habit).where(Habit.household_id == hh, Habit.device_slug == DEMO_DEVICE_SLUG))
    assert habit is not None and habit.hour == 22


# --- TC-09 ------------------------------------------------------------------------
@_blocked_by("13", "khi tải cao, lệnh mới bị bỏ im lặng, không giải thích")
async def test_tc09_tai_dien_cao(client, login):
    bo = await login("bo")
    for message in ("bật điều hoà phòng khách", "bật điều hoà phòng con", "bật bình nóng lạnh", "bật máy rửa bát"):
        await _send(client, bo, message)
    resp = await _send(client, bo, "Bật robot hút bụi.")
    robot_on = (await _state(client, bo, "robot_hut_bui"))["power"] == "on"
    explained = any(word in resp["response_vi"].lower() for word in ("điện", "tải", "công suất"))
    assert robot_on or explained, resp["response_vi"]


# --- TC-10 ------------------------------------------------------------------------
async def test_tc10_bui_cao_tu_dong_dong_cua_bat_may_loc(seeded, monkeypatch, _automation_isolated):
    monkeypatch.setenv("ENVIRONMENT_REALTIME_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT_AUTO_EXECUTE", "true")
    get_settings.cache_clear()
    window = seeded.scalar(select(Device).where(Device.slug == "cua_so_phong_khach"))
    purifier = seeded.scalar(select(Device).where(Device.slug == "may_loc_phong_khach"))
    window.state = {**(window.state or {}), "position": 100}
    purifier.state = {**(purifier.state or {}), "power": "off"}
    seeded.commit()

    await AutomationEngine(environment_tool=_StaticEnvironmentTool(_snapshot(pm25_ugm3=82.0))).run_once()

    seeded.expire_all()
    assert seeded.scalar(select(Device).where(Device.slug == "cua_so_phong_khach")).state["position"] == 0
    assert seeded.scalar(select(Device).where(Device.slug == "may_loc_phong_khach")).state["power"] == "on"


# --- TC-11 ------------------------------------------------------------------------
async def test_tc11_thiet_bi_khong_co(client, login):
    bo = await login("bo")
    resp = await _send(client, bo, "Bật quạt trần.")
    assert resp["plan"] == []
    assert "không có" in resp["response_vi"].lower()


# --- TC-12 ------------------------------------------------------------------------
@_blocked_by("02", "“mười phút nữa tắt đèn” tắt đèn ngay")
async def test_tc12_lenh_co_moc_thoi_gian_khong_chay_ngay(client, login):
    bo = await login("bo")
    await _send(client, bo, "Bật đèn phòng khách.")
    resp = await _send(client, bo, "Mười phút nữa tắt đèn phòng khách.")
    assert resp["plan"] == []
    assert (await _state(client, bo, "den_chum_phong_khach"))["power"] == "on"


# --- TC-13 ------------------------------------------------------------------------
@_blocked_by("03", "agent không nhận thiết bị thêm qua giao diện")
async def test_tc13_thiet_bi_moi_dieu_khien_duoc(client, login):
    bo = await login("bo")
    rooms = (await client.get("/api/v1/rooms", headers=bo)).json()
    living = next(r["id"] for r in rooms if r["name"] == "Phòng khách")
    created = await client.post(
        "/api/v1/devices", headers=bo, json={"device_type": "light", "name": "Đèn cầu thang", "room_id": living}
    )
    assert created.status_code == 201, created.text
    slug = created.json()["slug"]

    await _send(client, bo, "Bật đèn cầu thang.")
    assert (await _state(client, bo, slug))["power"] == "on"
