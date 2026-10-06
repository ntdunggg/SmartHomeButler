"""P1 acceptance tests for the single AI-agent execution boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update

from src.domain.enums import AccessEffect, ActionStatus, SensorType
from src.domain.models import AccessRule, Approval, AuditEnvelope, Device, Household, Sensor, User
from src.iot import factory
from src.services import agent_runner
from src.services.agent_execution import (
    AgentActionRequest,
    AgentExecutionService,
    ExecutionContext,
)

_LIGHT = "den_chum_phong_khach"
_AC = "dieu_hoa_phong_khach"
_LOCK = "khoa_cua_chinh"


class CountingBus:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.commands: list[tuple[int, str, dict]] = []

    async def get_state(self, household_id: int, slug: str) -> dict:
        return await self.delegate.get_state(household_id, slug)

    async def send_command(self, household_id: int, slug: str, payload: dict):
        self.commands.append((household_id, slug, dict(payload)))
        return await self.delegate.send_command(household_id, slug, payload)


def _user(session, username: str) -> User:
    return session.scalar(select(User).where(User.username == username))


def _request(slug: str, action: str, params: dict | None = None, **extra) -> AgentActionRequest:
    return AgentActionRequest(
        device_slug=slug,
        action=action,
        params=params or {},
        original={"device_slug": slug, "action": action, "params": params or {}},
        **extra,
    )


def _context(user: User, *, source: str = "chat", approval_id: int | None = None) -> ExecutionContext:
    return ExecutionContext(
        household_id=user.household_id,
        requester_id=user.id,
        actor_id=user.id,
        source=source,
        command_text="P1 test command",
        conversation_id="p1-test",
        approval_id=approval_id,
    )


async def test_normal_chat_execution_returns_structured_verified_result(seeded):
    owner = _user(seeded, "bo")
    counting = CountingBus(factory.get_bus())
    service = AgentExecutionService(counting)

    result = await service.execute(
        seeded,
        actions=[_request(_LIGHT, "turn_on")],
        context=_context(owner),
    )

    assert result.execution_id.startswith("exec_")
    assert result.status == "SUCCESS"
    assert result.actions[0].reason_code == "EXECUTED"
    assert result.actions[0].state_before["power"] == "off"
    assert result.actions[0].state_after["power"] == "on"
    assert len(counting.commands) == 1


async def test_permission_revoked_immediately_before_dispatch_is_denied(seeded, grant_access):
    grant_access("con_lon", _AC, effect=AccessEffect.ACCEPTED)
    member = _user(seeded, "con_lon")
    device = seeded.scalar(select(Device).where(Device.slug == _AC))
    seeded.execute(delete(AccessRule).where(AccessRule.user_id == member.id, AccessRule.device_id == device.id))
    seeded.commit()
    counting = CountingBus(factory.get_bus())

    result = await AgentExecutionService(counting).execute(
        seeded,
        actions=[_request(_AC, "turn_on")],
        context=_context(member),
    )

    assert result.status == "DENIED"
    assert result.actions[0].reason_code == "PERMISSION_DENIED"
    assert counting.commands == []


async def test_child_lock_enabled_immediately_before_dispatch_is_denied(seeded, grant_access):
    grant_access("con_lon", _AC, effect=AccessEffect.ACCEPTED)
    member = _user(seeded, "con_lon")
    household = seeded.get(Household, member.household_id)
    household.child_lock_enabled = True
    seeded.commit()
    counting = CountingBus(factory.get_bus())

    result = await AgentExecutionService(counting).execute(
        seeded,
        actions=[_request(_AC, "turn_on")],
        context=_context(member),
    )

    assert result.status == "DENIED"
    assert result.actions[0].reason_code == "CHILD_LOCKED"
    assert counting.commands == []


@pytest.mark.parametrize("mutation, reason_code", [("deleted", "DEVICE_NOT_FOUND"), ("offline", "DEVICE_OFFLINE")])
async def test_deleted_or_offline_device_fails_before_dispatch(seeded, mutation, reason_code):
    owner = _user(seeded, "bo")
    device = seeded.scalar(select(Device).where(Device.slug == _LIGHT))
    if mutation == "deleted":
        seeded.delete(device)
    else:
        device.online = False
    seeded.commit()
    counting = CountingBus(factory.get_bus())

    result = await AgentExecutionService(counting).execute(
        seeded,
        actions=[_request(_LIGHT, "turn_on")],
        context=_context(owner),
    )

    assert result.actions[0].reason_code == reason_code
    assert counting.commands == []


@pytest.mark.parametrize(
    ("action", "reason_code"),
    [("teleport", "ACTION_NOT_SUPPORTED"), ("set_temperature", "CAPABILITY_NOT_SUPPORTED")],
)
async def test_action_or_capability_no_longer_valid_is_rejected(seeded, action, reason_code):
    owner = _user(seeded, "bo")
    counting = CountingBus(factory.get_bus())

    result = await AgentExecutionService(counting).execute(
        seeded,
        actions=[_request(_LIGHT, action, {"temperature": 24} if action == "set_temperature" else {})],
        context=_context(owner),
    )

    assert result.actions[0].reason_code == reason_code
    assert counting.commands == []


async def test_out_of_range_value_is_rejected_before_dispatch(seeded):
    owner = _user(seeded, "bo")
    counting = CountingBus(factory.get_bus())

    result = await AgentExecutionService(counting).execute(
        seeded,
        actions=[_request(_AC, "set_temperature", {"temperature": 99})],
        context=_context(owner),
    )

    assert result.actions[0].reason_code == "TARGET_OUT_OF_RANGE"
    assert counting.commands == []


async def test_live_state_changed_to_noop_does_not_dispatch(seeded):
    owner = _user(seeded, "bo")
    counting = CountingBus(factory.get_bus())

    result = await AgentExecutionService(counting).execute(
        seeded,
        actions=[_request(_LIGHT, "turn_off")],
        context=_context(owner),
    )

    assert result.status == "NO_OP"
    assert result.actions[0].reason_code == "ALREADY_IN_DESIRED_STATE"
    assert result.actions[0].state_before == result.actions[0].state_after
    assert counting.commands == []


async def test_new_blocking_conflict_after_approval_requires_fresh_approval(seeded):
    owner = _user(seeded, "bo")
    approval = Approval(
        household_id=owner.household_id,
        requested_by_id=owner.id,
        conversation_id="conflict-after-wait",
        command_text="mở khóa cửa chính",
        steps=[{"device_slug": _LOCK, "action": "unlock", "params": {}, "allowed": True}],
        status=ActionStatus.PENDING_APPROVAL,
        required_role="owner",
        source="chat",
    )
    seeded.add(approval)
    seeded.commit()
    seeded.refresh(approval)
    seeded.execute(
        update(Sensor)
        .where(Sensor.household_id == owner.household_id, Sensor.sensor_type == SensorType.PRESENCE)
        .values(value=0)
    )
    seeded.commit()
    counting = CountingBus(factory.get_bus())
    factory.set_bus(counting)

    resumed = await agent_runner.resume_after_approval(
        seeded,
        approval=approval,
        approved=True,
        actor_id=owner.id,
    )

    execution = resumed["state"]["diagnostics"]["execution_result"]
    assert execution["status"] == "DENIED"
    assert execution["actions"][0]["reason_code"] == "FRESH_APPROVAL_REQUIRED"
    seeded.expire_all()
    assert seeded.get(Approval, approval.id).status == ActionStatus.FAILED
    assert counting.commands == []


async def test_concurrent_approval_requests_dispatch_exactly_once(client, login, grant_access, seeded):
    from src.iot.memory_bus import InMemoryBus

    grant_access("con_lon", _AC, effect=AccessEffect.REQUEST)
    member_headers = await login("con_lon")
    pending = await client.post(
        "/api/v1/agent/command",
        headers=member_headers,
        json={"message": "đặt điều hoà phòng khách 24 độ", "conversation_id": "p1-race"},
    )
    approval_id = pending.json()["pending_approval"]["id"]
    owner_headers = await login("bo")
    counting = CountingBus(InMemoryBus(latency=0.08))
    factory.set_bus(counting)

    first, second = await asyncio.gather(
        client.post(f"/api/v1/agent/approvals/{approval_id}", headers=owner_headers, json={"approved": True}),
        client.post(f"/api/v1/agent/approvals/{approval_id}", headers=owner_headers, json={"approved": True}),
    )

    assert first.status_code == second.status_code == 200
    assert len(counting.commands) == 1
    messages = (first.json()["response_vi"].lower(), second.json()["response_vi"].lower())
    assert any("đang được xử lý" in message or "đã được xử lý" in message for message in messages)
    seeded.expire_all()
    assert seeded.get(Approval, approval_id).status == ActionStatus.APPROVED


async def test_every_execution_outcome_has_audit(seeded):
    owner = _user(seeded, "bo")
    service = AgentExecutionService()
    cases = [
        _request(_LIGHT, "turn_off"),
        _request(_LIGHT, "turn_on"),
        _request("missing_device", "turn_on"),
        _request(_LIGHT, "turn_on", explicit_constraints=[f"avoid:{_LIGHT}"]),
    ]
    statuses = []
    execution_ids = []
    for request in cases:
        result = await service.execute(seeded, actions=[request], context=_context(owner))
        statuses.append(result.status)
        execution_ids.append(result.execution_id)

    envelopes = list(seeded.scalars(select(AuditEnvelope)))
    audited_ids = {entry.envelope.get("execution_id") for entry in envelopes}
    assert {"NO_OP", "SUCCESS", "FAILED", "DENIED"} <= set(statuses)
    assert set(execution_ids) <= audited_ids
    for entry in envelopes:
        if entry.envelope.get("execution_id") in execution_ids:
            assert entry.envelope["actor_id"] == owner.id
            assert entry.envelope["requester_id"] == owner.id
            assert entry.envelope["source"] == "chat"
            assert entry.envelope["execution_result"]["actions"][0]["reason_code"]


def test_static_ai_routes_do_not_call_device_bus_directly():
    root = Path(__file__).resolve().parents[2]
    route_files = [root / "src/api/agent.py", root / "src/services/agent_runner.py"]
    offenders = [str(path.relative_to(root)) for path in route_files if ".send_command(" in path.read_text(encoding="utf-8")]
    assert offenders == []
    boundary = (root / "src/services/agent_execution.py").read_text(encoding="utf-8")
    assert boundary.count(".send_command(") == 1
