"""Vòng đời yêu cầu duyệt: hết hạn, idempotent, recheck quyền lúc duyệt (Mục 6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from src.domain.enums import AccessEffect
from src.domain.models import ActionLog, Approval

_AC = "dieu_hoa_phong_khach"  # high_power
# Trạng thái do tầng THỰC THI ghi, không thuộc vòng đời DUYỆT.
_EXECUTION_OUTCOMES = {"executed", "failed", "no_op", "blocked_by_conflict"}


async def _send(client: AsyncClient, headers: dict, message: str) -> dict:
    resp = await client.post("/api/v1/agent/command", headers=headers, json={"message": message})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _ac_power(client: AsyncClient, headers: dict) -> str:
    dash = (await client.get("/api/v1/dashboard", headers=headers)).json()
    return next(d for d in dash["devices"] if d["slug"] == _AC)["state"]["power"]


async def _history_for_approval(client: AsyncClient, headers: dict, approval_id: int) -> list[dict]:
    history = (await client.get("/api/v1/history", headers=headers)).json()
    return [log for log in history if log["approval_id"] == approval_id]


async def _assert_history_status(client: AsyncClient, headers: dict, approval_id: int, status: str) -> None:
    """Log của CHÍNH yêu cầu duyệt phải mang kết cục của yêu cầu đó.

    Không khẳng định trên MỌI log gắn approval_id: tầng execution (agent_execution) cũng gắn
    approval_id cho log thực thi để truy vết được một yêu cầu đã chạy ra những gì, và log đó
    mang trạng thái THỰC THI ("executed"/"failed") — khác trạng thái DUYỆT. Trộn hai thứ vào
    một khẳng định thì hoặc mất truy vết, hoặc đọc sai ý nghĩa của cột status.
    """
    linked_history = await _history_for_approval(client, headers, approval_id)
    assert linked_history
    lifecycle_logs = [log for log in linked_history if log["status"] not in _EXECUTION_OUTCOMES]
    assert lifecycle_logs, "phải còn log theo vòng đời duyệt"
    assert all(log["status"] == status for log in lifecycle_logs)


async def test_yeu_cau_qua_han_khong_thuc_thi(client: AsyncClient, login, grant_access, seeded):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con = await login("con_lon")
    approval = (await _send(client, con, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    assert approval

    # Ép yêu cầu hết hạn.
    ap = seeded.get(Approval, approval["id"])
    ap.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    seeded.commit()

    bo = await login("bo")
    resp = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    assert resp.status_code == 200
    assert "hết hạn" in resp.json()["response_vi"].lower()
    assert await _ac_power(client, bo) == "off"  # không thực thi
    await _assert_history_status(client, con, approval["id"], "expired")


async def test_bat_khoa_vo_hieu_yeu_cau_cho_duyet(client: AsyncClient, login, grant_access):
    """Bật khoá trẻ em giữa lúc chờ → yêu cầu (thiết bị nhạy cảm) bị vô hiệu, không thực thi."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con = await login("con_lon")
    approval = (await _send(client, con, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    assert approval

    bo = await login("bo")
    await client.put("/api/v1/household/child-lock", headers=bo, json={"enabled": True})

    resp = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    assert resp.status_code == 200
    assert await _ac_power(client, bo) == "off"  # đã bị vô hiệu, không thực thi


async def test_recheck_thu_hoi_quyen_khi_duyet(client: AsyncClient, login, grant_access):
    """Quyền người yêu cầu bị thu hồi giữa lúc chờ → duyệt cũng KHÔNG thực thi (recheck)."""
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con = await login("con_lon")
    approval = (await _send(client, con, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    assert approval

    bo = await login("bo")
    members = (await client.get("/api/v1/members", headers=bo)).json()
    mid = next(m["id"] for m in members if m["username"] == "con_lon")
    # Chủ hộ thu hồi toàn bộ quyền của con.
    await client.put(f"/api/v1/members/{mid}/access", headers=bo, json={"rules": []})

    resp = await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    assert resp.status_code == 200
    assert "không thể thực hiện" in resp.json()["response_vi"].lower()
    assert await _ac_power(client, bo) == "off"
    await _assert_history_status(client, con, approval["id"], "expired")


async def test_duyet_tao_thong_bao_cho_nguoi_yeu_cau(client: AsyncClient, login, grant_access, seeded):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con = await login("con_lon")
    approval = (await _send(client, con, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    bo = await login("bo")
    await client.post(f"/api/v1/agent/approvals/{approval['id']}", headers=bo, json={"approved": True})
    resolved = seeded.get(Approval, approval["id"])
    assert str(resolved.status) == "approved"
    await _assert_history_status(client, con, approval["id"], "approved")
    history = (await client.get("/api/v1/history", headers=con)).json()
    assert any(
        log["status"] == "executed" and log["command_text"] == "đặt điều hoà phòng khách 24 độ" for log in history
    )
    # Con (người yêu cầu) nhận thông báo "approved".
    notes = (await client.get("/api/v1/notifications", headers=con)).json()
    assert any(n["notification_type"] == "approved" for n in notes)


async def test_history_log_lien_ket_approval_va_sync_moi_log_pending(client: AsyncClient, login, grant_access, seeded):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con = await login("con_lon")
    approval = (await _send(client, con, "đặt điều hoà phòng khách 24 độ"))["pending_approval"]
    approval_id = approval["id"]

    linked_logs = seeded.scalars(select(ActionLog).where(ActionLog.approval_id == approval_id)).all()
    assert linked_logs
    assert all(str(log.status) == "pending_approval" for log in linked_logs)

    history = (await client.get("/api/v1/history", headers=con)).json()
    assert any(log["approval_id"] == approval_id for log in history)

    seeded.add(
        ActionLog(
            household_id=linked_logs[0].household_id,
            user_id=linked_logs[0].user_id,
            device_id=linked_logs[0].device_id,
            conversation_id=linked_logs[0].conversation_id,
            approval_id=approval_id,
            command_text=linked_logs[0].command_text,
            action="request_approval",
            params={},
            status="pending",
            detail="legacy pending row",
            source=linked_logs[0].source,
        )
    )
    seeded.commit()

    bo = await login("bo")
    resp = await client.post(f"/api/v1/agent/approvals/{approval_id}", headers=bo, json={"approved": False})
    assert resp.status_code == 200

    synced_statuses = seeded.scalars(select(ActionLog.status).where(ActionLog.approval_id == approval_id)).all()
    assert synced_statuses
    assert all(str(status) == "rejected" for status in synced_statuses)

    history_after = (await client.get("/api/v1/history", headers=con)).json()
    linked_history = [log for log in history_after if log["approval_id"] == approval_id]
    assert linked_history
    assert all(log["status"] == "rejected" for log in linked_history)

    summary = (await client.get("/api/v1/history/summary", headers=con)).json()
    assert summary["pending_approval"] == 0
    assert summary["rejected"] == len(linked_history)


async def test_device_control_approval_log_lien_ket_va_sync_khi_tu_choi(
    client: AsyncClient, login, grant_access, seeded
):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    con = await login("con_lon")

    resp = await client.post(
        f"/api/v1/devices/{_AC}/control",
        headers=con,
        json={"action": "set_temperature", "params": {"temperature": 24}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["requires_approval"] is True
    approval_id = body["approval_id"]

    linked_logs = seeded.scalars(select(ActionLog).where(ActionLog.approval_id == approval_id)).all()
    assert linked_logs
    assert all(str(log.status) == "pending_approval" for log in linked_logs)
    await _assert_history_status(client, con, approval_id, "pending_approval")

    bo = await login("bo")
    reject = await client.post(f"/api/v1/agent/approvals/{approval_id}", headers=bo, json={"approved": False})
    assert reject.status_code == 200

    await _assert_history_status(client, con, approval_id, "rejected")
    summary = (await client.get("/api/v1/history/summary", headers=con)).json()
    assert summary["pending_approval"] == 0
