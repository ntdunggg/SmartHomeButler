"""Category-aware evaluator cho bộ 200 case (`smart_home_ragas_200_cases.jsonl`).

VÌ SAO CÓ FILE NÀY (đọc trước khi so sánh với `eval_ragas_agent200.py`):

`eval_ragas_agent200.py` ép 2 metric tất định + 2 LLM-judge lên MỌI case. Bốn metric
RAGAS gốc (`AgentGoalAccuracy`, `TopicAdherence`) đo SAI thứ chúng cần đo trên trace này:

  • Trace là PLANNER-ONLY: `pipeline_bridge.reason()` trả `CandidatePlan` ĐỀ XUẤT rồi
    dừng — không có `ToolMessage`, không `ExecutionResult`, không đọc lại end state.
    `AgentGoalAccuracyWithReference` suy end-state từ workflow nên 79/132 case
    tool-đúng vẫn ra 0. Đó là lỗi ĐO, không phải lỗi agent.
  • Cả 200 case mang cùng `reference_topics = [control, status, safety]`. Metric
    generic `TopicAdherence` phạt mọi câu trả lời tiếng Việt không lặp lại đúng 3
    token đó → 119/132 case tool-đúng ra 0, và nó KHÔNG thi hành yêu cầu sản phẩm
    "phần ngoài phạm vi phải bị từ chối rõ".

Evaluator này thay bằng MA TRẬN metric theo category, ưu tiên KIỂM TRA TẤT ĐỊNH từ
registry (`src/iot/registry.py` = source of truth) thay vì LLM judge khi hành vi mong
đợi xác định được bằng code:

  category                → metric CHÍNH
  single_control          → ToolCallAccuracy, ToolCallF1
  multi_step_ordered      → ToolCallAccuracy (strict_order), ToolCallF1
  multi_device_unordered  → ToolCallF1, ToolCallAccuracy
  state_query             → ToolCallAccuracy/F1 + StateAnswerAccuracy (giá trị registry)
  goal_oriented           → GoalPlanAlignment (tất định: grounding phòng/thiết bị/capability
                            + tránh thiết bị bị cấm) ; PlanSemanticAccuracy chỉ là chẩn đoán ;
                            EndStateGoalAccuracy = unavailable_due_to_harness (cần execution)
  clarification           → ClarificationAccuracy + ClarificationTargetAccuracy
  unsupported_in_domain   → UnsupportedHandlingAccuracy + HallucinatedDeviceRate
  topic_off_domain        → OutOfDomainRefusalAccuracy + UnexpectedToolCallRate
  topic_mixed             → ToolCallAccuracy/F1 (phần trong phạm vi) + MixedDomainBoundaryAccuracy
                            (phần ranh giới) — chấm TÁCH BIỆT, không gộp hai lỗi làm một

Báo cáo JSON tách bạch `primary_metrics` / `category_metrics` / `diagnostic_metrics` /
`infrastructure_errors` / `known_agent_failures` / `metric_applicability`. Lỗi hạ tầng
(judge timeout/rate-limit) KHÔNG bao giờ bị quy thành score 0 — nó vào
`infrastructure_errors`.

Cách chạy:
    python -m eval.eval_agent200_v2 --offline --limit 0        # tất cả metric tất định, không LLM
    python -m eval.eval_agent200_v2 --real-agent --repeat 3    # + agent live, trung vị 3 lượt
    python -m eval.eval_agent200_v2 --real-agent --judge-model gpt-4o-mini  # + judge chẩn đoán
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.eval_ragas_agent200 import (
    _build_sample,
    _plan_signature,
    _reply_text,
    run_case,
)
from src.domain.enums import Capability
from src.iot import registry as runtime_registry
from src.services.pipeline_bridge import ReasoningResult

_ROOT = Path(__file__).resolve().parents[1]
_DATASET = Path("/Users/phaihoang/Downloads/smart_home_ragas_200_cases.jsonl")

# ---------------------------------------------------------------------------
# Kết quả một metric cho một case (một lượt chạy)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Metric:
    """score None = KHÔNG áp dụng / lỗi hạ tầng (KHÔNG phải 0 điểm)."""

    score: float | None
    detail: str = ""
    infra_error: str | None = None
    failure_mode: str | None = None

# ---------------------------------------------------------------------------
# Trợ giúp registry
# ---------------------------------------------------------------------------
def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _git_dirty() -> bool | None:
    try:
        return bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except Exception:  # noqa: BLE001
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _infra(detail: str) -> Metric:
    """Một lỗi contract/harness không phải là agent failure và không được thành điểm 0."""
    return Metric(None, detail=detail, infra_error=detail)


def _canonical_room(text: str | None) -> str | None:
    """Cụm phòng tự do → tên phòng chuẩn trong ROOMS, qua ROOM_ALIASES (alias dài thắng)."""
    if not text:
        return None
    low = text.lower()
    for canon in runtime_registry.ROOMS:
        if canon.lower() in low:
            return canon
    best: tuple[str, int] | None = None
    for canon, aliases in runtime_registry.ROOM_ALIASES.items():
        for alias in aliases:
            if alias in low and (best is None or len(alias) > best[1]):
                best = (canon, len(alias))
    return best[0] if best is not None else None


def _device_slugs_in_calls(calls: list[dict[str, Any]], names: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for call in calls:
        if call["name"] in names:
            slug = call["args"].get("device_slug") or call["args"].get("sensor_slug")
            if slug:
                out.append(str(slug))
    return out


# ---------------------------------------------------------------------------
# StateAnswerAccuracy — giá trị đọc từ registry, không phải câu chữ mơ hồ trong dataset
# ---------------------------------------------------------------------------
# cue trong câu hỏi người dùng → field trạng thái được hỏi. Kiểm theo thứ tự: cue cụ
# thể nằm trước cue chung ("bao nhiêu độ" trước "nhiệt độ").
_FIELD_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("locked", ("khóa chưa", "khoá chưa", "đã khóa", "đã khoá")),
    ("battery", ("bao nhiêu pin", "còn bao nhiêu pin", "mức pin", "pin")),
    ("temperature", ("bao nhiêu độ", "để bao nhiêu", "mấy độ", "nhiệt độ")),
    ("brightness", ("sáng mức bao nhiêu", "độ sáng", "sáng mức", "sáng bao nhiêu")),
    ("fan_speed", ("quạt mức", "tốc độ quạt", "chạy quạt mức", "mức quạt")),
    ("position", ("mở bao nhiêu", "bao nhiêu phần trăm", "mấy phần trăm", "mở mức")),
    ("power", ("đang bật hay tắt", "bật hay tắt", "có đang bật", "có bật không", "đang chạy không", "có đang chạy")),
)


def _queried_field(user_input: str) -> str | None:
    low = user_input.lower()
    for field_name, cues in _FIELD_CUES:
        if any(cue in low for cue in cues):
            return field_name
    return None


def _seeded_state(case: dict, slug: str) -> dict[str, Any]:
    spec = runtime_registry.DEVICE_BY_SLUG.get(slug)
    state = dict(spec.initial_state) if spec is not None else {}
    override = (case.get("initial_state_overrides") or {}).get(slug)
    if isinstance(override, dict):
        state.update(override)
    return state


def m_state_answer(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]) -> Metric:
    ref_calls = case.get("reference_tool_calls") or []
    if not ref_calls:
        return _infra("state_query case thiếu reference_tool_calls")
    ref_args = ref_calls[0].get("args")
    if not isinstance(ref_args, dict):
        return _infra("state_query reference_tool_calls[0] thiếu args hợp lệ")
    target = ref_args.get("device_slug") or ref_args.get("sensor_slug")
    if not target:
        return _infra("state_query reference tool call thiếu device_slug/sensor_slug")
    is_sensor = "sensor_slug" in ref_args

    if result.outcome != "answer":
        return Metric(0.0, f"không trả lời từ trạng thái (outcome={result.outcome})", failure_mode="not_answered_from_state")

    read = _device_slugs_in_calls(calls, ("query_device_state", "query_sensor"))
    if target not in read:
        return Metric(0.0, f"đọc sai đích: cần {target}, đã đọc {read or '∅'}", failure_mode="wrong_state_target")

    reply = result.reply or ""
    low = reply.lower()

    if is_sensor:
        sensor = runtime_registry.SENSOR_BY_SLUG.get(target)
        if sensor is None:
            return _infra(f"sensor {target} không có trong runtime registry")
        val = sensor.value
        if sensor.sensor_type.value == "presence":
            want = "có người" if val >= 1 else ("không có ai" if val < 1 else "")
            ok = want in low
            return Metric(1.0 if ok else 0.0, f"presence {val} → mong '{want}', reply={reply!r}",
                          failure_mode=None if ok else "state_answer_wrong")
        if sensor.sensor_type.value == "rain":
            want_rain = val > 0
            ok = ("đang mưa" in low) if want_rain else ("không mưa" in low)
            return Metric(1.0 if ok else 0.0, f"rain {val} → reply={reply!r}",
                          failure_mode=None if ok else "state_answer_wrong")
        num = int(val) if float(val).is_integer() else round(val, 1)
        ok = str(num) in reply
        return Metric(1.0 if ok else 0.0, f"cần giá trị {num}{sensor.unit}, reply={reply!r}",
                      failure_mode=None if ok else "state_answer_wrong")

    field_name = _queried_field(case["user_input"]) or "power"
    if target not in runtime_registry.DEVICE_BY_SLUG:
        return _infra(f"device {target} không có trong runtime registry")
    state = _seeded_state(case, target)
    expected = state.get(field_name)
    powered_off = state.get("power") == "off"

    if field_name == "power":
        want_on = expected == "on"
        ok = ("đang bật" in low or "đang mở" in low or "vẫn bật" in low) if want_on else (
            "đang tắt" in low or "chưa bật" in low or "đang đóng" in low
        )
        return Metric(1.0 if ok else 0.0, f"power mong {'on' if want_on else 'off'}, reply={reply!r}",
                      failure_mode=None if ok else "state_answer_wrong")
    if field_name == "locked":
        ok = ("đang khoá" in low or "đang khóa" in low or "đã khoá" in low or "đã khóa" in low) if expected else (
            "mở khoá" in low or "mở khóa" in low or "chưa khoá" in low or "chưa khóa" in low
        )
        return Metric(1.0 if ok else 0.0, f"locked={expected}, reply={reply!r}",
                      failure_mode=None if ok else "state_answer_wrong")
    if field_name == "position":
        pos = expected
        if pos == 100:
            ok = "mở hoàn toàn" in low or "100" in reply
        elif pos == 0:
            ok = "đang đóng" in low or "0%" in reply
        else:
            ok = str(pos) in reply
        return Metric(1.0 if ok else 0.0, f"position={pos}, reply={reply!r}",
                      failure_mode=None if ok else "state_answer_wrong")

    # field số còn lại: temperature / brightness / fan_speed / battery
    if expected is None:
        return _infra(f"runtime registry không có field {field_name} cho {target}")
    num = int(expected) if float(expected).is_integer() else expected
    ok = str(num) in reply
    if not ok and powered_off and ("đang tắt" in low or "chưa bật" in low):
        return Metric(1.0, f"{field_name} setpoint {num} nhưng thiết bị OFF; reply nêu tắt là chấp nhận: {reply!r}")
    return Metric(1.0 if ok else 0.0, f"{field_name} mong {num}, reply={reply!r}",
                  failure_mode=None if ok else "state_answer_wrong")


# ---------------------------------------------------------------------------
# GoalPlanAlignment — grounding TẤT ĐỊNH của plan đề xuất vs registry + context
# ---------------------------------------------------------------------------
_WHOLE_HOME_CUES = (
    "ra ngoài", "đi làm", "rời nhà", "ra khỏi nhà", "đi ra ngoài", "cả nhà", "toàn bộ nhà",
    "các phòng", "mọi phòng", "tất cả các phòng", "tiết kiệm điện", "đóng các cửa",
    "đóng hết cửa", "khóa cửa", "khoá cửa", "đóng cửa sổ", "các cửa sổ",
)


def _allowed_rooms(case: dict, sg: Any) -> set[str]:
    rooms: set[str] = set()
    ctx_room = _canonical_room((case.get("context") or {}).get("current_room"))
    if ctx_room:
        rooms.add(ctx_room)
    target_area = getattr(sg, "target_area", None) if sg is not None else None
    canon_target = _canonical_room(target_area) or (
        target_area if target_area in runtime_registry.ROOMS else None
    )
    if canon_target:
        rooms.add(canon_target)
    if any(cue in case["user_input"].lower() for cue in _WHOLE_HOME_CUES):
        rooms.update(runtime_registry.ROOMS)
    return rooms


def m_goal_plan_alignment(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]) -> Metric:
    sg = result.semantic_goal
    plan = result.candidate_plan

    if result.outcome == "clarification":
        return Metric(0.0, "goal thu về câu hỏi lại thay vì kế hoạch", failure_mode="plan_collapsed_to_clarification")
    if result.outcome in ("no_action", "cancelled") or plan is None or not plan.actions:
        # Một số goal phủ định thuần ("đừng...") hợp lệ khi không có hành động; nhưng bộ
        # goal_oriented này đều là achieve_goal → no_action ở đây là nếp bị sập.
        return Metric(0.0, f"goal không sinh kế hoạch (outcome={result.outcome})", failure_mode="plan_collapsed_to_no_action")

    allowed = _allowed_rooms(case, sg)
    excluded = set(getattr(sg, "excluded_device_ids", []) or []) | set(getattr(sg, "no_change_device_ids", []) or [])

    bad_room: list[str] = []
    bad_device: list[str] = []
    bad_cap: list[str] = []
    forbidden: list[str] = []

    for action in plan.actions:
        slug = action.device_id
        spec = runtime_registry.DEVICE_BY_SLUG.get(slug)
        if spec is None:
            bad_device.append(slug)
            continue
        cap = getattr(action.capability, "value", str(action.capability))
        try:
            if Capability(cap) not in spec.capabilities:
                bad_cap.append(f"{slug}:{cap}")
        except ValueError:
            bad_cap.append(f"{slug}:{cap}")
        if allowed and spec.room not in allowed:
            bad_room.append(f"{slug}({spec.room})")
        if slug in excluded:
            forbidden.append(slug)

    problems: list[str] = []
    fmode: str | None = None
    if bad_device:
        problems.append(f"thiết bị không có trong registry: {bad_device}")
        fmode = "hallucinated_device"
    if bad_cap:
        problems.append(f"capability không được hỗ trợ: {bad_cap}")
        fmode = fmode or "unsupported_capability"
    if bad_room:
        problems.append(f"ground sai phòng (cho phép {sorted(allowed)}): {bad_room}")
        fmode = fmode or "wrong_room_grounding"
    if forbidden:
        problems.append(f"chạm thiết bị người dùng đã loại trừ: {forbidden}")
        fmode = fmode or "forbidden_device_touched"

    if problems:
        return Metric(0.0, "; ".join(problems), failure_mode=fmode)
    checked = "phòng+" if allowed else ""
    return Metric(1.0, f"kế hoạch {len(plan.actions)} bước, grounding {checked}thiết bị+capability hợp lệ")


# ---------------------------------------------------------------------------
# Clarification — không tự chọn khi registry có ≥2 ứng viên hợp lệ
# ---------------------------------------------------------------------------
_TARGET_Q_CUES = (
    "phòng nào", "phòng bố mẹ", "phòng con", "phòng khách", "phòng bếp", "thiết bị nào",
    "đèn nào", "loa nào", "tv nào", "cửa nào", "rèm nào", "máy lọc nào", "điều hòa nào",
    "điều hoà nào", "cái nào", "ở đâu", "chỗ nào", "cửa sổ nào",
)
_VALUE_Q_CUES = (
    "bao nhiêu độ", "âm lượng", "độ sáng bao nhiêu", "mức bao nhiêu", "nhiệt độ bao nhiêu",
    "muốn đặt", "giá trị nào", "mấy phần trăm", "bao nhiêu phần trăm",
)


def m_clarification(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]) -> tuple[Metric, Metric]:
    device_calls = [c for c in calls if c["name"] == "control_device"]
    is_clar = result.outcome == "clarification"
    if device_calls:
        touched = sorted({c["args"].get("device_slug") for c in device_calls})
        acc = Metric(0.0, f"tự chọn thiết bị {touched} thay vì hỏi lại", failure_mode="auto_selected_ambiguous_target")
        return acc, Metric(None, "không chấm target vì đã hành động")
    if not is_clar:
        acc = Metric(0.0, f"không hỏi lại (outcome={result.outcome}): {result.reply!r}", failure_mode="missing_clarification")
        return acc, Metric(None, "không chấm target vì không có câu hỏi")

    acc = Metric(1.0, "không hành động + hỏi lại")
    question = ""
    if result.clarification is not None:
        question = result.clarification.question_vi or ""
    question = (question or result.reply or "").lower()
    asks_target = any(cue in question for cue in _TARGET_Q_CUES)
    asks_value = any(cue in question for cue in _VALUE_Q_CUES)
    if asks_target and not asks_value:
        tgt = Metric(1.0, f"hỏi đúng slot đích: {question!r}")
    elif asks_value:
        tgt = Metric(0.0, f"hỏi giá trị trong khi slot thiếu là phòng/thiết bị: {question!r}",
                     failure_mode="wrong_clarification_slot")
    else:
        tgt = Metric(0.0, f"câu hỏi không nêu rõ slot đích: {question!r}", failure_mode="vague_clarification")
    return acc, tgt


# ---------------------------------------------------------------------------
# Unsupported in-domain — thiết bị/capability KHÔNG tồn tại
# ---------------------------------------------------------------------------
_ABSENT_CUES = (
    "không có", "không tồn tại", "không tìm thấy", "chưa được lắp", "chưa lắp", "không hỗ trợ",
    "không điều khiển được", "không phải thiết bị", "không nhận ra thiết bị", "hiện không có",
    "nhà mình không", "không lắp", "không khả dụng",
)


def m_unsupported(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]) -> tuple[Metric, Metric]:
    dev_calls = _device_slugs_in_calls(calls, ("control_device", "query_device_state"))
    hallucinated = len(dev_calls) > 0
    reply = (result.reply or "")
    low = reply.lower()
    says_absent = any(cue in low for cue in _ABSENT_CUES)
    ref = (case.get("reference_goal") or "").lower()
    clarify_allowed = ("làm rõ" in ref or "hoặc" in ref) and result.outcome == "clarification"

    halluc_metric = Metric(
        1.0 if hallucinated else 0.0,
        f"gọi tool lên thiết bị {dev_calls}" if hallucinated else "không gọi tool thiết bị",
        failure_mode="hallucinated_device" if hallucinated else None,
    )

    if hallucinated:
        handling = Metric(0.0, f"hallucinate tool call: {dev_calls}", failure_mode="hallucinated_device")
    elif says_absent or clarify_allowed:
        handling = Metric(1.0, f"báo thiết bị không khả dụng: {reply!r}")
    else:
        handling = Metric(
            0.0,
            f"không báo thiết bị không tồn tại (outcome={result.outcome}): {reply!r}",
            failure_mode="generic_clarify_for_nonexistent",
        )
    return handling, halluc_metric


# ---------------------------------------------------------------------------
# Out-of-domain — không gọi tool, không làm việc ngoài phạm vi, giữ ranh giới
# ---------------------------------------------------------------------------
_BOUNDARY_CUES = (
    "ngoài phạm vi", "chỉ hỗ trợ", "chỉ xử lý", "nhà thông minh", "không thể giúp", "mình chỉ",
    "chỉ có thể", "không nằm trong phạm vi", "ngoài khả năng", "không thực hiện được yêu cầu này",
    "mình không hỗ trợ", "không phải việc", "chỉ điều khiển",
)


def m_ood(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]) -> tuple[Metric, Metric]:
    any_tool = len(calls) > 0
    unexpected = Metric(
        1.0 if any_tool else 0.0,
        f"gọi {len(calls)} tool cho câu ngoài phạm vi: {calls}" if any_tool else "không gọi tool",
        failure_mode="off_domain_tool_call" if any_tool else None,
    )
    reply = result.reply or ""
    low = reply.lower()
    stays = any(cue in low for cue in _BOUNDARY_CUES)
    if any_tool:
        refusal = Metric(0.0, f"gọi tool thay vì từ chối: {calls}", failure_mode="off_domain_tool_call")
    elif result.outcome == "candidate_plan":
        refusal = Metric(0.0, "sinh kế hoạch cho yêu cầu ngoài phạm vi", failure_mode="off_domain_acted")
    elif stays:
        refusal = Metric(1.0, f"từ chối, giữ ranh giới: {reply!r}")
    else:
        refusal = Metric(0.0, f"không nêu rõ ranh giới smart-home: {reply!r}", failure_mode="missing_boundary_statement")
    return refusal, unexpected


# ---------------------------------------------------------------------------
# Mixed-domain — phần trong phạm vi (tool metrics lo) + phần ranh giới (metric này)
# ---------------------------------------------------------------------------
def m_mixed_boundary(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]) -> Metric:
    low = (result.reply or "").lower()
    refuses = any(cue in low for cue in _BOUNDARY_CUES)
    if refuses:
        return Metric(1.0, f"nêu rõ phần ngoài phạm vi không thực hiện: {result.reply!r}")
    return Metric(0.0, f"làm phần smart-home nhưng KHÔNG từ chối rõ phần ngoài phạm vi: {result.reply!r}",
                  failure_mode="missing_boundary_refusal")


# ---------------------------------------------------------------------------
# Ma trận metric theo category
# ---------------------------------------------------------------------------
_LEGACY_DIAGNOSTICS = ["agent_goal_accuracy", "topic_adherence"]

CATEGORY_PLAN: dict[str, dict[str, Any]] = {
    "single_control": {
        "primary": ["tool_call_accuracy", "tool_call_f1"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": True,
        "strict_order": True,
    },
    "multi_step_ordered": {
        "primary": ["tool_call_accuracy", "tool_call_f1"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": True,
        "strict_order": True,
    },
    "multi_device_unordered": {
        "primary": ["tool_call_f1", "tool_call_accuracy"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": True,
        "strict_order": False,
    },
    "state_query": {
        "primary": ["tool_call_accuracy", "tool_call_f1", "state_answer_accuracy"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": True,
        "strict_order": True,
    },
    "goal_oriented": {
        "primary": ["goal_plan_alignment"],
        "diagnostic": [*_LEGACY_DIAGNOSTICS, "plan_semantic_accuracy", "end_state_goal_accuracy"],
        "tool_metrics_applicable": False,
        "strict_order": False,
    },
    "clarification": {
        "primary": ["clarification_accuracy", "clarification_target_accuracy"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": False,
        "strict_order": False,
    },
    "unsupported_in_domain": {
        "primary": ["unsupported_handling_accuracy", "hallucinated_device_rate"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": False,
        "strict_order": False,
    },
    "topic_off_domain": {
        "primary": ["out_of_domain_refusal_accuracy", "unexpected_tool_call_rate"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": False,
        "strict_order": False,
    },
    "topic_mixed": {
        "primary": ["tool_call_accuracy", "tool_call_f1", "mixed_domain_boundary_accuracy"],
        "diagnostic": _LEGACY_DIAGNOSTICS,
        "tool_metrics_applicable": True,
        "strict_order": True,
    },
}

# metric nào "thấp hơn là tốt" — pass khi = 0.
_RATE_METRICS = {"hallucinated_device_rate", "unexpected_tool_call_rate"}
# metric CHẨN ĐOÁN, KHÔNG bao giờ là release gate.
_DIAGNOSTIC_METRICS = {
    "agent_goal_accuracy",
    "topic_adherence",
    "plan_semantic_accuracy",
    "end_state_goal_accuracy",
}

_CUSTOM_DETERMINISTIC_METRICS = {
    category: [name for name in plan["primary"] if name not in {"tool_call_accuracy", "tool_call_f1"}]
    for category, plan in CATEGORY_PLAN.items()
}


def validate_cases(cases: list[dict]) -> None:
    """Fail fast nếu dataset không còn khớp ma trận evaluator đã duyệt."""
    errors: list[str] = []
    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"row-{index}")
        if case_id in seen:
            errors.append(f"{case_id}: case_id trùng")
        seen.add(case_id)
        category = case.get("category")
        plan = CATEGORY_PLAN.get(category)
        if plan is None:
            errors.append(f"{case_id}: category không hỗ trợ {category!r}")
            continue
        for field_name in ("user_input", "reference_tool_calls"):
            if field_name not in case:
                errors.append(f"{case_id}: thiếu field {field_name}")
        for flag in ("tool_metrics_applicable", "strict_order"):
            actual = case.get(flag)
            expected = plan[flag]
            if actual is not expected:
                errors.append(f"{case_id}: {flag}={actual!r}, ma trận yêu cầu {expected!r}")
    if errors:
        preview = "\n  - ".join(errors[:20])
        suffix = f"\n  ... và {len(errors) - 20} lỗi khác" if len(errors) > 20 else ""
        raise ValueError(f"Dataset không khớp evaluator contract:\n  - {preview}{suffix}")


def score_case_deterministic(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]) -> dict[str, Metric]:
    cat = case["category"]
    if cat not in CATEGORY_PLAN:
        raise ValueError(f"category không hỗ trợ: {cat!r}")
    out: dict[str, Metric] = {}
    try:
        if cat == "state_query":
            out["state_answer_accuracy"] = m_state_answer(case, result, calls)
        elif cat == "goal_oriented":
            out["goal_plan_alignment"] = m_goal_plan_alignment(case, result, calls)
            out["end_state_goal_accuracy"] = Metric(
                None,
                "unavailable_due_to_harness: trace planner-only, không có execution/end state",
            )
        elif cat == "clarification":
            acc, tgt = m_clarification(case, result, calls)
            out["clarification_accuracy"] = acc
            out["clarification_target_accuracy"] = tgt
        elif cat == "unsupported_in_domain":
            handling, halluc = m_unsupported(case, result, calls)
            out["unsupported_handling_accuracy"] = handling
            out["hallucinated_device_rate"] = halluc
        elif cat == "topic_off_domain":
            refusal, unexpected = m_ood(case, result, calls)
            out["out_of_domain_refusal_accuracy"] = refusal
            out["unexpected_tool_call_rate"] = unexpected
        elif cat == "topic_mixed":
            out["mixed_domain_boundary_accuracy"] = m_mixed_boundary(case, result, calls)
    except Exception as exc:  # noqa: BLE001
        detail = f"deterministic evaluator error: {type(exc).__name__}: {exc}"[:300]
        out = {name: _infra(detail) for name in _CUSTOM_DETERMINISTIC_METRICS[cat]}
    return out


# ---------------------------------------------------------------------------
# Tool metrics (RAGAS, tất định nhưng async) + judge chẩn đoán
# ---------------------------------------------------------------------------
async def _tool_scores(sample: Any, case: dict, acc_metric: Any, f1_metric: Any) -> dict[str, Metric]:
    out: dict[str, Metric] = {}
    if not case.get("tool_metrics_applicable"):
        return out
    acc_metric.strict_order = bool(case.get("strict_order", True))
    for name, metric in (("tool_call_accuracy", acc_metric), ("tool_call_f1", f1_metric)):
        try:
            out[name] = Metric(float(await metric.multi_turn_ascore(sample)))
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"[:200]
            out[name] = Metric(None, detail=detail, infra_error=detail)
    return out


# ---------------------------------------------------------------------------
# Gộp repeat
# ---------------------------------------------------------------------------
def _fold(values: list[float | None]) -> float | None:
    observed = [float(v) for v in values if v is not None]
    return statistics.median_low(observed) if observed else None


def _is_pass(name: str, score: float) -> bool:
    return score < 0.999 if name in _RATE_METRICS else score >= 0.999


def _fold_metric(runs: list[Metric]) -> Metric:
    folded = _fold([m.score for m in runs])
    # detail/failure_mode lấy từ lượt có score = folded (đại diện), nếu không có thì lượt đầu.
    rep = next((m for m in runs if m.score == folded), runs[0])
    infra = next((m.infra_error for m in runs if m.infra_error), None)
    return Metric(folded, rep.detail, infra_error=infra, failure_mode=rep.failure_mode)


# ---------------------------------------------------------------------------
# Báo cáo
# ---------------------------------------------------------------------------
_METRIC_APPLICABILITY_NOTES = {
    "agent_goal_accuracy": (
        "GỠ KHỎI GATING. Trace là planner-only (reason() dừng ở CandidatePlan; không ToolMessage/"
        "ExecutionResult/end state). AgentGoalAccuracyWithReference suy end-state từ workflow nên "
        "phạt oan ~79/132 case tool-đúng. Thay bằng goal_plan_alignment (tất định) + "
        "plan_semantic_accuracy (chẩn đoán)."
    ),
    "topic_adherence": (
        "GỠ KHỎI GATING. Cả 200 case cùng reference_topics=[control,status,safety]; metric generic "
        "phạt mọi câu trả lời tiếng Việt không lặp token đó (~119/132 case tool-đúng ra 0) và không "
        "thi hành yêu cầu 'từ chối rõ phần ngoài phạm vi'. Thay bằng out_of_domain_refusal_accuracy "
        "+ mixed_domain_boundary_accuracy + unexpected_tool_call_rate."
    ),
    "end_state_goal_accuracy": (
        "unavailable_due_to_harness. Cần execution thật (DB Session + simulator bus + HITL Approval "
        "mỗi case) — nằm ở agent_runner, ngoài runner planner-only này. Gate goal_oriented bằng "
        "goal_plan_alignment cho tới khi có execution harness."
    ),
    "goal_plan_alignment": (
        "Tất định: kiểm grounding phòng (vs context.current_room / target_area / whole-home cue), "
        "grounding thiết bị (registry), capability (spec.capabilities), tránh excluded/no_change. "
        "KHÔNG chấm hướng ngữ nghĩa tinh vi (bật vs tắt đèn tác vụ) — việc đó để plan_semantic_accuracy."
    ),
}


def _tool_failure_evidence(row: dict) -> tuple[str, str]:
    actual = row.get("agent_tool_calls") or []
    expected = row.get("reference_tool_calls") or []
    detail = f"expected={expected!r}; actual={actual!r}"
    if expected and not actual:
        return "missing_tool_call", detail
    if actual and not expected:
        return "unexpected_tool_call", detail
    if len(actual) == len(expected):
        expected_names = [call.get("name") for call in expected]
        actual_names = [call.get("name") for call in actual]
        if expected_names != actual_names:
            return "wrong_tool_name_or_order", detail
        expected_targets = [
            call.get("args", {}).get("device_slug") or call.get("args", {}).get("sensor_slug")
            for call in expected
        ]
        actual_targets = [
            call.get("args", {}).get("device_slug") or call.get("args", {}).get("sensor_slug")
            for call in actual
        ]
        if expected_targets != actual_targets:
            return "wrong_tool_target", detail
        return "wrong_tool_arguments", detail
    return "tool_call_count_mismatch", detail


def build_report(rows: list[dict], *, meta: dict, judged: bool) -> dict:
    cat_metric_vals: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    infra: dict[str, list[dict]] = defaultdict(list)
    known_failures: list[dict] = []

    for row in rows:
        cat = row["category"]
        for name, m in row["metrics"].items():
            if m.infra_error:
                infra[name].append({"case_id": row["case_id"], "error": m.infra_error})
            if m.score is None:
                continue
            cat_metric_vals[cat][name].append(m.score)
            failed = not _is_pass(name, m.score)
            if failed and name in CATEGORY_PLAN[cat]["primary"]:
                failure_mode = m.failure_mode
                detail = m.detail
                if name in {"tool_call_accuracy", "tool_call_f1"} and not failure_mode:
                    failure_mode, detail = _tool_failure_evidence(row)
                known_failures.append(
                    {
                        "case_id": row["case_id"],
                        "category": cat,
                        "user_input": row["user_input"],
                        "metric": name,
                        "failure_mode": failure_mode
                        or ("rate_nonzero" if name in _RATE_METRICS else "metric_failed"),
                        "detail": detail,
                    }
                )

    def agg(names: set[str]) -> dict[str, dict[str, float | int | None]]:
        block: dict[str, dict[str, float | int | None]] = {}
        for cat, metrics in cat_metric_vals.items():
            for name, vals in metrics.items():
                if name not in names or not vals:
                    continue
                cur = block.setdefault(name, {"mean": 0.0, "n_scored": 0, "n_pass": 0})
                cur["_sum"] = cur.get("_sum", 0.0) + sum(vals)  # type: ignore[operator]
                cur["n_scored"] = int(cur["n_scored"]) + len(vals)
                cur["n_pass"] = int(cur["n_pass"]) + sum(1 for v in vals if _is_pass(name, v))
        for name, cur in block.items():
            cur["mean"] = round(float(cur.pop("_sum", 0.0)) / cur["n_scored"], 4) if cur["n_scored"] else None
        return block

    primary_names = {n for plan in CATEGORY_PLAN.values() for n in plan["primary"]}
    diag_names = {n for plan in CATEGORY_PLAN.values() for n in plan["diagnostic"]}

    primary_flat = agg(primary_names)
    diag_flat = agg(diag_names)

    def pick(*names: str) -> dict:
        return {n: primary_flat[n] for n in names if n in primary_flat}

    categories_present = sorted({row["category"] for row in rows})
    category_metrics: dict[str, dict[str, Any]] = {}
    for cat in categories_present:
        plan = CATEGORY_PLAN[cat]
        n_cases = sum(1 for row in rows if row["category"] == cat)
        entry: dict[str, Any] = {"n_cases": n_cases, "metrics": {}}
        for role in ("primary", "diagnostic"):
            for name in plan[role]:
                vals = cat_metric_vals[cat].get(name, [])
                metric_entry: dict[str, Any] = {
                    "mean": round(sum(vals) / len(vals), 4) if vals else None,
                    "n_applicable": n_cases,
                    "n_scored": len(vals),
                    "n_pass": sum(1 for value in vals if _is_pass(name, value)),
                    "role": role,
                }
                if name == "end_state_goal_accuracy":
                    metric_entry["status"] = "unavailable_due_to_harness"
                elif not vals and role == "diagnostic" and not judged:
                    metric_entry["status"] = "not_run_in_deterministic_baseline"
                elif not vals:
                    metric_entry["status"] = "unscored"
                entry["metrics"][name] = metric_entry
        category_metrics[cat] = entry

    metric_applicability = {
        cat: {
            "primary": plan["primary"],
            "diagnostic": plan["diagnostic"],
            "tool_metrics_applicable": plan["tool_metrics_applicable"],
            "strict_order": plan["strict_order"],
        }
        for cat, plan in CATEGORY_PLAN.items()
    }
    metric_applicability["_notes"] = _METRIC_APPLICABILITY_NOTES

    by_case_id: dict[str, dict[str, Any]] = {}
    for failure in known_failures:
        case_entry = by_case_id.setdefault(
            failure["case_id"],
            {
                "category": failure["category"],
                "user_input": failure["user_input"],
                "failures": [],
            },
        )
        case_entry["failures"].append(
            {
                "metric": failure["metric"],
                "failure_mode": failure["failure_mode"],
                "detail": failure["detail"],
            }
        )

    def failure_breakdown(key: str) -> dict[str, int]:
        return dict(sorted(Counter(failure[key] for failure in known_failures).items()))

    category_cases: dict[str, set[str]] = defaultdict(set)
    for failure in known_failures:
        category_cases[failure["category"]].add(failure["case_id"])
    failure_analysis = {
        "affected_case_count": len(by_case_id),
        "failure_event_count": len(known_failures),
        "by_category": {
            category: {
                "affected_case_count": len(case_ids),
                "failure_event_count": sum(1 for failure in known_failures if failure["category"] == category),
            }
            for category, case_ids in sorted(category_cases.items())
        },
        "by_metric": failure_breakdown("metric"),
        "by_failure_mode": failure_breakdown("failure_mode"),
        "by_case_id": by_case_id,
    }

    diagnostic_metrics: dict[str, Any] = {}
    for name in ("agent_goal_accuracy", "topic_adherence", "plan_semantic_accuracy"):
        if name in diag_flat:
            diagnostic_metrics[name] = {**diag_flat[name], "role": "diagnostic", "gating": False}
        else:
            diagnostic_metrics[name] = {
                "mean": None,
                "n_scored": 0,
                "n_pass": 0,
                "role": "diagnostic",
                "gating": False,
                "status": "not_run_in_deterministic_baseline" if not judged else "unscored",
            }
    diagnostic_metrics["end_state_goal_accuracy"] = {
        "mean": None,
        "n_scored": 0,
        "n_pass": 0,
        "role": "diagnostic",
        "gating": False,
        "status": "unavailable_due_to_harness",
    }
    diagnostic_metrics["note"] = "Các metric ở đây KHÔNG phải release gate."

    return {
        "meta": meta,
        "primary_metrics": {
            "tool_execution": pick("tool_call_accuracy", "tool_call_f1"),
            "reasoning": pick(
                "goal_plan_alignment",
                "clarification_accuracy",
                "clarification_target_accuracy",
                "state_answer_accuracy",
            ),
            "boundary": pick(
                "unsupported_handling_accuracy",
                "hallucinated_device_rate",
                "out_of_domain_refusal_accuracy",
                "unexpected_tool_call_rate",
                "mixed_domain_boundary_accuracy",
            ),
            "end_to_end": {"end_state_goal_accuracy": None, "status": "unavailable_due_to_harness"},
        },
        "category_metrics": category_metrics,
        "diagnostic_metrics": diagnostic_metrics,
        "infrastructure_errors": {k: v for k, v in infra.items()},
        "known_agent_failures": known_failures,
        "failure_analysis": failure_analysis,
        "metric_applicability": metric_applicability,
    }


def print_tables(report: dict) -> None:
    print("\n=== PRIMARY METRICS ===")
    for group, metrics in report["primary_metrics"].items():
        print(f"  [{group}]")
        for name, v in metrics.items():
            if isinstance(v, dict) and "mean" in v:
                print(f"    {name:34} {v['mean']!s:>7}   (n={v['n_scored']}, pass={v['n_pass']})")
            else:
                print(f"    {name:34} {v!s:>7}")
    print("\n=== CATEGORY METRICS ===")
    for cat, entry in report["category_metrics"].items():
        print(f"  {cat} (n={entry['n_cases']})")
        for name, v in entry["metrics"].items():
            print(f"    {name:34} {v['mean']!s:>7}   (n={v['n_scored']}, pass={v['n_pass']}, {v['role']})")
    kf = report["known_agent_failures"]
    print(f"\n=== KNOWN AGENT FAILURES: {len(kf)} ===")
    by_mode = Counter(f["failure_mode"] for f in kf)
    for mode, n in by_mode.most_common():
        print(f"    {mode:32} {n}")
    if report["infrastructure_errors"]:
        print("\n=== INFRASTRUCTURE ERRORS ===")
        for name, errs in report["infrastructure_errors"].items():
            print(f"    {name}: {len(errs)} case")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(_DATASET))
    ap.add_argument("--limit", type=int, default=0, help="0 = tất cả")
    ap.add_argument("--category", default=None)
    ap.add_argument("--offline", action="store_true", help="agent Fake, chỉ metric tất định")
    ap.add_argument("--real-agent", action="store_true", help="agent model live")
    ap.add_argument(
        "--judge-model",
        default=None,
        help="bật AgentGoalAccuracy, TopicAdherence và PlanSemanticAccuracy (chỉ chẩn đoán)",
    )
    ap.add_argument("--repeat", type=int, default=1, help="chạy mỗi case N lần, lấy trung vị (số LẺ)")
    ap.add_argument("--agent-temperature", type=float, default=0.0)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument(
        "--out", default=str(_ROOT / "eval" / "results" / "report_agent200_v2.json")
    )
    ap.add_argument(
        "--samples-out",
        default=str(_ROOT / "eval" / "results" / "report_agent200_v2_samples.json"),
    )
    args = ap.parse_args()

    if args.offline and args.real_agent:
        ap.error("--offline và --real-agent loại trừ nhau")
    if args.offline and args.judge_model:
        ap.error("--offline không chạy LLM diagnostics; bỏ --judge-model")
    if args.repeat < 1:
        ap.error("--repeat phải >= 1")
    if args.limit < 0:
        ap.error("--limit phải >= 0")
    if args.category and args.category not in CATEGORY_PLAN:
        ap.error(f"category không hỗ trợ: {args.category!r}")

    from eval.eval_ragas import _ensure_ragas_importable

    _ensure_ragas_importable()
    from ragas.metrics import _ToolCallAccuracy as ToolCallAccuracy
    from ragas.metrics import _ToolCallF1 as ToolCallF1

    dataset_path = Path(args.dataset)
    cases = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    validate_cases(cases)
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[: args.limit]

    judged = bool(args.judge_model) and not args.offline
    judge_model = "—"
    llm_metrics: dict[str, Any] = {}
    if judged:
        from ragas.metrics import _AgentGoalAccuracyWithReference as AgentGoalAccuracyWithReference
        from ragas.metrics import _TopicAdherenceScore as TopicAdherenceScore

        from eval.eval_ragas import build_judge
        from eval.eval_ragas_agent200 import _build_llm_metrics

        llm, _emb, judge_model = build_judge(args.judge_model, "text-embedding-3-small")
        llm_metrics = {
            "agent_goal_accuracy": AgentGoalAccuracyWithReference(llm=llm),
            "topic_adherence": TopicAdherenceScore(llm=llm),
            "plan_semantic_accuracy": _build_llm_metrics(llm)["plan_semantic_accuracy"],
        }

    if args.real_agent:
        from src.nlu.model_client import build_nlu_model_client

        agent_model = build_nlu_model_client()
        if agent_model is None:
            raise SystemExit("[LỖI] --real-agent cần model client (kiểm .env).")
        agent_model.temperature = float(args.agent_temperature)
        if getattr(agent_model, "_is_reasoning", False):
            print("[CẢNH BÁO] model reasoning: temperature bị bỏ qua — dùng --repeat lẻ.", flush=True)
    else:
        from src.core.reasoning import FakeReasoningModel

        agent_model = FakeReasoningModel()

    repeat = int(args.repeat)
    if repeat % 2 == 0:
        print(f"[CẢNH BÁO] --repeat={repeat} chẵn; trung vị sẽ lấy giá trị thấp hơn ở giữa.")

    acc_metric, f1_metric = ToolCallAccuracy(), ToolCallF1()
    started = time.time()
    rows: list[dict] = []

    for index, case in enumerate(cases, start=1):
        runs: list[tuple[ReasoningResult, list[dict[str, Any]]]] = []
        det_runs: dict[str, list[Metric]] = defaultdict(list)
        tool_runs: dict[str, list[Metric]] = defaultdict(list)
        samples: list[Any] = []
        for _ in range(repeat):
            result, calls = run_case(case, agent_model)
            runs.append((result, calls))
            sample = _build_sample(case, result, calls)
            samples.append(sample)
            for name, metric in score_case_deterministic(case, result, calls).items():
                det_runs[name].append(metric)
            for name, metric in asyncio.run(_tool_scores(sample, case, acc_metric, f1_metric)).items():
                tool_runs[name].append(metric)

        signatures = [_plan_signature(r, c) for r, c in runs]
        modal, modal_count = Counter(signatures).most_common(1)[0]
        rep_idx = signatures.index(modal)
        rep_result, rep_calls = runs[rep_idx]

        folded: dict[str, Metric] = {}
        for name, ms in {**det_runs, **tool_runs}.items():
            folded[name] = _fold_metric(ms)

        rows.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "user_input": case["user_input"],
                "tool_metrics_applicable": bool(case.get("tool_metrics_applicable")),
                "outcome": rep_result.outcome,
                "reply": _reply_text(rep_result),
                "agent_tool_calls": rep_calls,
                "reference_tool_calls": case.get("reference_tool_calls") or [],
                "reference_goal": case.get("reference_goal", ""),
                "plan_stability": modal_count / repeat,
                "metrics": folded,
                "_samples": samples,
            }
        )
        if index % 40 == 0 or index == len(cases):
            print(f"  agent {index}/{len(cases)} ({time.time() - started:.0f}s)", flush=True)

    # LLM diagnostics chạy tách khỏi deterministic gate và không tạo known_agent_failures.
    if llm_metrics:
        gate = asyncio.Semaphore(args.concurrency)

        async def _one(row: dict) -> None:
            async with gate:
                for name, judge in llm_metrics.items():
                    if name not in CATEGORY_PLAN[row["category"]]["diagnostic"]:
                        continue
                    metric_runs: list[Metric] = []
                    for sample in row["_samples"]:
                        try:
                            metric_runs.append(Metric(float(await judge.multi_turn_ascore(sample))))
                        except Exception as exc:  # noqa: BLE001
                            detail = f"{type(exc).__name__}: {exc}"[:200]
                            metric_runs.append(Metric(None, detail=detail, infra_error=detail))
                    row["metrics"][name] = _fold_metric(metric_runs)
                    if row["metrics"][name].score is not None:
                        row["metrics"][name].detail = "LLM diagnostic (không gating)"

        async def _run_all() -> None:
            await asyncio.gather(*(_one(row) for row in rows))

        asyncio.run(_run_all())
        print(f"  judge xong ({time.time() - started:.0f}s)", flush=True)

    meta = {
        "dataset": str(args.dataset),
        "dataset_sha256": _sha256(dataset_path),
        "evaluator_sha256": _sha256(Path(__file__)),
        "n_cases": len(rows),
        "git_head": _git_head(),
        "git_dirty": _git_dirty(),
        "agent_mode": "real" if args.real_agent else "fake",
        "offline": bool(args.offline),
        "agent_temperature": float(args.agent_temperature) if args.real_agent else None,
        "repeat": repeat,
        "score_aggregation": "median_low" if repeat > 1 else "single_run",
        "judge_model": judge_model,
        "judged": judged,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    report = build_report(rows, meta=meta, judged=judged)
    print_tables(report)

    output_path = Path(args.out)
    samples_path = Path(args.samples_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    samples_dump = [
        {
            **{k: v for k, v in row.items() if k not in ("_samples", "metrics")},
            "metrics": {
                name: {"score": m.score, "detail": m.detail, "failure_mode": m.failure_mode, "infra_error": m.infra_error}
                for name, m in row["metrics"].items()
            },
        }
        for row in rows
    ]
    samples_path.write_text(json.dumps(samples_dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[report]  {args.out}\n[samples] {args.samples_out}")


if __name__ == "__main__":
    main()
