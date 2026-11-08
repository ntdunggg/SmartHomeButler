"""Probe: mức độ tương đối (chút/nhiều) có đi tới con số cuối không.

Chạy từ gốc repo:  OPENAI_API_KEY= .venv/bin/python docs/findings/probe_relative_magnitude.py
Không gọi LLM: nhánh LLM được giả lập bằng cách tiêm SemanticGoal trực tiếp vào run_planning.
"""
import os, sys, logging
sys.path.insert(0, os.getcwd()); logging.disable(logging.CRITICAL)
from datetime import UTC, datetime
from src.agent.pipeline import PipelineDeps, run_planning
from src.agent.schemas import DesiredOutcome, SemanticGoal
from src.core.interfaces import DeviceSelector
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType

def goal(token, cap, dev, dom):
    return SemanticGoal(intent="adjust", raw_utterance=f"probe {token}", utterance_type=UtteranceType.ROUTINE_INTENT,
        goal_description="adjust", confidence=0.92, target_area="Phòng khách",
        desired_outcomes=[DesiredOutcome(selector=DeviceSelector(device_id=dev, area="Phòng khách", domain=dom),
                                         relative_change={cap: token}, rationale="probe")])
for cap, dev, dom, live in [
    ("brightness", "den_chum_phong_khach", "light", {"power": "on", "brightness": 80}),
    ("temperature", "dieu_hoa_phong_khach", "air_conditioner", {"power": "on", "temperature": 26}),
]:
    for token in ["decrease_slight", "decrease", "decrease_large"]:
        st = {"conversation_id": f"p-{cap}-{token}", "user_id": "u", "user_message": f"probe {token}",
              "speaker_role": "owner", "speaker_location": "Phòng khách",
              "now": datetime(2026, 8, 20, 20, 0, tzinfo=UTC), "semantic_goal": goal(token, cap, dev, dom),
              "live_device_states": {dev: live}}
        r = run_planning(st, PipelineDeps(model_client=FakeReasoningModel()))
        g = r.get("semantic_goal"); rc = g.desired_outcomes[0].relative_change if g and g.desired_outcomes else None
        subs = [(s.capability, s.direction) for s in (r.get("subgoals") or [])]
        sp = r.get("selected_plan")
        acts = [(a.device_id, a.action, a.target) for a in (sp.actions if sp else [])]
        print(f"{cap:11} {token:16} | goal sau pipeline: {rc} | subgoals: {subs} | plan: {acts}")

# --- Nhánh lệnh tường minh (không gọi LLM) -------------------------------------------
from src.services.pipeline_bridge import reason
from src.domain.action_registry import translate_semantic_action, resolve_delta

print()
for msg in ["Giảm độ sáng đèn trần phòng khách một chút.",
            "Giảm độ sáng đèn trần phòng khách nhiều vào.",
            "Giảm độ sáng đèn trần phòng khách."]:
    r = reason(message=msg, conversation_id=f"x-{msg}", user_id="u", role="owner",
               now=datetime(2026, 8, 20, 20, 0, tzinfo=UTC), deps=PipelineDeps(),
               live_device_states={"den_chum_phong_khach": {"power": "on", "brightness": 80}})
    acts = [(a.device_id, a.action.value, a.params) for a in (r.candidate_plan.actions if r.candidate_plan else [])]
    print(f"tường minh  {msg!r:48} | plan: {acts}")
a, p = translate_semantic_action("brightness", "decrease", {})
print(f"translate_semantic_action(brightness, decrease, {{}}) -> {a} {p} -> từ 80: {resolve_delta(a, p, {'brightness': 80})}")
