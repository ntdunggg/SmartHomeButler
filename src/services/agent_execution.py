"""Single trust boundary for every AI-generated physical device action.

Chat commands, resumed approvals, and accepted proactive suggestions all enter
this service immediately before dispatch.  Planning-time decisions are treated
as hints only: devices, permissions, policy, conflicts, and state are refreshed
and checked again for every attempt.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from src.agent.cognitive.ledger_updater import violates_constraint
from src.agent.harness.state_resolution import matches_expected_state, resolve_delta
from src.agent.planning.conflict import Conflict, PlanStep
from src.agent.planning.conflict import detect as detect_conflicts
from src.agent.schemas import RequirementLedger
from src.core.errors import SmartHomeError
from src.core.permissions import Decision, resolve_access
from src.domain.action_registry import (
    ALL_ACTIONS,
    capability_for_action,
    validate_action,
    validate_action_binding,
)
from src.domain.enums import ActionStatus
from src.domain.models import Approval, Device, User
from src.iot.factory import get_bus
from src.iot.registry import spec_from_device
from src.iot.simulator import apply_command
from src.services.audit import log_action, record_audit_envelope
from src.services.context import build_snapshot

ExecutionSource = Literal["chat", "approval", "suggestion"]
ExecutionStatus = Literal["SUCCESS", "PARTIAL_SUCCESS", "FAILED", "NO_OP", "DENIED"]
ActionResultStatus = Literal["SUCCESS", "FAILED", "NO_OP", "DENIED"]


class AgentActionRequest(BaseModel):
    """Normalized simulator-level action submitted to the P1 boundary."""

    model_config = ConfigDict(extra="forbid")

    device_slug: str = Field(min_length=1)
    action: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    capability: str | None = None
    reason_vi: str = ""
    explicit_constraints: list[str] = Field(default_factory=list)
    rejected_assumptions: list[str] = Field(default_factory=list)
    notify_owner: bool = False
    original: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_step(cls, step: dict[str, Any]) -> AgentActionRequest:
        return cls(
            device_slug=str(step.get("device_slug") or "").strip(),
            action=str(step.get("action") or "").strip().lower(),
            params=dict(step.get("params") or {}),
            capability=(str(step["capability"]) if step.get("capability") else None),
            reason_vi=str(step.get("reason_vi") or ""),
            explicit_constraints=list(step.get("explicit_constraints") or []),
            rejected_assumptions=list(step.get("rejected_assumptions") or []),
            notify_owner=bool(step.get("notify_owner")),
            original=dict(step),
        )


class ExecutionContext(BaseModel):
    """Identity, provenance, and approval evidence for one execution attempt."""

    model_config = ConfigDict(extra="forbid")

    household_id: int
    requester_id: int
    actor_id: int | None = None
    attribution_user_id: int | None = None
    source: ExecutionSource
    origin_source: str = ""
    command_text: str = ""
    conversation_id: str = ""
    approval_id: int | None = None
    original_plan: list[dict[str, Any]] = Field(default_factory=list)
    approved_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    audit_context: dict[str, Any] = Field(default_factory=dict)


class ActionExecutionResult(BaseModel):
    """Observable result of one normalized action."""

    model_config = ConfigDict(extra="forbid")

    device_slug: str
    status: ActionResultStatus
    reason_code: str
    detail_vi: str
    state_before: dict[str, Any] = Field(default_factory=dict)
    state_after: dict[str, Any] = Field(default_factory=dict)
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    authorization: dict[str, Any] = Field(default_factory=dict)
    policy_decision: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0


class AgentExecutionResult(BaseModel):
    """Unified P1 result returned by every AI physical-action attempt."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str
    status: ExecutionStatus
    actions: list[ActionExecutionResult] = Field(default_factory=list)

    def legacy_steps(self, requests: list[AgentActionRequest]) -> list[dict[str, Any]]:
        """Preserve the existing chat response shape while exposing P1 fields."""
        legacy: list[dict[str, Any]] = []
        status_map = {
            "SUCCESS": str(ActionStatus.EXECUTED),
            "NO_OP": str(ActionStatus.NO_OP),
            "FAILED": str(ActionStatus.FAILED),
            "DENIED": str(ActionStatus.DENIED),
        }
        for request, result in zip(requests, self.actions, strict=True):
            step = dict(request.original)
            step.update(
                {
                    "device_slug": result.device_slug,
                    "action": result.action,
                    "params": result.params,
                    "status": status_map[result.status],
                    "reason_code": result.reason_code,
                    "detail_vi": result.detail_vi,
                    "state_before": result.state_before,
                    "state_after": result.state_after,
                    "latency_ms": result.latency_ms,
                    "skipped": result.status == "NO_OP",
                }
            )
            legacy.append(step)
        return legacy


class ApprovalClaimCode(StrEnum):
    CLAIMED = "claimed"
    ALREADY_PROCESSING = "already_processing"
    ALREADY_RESOLVED = "already_resolved"


@dataclass(frozen=True, slots=True)
class ApprovalClaim:
    code: ApprovalClaimCode
    approval: Approval | None = None

    @property
    def claimed(self) -> bool:
        return self.code is ApprovalClaimCode.CLAIMED


def conflict_fingerprint(conflict: Mapping[str, Any]) -> str:
    """Stable identity for deciding whether an approval covered a conflict."""
    return "|".join(
        (
            str(conflict.get("type") or ""),
            str(conflict.get("device_slug") or ""),
            str(conflict.get("severity") or ""),
        )
    )


def blocking_conflict_evidence(conflicts: list[Conflict]) -> list[dict[str, Any]]:
    return [
        {**dict(conflict), "fingerprint": conflict_fingerprint(conflict)}
        for conflict in conflicts
        if conflict.get("blocking")
    ]


def _error_detail(exc: Exception) -> str:
    return str(getattr(exc, "message", "") or str(exc) or "Lỗi thiết bị không xác định.")


def _authorization_dict(decision: Decision) -> dict[str, Any]:
    return {
        "allowed": decision.allowed,
        "requires_approval": decision.requires_approval,
        "notify_owner": decision.notify_owner,
        "child_locked": decision.child_locked,
        "reason_vi": decision.reason_vi,
    }


def _constraint_action(action: str) -> str:
    if action.startswith("set_"):
        return "set"
    return action


def _aggregate_status(actions: list[ActionExecutionResult]) -> ExecutionStatus:
    statuses = {action.status for action in actions}
    if not actions or statuses == {"FAILED"}:
        return "FAILED"
    if statuses == {"DENIED"}:
        return "DENIED"
    if statuses == {"NO_OP"}:
        return "NO_OP"
    if statuses <= {"SUCCESS", "NO_OP"}:
        return "SUCCESS" if "SUCCESS" in statuses else "NO_OP"
    if statuses & {"SUCCESS", "NO_OP"}:
        return "PARTIAL_SUCCESS"
    return "FAILED" if "FAILED" in statuses else "DENIED"


def _noop_detail_vi(device_name: str, action: str) -> str:
    state = {
        "unlock": "đã mở khóa",
        "lock": "đã khóa",
        "open": "đã mở",
        "close": "đã đóng",
        "turn_on": "đã bật",
        "turn_off": "đã tắt",
    }.get(action, "đã ở đúng trạng thái")
    return f"{device_name} {state}, không cần đổi."


class AgentExecutionService:
    """Refresh/revalidate/dispatch/verify/audit boundary for AI actions."""

    def __init__(self, bus: Any | None = None) -> None:
        self._bus = bus

    @property
    def bus(self) -> Any:
        return self._bus or get_bus()

    @staticmethod
    def normalize_actions(steps: list[dict[str, Any]]) -> list[AgentActionRequest]:
        return [AgentActionRequest.from_step(step) for step in steps]

    @staticmethod
    def claim_approval(session: Session, *, approval_id: int, actor_id: int) -> ApprovalClaim:
        """Atomically claim PENDING_APPROVAL -> EXECUTING before any dispatch."""
        claimed = session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ActionStatus.PENDING_APPROVAL)
            .values(status=ActionStatus.EXECUTING, resolved_by_id=actor_id)
            .execution_options(synchronize_session=False)
        )
        session.commit()
        session.expire_all()
        approval = session.get(Approval, approval_id)
        if cast("CursorResult[Any]", claimed).rowcount == 1:
            return ApprovalClaim(ApprovalClaimCode.CLAIMED, approval)
        if approval is not None and approval.status == ActionStatus.EXECUTING:
            return ApprovalClaim(ApprovalClaimCode.ALREADY_PROCESSING, approval)
        return ApprovalClaim(ApprovalClaimCode.ALREADY_RESOLVED, approval)

    @staticmethod
    def reject_approval(session: Session, *, approval_id: int, actor_id: int) -> ApprovalClaimCode:
        rejected = session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ActionStatus.PENDING_APPROVAL)
            .values(
                status=ActionStatus.REJECTED,
                resolved_by_id=actor_id,
                resolved_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        session.commit()
        if cast("CursorResult[Any]", rejected).rowcount == 1:
            return ApprovalClaimCode.CLAIMED
        session.expire_all()
        approval = session.get(Approval, approval_id)
        if approval is not None and approval.status == ActionStatus.EXECUTING:
            return ApprovalClaimCode.ALREADY_PROCESSING
        return ApprovalClaimCode.ALREADY_RESOLVED

    @staticmethod
    def finalize_approval(session: Session, *, approval_id: int, result: AgentExecutionResult) -> None:
        final_status = (
            ActionStatus.APPROVED
            if result.status in {"SUCCESS", "PARTIAL_SUCCESS", "NO_OP"}
            else ActionStatus.FAILED
        )
        session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ActionStatus.EXECUTING)
            .values(status=final_status, resolved_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        session.commit()
        session.expire_all()

    async def execute(
        self,
        session: Session,
        *,
        actions: list[AgentActionRequest],
        context: ExecutionContext,
    ) -> AgentExecutionResult:
        execution_id = f"exec_{uuid.uuid4().hex[:12]}"
        results: list[ActionExecutionResult | None] = [None] * len(actions)
        prepared: list[tuple[int, AgentActionRequest]] = []

        # Normalize -> validate device/capability/action -> authorize again.
        for index, request in enumerate(actions):
            session.expire_all()
            requester = session.get(User, context.requester_id)
            device = session.scalar(
                select(Device).where(
                    Device.household_id == context.household_id,
                    Device.slug == request.device_slug,
                )
            )
            if requester is None or requester.household_id != context.household_id:
                results[index] = self._result(request, "DENIED", "REQUESTER_NOT_FOUND", "Không tìm thấy người yêu cầu.")
                continue
            if device is None:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "DEVICE_NOT_FOUND",
                    f"Không tìm thấy thiết bị '{request.device_slug}' trong hộ này.",
                )
                continue
            state = dict(device.state or {})
            if not device.online:
                results[index] = self._result(
                    request, "FAILED", "DEVICE_OFFLINE", f"{device.name} đang offline, không thực hiện.", state
                )
                continue
            required = capability_for_action(request.action)
            binding_issue = validate_action_binding(
                request.action,
                capabilities=device.capabilities or [],
                device_type=device.device_type,
                capability_hint=request.capability,
            )
            if request.action not in ALL_ACTIONS or required is None or binding_issue is not None:
                code = binding_issue.code if binding_issue is not None else "ACTION_NOT_SUPPORTED"
                detail = binding_issue.message_vi if binding_issue is not None else f"Hành động '{request.action}' không hợp lệ."
                results[index] = self._result(
                    request, "FAILED", code, detail, state
                )
                continue
            if "delta" not in request.params:
                contract = validate_action(
                    request.action,
                    request.params,
                    capabilities=device.capabilities or [],
                    device_type=device.device_type,
                    capability_hint=request.capability,
                )
                if not contract.ok:
                    assert contract.issue is not None
                    results[index] = self._result(
                        request, "FAILED", contract.issue.code, contract.issue.message_vi, state
                    )
                    continue
            violation = violates_constraint(
                RequirementLedger(
                    constraints=request.explicit_constraints,
                    rejected_assumptions=request.rejected_assumptions,
                ),
                device_id=request.device_slug,
                action=_constraint_action(request.action),
            )
            if violation:
                results[index] = self._result(
                    request,
                    "DENIED",
                    "EXPLICIT_CONSTRAINT_VIOLATION",
                    f"Vi phạm ràng buộc người dùng: {violation}.",
                    state,
                    policy={"decision": "REJECT", "priority": "P2_explicit_constraint"},
                )
                continue
            decision = resolve_access(session, user=requester, device=device)
            authorization = _authorization_dict(decision)
            if not decision.allowed:
                code = "CHILD_LOCKED" if decision.child_locked else "PERMISSION_DENIED"
                results[index] = self._result(
                    request, "DENIED", code, decision.reason_vi, state, authorization=authorization
                )
                continue
            if decision.requires_approval and context.source != "approval":
                results[index] = self._result(
                    request,
                    "DENIED",
                    "FRESH_APPROVAL_REQUIRED",
                    decision.reason_vi,
                    state,
                    authorization=authorization,
                )
                continue
            prepared.append((index, request))

        # Hard policy + current conflict check.  An approval may cover only the
        # exact blocking conflict fingerprints stored when it was created.
        if prepared:
            session.expire_all()
            requester = session.get(User, context.requester_id)
            actor_name = requester.full_name or requester.username if requester is not None else ""
            snapshot = build_snapshot(session, household_id=context.household_id, actor_name=actor_name)
            plan: list[PlanStep] = [
                {"device_slug": request.device_slug, "action": request.action, "params": dict(request.params)}
                for _, request in prepared
            ]
            blocking = blocking_conflict_evidence(detect_conflicts(plan, snapshot))
            approved = {
                str(item.get("fingerprint") or conflict_fingerprint(item)) for item in context.approved_conflicts
            }
            new_blocking = [item for item in blocking if item["fingerprint"] not in approved]
            if new_blocking:
                detail = " ".join(str(item.get("message_vi") or "") for item in new_blocking).strip()
                for index, request in prepared:
                    results[index] = self._result(
                        request,
                        "DENIED",
                        "FRESH_APPROVAL_REQUIRED",
                        detail or "Xuất hiện xung đột blocking mới; cần duyệt lại.",
                        policy={
                            "decision": "REJECT",
                            "priority": "P0_hard_safety",
                            "conflicts": new_blocking,
                        },
                    )
                prepared = []

        # Refresh live state -> reground -> revalidate -> execute -> verify.
        for index, request in prepared:
            started = time.perf_counter()
            session.expire_all()
            requester = session.get(User, context.requester_id)
            device = session.scalar(
                select(Device).where(
                    Device.household_id == context.household_id,
                    Device.slug == request.device_slug,
                )
            )
            if requester is None or requester.household_id != context.household_id:
                results[index] = self._result(request, "DENIED", "REQUESTER_NOT_FOUND", "Không tìm thấy người yêu cầu.")
                continue
            if device is None:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "DEVICE_NOT_FOUND",
                    f"Không tìm thấy thiết bị '{request.device_slug}' trong hộ này.",
                )
                continue
            if not device.online:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "DEVICE_OFFLINE",
                    f"{device.name} đang offline, không thực hiện.",
                    dict(device.state or {}),
                )
                continue
            decision = resolve_access(session, user=requester, device=device)
            authorization = _authorization_dict(decision)
            if not decision.allowed:
                code = "CHILD_LOCKED" if decision.child_locked else "PERMISSION_DENIED"
                results[index] = self._result(
                    request, "DENIED", code, decision.reason_vi, dict(device.state or {}), authorization=authorization
                )
                continue
            if decision.requires_approval and context.source != "approval":
                results[index] = self._result(
                    request,
                    "DENIED",
                    "FRESH_APPROVAL_REQUIRED",
                    decision.reason_vi,
                    dict(device.state or {}),
                    authorization=authorization,
                )
                continue

            try:
                live = dict(await self.bus.get_state(context.household_id, request.device_slug))
            except Exception as exc:
                results[index] = self._result(
                    request, "FAILED", "LIVE_STATE_UNAVAILABLE", _error_detail(exc), authorization=authorization
                )
                continue

            params = resolve_delta(
                request.action,
                dict(request.params),
                live,
                device_type=device.device_type,
            )

            # Revalidate once more after the live-state refresh.  This closes the
            # wait window of a remote bus read: permissions, child lock, device
            # membership/online status, capabilities, and conflicts may all have
            # changed while refresh was in flight.
            session.expire_all()
            requester = session.get(User, context.requester_id)
            device = session.scalar(
                select(Device).where(
                    Device.household_id == context.household_id,
                    Device.slug == request.device_slug,
                )
            )
            if requester is None or requester.household_id != context.household_id:
                results[index] = self._result(
                    request, "DENIED", "REQUESTER_NOT_FOUND", "Không tìm thấy người yêu cầu.", live, params=params
                )
                continue
            if device is None:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "DEVICE_NOT_FOUND",
                    f"Không tìm thấy thiết bị '{request.device_slug}' trong hộ này.",
                    live,
                    params=params,
                )
                continue
            if not device.online:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "DEVICE_OFFLINE",
                    f"{device.name} đang offline, không thực hiện.",
                    live,
                    params=params,
                )
                continue
            contract = validate_action(
                request.action,
                params,
                capabilities=device.capabilities or [],
                device_type=device.device_type,
                capability_hint=request.capability,
            )
            if not contract.ok:
                assert contract.issue is not None
                results[index] = self._result(
                    request,
                    "FAILED",
                    contract.issue.code,
                    contract.issue.message_vi,
                    live,
                    params=params,
                )
                continue
            params = contract.params
            decision = resolve_access(session, user=requester, device=device)
            authorization = _authorization_dict(decision)
            if not decision.allowed:
                code = "CHILD_LOCKED" if decision.child_locked else "PERMISSION_DENIED"
                results[index] = self._result(
                    request, "DENIED", code, decision.reason_vi, live, params=params, authorization=authorization
                )
                continue
            if decision.requires_approval and context.source != "approval":
                results[index] = self._result(
                    request,
                    "DENIED",
                    "FRESH_APPROVAL_REQUIRED",
                    decision.reason_vi,
                    live,
                    params=params,
                    authorization=authorization,
                )
                continue
            snapshot = build_snapshot(
                session,
                household_id=context.household_id,
                actor_name=requester.full_name or requester.username,
            )
            snapshot.device_states[request.device_slug] = dict(live)
            current_plan: list[PlanStep] = [
                {"device_slug": request.device_slug, "action": request.action, "params": params}
            ]
            current_blocking = blocking_conflict_evidence(detect_conflicts(current_plan, snapshot))
            approved = {
                str(item.get("fingerprint") or conflict_fingerprint(item)) for item in context.approved_conflicts
            }
            new_blocking = [item for item in current_blocking if item["fingerprint"] not in approved]
            if new_blocking:
                results[index] = self._result(
                    request,
                    "DENIED",
                    "FRESH_APPROVAL_REQUIRED",
                    " ".join(str(item.get("message_vi") or "") for item in new_blocking).strip(),
                    live,
                    params=params,
                    authorization=authorization,
                    policy={"decision": "REJECT", "priority": "P0_hard_safety", "conflicts": new_blocking},
                )
                continue
            try:
                expected = apply_command(spec_from_device(device), live, request.action, params).state
            except SmartHomeError as exc:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "INVALID_ACTION_PARAMETERS",
                    _error_detail(exc),
                    live,
                    params=params,
                    authorization=authorization,
                )
                continue
            if matches_expected_state(request.action, params, live):
                results[index] = self._result(
                    request,
                    "NO_OP",
                    "ALREADY_IN_DESIRED_STATE",
                    _noop_detail_vi(device.name, request.action),
                    live,
                    live,
                    params=params,
                    authorization=authorization,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                continue

            try:
                command = await self.bus.send_command(
                    context.household_id,
                    request.device_slug,
                    {"action": request.action, "params": params},
                )
            except Exception as exc:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "DISPATCH_FAILED",
                    _error_detail(exc),
                    live,
                    params=params,
                    authorization=authorization,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                continue
            if not command.ok:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "DEVICE_COMMAND_FAILED",
                    command.detail or "Thiết bị từ chối lệnh.",
                    live,
                    dict(command.state or {}),
                    params=params,
                    authorization=authorization,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                continue
            try:
                verified = dict(await self.bus.get_state(context.household_id, request.device_slug))
            except Exception as exc:
                results[index] = self._result(
                    request,
                    "FAILED",
                    "VERIFY_FAILED",
                    _error_detail(exc),
                    live,
                    dict(command.state or {}),
                    params=params,
                    authorization=authorization,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                continue
            changed_keys = {key for key, value in expected.items() if live.get(key) != value}
            if changed_keys and any(verified.get(key) != expected.get(key) for key in changed_keys):
                results[index] = self._result(
                    request,
                    "FAILED",
                    "VERIFY_MISMATCH",
                    "Thiết bị không đạt trạng thái mong đợi sau khi thực hiện.",
                    live,
                    verified,
                    params=params,
                    authorization=authorization,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                continue
            results[index] = self._result(
                request,
                "SUCCESS",
                "EXECUTED",
                command.detail or "Đã thực hiện.",
                live,
                verified,
                params=params,
                authorization=authorization,
                policy={"decision": "PROCEED"},
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        finalized = [result for result in results if result is not None]
        execution = AgentExecutionResult(
            execution_id=execution_id,
            status=_aggregate_status(finalized),
            actions=finalized,
        )
        for request, action_result in zip(actions, execution.actions, strict=True):
            if not request.notify_owner or action_result.status != "SUCCESS":
                continue
            actor = session.get(User, context.requester_id)
            device = session.scalar(
                select(Device).where(
                    Device.household_id == context.household_id,
                    Device.slug == request.device_slug,
                )
            )
            if actor is not None and device is not None:
                from src.api import notifications

                await notifications.push_alert(session, actor=actor, device=device)
        await self._audit(session, actions=actions, context=context, result=execution)
        return execution

    @staticmethod
    def _result(
        request: AgentActionRequest,
        status: ActionResultStatus,
        reason_code: str,
        detail_vi: str,
        state_before: dict[str, Any] | None = None,
        state_after: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        authorization: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        latency_ms: int = 0,
    ) -> ActionExecutionResult:
        return ActionExecutionResult(
            device_slug=request.device_slug,
            status=status,
            reason_code=reason_code,
            detail_vi=detail_vi,
            state_before=dict(state_before or {}),
            state_after=dict(state_after or {}),
            action=request.action,
            params=dict(request.params if params is None else params),
            authorization=dict(authorization or {}),
            policy_decision=dict(policy or {"decision": "REJECT" if status in {"FAILED", "DENIED"} else "PROCEED"}),
            latency_ms=latency_ms,
        )

    async def _audit(
        self,
        session: Session,
        *,
        actions: list[AgentActionRequest],
        context: ExecutionContext,
        result: AgentExecutionResult,
    ) -> None:
        log_entries = []
        status_map = {
            "SUCCESS": ActionStatus.EXECUTED,
            "NO_OP": ActionStatus.NO_OP,
            "FAILED": ActionStatus.FAILED,
            "DENIED": ActionStatus.DENIED,
        }
        action_log_source = {"chat": "agent", "approval": "user", "suggestion": "automation"}[context.source]
        for action in result.actions:
            log_entries.append(
                log_action(
                    session,
                    household_id=context.household_id,
                    user_id=context.attribution_user_id or context.requester_id,
                    device_slug=action.device_slug,
                    action=action.action,
                    params=action.params,
                    status=status_map[action.status],
                    command_text=context.command_text,
                    conversation_id=context.conversation_id,
                    approval_id=context.approval_id,
                    detail=f"[{action.reason_code}] {action.detail_vi}",
                    source=action_log_source,
                    latency_ms=action.latency_ms,
                )
            )
        envelope = dict(context.audit_context)
        planning_policy = envelope.get("policy_decision")
        envelope.update({
            "execution_id": result.execution_id,
            "actor_id": context.actor_id,
            "requester_id": context.requester_id,
            "source": context.source,
            "origin_source": context.origin_source,
            "original_plan": context.original_plan or [action.original for action in actions],
            "normalized_actions": [action.model_dump(mode="json") for action in actions],
            "planning_policy_decision": planning_policy,
            "policy_decision": {
                "decision": "PROCEED" if result.status in {"SUCCESS", "PARTIAL_SUCCESS", "NO_OP"} else "REJECT",
                "actions": [action.policy_decision for action in result.actions],
            },
            "authorization_at_execute": [action.authorization for action in result.actions],
            "state_before": [action.state_before for action in result.actions],
            "state_after": [action.state_after for action in result.actions],
            "approval_id": context.approval_id,
            "execution_result": result.model_dump(mode="json"),
        })
        record_audit_envelope(
            session,
            household_id=context.household_id,
            user_id=context.requester_id,
            conversation_id=context.conversation_id,
            input_text=context.command_text,
            plan_id=str(context.approval_id or result.execution_id),
            envelope=envelope,
        )
        session.commit()

        from src.api import history

        for entry in log_entries:
            await history.push_log(session, context.household_id, entry)


agent_execution = AgentExecutionService()
