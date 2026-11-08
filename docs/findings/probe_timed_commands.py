"""Probe: câu có mốc thời gian / hoãn / huỷ đi tới kết quả gì.

Chạy từ gốc repo:  OPENAI_API_KEY= .venv/bin/python docs/findings/probe_timed_commands.py
Không gọi LLM (key rỗng → đường tất định). Lệnh chỉ được lập kế hoạch, không thực thi.
"""
import os, sys, logging
sys.path.insert(0, os.getcwd()); logging.disable(logging.CRITICAL)
from datetime import UTC, datetime
from src.agent.pipeline import PipelineDeps
from src.services.pipeline_bridge import reason

NOW = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)
LIVE = {"den_chum_phong_khach": {"power": "on", "brightness": 80}}

def run(turns):
    deps = PipelineDeps(); cid = "timed-" + str(abs(hash(tuple(turns))))
    for m in turns:
        r = reason(message=m, conversation_id=cid, user_id="u", role="owner", now=NOW, deps=deps,
                   live_device_states=LIVE)
        acts = [(a.device_id, a.action.value, a.params) for a in (r.candidate_plan.actions if r.candidate_plan else [])]
        print(f"{m!r:46} -> {r.outcome:15} {acts} {(r.reply or '')[:60]!r}")

for seq in [
    ["Lát nữa tắt đèn trần phòng khách."],
    ["Mười phút nữa tắt đèn trần phòng khách."],
    ["Tắt đèn trần phòng khách sau 10 phút."],
    ["Để đèn trần phòng khách lát nữa."],
    ["10 phút"],
    ["Hẹn giờ 10 phút"],
    ["Bật đèn trần phòng khách.", "Thôi huỷ đi."],
]:
    run(seq)
