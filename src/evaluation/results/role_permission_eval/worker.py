"""Chạy 100 request cho MỘT thành viên qua LLM thật, phân loại outcome + quyền theo role.

Dùng pipeline multi-agent production để hiểu + lập kế hoạch qua LLM thật, rồi
build_executable_steps để chấm quyền theo role. KHÔNG execute
lên thiết bị → mỗi request độc lập, thấy cùng trạng thái seed, so sánh được giữa các role.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

SCRATCH = "/private/tmp/claude-501/-Users-phaihoang-Documents-P-021/4bbe65d4-5541-434d-91ab-9ea7c915ade8/scratchpad"


def main() -> None:
    username = sys.argv[1]
    out_path = sys.argv[2]
    # DB riêng cho từng process để cô lập, tránh khoá ghi SQLite khi chạy song song.
    os.environ["DATABASE_URL"] = f"sqlite:///{SCRATCH}/eval_{username}.db"
    os.environ["AUTOMATION_ENABLED"] = "false"
    os.environ["APP_ENV"] = "test"
    sys.path.insert(0, SCRATCH)

    from src.config import get_settings
    get_settings.cache_clear()
    assert get_settings().llm_available, "LLM phải bật (có key, không disabled)"

    from src.db import session as db_session
    db_session.reset_engine()
    from src.iot import factory
    from src.iot.memory_bus import InMemoryBus
    factory.set_bus(InMemoryBus(latency=0))
    from src.db.seed import run_seed
    run_seed()

    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.agent.execution import build_executable_steps
    from src.domain.models import Device, User
    from src.evaluation.results.role_permission_eval.requests100 import REQUESTS
    from src.nlu.model_client import build_nlu_model_client
    from src.services import context as context_service
    from src.services.pipeline_bridge import reason
    limit = int(os.environ.get("LIMIT", "0"))
    reqs = REQUESTS[:limit] if limit else REQUESTS

    with db_session.session_scope() as sess:
        user = sess.scalar(select(User).where(User.username == username))
        if user is None:
            raise RuntimeError(f"Không tìm thấy user eval: {username}")
        role = str(user.role)
        hh = user.household_id
        devices = {d.slug: d for d in sess.scalars(select(Device).where(Device.household_id == hh))}
        live_states = context_service.device_states(sess, household_id=hh)
        live_sensors = context_service.sensor_readings(sess, household_id=hh)

        results = []
        for cat, msg in reqs:
            conv = uuid.uuid4().hex[:16]
            t = time.perf_counter()
            rec: dict[str, Any] = {"category": cat, "request": msg}
            try:
                oc = reason(
                    message=msg, conversation_id=conv, role=role,
                    user_id=str(user.id), household_id=hh, now=datetime.now(UTC), speaker_location=None,
                    live_device_states=live_states, live_sensors=live_sensors,
                    model_client=build_nlu_model_client(),
                )
                rec["latency_ms"] = int((time.perf_counter() - t) * 1000)
                rec["reused_routine"] = False
                plan = oc.candidate_plan
                if oc.outcome == "candidate_plan" and plan is not None and plan.actions:
                    steps = build_executable_steps(plan.actions, devices_by_slug=devices, session=sess, user=user)
                    actionable = [s for s in steps if s["allowed"] and not s["skipped"]]
                    blocked = [s for s in steps if not s["allowed"]]
                    risks = sorted({s["risk_level"] for s in steps})
                    rec["risks"] = risks
                    rec["n_actions"] = len(steps)
                    if actionable and any(s["requires_approval"] for s in actionable):
                        rec["outcome"] = "approval"
                    elif actionable:
                        rec["outcome"] = "executed"
                    elif blocked:
                        rec["outcome"] = "blocked"
                        rec["deny"] = blocked[0]["deny_reason_vi"][:60]
                    else:
                        rec["outcome"] = "noop"
                elif oc.outcome == "answer":
                    rec["outcome"] = "answered"
                elif oc.outcome == "cancelled":
                    rec["outcome"] = "cancelled"
                elif oc.outcome == "clarification":
                    rec["outcome"] = "clarification"
                else:
                    rec["outcome"] = "no_goal"
                rec["reply"] = (oc.reply or "")[:70]
            except Exception as e:  # noqa: BLE001 — ghi lỗi để báo cáo, không dừng cả loạt
                rec["latency_ms"] = int((time.perf_counter() - t) * 1000)
                rec["outcome"] = "error"
                rec["error"] = f"{type(e).__name__}: {e}"[:120]
            results.append(rec)

    payload = {"username": username, "role": role, "results": results}
    with open(out_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"DONE {username} ({role}) → {out_path}")


if __name__ == "__main__":
    main()
