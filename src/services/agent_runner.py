"""Cầu nối giữa API và pipeline open-ended + thực thi thiết bị.

Một stack agent duy nhất: `run_pipeline` (understand → suy luận mục tiêu → tổng hợp
kế hoạch → hard-validate → policy) sinh proposal; module này chấm quyền, xử lý HITL
(lưu Approval, resume mới execute), rồi thực thi qua bus và verify.

HITL ở tầng service (không dùng interrupt của LangGraph): nếu có bước cần duyệt, lưu
Approval PENDING và KHÔNG execute; khi được duyệt, `resume_after_approval` chạy các
bước đã lưu. Đơn giản, sống sót qua restart cùng bản ghi Approval trong DB.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.agent.execution import build_executable_steps
from src.agent.planning.conflict import Conflict, PlanStep, has_blocking
from src.agent.planning.conflict import resolve as resolve_conflicts
from src.config import get_settings
from src.core.permissions import can_approve, resolve_access
from src.domain.enums import ActionStatus, Role
from src.domain.models import ActionLog, Approval, Device, Habit, LearningDecision, User
from src.nlu.model_client import build_nlu_model_client
from src.nlu.normalizer import analyze, classify_reply
from src.services.agent_execution import (
    ExecutionContext,
    agent_execution,
    blocking_conflict_evidence,
)
from src.services.approval_guard import ApprovalCheckCode, check_approval, expire_approval
from src.services.audit import SOURCE_AGENT, log_action, record_audit_envelope
from src.services.context import build_snapshot


async def close_agent() -> None:
    """Hook shutdown giữ lại cho vòng đời app.

    Pipeline multi-agent tạo mới graph thực thi theo từng lượt, không
    giữ connection pool hay checkpointer bền vững nào, nên không có gì để đóng.
    Giữ hàm no-op này để ``main.py`` gọi lúc shutdown mà không phải đổi BE."""
    return None


def new_conversation_id() -> str:
    return uuid.uuid4().hex[:16]


def _devices_by_slug(session: Session, household_id: int) -> dict[str, Device]:
    return {d.slug: d for d in session.scalars(select(Device).where(Device.household_id == household_id))}


def _latest_open_approval(session: Session, *, household_id: int, conversation_id: str) -> Approval | None:
    """Approval pending/expired gần nhất để câu xác nhận không bị hiểu thành lệnh mới."""
    return session.scalars(
        select(Approval)
        .where(
            Approval.household_id == household_id,
            Approval.conversation_id == conversation_id,
            Approval.status.in_((ActionStatus.PENDING_APPROVAL, ActionStatus.EXECUTING, ActionStatus.EXPIRED)),
        )
        .order_by(Approval.created_at.desc())
    ).first()


async def _resolve_pending_via_chat(
    session: Session, *, user: User, approval: Approval, reply: str, conversation_id: str, started: float
) -> dict:
    """Giải quyết một Approval đang chờ bằng câu gật/từ chối trong chat. Tôn trọng can_approve.

    - reject: người YÊU CẦU hoặc người ĐỦ QUYỀN duyệt đều huỷ được (không phá gì).
    - confirm: CHỈ khi đủ quyền duyệt (can_approve); không đủ → nhắc cần chủ hộ, giữ chờ.
    """
    def _wrap(state: dict, latency_ms: int, *, keep_pending: Approval | None = None) -> dict:
        return {
            "state": state,
            "conversation_id": conversation_id,
            "latency_ms": latency_ms,
            "approval": keep_pending,
        }

    if reply != "confirm":
        freshness = check_approval(
            session,
            approval=approval,
            approver=user,
            for_execution=False,
        )
        if freshness.code is ApprovalCheckCode.EXPIRED:
            if approval.status != ActionStatus.EXPIRED:
                expire_approval(approval, resolved_by_id=user.id)
                session.commit()
            state = {
                "response_vi": freshness.reason_vi,
                "intent": "", "nlu_source": _nlu_source(), "scene_name_vi": "", "conflicts": [], "plan": [], "executed": [],
            }
            return _wrap(state, int((time.perf_counter() - started) * 1000))
        if freshness.code is ApprovalCheckCode.ALREADY_RESOLVED:
            state = {
                "response_vi": freshness.reason_vi,
                "intent": "", "nlu_source": _nlu_source(), "scene_name_vi": "", "conflicts": [], "plan": [], "executed": [],
            }
            return _wrap(state, int((time.perf_counter() - started) * 1000))
        if freshness.code is ApprovalCheckCode.INVALIDATED:
            expire_approval(approval, resolved_by_id=user.id)
            session.commit()
            state = {
                "response_vi": f"Không thể xử lý: {freshness.reason_vi}",
                "intent": "", "nlu_source": _nlu_source(), "scene_name_vi": "", "conflicts": [], "plan": [], "executed": [],
            }
            return _wrap(state, int((time.perf_counter() - started) * 1000))

    if reply == "confirm":
        check = check_approval(
            session,
            approval=approval,
            approver=user,
            for_execution=True,
        )
        if check.code is ApprovalCheckCode.FORBIDDEN:
            # Nhớ việc đang chờ, nhưng người này không đủ quyền gật → giữ nguyên PENDING.
            state = {
                "response_vi": check.reason_vi,
                "intent": "", "nlu_source": _nlu_source(), "scene_name_vi": "", "conflicts": [], "plan": [], "executed": [],
            }
            return _wrap(state, int((time.perf_counter() - started) * 1000), keep_pending=approval)
        if check.code in (ApprovalCheckCode.EXPIRED, ApprovalCheckCode.INVALIDATED):
            if approval.status != ActionStatus.EXPIRED:
                expire_approval(approval, resolved_by_id=user.id)
                session.commit()
            message = check.reason_vi if check.code is ApprovalCheckCode.EXPIRED else f"Không thể thực hiện: {check.reason_vi}"
            state = {
                "response_vi": message,
                "intent": "", "nlu_source": _nlu_source(), "scene_name_vi": "", "conflicts": [], "plan": [], "executed": [],
            }
            return _wrap(state, int((time.perf_counter() - started) * 1000))
        if check.code in (ApprovalCheckCode.ALREADY_PROCESSING, ApprovalCheckCode.ALREADY_RESOLVED):
            state = {
                "response_vi": check.reason_vi,
                "intent": "", "nlu_source": _nlu_source(), "scene_name_vi": "", "conflicts": [], "plan": [], "executed": [],
            }
            return _wrap(state, int((time.perf_counter() - started) * 1000))

    required = Role(approval.required_role) if approval.required_role else Role.OWNER
    authorized = can_approve(approver_role=user.role, required_role=required)
    if reply != "confirm" and user.id != approval.requested_by_id and not authorized:
        state = {
            "response_vi": "Lệnh này cần bố/mẹ (chủ hộ, từ 18 tuổi) xác nhận — bạn chưa đủ quyền duyệt.",
            "intent": "",
            "nlu_source": _nlu_source(),
            "scene_name_vi": "",
            "conflicts": [],
            "plan": [],
            "executed": [],
        }
        return _wrap(state, int((time.perf_counter() - started) * 1000), keep_pending=approval)

    approved = reply == "confirm"
    result = await resume_after_approval(
        session,
        approval=approval,
        approved=approved,
        actor_id=user.id,
    )
    return _wrap(result["state"], result["latency_ms"])


def sync_action_log_status(session: Session, approval_id: int, status: ActionStatus) -> None:
    logs = session.scalars(
        select(ActionLog).where(
            ActionLog.approval_id == approval_id,
            ActionLog.status.in_([ActionStatus.PENDING_APPROVAL, "pending"]),
        )
    ).all()
    for log in logs:
        log.status = status


def _nlu_source(diagnostics: dict | None = None) -> str:
    """Nguồn suy luận THỰC của lượt này, không phải khả năng cấu hình toàn hệ thống.

    Một model client có thể đang sẵn sàng nhưng lệnh tường minh vẫn đi hoàn toàn qua parser
    tất định. Gắn nhãn mọi lượt là ``llm`` trong trường hợp đó khiến UI và audit nói sai đường
    thực thi, đặc biệt ở các lượt clarification/slot-fill cần debug context.
    """
    diagnostics = diagnostics or {}
    model_calls = diagnostics.get("model_calls", 0) or diagnostics.get("structured_calls", 0)
    try:
        return "llm" if int(model_calls) > 0 else "rules"
    except (TypeError, ValueError):
        return "rules"


async def _notify_progress(callback: Any, stage: str, message_vi: str, **extra: Any) -> None:
    if callback is None:
        return
    value = callback({"stage": stage, "message_vi": message_vi, **extra})
    if inspect.isawaitable(value):
        await value


async def run_command(
    session: Session,
    *,
    user: User,
    message: str,
    conversation_id: str = "",
    speaker_location: str | None = None,
    progress_callback: Any = None,
) -> dict:
    """Serialize per household; newer turns supersede only their own conversation."""
    from src.services.agent_concurrency import coordinator

    conversation_id = conversation_id or new_conversation_id()
    lease = coordinator.issue(user.household_id, conversation_id)
    await _notify_progress(progress_callback, "queued", "Đã nhận yêu cầu, đang xếp lượt xử lý.")
    async with lease.lock:
        return await _run_command_serialized(
            session,
            user=user,
            message=message,
            conversation_id=conversation_id,
            speaker_location=speaker_location,
            progress_callback=progress_callback,
            command_lease=lease,
        )


async def _run_command_serialized(
    session: Session,
    *,
    user: User,
    message: str,
    conversation_id: str = "",
    speaker_location: str | None = None,
    progress_callback: Any = None,
    command_lease: Any = None,
) -> dict:
    """Chạy một câu lệnh qua pipeline open-ended, thực thi hoặc xin duyệt HITL.

    `speaker_location` = phòng người nói đang đứng (client gửi). Cùng snapshot LIVE
    (trạng thái thiết bị + cảm biến thật) được bơm vào pipeline để hiểu & lập kế hoạch
    theo hoàn cảnh thực tế."""
    conversation_id = conversation_id or new_conversation_id()
    started = time.perf_counter()

    # --- Bộ nhớ HITL: nếu hội thoại này có việc ĐANG CHỜ XÁC NHẬN, cho phép gật/từ chối
    # bằng lời ("đồng ý", "thôi") ngay ở lượt sau. Câu có động từ/thiết bị riêng KHÔNG bị
    # coi là câu gật (classify_reply trả None) → rơi xuống xử lý như lệnh mới bình thường.
    pending = _latest_open_approval(session, household_id=user.household_id, conversation_id=conversation_id)
    if pending is not None:
        reply = classify_reply(analyze(message))
        if reply is not None:
            can_reject_others = can_approve(
                approver_role=user.role,
                required_role=Role(pending.required_role) if pending.required_role else Role.OWNER,
            )
            # Người lạ (không phải người yêu cầu, không đủ quyền duyệt) → bỏ qua, xử như lệnh thường.
            if reply == "confirm" or user.id == pending.requested_by_id or can_reject_others:
                return await _resolve_pending_via_chat(
                    session,
                    user=user,
                    approval=pending,
                    reply=reply,
                    conversation_id=conversation_id,
                    started=started,
                )

    from src.iot.registry import ROOMS
    from src.services import context as context_service
    from src.services.location_resolver import build_signals, resolve_location

    speaker_rooms = context_service.speaker_room_context_for(session, user)

    live_device_states = context_service.device_states(session, household_id=user.household_id)
    live_sensors = context_service.sensor_readings(session, household_id=user.household_id)

    # Location Resolver: gộp client hint (loa/mic) + cảm biến hiện diện thật → vị trí + độ
    # tin cậy. Chỉ dùng khi đủ chắc; không thì để None cho pipeline hỏi lại.
    estimate = resolve_location(
        build_signals(
            capture_room=speaker_location,
            presence_rooms=context_service.presence_rooms(session, household_id=user.household_id),
            valid_rooms=set(ROOMS),
        )
    )

    runtime_model_client = build_nlu_model_client()
    await _notify_progress(progress_callback, "planning", "Đang hiểu yêu cầu và lập kế hoạch phù hợp.")
    # Only the open-ended/live-LLM path needs thread offload. Deterministic commands
    # stay on the request thread, which also supports externally supplied SQLite
    # sessions that enforce same-thread access in tests/integrations.
    offload_reasoning = runtime_model_client is not None and not analyze(message).has_action_verb
    from src.services import pipeline_bridge

    reason_call = partial(
        pipeline_bridge.reason,
        message=message,
        conversation_id=conversation_id,
        role=str(user.role),
        user_id=str(user.id),
        household_id=user.household_id,
        session=session,
        now=datetime.now(UTC),
        speaker_location=estimate.room,
        speaker_location_source=estimate.source,
        speaker_location_confidence=estimate.confidence,
        speaker_home_room=speaker_rooms.home_room,
        speaker_private_room=speaker_rooms.private_room,
        live_device_states=live_device_states,
        live_sensors=live_sensors,
        model_client=runtime_model_client,
    )
    outcome_obj = await asyncio.to_thread(reason_call) if offload_reasoning else reason_call()
    goal = outcome_obj.semantic_goal
    plan = outcome_obj.candidate_plan
    outcome = outcome_obj.outcome
    intent = (getattr(goal, "intent", "") if goal is not None else "") or ""

    diagnostics = getattr(outcome_obj, "diagnostics", None) or (
        getattr(runtime_model_client, "last_pipeline_diagnostics", {}) if runtime_model_client else {}
    )
    base_state: dict = {
        "response_vi": "",
        "intent": intent,
        "nlu_source": _nlu_source(diagnostics),
        "scene_name_vi": "",
        "conflicts": [],
        "plan": [],
        "executed": [],
        "diagnostics": diagnostics,
    }

    from src.services.agent_concurrency import coordinator

    if command_lease is not None and not coordinator.is_current(command_lease):
        base_state["response_vi"] = "Yêu cầu này đã được thay thế bởi yêu cầu mới hơn nên mình chưa thực hiện gì."
        base_state["diagnostics"] = {**base_state["diagnostics"], "superseded": True}
        await _notify_progress(progress_callback, "superseded", base_state["response_vi"])
        return {
            "state": base_state,
            "conversation_id": conversation_id,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "approval": None,
        }

    await _notify_progress(
        progress_callback,
        "validated",
        "Đã lập xong kế hoạch, đang kiểm tra và áp dụng.",
        action_count=len(plan.actions) if plan is not None else 0,
    )

    # --- Không có kế hoạch để chạy: clarification / cancel / hỏi đáp ---
    if outcome != "candidate_plan" or plan is None or not plan.actions:
        if outcome == "answer":
            # Câu hỏi trạng thái: pipeline đã soạn sẵn câu trả lời từ snapshot sống.
            base_state["response_vi"] = outcome_obj.reply
        elif outcome == "cancelled" or (goal is not None and getattr(goal, "is_cancellation", False)):
            base_state["response_vi"] = outcome_obj.reply or "Đã huỷ thao tác."
        elif outcome == "clarification":
            base_state["response_vi"] = (
                outcome_obj.clarification.question_vi if outcome_obj.clarification else outcome_obj.reply
            ) or "Bạn nói rõ hơn giúp mình nhé?"
        else:
            base_state["response_vi"] = outcome_obj.reply or (
                plan.missing_information[0]
                if (plan and plan.missing_information)
                else "Mình đã hiểu, nhưng chưa có hành động cụ thể nào để đề xuất."
            )
        return {
            "state": base_state,
            "conversation_id": conversation_id,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "approval": None,
        }

    # --- Có kế hoạch: chấm quyền từng bước ---
    now = datetime.now(UTC)
    rl_state_dict = getattr(outcome_obj, "rl_state", None)
    if rl_state_dict is not None and hasattr(rl_state_dict, "to_dict"):
        rl_state_dict = rl_state_dict.to_dict()
    if rl_state_dict is None:
        rl_state_dict = _build_current_rl_state(
            session,
            household_id=user.household_id,
            user_id=user.id,
            room=estimate.room or speaker_location,
            now=now,
        )
    decision_context = getattr(outcome_obj, "decision_context", None) or {"rl_state": rl_state_dict}

    devices = _devices_by_slug(session, user.household_id)
    steps = build_executable_steps(plan.actions, devices_by_slug=devices, session=session, user=user)
    constraints = list(getattr(outcome_obj, "explicit_constraints", []) or [])
    rejected = list(getattr(outcome_obj, "rejected_assumptions", []) or [])
    if goal is not None and not constraints and not rejected:
        from src.agent.cognitive.ledger_updater import derive_constraints

        constraints, rejected = derive_constraints(goal)
    for step in steps:
        step["explicit_constraints"] = constraints
        step["rejected_assumptions"] = rejected
    allowed_actionable = [s for s in steps if s["allowed"] and not s["skipped"]]
    needs_approval = any(s["requires_approval"] for s in allowed_actionable)

    # --- Phát hiện xung đột + tự gỡ (auto-resolve đóng cửa sổ, ưu tiên vai trò chủ hộ) ---
    conflicts: list[Conflict] = []
    conflict_notes: list[str] = []
    affected_people: list = []
    if allowed_actionable:
        snapshot = build_snapshot(session, household_id=user.household_id, actor_name=user.full_name)
        conflict_plan: list[PlanStep] = [
            {
                "device_slug": str(step.get("device_slug") or ""),
                "action": str(step.get("action") or ""),
                "params": dict(step.get("params") or {}),
            }
            for step in allowed_actionable
        ]
        resolution = resolve_conflicts(conflict_plan, snapshot, actor_role=str(user.role))
        conflicts = resolution.conflicts
        conflict_notes = resolution.notes
        affected_people = resolution.affected
        # Auto-resolve: chèn bước khắc phục (đóng cửa sổ cùng phòng) TRƯỚC các bước chính.
        if resolution.remediation:
            rem_steps = _remediation_steps(session, user=user, remediation=resolution.remediation)
            if rem_steps:
                steps = rem_steps + steps
                allowed_actionable = rem_steps + allowed_actionable
                needs_approval = any(s["requires_approval"] for s in allowed_actionable)
        base_state["conflicts"] = conflicts

    # Xung đột blocking → KHÔNG chặn cứng nữa: ép qua HITL để chủ hộ xác nhận (override).
    # Fingerprint của conflict hiện tại được đóng băng vào approval. Khi resume,
    # dispatcher chấm lại và chỉ cho override đúng conflict đã được duyệt.
    conflict_override_reason = ""
    if conflicts and has_blocking(conflicts):
        conflict_override_reason = " ".join(c["message_vi"] for c in conflicts if c.get("blocking"))
        needs_approval = True

    # --- HITL: có bước cần duyệt (hoặc override xung đột blocking) → lưu Approval, KHÔNG execute ---
    if allowed_actionable and needs_approval:
        for s in steps:
            if s["allowed"] and not s["skipped"]:
                s["status"] = str(ActionStatus.PENDING_APPROVAL)
        # Ghi lại episodic memory ngay khi tạo Approval để liên kết chính xác (Finding 2)
        from src.services import memory_service

        episode_id = memory_service.record_turn(
            session,
            household_id=user.household_id,
            user_id=user.id,
            utterance=message,
            response=outcome_obj,
            room=estimate.room or speaker_location,
            now=now,
        )

        if allowed_actionable:
            allowed_actionable[0]["_rl_state"] = rl_state_dict
            allowed_actionable[0]["_decision_context"] = decision_context
            allowed_actionable[0]["_room"] = estimate.room or speaker_location
            allowed_actionable[0]["_episode_id"] = episode_id
            allowed_actionable[0]["_evidence_trace"] = list(outcome_obj.evidence_trace)

        approval = _record_approval(
            session,
            user=user,
            conversation_id=conversation_id,
            message=message,
            steps=allowed_actionable,
            episode_id=episode_id,
            extra_reason=conflict_override_reason,
            approved_conflicts=blocking_conflict_evidence(conflicts),
        )
        record_audit_envelope(
            session, household_id=user.household_id, user_id=user.id, conversation_id=conversation_id,
            input_text=message, plan_id=str(approval.id),
            envelope=_audit_envelope(
                goal,
                outcome,
                steps,
                [],
                {"decision": "WAITING_FOR_USER_APPROVAL"},
                evidence_trace=outcome_obj.evidence_trace,
            ),
        )
        session.commit()
        base_state["response_vi"] = _plan_summary_vi(steps, pending=True)
        if conflict_override_reason:
            base_state["response_vi"] += "\n⚠️ " + conflict_override_reason + " Cần chủ hộ xác nhận."
        if conflicts:
            warnings = [c["message_vi"] for c in conflicts if not c.get("blocking")]
            if warnings:
                base_state["response_vi"] += "\n⚠️ " + " ".join(warnings)
        base_state["plan"] = steps
        return {
            "state": base_state,
            "conversation_id": conversation_id,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "approval": approval,
        }

    # --- Không cần duyệt: thực thi ngay (kèm cảnh báo non-blocking nếu có) ---
    await _notify_progress(progress_callback, "executing", "Đang thực hiện các bước đã kiểm tra.")
    action_requests = agent_execution.normalize_actions(steps)
    execution_result = await agent_execution.execute(
        session,
        actions=action_requests,
        context=ExecutionContext(
            household_id=user.household_id,
            requester_id=user.id,
            actor_id=user.id,
            source="chat",
            origin_source="chat",
            command_text=message,
            conversation_id=conversation_id,
            original_plan=steps,
            audit_context=_audit_envelope(
                goal,
                outcome,
                steps,
                [],
                {"decision": "PROCEED"},
                evidence_trace=outcome_obj.evidence_trace,
            ),
        ),
    )
    executed = execution_result.legacy_steps(action_requests)
    execution_id = execution_result.execution_id
    base_state["diagnostics"] = {
        **base_state["diagnostics"],
        "execution_result": execution_result.model_dump(mode="json"),
    }

    # Ghi lại episodic memory để có episode_id liên kết chặt chẽ (Finding 1)
    from src.services import memory_service

    episode_id = memory_service.record_turn(
        session,
        household_id=user.household_id,
        user_id=user.id,
        utterance=message,
        response=outcome_obj,
        room=estimate.room or speaker_location,
        now=now,
    )

    if get_settings().learning_v2_write:
        _record_learning_decision(
            session,
            execution_id=execution_id,
            household_id=user.household_id,
            user_id=user.id,
            episode_id=episode_id,
            conversation_id=conversation_id,
            plan_id=None,
            rl_state=rl_state_dict,
            decision_context=decision_context,
            executed=executed,
        )
    session.commit()
    # Override-rồi-báo-sau: lệnh đã chạy, báo cho NGƯỜI BỊ ĐÈ (sở thích/thói quen) biết.
    if affected_people:
        await _notify_affected(session, household_id=user.household_id, affected=affected_people)
    for step in executed:
        if step.get("status") == str(ActionStatus.EXECUTED):
            await _notify_habit_overridden(
                session, household_id=user.household_id, actor=user,
                slug=step["device_slug"], action=step["action"],
            )
    base_state["plan"] = executed
    base_state["executed"] = executed
    response = _executed_summary_vi(executed)
    if conflict_notes:
        response += "\n" + " ".join(conflict_notes)
    if conflicts:
        warnings = [c["message_vi"] for c in conflicts if not c.get("blocking")]
        if warnings:
            response += "\n" + " ".join(warnings)
    base_state["response_vi"] = response
    return {
        "state": base_state,
        "conversation_id": conversation_id,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "approval": None,
        "execution_id": execution_id,
    }


def _build_current_rl_state(
    session: Session,
    *,
    household_id: int,
    user_id: int | None,
    room: str | None,
    now: datetime,
) -> dict:
    from src.agent.preference.state_encoder import encode_state
    from src.services import context as context_service

    sensors = context_service.sensor_readings(session, household_id=household_id)
    outside_temp_val = None
    occ_val = None
    for s in sensors:
        stype = s.get("sensor_type")
        sroom = s.get("room")
        # Cảm biến nhiệt độ ngoài trời có sroom=None hoặc slug ngoai_troi (Finding 4)
        if stype == "temperature" and (not sroom or "ngoai_troi" in s.get("slug", "")) and outside_temp_val is None:
            outside_temp_val = s.get("value")
        elif stype == "presence" and sroom == room:
            occ_val = bool(s.get("value", 0))

    state_obj = encode_state(
        resident=str(user_id) if user_id is not None else None,
        room=room,
        now=now,
        outside_temp_c=outside_temp_val,
        occupied=occ_val,
        power_mode="NORMAL",
    )
    return state_obj.to_dict()


def _record_learning_decision(
    session: Session,
    *,
    execution_id: str,
    household_id: int,
    user_id: int | None,
    episode_id: int | None = None,
    conversation_id: str,
    plan_id: str | None,
    rl_state: dict,
    decision_context: dict | None = None,
    executed: list[dict],
) -> Any:
    successful_actions = []
    for s in executed:
        if s.get("status") == str(ActionStatus.EXECUTED) and not s.get("skipped"):
            successful_actions.append(
                {
                    "device_slug": s.get("device_slug"),
                    "device_name": s.get("device_name"),
                    "action": s.get("action"),
                    "params": s.get("params") or {},
                    "risk_level": s.get("risk_level"),
                }
            )

    decision = LearningDecision(
        execution_id=execution_id,
        household_id=household_id,
        user_id=user_id,
        episode_id=episode_id,
        conversation_id=conversation_id,
        plan_id=plan_id,
        rl_state=rl_state,
        decision_context=decision_context or {},
        executed_actions=successful_actions,
        created_at=datetime.now(UTC),
    )
    session.add(decision)
    return decision


def _audit_envelope(
    goal,
    outcome: str,
    steps: list[dict],
    executed: list[dict],
    policy: dict,
    *,
    evidence_trace: list[dict] | None = None,
) -> dict:
    """Dựng envelope quyết định §53 từ dữ liệu lượt (FR-14) — persist để tái dựng sau này."""
    return {
        "semantic_goal": goal.model_dump(mode="json") if hasattr(goal, "model_dump") else {},
        "evidence_trace": list(evidence_trace or []),
        "outcome": outcome,
        "plan": list(steps or []),
        "execution_result": list(executed or []),
        "policy_decision": dict(policy or {}),
    }


def _remediation_steps(session: Session, *, user: User, remediation: list[PlanStep]) -> list[dict]:
    """Bước khắc phục (đóng cửa sổ) → step thực thi được, CHỈ giữ bước người dùng đủ quyền.

    Auto-resolve không được nới quyền: nếu người ra lệnh không được phép điều khiển cửa
    đó (hoặc cần duyệt) thì bỏ qua, không tự đóng."""
    out: list[dict] = []
    for r in remediation:
        slug = r["device_slug"]
        device = session.scalar(select(Device).where(Device.household_id == user.household_id, Device.slug == slug))
        if device is None:
            continue
        decision = resolve_access(session, user=user, device=device)
        if not decision.allowed or decision.requires_approval:
            continue
        out.append(
            {
                "device_slug": slug,
                "device_name": device.name,
                "action": r["action"],
                "params": dict(r.get("params") or {}),
                "risk_level": str(device.risk_level),
                "allowed": True,
                "skipped": False,
                "requires_approval": False,
                "auto_resolved": True,
            }
        )
    return out


_ACTION_VI: dict[str, str] = {
    "turn_on": "bật", "turn_off": "tắt", "set_brightness": "chỉnh độ sáng",
    "set_temperature": "chỉnh nhiệt độ", "set_speed": "chỉnh tốc độ",
    "set_volume": "chỉnh âm lượng", "lock": "khoá", "unlock": "mở khoá",
    "open": "mở", "close": "đóng",
}


async def _notify_habit_overridden(
    session: Session, *, household_id: int, actor: User, slug: str, action: str,
) -> None:
    from src.api import notifications
    from src.core import clock

    current_hour = clock.local_now().hour
    habits = session.scalars(
        select(Habit).where(
            Habit.household_id == household_id,
            Habit.device_slug == slug,
            Habit.action != action,
            Habit.hour == current_hour,
            Habit.user_id != actor.id,
            Habit.user_id.is_not(None),
            Habit.enabled.is_(True),
        )
    ).all()
    if not habits:
        return
    users = {u.id: u for u in session.scalars(select(User).where(User.household_id == household_id)).all()}
    device = session.scalar(select(Device).where(Device.household_id == household_id, Device.slug == slug))
    device_name = device.name if device else slug
    actor_name = actor.full_name or actor.username
    action_vi = _ACTION_VI.get(action, action)
    for habit in habits:
        recipient_id = habit.user_id
        if recipient_id is None:
            continue
        owner = users.get(recipient_id)
        if not owner:
            continue
        habit_action_vi = _ACTION_VI.get(habit.action, habit.action)
        msg = (
            f"{actor_name} vừa {action_vi} {device_name}, "
            f"khác với thói quen {habit_action_vi} lúc {habit.hour}h của bạn."
        )
        await notifications.notify_user(
            session, household_id=household_id, recipient_id=owner.id,
            notification_type="conflict_affected", message_vi=msg,
        )


async def _notify_affected(session: Session, *, household_id: int, affected: list) -> None:
    """Báo cho người bị đè bởi xung đột sở thích/thói quen (sau khi override đã chạy)."""
    if not affected:
        return
    from src.api import notifications

    users = session.scalars(select(User).where(User.household_id == household_id)).all()
    by_name: dict[str, int] = {}
    for u in users:
        by_name.setdefault(u.full_name or u.username, u.id)
    for person in affected:
        recipient_id = by_name.get(person.name)
        if recipient_id is None:
            continue  # không map được tên → bỏ qua (best-effort)
        await notifications.notify_user(
            session,
            household_id=household_id,
            recipient_id=recipient_id,
            notification_type="conflict_affected",
            message_vi=person.message_vi,
        )


def _record_approval(
    session: Session,
    *,
    user: User,
    conversation_id: str,
    message: str,
    steps: list[dict],
    episode_id: int | None = None,
    extra_reason: str = "",
    approved_conflicts: list[dict] | None = None,
) -> Approval:
    """Lưu yêu cầu duyệt (HITL) vào DB.

    ``extra_reason`` (nếu có) là lý do bổ sung — dùng cho override xung đột blocking,
    để chủ hộ thấy vì sao cần xác nhận thay vì chỉ "cần xác nhận trước khi thực hiện"."""
    # Bỏ age: mọi yêu cầu của thành viên đều cần CHỦ HỘ duyệt (không tự duyệt được).
    required_role = Role.OWNER
    reason_vi = " ".join(sorted({s.get("reason_vi") or "Cần xác nhận trước khi thực hiện." for s in steps}))
    if extra_reason:
        reason_vi = f"{extra_reason} {reason_vi}".strip()
    frozen_steps = [dict(step) for step in steps]
    if frozen_steps and approved_conflicts:
        frozen_steps[0]["_approved_blocking_conflicts"] = list(approved_conflicts)
    approval = Approval(
        household_id=user.household_id,
        requested_by_id=user.id,
        conversation_id=conversation_id,
        command_text=message,
        reason_vi=reason_vi,
        steps=frozen_steps,
        required_role=str(required_role),
        status=ActionStatus.PENDING_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        source="chat",
        episode_id=episode_id,
    )
    session.add(approval)
    session.flush()
    log_action(
        session,
        household_id=user.household_id,
        user_id=user.id,
        action="request_approval",
        status=ActionStatus.PENDING_APPROVAL,
        command_text=message,
        conversation_id=conversation_id,
        approval_id=approval.id,
        detail=approval.reason_vi,
        source=SOURCE_AGENT,
    )
    session.commit()
    session.refresh(approval)
    return approval


async def resume_after_approval(
    session: Session,
    *,
    approval: Approval,
    approved: bool,
    note: str = "",
    actor_id: int | None = None,
) -> dict:
    """Chạy tiếp sau khi người dùng quyết định duyệt."""
    started = time.perf_counter()
    resolver_id = actor_id or approval.resolved_by_id or approval.requested_by_id
    if not approved:
        resolution = agent_execution.reject_approval(session, approval_id=approval.id, actor_id=resolver_id)
        if resolution.value != "claimed":
            message = "Yêu cầu này đang được xử lý." if resolution.value == "already_processing" else "Yêu cầu này đã được xử lý."
            state = {"response_vi": message, "executed": [], "plan": [], "diagnostics": {"reason_code": resolution.value}}
            return {"state": state, "latency_ms": int((time.perf_counter() - started) * 1000), "resolution_code": resolution.value}
        # ActionLog đang chờ phải theo trạng thái cuối của approval. Bản refactor
        # (agent_execution) chỉ chuyển trạng thái Approval nên không đụng ActionLog — giữ
        # hành vi này từ develop_TrongDung, đặt trong resume_after_approval để MỌI đường vào
        # (API duyệt và trả lời "confirm" trong chat) đều đi qua cùng một chỗ.
        sync_action_log_status(session, approval.id, ActionStatus.REJECTED)
        session.commit()
        state = {"response_vi": "Bạn đã từ chối nên tôi không thực hiện lệnh này.", "executed": [], "plan": []}
        return {"state": state, "latency_ms": int((time.perf_counter() - started) * 1000), "resolution_code": "rejected"}

    claim = agent_execution.claim_approval(session, approval_id=approval.id, actor_id=resolver_id)
    if not claim.claimed or claim.approval is None:
        message = "Yêu cầu này đang được xử lý." if claim.code.value == "already_processing" else "Yêu cầu này đã được xử lý."
        state = {"response_vi": message, "executed": [], "plan": [], "diagnostics": {"reason_code": claim.code.value}}
        return {"state": state, "latency_ms": int((time.perf_counter() - started) * 1000), "resolution_code": claim.code.value}

    approval = claim.approval
    action_requests = agent_execution.normalize_actions(list(approval.steps or []))
    first_step = (approval.steps or [{}])[0] if approval.steps else {}
    try:
        execution_result = await agent_execution.execute(
            session,
            actions=action_requests,
            context=ExecutionContext(
                household_id=approval.household_id,
                requester_id=approval.requested_by_id,
                actor_id=resolver_id,
                source="approval",
                origin_source=approval.source,
                command_text=approval.command_text,
                conversation_id=approval.conversation_id,
                approval_id=approval.id,
                original_plan=list(approval.steps or []),
                approved_conflicts=list(first_step.get("_approved_blocking_conflicts") or []),
                audit_context=_audit_envelope(
                    None,
                    "candidate_plan",
                    list(approval.steps or []),
                    [],
                    {"decision": "APPROVED"},
                    evidence_trace=list(first_step.get("_evidence_trace") or []),
                ),
            ),
        )
    except Exception:
        session.rollback()
        session.execute(
            update(Approval)
            .where(Approval.id == approval.id, Approval.status == ActionStatus.EXECUTING)
            .values(status=ActionStatus.FAILED, resolved_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        session.commit()
        raise
    agent_execution.finalize_approval(session, approval_id=approval.id, result=execution_result)
    sync_action_log_status(session, approval.id, ActionStatus.APPROVED)
    executed = execution_result.legacy_steps(action_requests)
    execution_id = execution_result.execution_id

    # Tái sử dụng chính xác RLState và decision context từ thời điểm lập kế hoạch (Finding 4)
    rl_state = first_step.get("_rl_state")
    decision_context = first_step.get("_decision_context")
    room = first_step.get("_room")
    episode_id = getattr(approval, "episode_id", None) or first_step.get("_episode_id")

    now = datetime.now(UTC)
    if rl_state is None:
        rl_state = _build_current_rl_state(
            session,
            household_id=approval.household_id,
            user_id=approval.requested_by_id,
            room=room,
            now=now,
        )

    if get_settings().learning_v2_write:
        _record_learning_decision(
            session,
            execution_id=execution_id,
            household_id=approval.household_id,
            user_id=approval.requested_by_id,
            episode_id=episode_id,
            conversation_id=approval.conversation_id,
            plan_id=str(approval.id),
            rl_state=rl_state,
            decision_context=decision_context,
            executed=executed,
        )
    session.commit()
    response_state: dict[str, Any] = {
        "response_vi": _executed_summary_vi(executed),
        "executed": executed,
        "plan": executed,
    }
    response_state["diagnostics"] = {"execution_result": execution_result.model_dump(mode="json")}
    return {
        "state": response_state,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "execution_id": execution_id,
        "resolution_code": "executed",
    }


def _plan_summary_vi(steps: list[dict], *, pending: bool) -> str:
    lines = ["Thiết bị nhạy cảm nên cần xác nhận." if pending else "Kế hoạch đề xuất:"]
    for i, s in enumerate(steps, 1):
        if not s["allowed"]:
            lines.append(f"{i}. {s['device_name']} — {s['deny_reason_vi']}")
        elif s["skipped"]:
            lines.append(f"{i}. {s['device_name']} — đã đúng trạng thái")
        else:
            lines.append(f"{i}. {s['device_name']} · {s['action']} — {s.get('reason_vi') or ''}".rstrip(" —"))
    return "\n".join(lines)


def _executed_summary_vi(executed: list[dict]) -> str:
    lines: list[str] = []
    for s in executed:
        status = s.get("status", "")
        if status == str(ActionStatus.DENIED):
            lines.append(f"{s.get('device_name', '')} — {s.get('detail_vi', '')}")
        elif s.get("skipped"):
            detail = s.get("detail_vi") or f"Giữ nguyên (đã đúng trạng thái): {s.get('device_name', '')}"
            lines.append(f"Giữ nguyên: {detail}")
        elif status == str(ActionStatus.FAILED):
            lines.append(f"{s.get('detail_vi', '')}")
        else:
            lines.append(f"{s.get('detail_vi', '')}")
    return "\n".join(lines) or "Không có gì cần thay đổi."
