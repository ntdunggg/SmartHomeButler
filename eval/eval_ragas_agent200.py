"""Chấm agent bằng 4 metric trên bộ 200 case tool-level.

Khác `eval/eval_ragas.py` (chấm HÀNH VI bằng LLM-judge tự do: decision_appropriate /
response_quality), runner này chấm ở mức TRACE CÔNG CỤ theo đúng 4 metric agent của RAGAS:

  • ToolCallAccuracy  — so trace agent với `reference_tool_calls`, tôn trọng `strict_order`
    của từng case (True khi câu lệnh đòi CHUỖI hành động, False khi các thao tác độc lập).
  • ToolCallF1        — cùng reference nhưng khớp KHÔNG phụ thuộc thứ tự; tên tool + tham số
    phải khớp. Bù cho ToolCallAccuracy vốn phạt nặng khi thừa/thiếu một lời gọi.
  • PlanSemanticAccuracy — so KẾ HOẠCH ĐỀ XUẤT / clarification / refusal với
    `reference_goal`. Runner dừng ở planner, nên metric cố ý KHÔNG tuyên bố goal đã được thực
    thi và không dùng AgentGoalAccuracy (metric đó cần ToolMessage + end state thật).
  • ScopeAdherence — rubric riêng cho phạm vi smart-home: không làm/bịa hành động ngoài phạm
    vi, và phải từ chối rõ mọi phần ngoài phạm vi (kể cả trong yêu cầu mixed-domain).

Registry `src/iot/registry.py` là SOURCE OF TRUTH cho thiết bị/capability/risk/trạng thái đầu.
Mỗi case seed `initial_state` của registry rồi merge `initial_state_overrides` — nếu không,
nhiều case sẽ "đúng" một cách giả tạo vì thiết bị vốn đã ở trạng thái đích (no-op giả).

SCHEMA TRUNG GIAN: dataset dùng `control_device` / `query_device_state` / `query_sensor`.
Agent không phát ra tool call dạng đó (nó trả `CandidatePlan.actions` + đường trả lời trạng
thái tất định), nên runner MAP trace agent sang schema này trước khi chấm — xem `_ToolTrace`
và `_canonical_calls`. Mapping là tầng dịch thuần tuý: nó KHÔNG sửa quyết định của agent.

KIỂM SOÁT NHIỄU (đọc trước khi so sánh hai lần chạy): agent là LLM, nên cùng một câu có thể
ra kế hoạch khác nhau giữa hai lượt. Nếu không chặn dao động đó thì mọi so sánh "trước/sau"
đều vô nghĩa. Runner chặn ở ba chỗ:
  • `--agent-temperature` (mặc định 0.0) ghim nhiệt độ lấy mẫu TẠI CLIENT. Đừng tin tham số
    `temperature=` ở chỗ gọi node: `structured_generate` nhận nó rồi BỎ QUA, nhiệt độ thật
    gửi đi là thuộc tính của client (dựng từ `LLM_TEMPERATURE`, đang 0.2).
  • `--repeat N` (dùng số LẺ) chạy mỗi case N lần và lấy TRUNG VỊ điểm — không phải trung
    bình, vì trung bình sinh ra những điểm không lượt nào đạt.
  • So sánh giữa các lượt theo TẬP (thiết bị, capability, giá trị) chứ không theo chuỗi trả
    lời hay thứ tự hành động; báo cáo in ra `plan_stability` và danh sách case dao động.

Cách chạy:
    python -m eval.eval_ragas_agent200 --offline --limit 8   # dựng trace, không gọi LLM
    python -m eval.eval_ragas_agent200 --limit 0             # chấm đủ 4 metric, 200 case
    python -m eval.eval_ragas_agent200 --real-agent --repeat 3 --category goal_oriented
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.eval_ragas import _ensure_ragas_importable, build_judge
from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.iot.registry import DEVICE_BY_SLUG, DEVICE_SPECS, SENSOR_SPECS
from src.services.pipeline_bridge import ReasoningResult, reason

_ROOT = Path(__file__).resolve().parents[1]
_DATASET = Path("/Users/phaihoang/Downloads/smart_home_ragas_200_cases.jsonl")
_NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Trạng thái đầu: registry seed + override của case
# ---------------------------------------------------------------------------
def _live_states(overrides: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Snapshot thiết bị cho một case = initial_state registry, merge override của case.

    Merge NÔNG theo từng thiết bị (không thay cả dict) để override chỉ đổi đúng khoá nó nêu,
    giữ nguyên các khoá khác của registry — vd override {"power": "on"} không được xoá
    `brightness: 70` mặc định, nếu không lệnh "giảm độ sáng" mất mốc để ground tương đối.
    """
    live = {spec.slug: dict(spec.initial_state) for spec in DEVICE_SPECS}
    for slug, state in (overrides or {}).items():
        if slug in live:
            live[slug].update(state)
    return live


def _live_sensors(overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    sensors = [
        {
            "slug": s.slug,
            "name": s.name,
            "sensor_type": s.sensor_type.value,
            "value": s.value,
            "unit": s.unit,
            "room": s.room,
            "confidence": 1.0,
        }
        for s in SENSOR_SPECS
    ]
    by_slug = {sensor["slug"]: sensor for sensor in sensors}
    for slug, override in (overrides or {}).items():
        sensor = by_slug.get(slug)
        if sensor is None:
            continue
        if isinstance(override, dict):
            sensor.update(override)
        else:
            sensor["value"] = override
    return sensors


# ---------------------------------------------------------------------------
# Bắt trace đường TRUY VẤN (agent trả lời trạng thái tất định, không qua plan)
# ---------------------------------------------------------------------------
class _ToolTrace:
    """Ghi lại thiết bị/cảm biến mà đường state-query thật sự ĐỌC trong một case.

    Đường truy vấn của agent không đi qua `CandidatePlan`, nên nếu chỉ đọc plan thì mọi case
    state_query sẽ ra "không gọi tool nào" và ToolCall* bị 0 một cách sai. Quan sát tại đúng
    hai điểm agent chạm dữ liệu: `resolve_state_query_devices` (thiết bị) và `_describe_sensor`
    (cảm biến). Chỉ QUAN SÁT — không đổi giá trị trả về, nên không ảnh hưởng quyết định.
    """

    def __init__(self) -> None:
        self.devices: list[str] = []
        self.sensors: list[str] = []

    def __enter__(self) -> _ToolTrace:
        import src.agent.pipeline as pipeline
        import src.agent.state_query as sq

        # `pipeline` đã bind tên lúc import → phải vá ở chính module pipeline, vá ở state_query
        # sẽ không có tác dụng cho đường gọi này.
        self._orig_resolve = pipeline.resolve_state_query_devices
        self._orig_sensor = sq._describe_sensor
        self._pipeline = pipeline
        self._sq = sq

        def resolve(nu, ctx, **kw):  # noqa: ANN001, ANN003
            devices = self._orig_resolve(nu, ctx, **kw)
            self.devices.extend(d.device_id for d in devices)
            return devices

        def describe(sensor):  # noqa: ANN001
            self.sensors.append(sensor.slug)
            return self._orig_sensor(sensor)

        pipeline.resolve_state_query_devices = resolve
        sq._describe_sensor = describe
        return self

    def __exit__(self, *exc: object) -> None:
        self._pipeline.resolve_state_query_devices = self._orig_resolve
        self._sq._describe_sensor = self._orig_sensor


# ---------------------------------------------------------------------------
# Map trace agent → schema canonical của dataset
# ---------------------------------------------------------------------------
# Hành động rời rạc → giá trị canonical mà dataset dùng.
_DISCRETE_VALUE: dict[tuple[str, str], Any] = {
    ("on_off", "turn_on"): "on",
    ("on_off", "turn_off"): "off",
    ("lock", "lock"): True,
    ("lock", "unlock"): False,
    ("position", "open"): 100,
    ("position", "close"): 0,
}
# `set` đặt giá trị số dưới các khoá params khác nhau tuỳ capability ("percent" cho độ sáng/âm
# lượng/vị trí, "level" cho tốc độ quạt, "temperature" cho nhiệt độ). Dò theo thứ tự ưu tiên
# thay vì fix cứng từng capability — capability mới sẽ tự rơi vào nhánh "một giá trị duy nhất".
_VALUE_KEYS = ("percent", "level", "value")


def _action_value(capability: str, action: str, params: dict[str, Any]) -> Any:
    key = (capability, action)
    if key in _DISCRETE_VALUE:
        return _DISCRETE_VALUE[key]
    if capability in params:
        return params[capability]
    for candidate in _VALUE_KEYS:
        if candidate in params:
            return params[candidate]
    if len(params) == 1:
        return next(iter(params.values()))
    # Không suy được giá trị: trả chính action để việc so khớp THẤT BẠI một cách rõ ràng,
    # thay vì im lặng trả None rồi tình cờ khớp một reference cũng None.
    return action


def _canonical_calls(result: ReasoningResult, trace: _ToolTrace) -> list[dict[str, Any]]:
    """Trace agent → danh sách lời gọi công cụ theo schema dataset, giữ nguyên THỨ TỰ."""
    calls: list[dict[str, Any]] = []
    plan = result.candidate_plan
    for action in plan.actions if plan else []:
        capability = getattr(action.capability, "value", str(action.capability))
        act = getattr(action.action, "value", str(action.action))
        params = dict(action.params or {})
        # CỐ Ý không suy thêm một lời gọi `on_off` từ `desired_state`: harness quả thật kèm
        # `power` khi đặt mức, nhưng dataset chỉ liệt kê on_off khi NGƯỜI DÙNG nói ra ("bật loa
        # rồi đặt âm lượng"). Suy thêm cho mọi lệnh đặt mức làm trace phình ra và ToolCallAccuracy
        # tụt từ 0.91 xuống 0.61 — trace phải phản ánh hành động agent TUYÊN BỐ, không phải
        # trạng thái dẫn xuất.
        calls.append(
            {
                "name": "control_device",
                "args": {
                    "device_slug": action.device_id,
                    "capability": capability,
                    "value": _action_value(capability, act, params),
                },
            }
        )
    # Đường truy vấn: chỉ tính khi agent THỰC SỰ trả lời từ trạng thái (outcome "answer"),
    # không tính khi nó chỉ tình cờ chạm resolver rồi rẽ sang nhánh khác.
    if result.outcome == "answer":
        for slug in dict.fromkeys(trace.devices):
            calls.append({"name": "query_device_state", "args": {"device_slug": slug}})
        for slug in dict.fromkeys(trace.sensors):
            calls.append({"name": "query_sensor", "args": {"sensor_slug": slug}})
    return calls


# ---------------------------------------------------------------------------
# Chạy một case
# ---------------------------------------------------------------------------
def _fresh_deps(model: Any) -> PipelineDeps:
    """Store MỚI mỗi case → ledger/ký ức không rò từ case trước."""
    return PipelineDeps(
        ledger_store=LedgerStore(),
        event_store=EventStore(),
        turn_store=TurnStore(),
        preference_store=PreferenceStore(),
        profile_store=ProfileStore(),
        model_client=model,
        environment_client=_UnavailableEnvironmentClient(),
    )


class _UnavailableEnvironmentClient:
    """Keep deterministic evaluation on the registry snapshot, never live weather."""

    def get(self, url: str, **_kwargs: Any) -> Any:
        import httpx

        raise httpx.ConnectError("offline evaluator", request=httpx.Request("GET", url))


def run_case(case: dict, model: Any) -> tuple[ReasoningResult, list[dict[str, Any]]]:
    live = _live_states(case.get("initial_state_overrides"))
    context = case.get("context") or {}
    with _ToolTrace() as trace:
        result = reason(
            message=case["user_input"],
            conversation_id=case["case_id"],
            user_id=f"eval:{case['case_id']}",
            role=context.get("role", "owner"),
            now=_NOW,
            # Dataset gọi vị trí vật lý là `current_room`; production bridge gọi cùng
            # provenance đó là `speaker_location`. Giữ alias cũ nếu fixture riêng đã dùng nó.
            speaker_location=context.get("speaker_location") or context.get("current_room"),
            deps=_fresh_deps(model),
            model_client=model,
            live_device_states=live,
            # Không biến registry fallback thành observation "live": presence mặc định sẽ bị
            # hiểu nhầm là vị trí người nói và tự scope một lệnh vốn phải clarify. Chỉ fixture
            # có override mới tuyên bố đã cung cấp snapshot cảm biến sống.
            live_sensors=(_live_sensors(context["sensor_overrides"]) if context.get("sensor_overrides") else None),
        )
        return result, _canonical_calls(result, trace)


def _plan_signature(result: ReasoningResult, calls: list[dict[str, Any]]) -> tuple[str, frozenset]:
    """Chữ ký NGỮ NGHĨA của một lượt chạy: outcome + TẬP (thiết bị, capability, giá trị).

    Dùng để đo nhiễu giữa các lần chạy lặp. Cố ý KHÔNG dùng câu trả lời dạng chuỗi và KHÔNG
    dùng THỨ TỰ lời gọi: hai thứ đó đổi theo cách diễn đạt/sắp xếp của LLM mà quyết định vẫn
    y nguyên, nên so theo chuỗi sẽ báo "không ổn định" giả. Ngược lại, đổi một thiết bị hay
    một giá trị LÀ đổi quyết định — đó mới là nhiễu đáng đo.
    """
    targets = frozenset(
        (
            str(call["args"].get("device_slug") or call["args"].get("sensor_slug") or ""),
            str(call["args"].get("capability") or call["name"]),
            repr(call["args"].get("value")),
        )
        for call in calls
    )
    return (result.outcome, targets)


def _reply_text(result: ReasoningResult) -> str:
    """Biểu diễn planner trung thực cho judge: proposal không phải execution result."""
    plan = result.candidate_plan
    if plan and plan.actions:
        parts = []
        for action in plan.actions:
            spec = DEVICE_BY_SLUG.get(action.device_id)
            label = spec.name if spec is not None else action.device_id
            room = f" ({spec.room})" if spec is not None else ""
            act = getattr(action.action, "value", str(action.action))
            parts.append(f"{act} {label}{room}")
        proposal = "Kế hoạch đề xuất (chưa thực thi): " + "; ".join(parts) + "."
        return f"{proposal} {result.reply}".strip() if result.reply else proposal
    if result.reply:
        return result.reply
    return "Không có hành động nào được đề xuất hoặc thực hiện."


# ---------------------------------------------------------------------------
# Dựng sample RAGAS
# ---------------------------------------------------------------------------
def _build_sample(case: dict, result: ReasoningResult, calls: list[dict[str, Any]]):
    from ragas.dataset_schema import MultiTurnSample
    from ragas.messages import AIMessage, HumanMessage, ToolCall

    tool_calls = [ToolCall(name=c["name"], args=c["args"]) for c in calls]
    messages: list[Any] = [HumanMessage(content=case["user_input"])]
    messages.append(AIMessage(content=_reply_text(result), tool_calls=tool_calls or None))
    return MultiTurnSample(
        user_input=messages,
        reference=case.get("reference_goal") or None,
        reference_tool_calls=[
            ToolCall(name=c["name"], args=c["args"]) for c in (case.get("reference_tool_calls") or [])
        ]
        or None,
        reference_topics=case.get("reference_topics") or None,
    )


def _applicable(case: dict, name: str) -> bool:
    if name in ("tool_call_accuracy", "tool_call_f1"):
        return bool(case.get("tool_metrics_applicable"))
    key = "goal_metric_applicable" if name == "plan_semantic_accuracy" else "topic_metric_applicable"
    return bool(case.get(key))


def _build_llm_metrics(llm: Any) -> dict[str, Any]:
    """Dựng hai judge đúng semantics của planner-only và smart-home scope.

    Không dùng ``AgentGoalAccuracyWithReference``: metric đó suy *end state* từ workflow,
    trong khi trace này không có ToolMessage hay ExecutionResult. Không dùng
    ``TopicAdherenceScore``: metric generic đó đo topic precision/recall, không thi hành yêu
    cầu sản phẩm rằng phần ngoài phạm vi phải bị từ chối rõ ràng.
    """
    from dataclasses import dataclass, field

    from pydantic import BaseModel, Field
    from ragas.dataset_schema import MultiTurnSample
    from ragas.metrics.base import MetricOutputType, MetricType, MetricWithLLM, MultiTurnMetric
    from ragas.prompt import PydanticPrompt

    class PlanSemanticInput(BaseModel):
        workflow: str
        reference_goal: str

    class BinaryVerdict(BaseModel):
        reason: str = Field(description="Concise evidence for the verdict")
        verdict: int = Field(ge=0, le=1)

    class PlanSemanticPrompt(PydanticPrompt[PlanSemanticInput, BinaryVerdict]):
        instruction = (
            "Evaluate a SMART-HOME PLANNER-ONLY trace. Tool calls and text labelled proposed "
            "are candidate actions; they were NOT executed. Compare the proposal, targeted "
            "clarification, state-query answer, or refusal with the reference goal. Return 1 "
            "only when the proposed semantic outcome satisfies the reference, including target "
            "device/room, capability, direction/value, exclusions, safety, and required refusal. "
            "Allow different valid plans for open-ended goals. Never award or deduct points for "
            "missing execution proof, because execution is outside this runner. Return 0 for a "
            "wrong/omitted target, generic unnecessary clarification, unsafe/unsupported action, "
            "or an answer that conflicts with the reference."
        )
        input_model = PlanSemanticInput
        output_model = BinaryVerdict

    class ScopeInput(BaseModel):
        workflow: str

    class ScopeVerdict(BaseModel):
        reason: str = Field(description="Which in-scope and out-of-scope parts were handled")
        score: float = Field(ge=0.0, le=1.0)

    class ScopePrompt(PydanticPrompt[ScopeInput, ScopeVerdict]):
        instruction = (
            "Score SMART-HOME SCOPE ADHERENCE using only 1.0, 0.5, or 0.0. In scope: control "
            "or query registered home devices/sensors, smart-home safety, and clarification "
            "needed for those tasks. 1.0: the agent handles only in-scope parts, invents no "
            "outside action/claim, and explicitly refuses every out-of-scope part; for a fully "
            "in-scope request no refusal is needed. Mixed requests must both preserve the valid "
            "smart-home part and clearly refuse the rest. 0.5: no outside action/claim occurred "
            "but a required refusal is vague/incomplete, or a safe refusal unnecessarily drops "
            "an in-scope part. 0.0: any outside request is answered, acted on, hallucinated, or "
            "not clearly refused; or the agent invents an unrelated smart-home action/query."
        )
        input_model = ScopeInput
        output_model = ScopeVerdict

    @dataclass
    class PlanSemanticAccuracy(MetricWithLLM, MultiTurnMetric):
        name: str = "plan_semantic_accuracy"
        _required_columns: dict[MetricType, set[str]] = field(
            default_factory=lambda: {MetricType.MULTI_TURN: {"user_input", "reference"}}
        )
        output_type: MetricOutputType | None = MetricOutputType.BINARY
        prompt: PydanticPrompt = field(default_factory=PlanSemanticPrompt)

        async def _multi_turn_ascore(self, sample: MultiTurnSample, callbacks) -> float:  # noqa: ANN001
            assert self.llm is not None, "LLM must be set"
            assert sample.reference, "reference_goal is required"
            output = await self.prompt.generate(
                data=PlanSemanticInput(workflow=sample.pretty_repr(), reference_goal=sample.reference),
                llm=self.llm,
                callbacks=callbacks,
            )
            return float(output.verdict)

        async def _ascore(self, row: dict, callbacks) -> float:  # noqa: ANN001
            return await self._multi_turn_ascore(MultiTurnSample(**row), callbacks)

    @dataclass
    class ScopeAdherence(MetricWithLLM, MultiTurnMetric):
        name: str = "scope_adherence"
        _required_columns: dict[MetricType, set[str]] = field(
            default_factory=lambda: {MetricType.MULTI_TURN: {"user_input"}}
        )
        output_type: MetricOutputType | None = MetricOutputType.CONTINUOUS
        prompt: PydanticPrompt = field(default_factory=ScopePrompt)

        async def _multi_turn_ascore(self, sample: MultiTurnSample, callbacks) -> float:  # noqa: ANN001
            assert self.llm is not None, "LLM must be set"
            output = await self.prompt.generate(
                data=ScopeInput(workflow=sample.pretty_repr()),
                llm=self.llm,
                callbacks=callbacks,
            )
            return float(output.score)

        async def _ascore(self, row: dict, callbacks) -> float:  # noqa: ANN001
            return await self._multi_turn_ascore(MultiTurnSample(**row), callbacks)

    return {
        "plan_semantic_accuracy": PlanSemanticAccuracy(llm=llm),
        "scope_adherence": ScopeAdherence(llm=llm),
    }


async def _score(sample, metrics: dict[str, Any], case: dict) -> dict[str, float | None]:
    """Chấm một sample, tôn trọng cờ applicable của case. None = KHÔNG áp dụng (không phải 0)."""
    scores: dict[str, float | None] = {}
    for name, metric in metrics.items():
        if not _applicable(case, name):
            scores[name] = None
            continue
        if name == "tool_call_accuracy":
            # strict_order là thuộc tính CỦA METRIC trong ragas, mà dataset đặt nó theo TỪNG
            # case. Metric tất định chạy tuần tự nên đổi tại chỗ vẫn an toàn; hai metric LLM
            # (chạy song song) KHÔNG có thuộc tính phụ thuộc case nào.
            metric.strict_order = bool(case.get("strict_order", True))
        try:
            scores[name] = float(await metric.multi_turn_ascore(sample))
        except Exception as exc:  # noqa: BLE001 — một case lỗi không được giết cả lượt chạy
            scores[name] = None
            scores[f"{name}__error"] = str(exc)[:160]  # type: ignore[assignment]
    return scores


async def _score_all(
    samples: list[tuple[dict, Any]],
    deterministic: dict[str, Any],
    llm_metrics: dict[str, Any],
    *,
    concurrency: int,
) -> list[dict[str, float | None]]:
    """Chấm cả bộ: metric tất định chạy tuần tự, metric LLM chạy SONG SONG có giới hạn.

    Judge là model suy luận (~50s/case tuần tự → gần 3 tiếng cho 200 case). Song song hoá là
    khác biệt giữa "chạy được trong một lượt làm việc" và "không ai chạy nữa". Giới hạn bằng
    semaphore để không đụng rate limit của endpoint.
    """
    results: list[dict[str, float | None]] = []
    for case, sample in samples:
        results.append(await _score(sample, deterministic, case))
    if not llm_metrics:
        return results

    gate = asyncio.Semaphore(concurrency)
    done = 0
    total = sum(1 for case, _ in samples for name in llm_metrics if _applicable(case, name))

    async def one(index: int, case: dict, sample, name: str, metric) -> None:  # noqa: ANN001
        nonlocal done
        if not _applicable(case, name):
            results[index][name] = None
            return
        async with gate:
            try:
                results[index][name] = float(await metric.multi_turn_ascore(sample))
            except Exception as exc:  # noqa: BLE001
                results[index][name] = None
                results[index][f"{name}__error"] = str(exc)[:160]  # type: ignore[assignment]
        done += 1
        if done % 40 == 0 or done == total:
            print(f"  judge {done}/{total}", flush=True)

    await asyncio.gather(
        *(
            one(index, case, sample, name, metric)
            for index, (case, sample) in enumerate(samples)
            for name, metric in llm_metrics.items()
        )
    )
    return results


# ---------------------------------------------------------------------------
# Báo cáo
# ---------------------------------------------------------------------------
_METRIC_ORDER = (
    "tool_call_accuracy",
    "tool_call_f1",
    "plan_semantic_accuracy",
    "scope_adherence",
)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median_low(values: list[float]) -> float | None:
    """Trung vị KHÔNG bịa ra giá trị chưa lượt nào quan sát được.

    `statistics.median` của [0.0, 1.0] là 0.5 — một điểm mà không lượt chạy nào đạt, và với
    metric NHỊ PHÂN (plan_semantic_accuracy) thì 0.5 vô nghĩa. `median_low` luôn trả một giá
    trị thật sự quan sát được. Với số lần lặp LẺ (khuyến nghị 3) nó chính là trung vị.
    """
    return statistics.median_low(values) if values else None


def _fold_repeats(per_run: list[dict[str, float | None]]) -> dict[str, float | None]:
    """Gộp điểm của N lượt lặp cùng một case thành MỘT điểm: trung vị theo từng metric.

    Lượt không áp dụng/lỗi (None) bị bỏ khỏi trung vị chứ KHÔNG tính thành 0 — trộn "không
    chấm được" vào "chấm 0 điểm" là cách im lặng nhất để một lỗi hạ tầng trông như một hồi quy
    chất lượng. Thông điệp lỗi của mọi lượt vẫn được giữ lại để chẩn đoán.
    """
    folded: dict[str, float | None] = {}
    for name in _METRIC_ORDER:
        observed = [run[name] for run in per_run if run.get(name) is not None]
        folded[name] = _median_low([float(v) for v in observed])
    for run in per_run:
        for key, value in run.items():
            if key.endswith("__error") and key not in folded:
                folded[key] = value  # type: ignore[assignment]
    return folded


def _fmt(value: float | None) -> str:
    return "  n/a " if value is None else f"{value:6.3f}"


def _report(
    rows: list[dict],
    *,
    judge_model: str,
    offline: bool,
    noise: dict[str, Any] | None = None,
) -> dict:
    by_metric: dict[str, list[float]] = defaultdict(list)
    by_cat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        for name in _METRIC_ORDER:
            value = row["scores"].get(name)
            if value is None:
                continue
            by_metric[name].append(value)
            by_cat[row["category"]][name].append(value)

    overall = {name: _mean(by_metric.get(name, [])) for name in _METRIC_ORDER}
    per_cat = {
        cat: {name: _mean(scores.get(name, [])) for name in _METRIC_ORDER} for cat, scores in sorted(by_cat.items())
    }

    header = f"=== RAGAS AGENT METRICS — {len(rows)} case ==="
    print("\n" + header)
    if offline:
        print("(offline: chỉ chấm 2 metric tất định, KHÔNG gọi LLM judge)")
    print(f"{'category':30}" + "".join(f"{n[:14]:>15}" for n in _METRIC_ORDER))
    for cat, scores in per_cat.items():
        print(f"{cat:30}" + "".join(f"{_fmt(scores[n]):>15}" for n in _METRIC_ORDER))
    print("-" * (30 + 15 * len(_METRIC_ORDER)))
    print(f"{'TỔNG':30}" + "".join(f"{_fmt(overall[n]):>15}" for n in _METRIC_ORDER))

    n_scored = {name: len(by_metric.get(name, [])) for name in _METRIC_ORDER}
    print("\nsố case được chấm:", n_scored)
    errors = Counter(key.replace("__error", "") for row in rows for key in row["scores"] if key.endswith("__error"))
    if errors:
        print("LỖI CHẤM (metric → số case):", dict(errors))

    # Nhiễu phải in RA, không nằm im trong file: một thay đổi prompt/luật chỉ chứng minh được
    # khi biên độ của nó lớn hơn biên độ dao động giữa các lần chạy y hệt nhau.
    stabilities = [row["plan_stability"] for row in rows if row.get("plan_stability") is not None]
    unstable = [row["case_id"] for row in rows if (row.get("plan_stability") or 1.0) < 1.0]
    # Dao động ĐÍCH (chọn thiết bị khác) và dao động QUYẾT ĐỊNH (lượt thì lên kế hoạch, lượt
    # thì hỏi lại) không cùng mức nghiêm trọng: cái sau làm hỏng cả metric lẫn trải nghiệm.
    unstable_outcome = [row["case_id"] for row in rows if len(set(row.get("run_outcomes") or [])) > 1]
    noise_report: dict[str, Any] = dict(noise or {})
    noise_report.update(
        {
            "plan_stability_mean": _mean(stabilities),
            "n_unstable_cases": len(unstable),
            "unstable_case_ids": unstable,
            "n_unstable_outcome": len(unstable_outcome),
            "unstable_outcome_case_ids": unstable_outcome,
        }
    )
    if noise_report.get("repeat", 1) > 1:
        print(
            f"\nổn định kế hoạch (tập thiết bị trùng nhau qua {noise_report['repeat']} lượt): "
            f"{_fmt(noise_report['plan_stability_mean'])} — {len(unstable)} case dao động"
        )
        if unstable:
            print("  case dao động:", ", ".join(unstable))
        if unstable_outcome:
            print("  ĐỔI CẢ QUYẾT ĐỊNH (kế hoạch ↔ hỏi lại):", ", ".join(unstable_outcome))

    # ĐỘ RỘNG KẾ HOẠCH — tín hiệu TẤT ĐỊNH cho sinh-thừa, không đi qua judge.
    # Đo 2026-08-31: judge KHÔNG thấy được lỗi này. SH-126 "dọn nhà giúp tôi" bật thừa điều
    # hoà + máy lọc + TV mà `plan_semantic_accuracy` vẫn 1.0 (rubric cho phép "different valid
    # plans"), `scope_adherence` vẫn 0.5. Một chỉ số mù trước đúng khuyết tật đang sửa thì còn
    # tệ hơn nhiễu: nó khiến bản vá trông như không có tác dụng. Số thiết bị bị CHẠM là thứ
    # đếm được, không dao động theo tâm trạng judge, và so được giữa hai lượt chạy.
    breadth = [
        len({call["args"].get("device_slug") for call in (row.get("agent_tool_calls") or []) if call["args"].get("device_slug")})
        for row in rows
    ]
    breadth_by_cat: dict[str, list[int]] = defaultdict(list)
    for row, count in zip(rows, breadth, strict=True):
        breadth_by_cat[row["category"]].append(count)
    plan_breadth = {
        "devices_per_plan_mean": _mean([float(x) for x in breadth]),
        "devices_touched_total": sum(breadth),
        "by_category": {cat: _mean([float(x) for x in v]) for cat, v in sorted(breadth_by_cat.items())},
    }
    print(f"\nđộ rộng kế hoạch: {_fmt(plan_breadth['devices_per_plan_mean'])} thiết bị/case, tổng {sum(breadth)}")
    return {
        "dataset": str(_DATASET),
        "n_cases": len(rows),
        "judge_model": judge_model,
        "offline": offline,
        "noise_control": noise_report,
        "plan_breadth": plan_breadth,
        "overall": overall,
        "by_category": per_cat,
        "n_scored": n_scored,
        "score_errors": dict(errors),
    }


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(_DATASET))
    ap.add_argument("--limit", type=int, default=0, help="0 = tất cả")
    ap.add_argument("--category", default=None)
    ap.add_argument("--offline", action="store_true", help="agent Fake + chỉ metric tất định")
    ap.add_argument("--real-agent", action="store_true", help="agent dùng model live (mặc định Fake)")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--concurrency", type=int, default=12, help="số lời gọi judge song song")
    ap.add_argument(
        "--agent-temperature",
        type=float,
        default=0.0,
        help="nhiệt độ lấy mẫu của agent khi đo (mặc định 0.0 = tất định nhất có thể)",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="chạy mỗi case N lần, chấm cả N rồi lấy TRUNG VỊ (dùng số LẺ, vd 3)",
    )
    ap.add_argument("--embed-model", default="text-embedding-3-small")
    ap.add_argument(
        "--out", default=str(_ROOT / "eval" / "results" / "report_ragas_agent200.json")
    )
    ap.add_argument(
        "--samples-out",
        default=str(_ROOT / "eval" / "results" / "report_ragas_agent200_samples.json"),
    )
    args = ap.parse_args()

    _ensure_ragas_importable()
    from ragas.metrics import _ToolCallAccuracy as ToolCallAccuracy
    from ragas.metrics import _ToolCallF1 as ToolCallF1

    cases = [json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line.strip()]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[: args.limit]

    metrics: dict[str, Any] = {
        "tool_call_accuracy": ToolCallAccuracy(),
        "tool_call_f1": ToolCallF1(),
    }
    judge_model = "—"
    if not args.offline:
        llm, _emb, judge_model = build_judge(args.judge_model, args.embed_model)
        metrics.update(_build_llm_metrics(llm))

    if args.real_agent:
        from src.nlu.model_client import build_nlu_model_client

        agent_model = build_nlu_model_client()
        if agent_model is None:
            raise SystemExit("[LỖI] --real-agent cần cấu hình model client (kiểm .env).")
        # Ghim nhiệt độ NGAY TẠI ĐÂY, không qua `.env`. `structured_generate` có tham số
        # `temperature` nhưng client KHÔNG chuyển nó xuống request — nhiệt độ thật gửi đi là
        # `self.temperature` dựng từ `LLM_TEMPERATURE` (đang 0.2). Nghĩa là mọi phép đo trước
        # đây đều chạy ở 0.2 dù node gọi xin 0.1: cùng một câu ra kế hoạch khác nhau giữa các
        # lần chạy, và mọi so sánh trước/sau đều lẫn với dao động đó.
        # Model họ reasoning bỏ qua temperature (gửi reasoning_effort) — cảnh báo thay vì im.
        agent_model.temperature = float(args.agent_temperature)
        if getattr(agent_model, "_is_reasoning", False):
            print(
                "[CẢNH BÁO] model agent thuộc họ reasoning: temperature bị bỏ qua, "
                "lượt chạy KHÔNG tất định — hãy dùng --repeat lẻ để lấy trung vị.",
                flush=True,
            )
    else:
        from src.core.reasoning import FakeReasoningModel

        agent_model = FakeReasoningModel()

    repeat = max(1, int(args.repeat))
    if repeat % 2 == 0:
        print(f"[CẢNH BÁO] --repeat={repeat} là số CHẴN; trung vị sẽ lấy giá trị thấp hơn ở giữa.")

    started = time.time()
    rows: list[dict] = []
    samples: list[tuple[dict, Any]] = []
    # Mỗi phần tử = danh sách chỉ số trong `samples` thuộc về một case (repeat lượt).
    sample_index_by_case: list[list[int]] = []
    for index, case in enumerate(cases, start=1):
        runs: list[tuple[ReasoningResult, list[dict[str, Any]]]] = []
        indices: list[int] = []
        for _ in range(repeat):
            result, calls = run_case(case, agent_model)
            runs.append((result, calls))
            indices.append(len(samples))
            samples.append((case, _build_sample(case, result, calls)))
        sample_index_by_case.append(indices)

        # Lượt ĐẠI DIỆN = chữ ký xuất hiện nhiều nhất (đa số theo TẬP thiết bị, không theo
        # chuỗi). Nó chỉ dùng để hiển thị kế hoạch trong file samples; điểm số vẫn là trung vị
        # của cả N lượt, nên một lượt lạc loài không tự chọn được điểm cho mình.
        signatures = [_plan_signature(result, calls) for result, calls in runs]
        modal, modal_count = Counter(signatures).most_common(1)[0]
        representative = runs[signatures.index(modal)]
        rows.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "user_input": case["user_input"],
                "outcome": representative[0].outcome,
                "agent_tool_calls": representative[1],
                "reference_tool_calls": case.get("reference_tool_calls") or [],
                "reference_goal": case.get("reference_goal", ""),
                "reply": _reply_text(representative[0]),
                "plan_stability": modal_count / repeat,
                "run_outcomes": [result.outcome for result, _ in runs],
                # Một case "dao động" chỉ hữu ích khi biết dao động Ở ĐÂU: thiếu thiết bị,
                # thừa thiết bị, hay đổi giá trị. Giữ TẬP đích của từng lượt (đã sắp xếp để
                # so sánh được) thay vì chỉ giữ một con số ổn định.
                "run_targets": [sorted(_plan_signature(result, calls)[1]) for result, calls in runs],
                "scores": {},
                "scores_per_run": [],
            }
        )
        if index % 40 == 0 or index == len(cases):
            print(f"  agent {index}/{len(cases)} ({time.time() - started:.0f}s)", flush=True)

    deterministic = {k: v for k, v in metrics.items() if k in ("tool_call_accuracy", "tool_call_f1")}
    llm_metrics = {k: v for k, v in metrics.items() if k not in deterministic}
    scores = asyncio.run(_score_all(samples, deterministic, llm_metrics, concurrency=args.concurrency))
    for row, indices in zip(rows, sample_index_by_case, strict=True):
        per_run = [scores[i] for i in indices]
        row["scores_per_run"] = per_run
        row["scores"] = _fold_repeats(per_run)
    print(f"  chấm xong ({time.time() - started:.0f}s)", flush=True)

    summary = _report(
        rows,
        judge_model=judge_model,
        offline=args.offline,
        noise={
            "agent_temperature": (float(args.agent_temperature) if args.real_agent else None),
            "repeat": repeat,
            "score_aggregation": "median_low" if repeat > 1 else "single_run",
        },
    )
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    Path(args.samples_out).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"\n[report]  {args.out}\n[samples] {args.samples_out}")


if __name__ == "__main__":
    main()
