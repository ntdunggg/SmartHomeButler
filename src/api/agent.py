"""Route của agent: nhận lệnh tiếng Việt, duyệt HITL, xử lý đề xuất chủ động."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import select

from src.api import history, notifications, ws
from src.api.deps import CurrentUser, DbSession
from src.core import clock
from src.core.permissions import resolve_access
from src.db.session import session_scope
from src.domain.enums import ActionStatus, Role
from src.domain.models import Approval, Device, Habit, User
from src.memory.habits import mark_triggered, snooze
from src.models.schemas import (
    ApprovalDecision,
    ApprovalOut,
    CommandJobAccepted,
    CommandJobStatus,
    CommandRequest,
    CommandResponse,
    ConflictOut,
    FeedbackIn,
    FeedbackOut,
    MemoryFeedbackIn,
    PlanStepOut,
    SuggestionDecision,
    SuggestionOut,
)
from src.services import agent_runner, memory_service
from src.services import context as context_service
from src.services.agent_execution import ExecutionContext, agent_execution
from src.services.agent_jobs import jobs
from src.services.approval_guard import ApprovalCheckCode, check_approval, expire_approval
from src.services.automation import registry
from src.services.nlu_gateway import NluRequest, NluResponse, analyze

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)
_AGENT_TASKS: set[asyncio.Task] = set()


@router.post("/nlu", response_model=NluResponse)
async def nlu(payload: NluRequest, user: CurrentUser, session: DbSession) -> NluResponse:
    """Phân tích NLU end-to-end (deterministic fast path hoặc model đã cấu hình).

    KHÔNG execute thiết bị: chỉ trả CandidatePlan (proposal, chờ policy validation)
    hoặc Clarification hoặc controlled error. Đây là đường tách biệt với /agent/command
    (đường thực thi cũ) — dùng cho khung chat NLU.

    Bơm snapshot LIVE (trạng thái thiết bị + cảm biến thật của hộ) vào pipeline để agent
    hiểu và lập kế hoạch theo hoàn cảnh hiện tại, kết hợp cùng vị trí người nói."""
    live_device_states = context_service.device_states(session, household_id=user.household_id)
    live_sensors = context_service.sensor_readings(session, household_id=user.household_id)
    speaker_rooms = context_service.speaker_room_context_for(session, user)
    response = await analyze(
        payload,
        live_device_states=live_device_states,
        live_sensors=live_sensors,
        household_id=user.household_id,
        user_id=user.id,
        role=str(user.role),
        speaker_home_room=speaker_rooms.home_room,
        speaker_private_room=speaker_rooms.private_room,
        session=session,
    )

    # Ghi lại lượt này để lần sau có bằng chứng. Trạng thái ban đầu là "proposed" —
    # nó chỉ thành bằng chứng thật khi người dùng phản hồi (xem /agent/memory/feedback).
    response.episode_id = memory_service.record_turn(
        session,
        household_id=user.household_id,
        user_id=user.id,
        utterance=payload.message,
        response=response,
        room=(response.location.room if response.location else None),
    )
    return response


@router.post("/feedback", response_model=FeedbackOut)
async def feedback(
    payload: FeedbackIn,
    user: CurrentUser,
    session: DbSession,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> FeedbackOut:
    """Tiếp nhận và xử lý feedback production độc lập 2 nhánh Memory & RL (FR-15)."""
    from src.services.feedback_service import FeedbackService

    try:
        return FeedbackService.handle_feedback(
            session,
            user=user,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/memory/feedback")
async def memory_feedback(
    payload: MemoryFeedbackIn,
    user: CurrentUser,
    session: DbSession,
) -> dict[str, bool]:
    """Ghi nhận người dùng đồng ý / từ chối / sửa một đề xuất, rồi quy tụ lại hồ sơ.

    Đây là chỗ vòng học khép lại: không có phản hồi thì mọi lượt vĩnh viễn ở trạng thái
    "proposed" và KHÔNG bao giờ được nâng thành thói quen — đúng ý fail-closed."""
    ok = memory_service.apply_feedback(
        session,
        household_id=user.household_id,
        episode_id=payload.episode_id,
        outcome=payload.outcome,
        note=payload.note,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phản hồi không hợp lệ hoặc không tìm thấy lượt tương ứng.",
        )
    return {"ok": True}


def _to_approval_out(approval: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        conversation_id=approval.conversation_id,
        command_text=approval.command_text,
        reason_vi=approval.reason_vi,
        steps=approval.steps or [],
        required_role=approval.required_role,
        status=str(approval.status),
        source=approval.source,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
    )


def _to_command_response(result: dict) -> CommandResponse:
    state = result["state"]
    # Sau khi thực thi, "executed" mới là danh sách có kết quả; trước đó dùng "plan"
    steps = state.get("executed") or state.get("plan") or []
    return CommandResponse(
        conversation_id=result["conversation_id"],
        response_vi=state.get("response_vi", ""),
        intent=state.get("intent", ""),
        nlu_source=state.get("nlu_source", "rules"),
        scene_name_vi=state.get("scene_name_vi", ""),
        plan=[PlanStepOut(**{k: v for k, v in step.items() if k in PlanStepOut.model_fields}) for step in steps],
        conflicts=[
            ConflictOut(**{k: v for k, v in c.items() if k in ConflictOut.model_fields})
            for c in state.get("conflicts") or []
        ],
        latency_ms=result["latency_ms"],
        pending_approval=_to_approval_out(result["approval"]) if result.get("approval") else None,
        execution_id=result.get("execution_id"),
        diagnostics=state.get("diagnostics") or {},
    )


@router.post("/command", response_model=CommandResponse)
async def command(payload: CommandRequest, user: CurrentUser, session: DbSession) -> CommandResponse:
    """Gửi một câu lệnh tiếng Việt cho agent."""
    result = await agent_runner.run_command(
        session,
        user=user,
        message=payload.message,
        conversation_id=payload.conversation_id,
        speaker_location=payload.speaker_location,
    )
    if result.get("approval"):
        return _to_command_response(result)

    state = result["state"]
    if not state.get("response_vi"):
        # Không nên xảy ra, nhưng thà trả câu xin lỗi còn hơn trả chuỗi rỗng
        state["response_vi"] = "Xin lỗi, tôi chưa xử lý được yêu cầu này."
    return _to_command_response(result)


async def _run_command_job(job_id: str, *, user_id: int, payload: CommandRequest) -> None:
    """Own-session background worker; progress is available via WS and polling."""
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise RuntimeError("Tài khoản không còn tồn tại.")

            async def progress(event: dict) -> None:
                jobs.progress(job_id, event)
                await ws.notify_agent_event(
                    user.household_id,
                    user.id,
                    {"type": "agent_progress", "job_id": job_id, **event},
                )

            result = await agent_runner.run_command(
                session,
                user=user,
                message=payload.message,
                conversation_id=payload.conversation_id,
                speaker_location=payload.speaker_location,
                progress_callback=progress,
            )
            response = _to_command_response(result).model_dump(mode="json")
            jobs.complete(job_id, response)
            await ws.notify_agent_event(
                user.household_id,
                user.id,
                {"type": "agent_result", "job_id": job_id, "result": response},
            )
    except Exception:
        logger.exception("Async agent job %s failed", job_id)
        job = jobs.fail(job_id, "Không thể hoàn tất yêu cầu. Vui lòng thử lại.")
        if job is not None:
            await ws.notify_agent_event(
                job.household_id,
                job.user_id,
                {"type": "agent_error", "job_id": job_id, "error_vi": job.error_vi},
            )


@router.post("/command/async", response_model=CommandJobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def command_async(payload: CommandRequest, user: CurrentUser) -> CommandJobAccepted:
    """Return immediately; planning progress and result arrive over WebSocket."""
    job = jobs.create(household_id=user.household_id, user_id=user.id)
    task = asyncio.create_task(_run_command_job(job.id, user_id=user.id, payload=payload))
    _AGENT_TASKS.add(task)
    task.add_done_callback(_AGENT_TASKS.discard)
    return CommandJobAccepted(job_id=job.id, message_vi="Đã nhận yêu cầu, agent đang lập kế hoạch.")


@router.get("/command/jobs/{job_id}", response_model=CommandJobStatus)
async def command_job_status(job_id: str, user: CurrentUser) -> CommandJobStatus:
    """Polling fallback when the browser WebSocket is temporarily disconnected."""
    job = jobs.get(job_id)
    if job is None or job.household_id != user.household_id or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy tác vụ agent.")
    return CommandJobStatus(**job.public())


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(user: CurrentUser, session: DbSession) -> list[ApprovalOut]:
    """Các yêu cầu đang chờ duyệt của hộ."""
    approvals = session.scalars(
        select(Approval)
        .where(Approval.household_id == user.household_id, Approval.status == ActionStatus.PENDING_APPROVAL)
        .order_by(Approval.created_at.desc())
    )
    return [_to_approval_out(a) for a in approvals]


def _msg_response(conversation_id: str, message_vi: str, *, reason_code: str = "") -> CommandResponse:
    """Câu trả lời tối giản cho các nhánh no-op/hết hạn/bị chặn khi duyệt."""
    return CommandResponse(
        conversation_id=conversation_id,
        response_vi=message_vi,
        intent="",
        nlu_source="rules",
        latency_ms=0,
        diagnostics={"reason_code": reason_code} if reason_code else {},
    )


@router.post("/approvals/{approval_id}", response_model=CommandResponse)
async def decide_approval(
    approval_id: int,
    decision: ApprovalDecision,
    user: CurrentUser,
    session: DbSession,
) -> CommandResponse:
    """Duyệt hoặc từ chối một lệnh nhạy cảm (Human-in-the-loop)."""
    approval = session.get(Approval, approval_id)
    if approval is None or approval.household_id != user.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy yêu cầu duyệt.")

    check = check_approval(
        session,
        approval=approval,
        approver=user,
        for_execution=decision.approved,
    )
    if check.code in (ApprovalCheckCode.ALREADY_PROCESSING, ApprovalCheckCode.ALREADY_RESOLVED):
        return _msg_response(approval.conversation_id, check.reason_vi, reason_code=check.code.value)
    if check.code is ApprovalCheckCode.FORBIDDEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=check.reason_vi,
        )
    if check.code in (ApprovalCheckCode.EXPIRED, ApprovalCheckCode.INVALIDATED):
        if approval.status == ActionStatus.EXPIRED:
            return _msg_response(approval.conversation_id, check.reason_vi)
        expire_approval(approval, resolved_by_id=user.id)
        # ActionLog đang chờ cũng phải hết hiệu lực — nhánh này KHÔNG đi qua
        # resume_after_approval nên phải đồng bộ ở đây (hành vi từ nhánh BE).
        agent_runner.sync_action_log_status(session, approval.id, ActionStatus.EXPIRED)
        session.commit()
        await ws.notify_approval_resolved(user.household_id, approval_id=approval.id, status=str(approval.status))
        await notifications.notify_user(
            session, household_id=approval.household_id, recipient_id=approval.requested_by_id,
            notification_type="expired", message_vi=f"Yêu cầu “{approval.command_text}” không còn hiệu lực.",
            approval_id=approval.id,
        )
        message = check.reason_vi if check.code is ApprovalCheckCode.EXPIRED else f"Không thể thực hiện: {check.reason_vi}"
        return _msg_response(approval.conversation_id, message)

    result = await agent_runner.resume_after_approval(
        session,
        approval=approval,
        approved=decision.approved,
        note=decision.note,
        actor_id=user.id,
    )

    if result.get("resolution_code") in {"already_processing", "already_resolved"}:
        return _to_command_response(
            {
                "state": result["state"],
                "conversation_id": approval.conversation_id,
                "latency_ms": result["latency_ms"],
                "approval": None,
            }
        )

    session.expire_all()
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Yêu cầu duyệt không còn tồn tại.",
        )

    # Báo cả hộ biết yêu cầu đã xử lý xong để đóng popup và cập nhật UI
    await ws.notify_approval_resolved(user.household_id, approval_id=approval.id, status=str(approval.status))
    # Thông báo riêng cho NGƯỜI YÊU CẦU biết kết quả.
    succeeded = approval is not None and approval.status == ActionStatus.APPROVED
    verb = "được chấp thuận" if succeeded else ("bị từ chối" if not decision.approved else "thực hiện thất bại")
    await notifications.notify_user(
        session,
        household_id=approval.household_id,
        recipient_id=approval.requested_by_id,
        notification_type="approved" if succeeded else ("rejected" if not decision.approved else "failed"),
        message_vi=f"Yêu cầu “{approval.command_text}” đã {verb}.",
        approval_id=approval.id,
    )

    return _to_command_response(
        {
            "state": result["state"],
            "conversation_id": approval.conversation_id,
            "latency_ms": result["latency_ms"],
            "approval": None,
            "execution_id": result.get("execution_id"),
        }
    )


@router.get("/suggestions", response_model=list[SuggestionOut])
async def list_suggestions(user: CurrentUser, session: DbSession) -> list[SuggestionOut]:
    """Đề xuất chủ động agent đang chờ phản hồi.

    Chỉ trả đề xuất nhắm tới chính người này (từ thói quen của họ) hoặc đề xuất
    chung cả hộ — không lộ đề xuất riêng của thành viên khác.
    Nạp thêm từ DB (phòng server restart mất cache) rồi merge vào memory registry.
    """
    registry.load_from_db(session)
    return [
        SuggestionOut(**s.to_dict())
        for s in registry.list_for(user.household_id)
        if s.user_id is None or s.user_id == user.id
    ]


@router.post("/suggestions/{suggestion_id}")
async def decide_suggestion(
    suggestion_id: str,
    decision: SuggestionDecision,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    """Chấp nhận, hoãn hoặc bỏ qua một đề xuất.

    Hoãn chỉ áp dụng cho đề xuất sinh ra từ thói quen — đúng tình huống đề bài mô
    tả: 22h tới giờ đi ngủ nhưng chủ hộ chưa muốn, hẹn 22h15 tắt cho riêng hôm đó.
    """
    # Xem trước để kiểm quyền, CHƯA lấy ra — đề xuất chỉ bị gỡ khi việc tương ứng
    # đã chạy xong, tránh mất đề xuất nếu bước áp dụng lỗi giữa chừng.
    suggestion = registry.get(suggestion_id)
    if suggestion is None or suggestion.household_id != user.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Đề xuất không còn hiệu lực.")
    # Đề xuất nhắm riêng một người thì chỉ người đó được quyết định
    if suggestion.user_id is not None and suggestion.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Đề xuất này không dành cho bạn.")

    if decision.snooze_minutes > 0:
        result = _snooze_suggestion(session, suggestion, minutes=decision.snooze_minutes)
        registry.pop(suggestion_id, session=session)
        session.commit()
        return result

    if not decision.accepted:
        _mark_habit_handled(session, suggestion)
        registry.pop(suggestion_id, session=session)
        session.commit()
        return {"ok": True, "message_vi": "Đã bỏ qua đề xuất."}

    executed, log_entries, approval = await _apply_suggestion(session, suggestion, user=user)
    _mark_habit_handled(session, suggestion)
    registry.pop(suggestion_id, session=session)
    session.commit()
    if approval is not None:
        session.refresh(approval)
    for entry in log_entries:
        await history.push_log(session, user.household_id, entry)
    if approval is not None:
        await ws.notify_approval_request(
            user.household_id,
            approval,
            requester_name=user.full_name or user.username,
        )
        await notifications.notify_recipients(
            session,
            household_id=user.household_id,
            recipient_ids=notifications.owner_ids(session, user.household_id),
            notification_type="approval_requested",
            message_vi=f"{user.full_name or user.username} xin duyệt đề xuất: {suggestion.title_vi}",
            approval_id=approval.id,
        )
        return {
            "ok": True,
            "requires_approval": True,
            "approval_id": approval.id,
            "message_vi": approval.reason_vi,
        }
    return {"ok": True, "message_vi": "\n".join(executed) or "Không có gì cần thay đổi."}


def _snooze_suggestion(session, suggestion, *, minutes: int) -> dict:
    if suggestion.habit_id is None:
        return {"ok": True, "message_vi": "Đã bỏ qua đề xuất."}
    habit = session.get(Habit, suggestion.habit_id)
    if habit is not None:
        # Mốc hoãn theo ĐỒNG HỒ MÔ PHỎNG để khớp với due_habits (cũng bám clock) —
        # nếu đặt bằng giờ thực, đồng hồ demo lệch sẽ làm mốc hoãn quá hạn ngay (Finding 3).
        snooze(session, habit, until=clock.now() + timedelta(minutes=minutes))
        session.commit()
    return {"ok": True, "message_vi": f"Được, tôi sẽ nhắc lại sau {minutes} phút."}


def _mark_habit_handled(session, suggestion) -> None:
    if suggestion.habit_id is None:
        return
    habit = session.get(Habit, suggestion.habit_id)
    if habit is not None:
        mark_triggered(session, habit)


def _attributed_user_id(session, suggestion, *, accepter) -> int:
    """Ai được ghi nhận là người gây ra hành động khi chấp nhận một đề xuất.

    Hành động sinh từ một THÓI QUEN phải quy về chủ của thói quen đó (``habit.user_id``),
    không phải người tình cờ bấm chấp nhận — nếu không, hồ sơ AI của người này sẽ
    dính thói quen của người khác. Đề xuất không gắn thói quen (chất lượng không khí,
    thời tiết, vắng nhà...) thì quy về người bấm là hợp lý.
    """
    if suggestion.habit_id is not None:
        habit = session.get(Habit, suggestion.habit_id)
        if habit is not None and habit.user_id is not None:
            return habit.user_id
    return accepter.id


async def _apply_suggestion(session, suggestion, *, user) -> tuple[list[str], list, Approval | None]:
    from src.services.audit import log_action

    actor_id = _attributed_user_id(session, suggestion, accepter=user)
    messages: list[str] = []
    log_entries: list = []  # gom log để đẩy realtime sau commit ở caller
    prepared: list[dict] = []
    approval_steps: list[dict] = []
    approval_reasons: list[str] = []
    for step in suggestion.steps:
        slug = step["device_slug"]
        device = session.scalar(select(Device).where(Device.household_id == user.household_id, Device.slug == slug))
        if device is None:
            prepared.append(dict(step))
            continue

        # Mọi suggestion vẫn qua ma trận quyền của người chấp nhận. ``request`` chỉ
        # cho phép TẠO yêu cầu HITL; không đồng nghĩa người đó tự duyệt được lệnh.
        decision = resolve_access(session, user=user, device=device)
        prepared_step = {
            "device_slug": slug,
            "device_name": device.name,
            "room": device.room,
            "action": step["action"],
            "params": dict(step.get("params") or {}),
            "reason_vi": step.get("reason_vi", ""),
            "risk_level": str(device.risk_level),
            "allowed": True,
            "requires_approval": decision.requires_approval,
            "notify_owner": decision.notify_owner,
            "skipped": False,
        }
        prepared.append(prepared_step)
        if decision.requires_approval and decision.reason_vi not in approval_reasons:
            approval_reasons.append(decision.reason_vi)
        if decision.allowed:
            approval_steps.append(prepared_step)

    # Không execute một phần rồi mới phát hiện bước sau cần duyệt. Nếu bất kỳ bước
    # nào cần HITL, đóng băng toàn bộ phần được phép thành một Approval bền vững.
    if approval_steps and approval_reasons:
        approval = Approval(
            household_id=user.household_id,
            requested_by_id=user.id,
            conversation_id="",
            command_text=suggestion.title_vi,
            reason_vi=" ".join(approval_reasons),
            steps=approval_steps,
            required_role=str(Role.OWNER),
            status=ActionStatus.PENDING_APPROVAL,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            source="suggestion",
        )
        session.add(approval)
        log_entries.append(
            log_action(
                session,
                household_id=user.household_id,
                user_id=user.id,
                action="request_approval",
                status=ActionStatus.PENDING_APPROVAL,
                command_text=suggestion.title_vi,
                detail=approval.reason_vi,
                source="suggestion",
            )
        )
        messages.append(f"⏳ {approval.reason_vi}")
        return messages, log_entries, approval

    action_requests = agent_execution.normalize_actions(prepared)
    execution_result = await agent_execution.execute(
        session,
        actions=action_requests,
        context=ExecutionContext(
            household_id=user.household_id,
            requester_id=user.id,
            actor_id=user.id,
            attribution_user_id=actor_id,
            source="suggestion",
            origin_source="suggestion",
            command_text=suggestion.title_vi,
            original_plan=prepared,
        ),
    )
    for action in execution_result.actions:
        icon = "✅" if action.status in {"SUCCESS", "NO_OP"} else ("⛔" if action.status == "DENIED" else "❌")
        messages.append(f"{icon} {action.detail_vi}")
    for action in execution_result.actions:
        if action.status in {"SUCCESS", "NO_OP"}:
            await _notify_habit_overridden_by_suggestion(
                session, household_id=user.household_id, actor_id=actor_id,
                slug=action.device_slug, action=action.action,
            )
    return messages, log_entries, None


async def _notify_habit_overridden_by_suggestion(
    session, *, household_id: int, actor_id: int, slug: str, action: str,
) -> None:
    from src.domain.action_registry import action_label_vi

    current_hour = clock.local_now().hour
    habits = session.scalars(
        select(Habit).where(
            Habit.household_id == household_id,
            Habit.device_slug == slug,
            Habit.action != action,
            Habit.hour == current_hour,
            Habit.user_id != actor_id,
            Habit.user_id.is_not(None),
            Habit.enabled.is_(True),
        )
    ).all()
    if not habits:
        return
    users = {u.id: u for u in session.scalars(select(User).where(User.household_id == household_id)).all()}
    device = session.scalar(select(Device).where(Device.household_id == household_id, Device.slug == slug))
    device_name = device.name if device else slug
    actor = users.get(actor_id)
    actor_name = (actor.full_name or actor.username) if actor else "Hệ thống"
    action_vi = action_label_vi(action)
    for habit in habits:
        owner = users.get(habit.user_id)
        if not owner:
            continue
        habit_action_vi = action_label_vi(habit.action)
        msg = (
            f"{actor_name} vừa {action_vi} {device_name} (qua đề xuất), "
            f"khác với thói quen {habit_action_vi} lúc {habit.hour}h của bạn."
        )
        await notifications.notify_user(
            session, household_id=household_id, recipient_id=owner.id,
            notification_type="conflict_affected", message_vi=msg,
        )
