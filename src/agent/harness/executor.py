"""Executor (spec §18, §51, §52) — thực thi vật lý qua gateway, có retry.

Bất biến (spec §73 #1, #5, #6): chỉ chạy sau khi ĐÃ validate + policy + refresh; LLM
không tham gia bước này. Natural-language response phải dựa trên ExecutionResult THẬT
(spec §51) — executor báo cáo trung thực SUCCESS/FAILED/NO_OP.
"""

from __future__ import annotations

from src.agent.harness.failure_handler import RetryPolicy, classify_failure, should_retry
from src.agent.harness.gateway import DeviceGateway, DeviceOfflineError
from src.agent.schemas import ExecutedAction, ExecutionResult, ValidatedAction


def execute_plan(
    gateway: DeviceGateway,
    actions: list[ValidatedAction],
    *,
    plan_id: str = "plan",
    noop_actions: list[ValidatedAction] | None = None,
    policy: RetryPolicy | None = None,
) -> ExecutionResult:
    """Thực thi từng action qua gateway; gộp no-op đã lọc ở reground vào kết quả."""
    policy = policy or RetryPolicy()
    executed: list[ExecutedAction] = []

    for a in actions:
        attempt = 0
        while True:
            try:
                resulting = gateway.send_command(a.entity_id, a.desired_state)
                executed.append(
                    ExecutedAction(
                        device_id=a.entity_id,
                        requested=dict(a.parameters or a.desired_state),
                        status="SUCCESS",
                        resulting_state=resulting,
                    )
                )
                break
            except DeviceOfflineError as e:
                reason = str(e)
            except Exception as e:  # noqa: BLE001 - lỗi gateway khác → coi là transient
                reason = f"TRANSIENT: {e}"
            attempt += 1
            if should_retry(reason, attempt, policy):
                continue
            executed.append(
                ExecutedAction(
                    device_id=a.entity_id,
                    requested=dict(a.parameters or a.desired_state),
                    status="FAILED",
                    reason=classify_failure(reason),
                )
            )
            break

    for a in noop_actions or []:
        executed.append(
            ExecutedAction(
                device_id=a.entity_id,
                requested=dict(a.parameters or a.desired_state),
                status="NO_OP",
                reason="Đã đúng trạng thái mong muốn",
                resulting_state=dict(a.previous_state or {}),
            )
        )

    return ExecutionResult(plan_id=plan_id, status=_overall_status(executed), actions=executed)


def _overall_status(actions: list[ExecutedAction]) -> str:
    if not actions:
        return "NO_OP"
    statuses = {a.status for a in actions}
    real = [a for a in actions if a.status != "NO_OP"]
    if not real:
        return "NO_OP"
    if statuses <= {"SUCCESS", "NO_OP"}:
        return "SUCCESS"
    if all(a.status == "FAILED" for a in real):
        return "FAILED"
    return "PARTIAL_SUCCESS"
