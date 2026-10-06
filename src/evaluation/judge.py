"""LLM-as-judge cho CHẤT LƯỢNG NGỮ NGHĨA của goal/plan (concern #4) — opt-in, tách gate.

Metric tất định (schema/grounding/safety) không trả lời được: goal_description có ĐÚNG ý
người dùng không? desired_outcomes có ĐỦ không? plan có COVERAGE tốt, có action THỪA
không? Judge chấm các trục đó bằng một model, trả điểm 0-1 + lý do ngắn.

Nguyên tắc:
- Judge KHÔNG bao giờ là hard gate (LLM chấm LLM — chỉ để theo dõi xu hướng, không chặn CI).
- Client judge TÁCH RIÊNG với client pipeline → token/latency judge không lẫn vào runtime.
- Chỉ chấm mẫu CÓ goal (bỏ qua clarify/social thuần — không có gì để chấm coverage).
- Không in secret; không lưu raw response.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("evaluation.judge")

JUDGE_SYSTEM_PROMPT = (
    "Bạn là giám khảo đánh giá chất lượng NGỮ NGHĨA của một trợ lý nhà thông minh tiếng Việt. "
    "Bạn nhận: câu người dùng, mục tiêu (goal_description) hệ thống hiểu được, danh sách "
    "desired_outcomes, và kế hoạch hành động (mỗi hành động gồm thiết bị + capability + action). "
    "Hãy chấm KHÁCH QUAN theo từng trục, thang 0.0–1.0. TUYỆT ĐỐI không bịa thiết bị. "
    "Nếu kế hoạch rỗng mà câu người dùng mơ hồ thì coverage có thể vẫn cao nếu việc hỏi lại là hợp lý. "
    "Trả JSON đúng schema, lý do ngắn gọn bằng tiếng Việt."
)


class JudgeVerdict(BaseModel):
    """Điểm chất lượng ngữ nghĩa do judge chấm (0.0–1.0 trừ các cờ bool)."""

    goal_faithfulness: float = Field(ge=0.0, le=1.0, description="goal_description có đúng ý người dùng")
    desired_outcomes_completeness: float = Field(ge=0.0, le=1.0, description="desired_outcomes có đủ và đúng")
    plan_coverage: float = Field(ge=0.0, le=1.0, description="kế hoạch có đạt mục tiêu người dùng")
    plan_has_redundant_actions: bool = Field(description="có hành động thừa/lặp không cần thiết")
    plan_actions_all_justified: bool = Field(description="mọi hành động đều phục vụ mục tiêu")
    rationale_vi: str = Field(default="", max_length=400)


def build_judge_client() -> Any | None:
    """Client riêng cho judge (fresh instance → counter tách khỏi pipeline). None nếu chưa cấu hình."""
    from src.nlu.model_client import build_nlu_model_client

    return build_nlu_model_client()


def _readable_plan(plan_actions: list) -> list[str]:
    return [f"{dev} · {cap} · {act}" for dev, cap, act in (plan_actions or [])]


def judge_actual(client: Any, utterance: str, actual: dict[str, Any]) -> dict[str, Any] | None:
    """Chấm một mẫu. None nếu không có goal để chấm hoặc judge lỗi (fail-open — không chặn eval)."""
    if not actual.get("goal_description") and not actual.get("plan_actions"):
        return None
    context = {
        "utterance": utterance,
        "goal_description": actual.get("goal_description"),
        "desired_outcomes_count": actual.get("desired_outcomes_count", 0),
        "plan_actions": _readable_plan(actual.get("plan_actions", [])),
        "missing_information": actual.get("missing_information", []),
        "final_decision": actual.get("decision"),
    }
    try:
        verdict: JudgeVerdict = client.structured_generate_sync(
            utterance, JudgeVerdict, system_prompt=JUDGE_SYSTEM_PROMPT, context=context, temperature=0.0
        )
    except Exception as e:  # judge lỗi KHÔNG được làm hỏng eval — bỏ qua mẫu này.
        logger.warning("Judge lỗi cho mẫu (%s) — bỏ qua: %s", utterance[:40], e)
        return None
    return verdict.model_dump()


def aggregate_judge(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Tổng hợp điểm judge trên các mẫu đã chấm. Rỗng nếu không mẫu nào có judge."""
    judged = [s["actual"]["judge"] for s in samples if s.get("actual", {}).get("judge")]
    if not judged:
        return {}

    def _mean(key: str) -> float:
        return round(sum(j[key] for j in judged) / len(judged), 4)

    return {
        "judged_samples": len(judged),
        "mean_goal_faithfulness": _mean("goal_faithfulness"),
        "mean_desired_outcomes_completeness": _mean("desired_outcomes_completeness"),
        "mean_plan_coverage": _mean("plan_coverage"),
        "redundant_plan_rate": round(sum(int(j["plan_has_redundant_actions"]) for j in judged) / len(judged), 4),
        "all_actions_justified_rate": round(sum(int(j["plan_actions_all_justified"]) for j in judged) / len(judged), 4),
    }
