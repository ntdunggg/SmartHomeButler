"""Trust-boundary regressions for agent step execution."""

from __future__ import annotations

from sqlalchemy import select

from src.domain.models import Device, User
from src.iot.factory import get_bus
from src.services.agent_execution import AgentExecutionService, ExecutionContext


async def _execute_steps(seeded, steps, *, household_id, command, conversation_id):
    owner = seeded.scalar(select(User).where(User.household_id == household_id, User.username == "bo"))
    service = AgentExecutionService()
    actions = service.normalize_actions(steps)
    result = await service.execute(
        seeded,
        actions=actions,
        context=ExecutionContext(
            household_id=household_id,
            requester_id=owner.id,
            actor_id=owner.id,
            source="chat",
            command_text=command,
            conversation_id=conversation_id,
            original_plan=steps,
        ),
    )
    return result.legacy_steps(actions)


async def test_executor_ignores_stale_planning_noop_flag(seeded):
    """A stale ``skipped=True`` must not suppress a command against fresh live state."""
    lock = seeded.scalar(select(Device).where(Device.slug == "khoa_cua_chinh"))
    assert lock is not None and lock.state["locked"] is True

    result = await _execute_steps(
        seeded,
        [
            {
                "device_slug": lock.slug,
                "device_name": lock.name,
                "action": "unlock",
                "params": {},
                "allowed": True,
                "skipped": True,  # stale snapshot from planning
            }
        ],
        household_id=lock.household_id,
        command="mở cửa chính",
        conversation_id="stale-planning-noop",
    )

    assert result[0]["status"] == "executed"
    assert result[0]["skipped"] is False
    assert result[0]["state_after"]["locked"] is False
    assert "mở khóa" in result[0]["detail_vi"]
    assert (await get_bus().get_state(lock.household_id, lock.slug))["locked"] is False


async def test_live_noop_reports_exact_lock_state(seeded):
    """No-op response carries live state and says unlocked, not physically open."""
    lock = seeded.scalar(select(Device).where(Device.slug == "khoa_cua_chinh"))
    assert lock is not None
    lock.state = {"locked": False}
    seeded.commit()

    result = await _execute_steps(
        seeded,
        [
            {
                "device_slug": lock.slug,
                "device_name": lock.name,
                "action": "unlock",
                "params": {},
                "allowed": True,
                "skipped": False,
            }
        ],
        household_id=lock.household_id,
        command="mở cửa chính",
        conversation_id="live-unlocked-noop",
    )

    assert result[0]["skipped"] is True
    assert result[0]["state_after"] == {"locked": False}
    assert result[0]["detail_vi"] == "Cửa ra vào chính đã mở khóa, không cần đổi."
