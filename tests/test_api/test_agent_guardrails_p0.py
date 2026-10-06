"""P0 AI-agent guardrails: stale chat approvals and suggestion HITL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from src.domain.enums import AccessEffect, ActionStatus, SuggestionKind
from src.domain.models import AccessRule, Approval, AuditEnvelope, Device, User
from src.services.automation import Suggestion, registry

_AC = "dieu_hoa_phong_khach"


@pytest.fixture(autouse=True)
def clear_suggestion_registry():
    registry.clear()
    yield
    registry.clear()


async def _send(
    client: AsyncClient,
    headers: dict,
    message: str,
    *,
    conversation_id: str = "",
) -> dict:
    response = await client.post(
        "/api/v1/agent/command",
        headers=headers,
        json={"message": message, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _ac_power(client: AsyncClient, headers: dict) -> str:
    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    return next(device for device in dashboard["devices"] if device["slug"] == _AC)["state"]["power"]


async def _pending_ac_approval(client, login, grant_access) -> tuple[dict, dict, dict]:
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    member_headers = await login("con_lon")
    body = await _send(client, member_headers, "đặt điều hoà phòng khách 24 độ")
    assert body["pending_approval"]
    owner_headers = await login("bo")
    return body, member_headers, owner_headers


async def test_chat_confirmation_rejects_expired_approval(client, login, grant_access, seeded):
    body, member_headers, owner_headers = await _pending_ac_approval(client, login, grant_access)
    approval = seeded.get(Approval, body["pending_approval"]["id"])
    approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    seeded.commit()

    resumed = await _send(
        client,
        owner_headers,
        "đồng ý",
        conversation_id=body["conversation_id"],
    )

    assert "hết hạn" in resumed["response_vi"].lower()
    assert await _ac_power(client, member_headers) == "off"
    seeded.expire_all()
    assert seeded.get(Approval, approval.id).status == ActionStatus.EXPIRED


async def test_chat_confirmation_rechecks_revoked_permission(client, login, grant_access, seeded):
    body, member_headers, owner_headers = await _pending_ac_approval(client, login, grant_access)
    requester = seeded.scalar(select(User).where(User.username == "con_lon"))
    device = seeded.scalar(select(Device).where(Device.slug == _AC))
    seeded.execute(
        delete(AccessRule).where(
            AccessRule.user_id == requester.id,
            AccessRule.device_id == device.id,
        )
    )
    seeded.commit()

    resumed = await _send(
        client,
        owner_headers,
        "đồng ý",
        conversation_id=body["conversation_id"],
    )

    assert "không thể thực hiện" in resumed["response_vi"].lower()
    assert await _ac_power(client, member_headers) == "off"
    seeded.expire_all()
    approval = seeded.get(Approval, body["pending_approval"]["id"])
    assert approval.status == ActionStatus.EXPIRED


async def test_chat_confirmation_rechecks_child_lock(client, login, grant_access, seeded):
    body, member_headers, owner_headers = await _pending_ac_approval(client, login, grant_access)
    enabled = await client.put(
        "/api/v1/household/child-lock",
        headers=owner_headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200

    resumed = await _send(
        client,
        owner_headers,
        "đồng ý",
        conversation_id=body["conversation_id"],
    )

    assert "hết hạn" in resumed["response_vi"].lower()
    assert await _ac_power(client, member_headers) == "off"
    seeded.expire_all()
    approval = seeded.get(Approval, body["pending_approval"]["id"])
    assert approval.status == ActionStatus.EXPIRED


async def test_suggestion_request_permission_creates_approval_instead_of_executing(
    client,
    login,
    grant_access,
    seeded,
):
    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    member = seeded.scalar(select(User).where(User.username == "con_lon"))
    suggestion = Suggestion(
        id="guardrail-request",
        household_id=member.household_id,
        kind=SuggestionKind.HABIT,
        title_vi="Tới giờ làm mát",
        message_vi="Bật điều hoà theo thói quen?",
        user_id=member.id,
        steps=[{"device_slug": _AC, "action": "turn_on", "params": {}}],
    )
    registry.add(suggestion)

    member_headers = await login("con_lon")
    response = await client.post(
        f"/api/v1/agent/suggestions/{suggestion.id}",
        headers=member_headers,
        json={"accepted": True},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["requires_approval"] is True
    assert payload["approval_id"]
    assert await _ac_power(client, member_headers) == "off"

    seeded.expire_all()
    approval = seeded.get(Approval, payload["approval_id"])
    assert approval.status == ActionStatus.PENDING_APPROVAL
    assert approval.source == "suggestion"

    owner_headers = await login("bo")
    approved = await client.post(
        f"/api/v1/agent/approvals/{approval.id}",
        headers=owner_headers,
        json={"approved": True},
    )
    assert approved.status_code == 200, approved.text
    assert await _ac_power(client, member_headers) == "on"
    seeded.expire_all()
    audit = seeded.scalars(select(AuditEnvelope).where(AuditEnvelope.plan_id == str(approval.id))).first()
    assert audit.envelope["source"] == "approval"
    assert audit.envelope["origin_source"] == "suggestion"
    assert audit.envelope["approval_id"] == approval.id


async def test_suggestion_accepted_permission_still_executes_immediately(client, login, grant_access, seeded):
    grant_access("con_lon", _AC, effect=AccessEffect.ACCEPTED)
    member = seeded.scalar(select(User).where(User.username == "con_lon"))
    suggestion = Suggestion(
        id="guardrail-accepted",
        household_id=member.household_id,
        kind=SuggestionKind.HABIT,
        title_vi="Tới giờ làm mát",
        message_vi="Bật điều hoà theo thói quen?",
        user_id=member.id,
        steps=[{"device_slug": _AC, "action": "turn_on", "params": {}}],
    )
    registry.add(suggestion)

    member_headers = await login("con_lon")
    response = await client.post(
        f"/api/v1/agent/suggestions/{suggestion.id}",
        headers=member_headers,
        json={"accepted": True},
    )

    assert response.status_code == 200, response.text
    assert response.json().get("requires_approval", False) is False
    assert await _ac_power(client, member_headers) == "on"
    seeded.expire_all()
    audit = seeded.scalars(
        select(AuditEnvelope).where(AuditEnvelope.input_text == suggestion.title_vi)
    ).first()
    assert audit.envelope["source"] == "suggestion"
    assert audit.envelope["origin_source"] == "suggestion"
