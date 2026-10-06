"""Đánh giá TOÀN BỘ agent bằng RAGAS (spec: đo agent ở thời điểm hiện tại).

RAGAS gốc sinh ra cho RAG (câu hỏi → truy hồi tài liệu → sinh câu trả lời). Agent này
chủ yếu RA QUYẾT ĐỊNH + LẬP KẾ HOẠCH điều khiển thiết bị, nên ta map từng phần của agent
sang đúng nhóm metric của RAGAS để có một bức tranh "toàn bộ agent":

TÍN HIỆU CHÍNH — bộ metric HÀNH VI (LLM-judge, áp cho MỌI case, đúng bản chất agent):
  • decision_appropriate (AspectCritic 0/1) — xét yêu cầu người dùng, quyết định của agent có
    PHÙ HỢP không (hỏi lại khi mơ hồ, hành động khi đủ rõ, huỷ khi bị huỷ, không bịa thiết bị,
    không over-act). Reference-free: judge chấm thẳng từ hội thoại → bắt được cả lỗi grounding
    sai phòng/thiết bị mà chỉ so outcome PROCEED==PROCEED KHÔNG thấy.
  • response_quality (RubricsScore 1–5) — chất lượng câu trả lời/kế hoạch (đúng, grounded, tự nhiên).
  • (tuỳ chọn --with-goal-accuracy) AgentGoalAccuracyWithReference — đa lượt: agent có đạt mục tiêu.

Kèm chỉ số THAM CHIẾU tất định (miễn phí, khách quan): decision accuracy = outcome có nằm trong
tập chấp nhận của decision kỳ vọng không. Lưu ý nó chỉ đo ĐÚNG LOẠI quyết định, KHÔNG đo target —
nên decision_appropriate (LLM-judge) thường THẤP HƠN và mới là thước đo "toàn agent" thực chất.

NHÓM RAG cổ điển (--with-rag, thử nghiệm): faithfulness / answer_relevancy / context precision /
recall. Chỉ áp cho case agent tạo kế hoạch/trả lời, retrieved_contexts = kho thiết bị registry
(phòng → thiết bị + capability). CẢNH BÁO: metric này sinh ra cho câu trả lời TUYÊN BỐ kiểm chứng
được với tài liệu; đầu ra agent là MỆNH LỆNH hành động nên faithsfulness thường ~0 dù kế hoạch
đúng — chỉ có nghĩa cho đường hỏi-đáp KIẾN THỨC (dataset lệnh này không kích hoạt), nên để sau cờ.

LLM judge + embeddings dùng chính model canonical của dự án (settings.model_name) qua endpoint
OpenAI-compatible — nhất quán runtime. RAGAS BẮT BUỘC cần LLM judge thật; --offline (agent=Fake)
chỉ dựng + dump samples để soi hạ tầng, KHÔNG chấm.

Cách chạy:

    # Smoke offline (agent=Fake, KHÔNG gọi mạng, chỉ dựng + dump samples để soi):
    python -m eval.eval_ragas --offline --limit 8

    # Chấm LIVE (agent + judge = model thật, tốn phí). Mặc định 12 case đầu để giới hạn chi phí:
    python -m eval.eval_ragas --limit 12

    # Toàn bộ 200 case + đạt-mục-tiêu đa lượt (tốn phí đáng kể):
    python -m eval.eval_ragas --limit 0 --with-goal-accuracy

    # Kèm nhóm RAG thử nghiệm:
    python -m eval.eval_ragas --limit 12 --with-rag
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import types
import warnings
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_live_semantic import map_rooms_to_registry
from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.iot.registry import DEVICE_BY_SLUG, DEVICE_SPECS, ROOMS
from src.services.pipeline_bridge import ReasoningResult, reason

warnings.filterwarnings("ignore", category=DeprecationWarning)

_ROOT = Path(__file__).resolve().parents[1]


def _ensure_ragas_importable() -> None:
    """Shim: langchain_community bản mới bỏ `chat_models.vertexai` mà ragas 0.4.x import cứng.

    Dự án KHÔNG dùng VertexAI → stub module trước khi import ragas để tránh ModuleNotFoundError,
    thay vì hạ cấp langchain của dự án (rủi ro vỡ langgraph). Gọi ngay trước mọi `import ragas`.
    """
    name = "langchain_community.chat_models.vertexai"
    if name not in sys.modules:
        stub = types.ModuleType(name)
        stub.ChatVertexAI = type("ChatVertexAI", (), {})
        sys.modules[name] = stub

_DATASET_DEFAULT = _ROOT / "src/evaluation/goldensets/smart_home_live_semantic_200.json"
_DEFAULT_LOCATION = "Phòng khách"
_NOW = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)

# Nhãn quyết định dataset → tập outcome bridge được coi là ĐÚNG (parity với eval_live_semantic).
_ACCEPT: dict[str, set[str]] = {
    "PROCEED": {"candidate_plan"},
    "CLARIFY": {"clarification"},
    "CANCEL": {"cancelled"},
    "NO_ACTION": {"no_action", "answer"},
    "STATE_QUERY": {"answer", "no_action"},
}
_OUTCOME_VI: dict[str, str] = {
    "candidate_plan": "ĐỀ XUẤT HÀNH ĐỘNG",
    "clarification": "HỎI LẠI ĐỂ LÀM RÕ",
    "cancelled": "ĐÃ HUỶ",
    "answer": "TRẢ LỜI TRỰC TIẾP",
    "no_action": "HIỂU NHƯNG KHÔNG HÀNH ĐỘNG",
}


# ---------------------------------------------------------------------------
# retrieved_contexts: kho thiết bị registry (tri thức nhà tất định agent grounding vào)
# ---------------------------------------------------------------------------
def _home_inventory_contexts() -> list[str]:
    """Một chuỗi context / phòng: liệt kê thiết bị + capability THẬT trong registry."""
    by_room: dict[str, list[str]] = defaultdict(list)
    for spec in DEVICE_SPECS:
        caps = ", ".join(c.value for c in spec.capabilities)
        by_room[spec.room].append(f"{spec.name} (loại {spec.device_type.value}; {caps or 'không rõ'})")
    ctx: list[str] = []
    for room in ROOMS:
        devices = by_room.get(room, [])
        ctx.append(f"Phòng '{room}' có các thiết bị: " + ("; ".join(devices) if devices else "không có thiết bị."))
    other = {r: d for r, d in by_room.items() if r not in ROOMS}
    for room, devices in other.items():
        ctx.append(f"Khu '{room}' có: " + "; ".join(devices))
    return ctx


_HOME_CONTEXTS = _home_inventory_contexts()


# ---------------------------------------------------------------------------
# Chạy pipeline một case → ReasoningResult cuối + văn bản hội thoại
# ---------------------------------------------------------------------------
def _fresh_deps(model: Any) -> PipelineDeps:
    """Store MỚI mỗi case → cô lập ký ức/ledger (kiểm stale-context đúng)."""
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=model,
    )


def _needs_location(case: dict) -> bool:
    exp = case.get("expected", {})
    return bool(exp.get("should_use_context")) or exp.get("room") == "current_room"


def run_case(case: dict, model: Any, *, map_rooms: bool = True) -> tuple[ReasoningResult, list[dict[str, str]]]:
    """Chạy các lượt USER (cùng conversation_id) → (kết quả cuối, transcript đầy đủ)."""
    deps = _fresh_deps(model)
    conv = case.get("id", "case")
    location = _DEFAULT_LOCATION if _needs_location(case) else None
    result: ReasoningResult | None = None
    transcript: list[dict[str, str]] = []
    is_first_user_turn = True
    for msg in case.get("messages", []):
        role = msg.get("role")
        content = msg.get("content", "")
        if role != "user":
            transcript.append({"role": "assistant", "content": content})
            continue  # lượt assistant = ngữ cảnh; pipeline tự sinh câu hỏi của mình
        text = map_rooms_to_registry(content) if map_rooms else content
        transcript.append({"role": "user", "content": text})
        result = reason(
            message=text, conversation_id=conv, role="owner",
            now=_NOW, speaker_location=location if is_first_user_turn else None,
            deps=deps, model_client=model,
        )
        is_first_user_turn = False
    if result is None:  # case không có lượt user (không xảy ra trong dataset) → no_action rỗng
        result = ReasoningResult(semantic_goal=None, candidate_plan=None, outcome="no_action")
    return result, transcript


# ---------------------------------------------------------------------------
# Render response / reference cho RAGAS
# ---------------------------------------------------------------------------
def _device_label(device_id: str) -> str:
    """slug → 'Tên thiết bị (Phòng)' từ registry để judge chấm grounding công bằng (không phải slug thô)."""
    spec = DEVICE_BY_SLUG.get(device_id)
    return f"{spec.name} ({spec.room})" if spec else device_id


def _plan_summary(result: ReasoningResult) -> str:
    plan = result.candidate_plan
    if plan is None or not getattr(plan, "actions", None):
        return ""
    parts: list[str] = []
    for a in plan.actions:
        params = getattr(a, "params", {}) or {}
        pstr = ", ".join(f"{k}={v}" for k, v in params.items())
        act = getattr(a, "action", "")
        act = getattr(act, "value", act)  # Enum → str
        parts.append(f"{_device_label(getattr(a, 'device_id', '?'))}: {act}" + (f" [{pstr}]" if pstr else ""))
    return "; ".join(parts)


def render_response(result: ReasoningResult) -> str:
    """Văn bản hoá QUYẾT ĐỊNH + hành động/câu trả lời của agent để judge chấm."""
    label = _OUTCOME_VI.get(result.outcome, result.outcome)
    body = (result.reply or "").strip()
    plan = _plan_summary(result)
    if plan:
        body = (body + " " if body else "") + f"Kế hoạch: {plan}."
    return f"[QUYẾT ĐỊNH: {label}] " + (body or "(không có nội dung)")


def render_reference(case: dict) -> str:
    exp = case.get("expected", {})
    decision = exp.get("decision", "?")
    parts = [f"Quyết định đúng phải là: {decision}."]
    if exp.get("canonical_goal"):
        parts.append(f"Mục tiêu người dùng: {exp['canonical_goal']}.")
    if exp.get("room"):
        parts.append(f"Phòng: {exp['room']}.")
    if exp.get("target"):
        parts.append(f"Thiết bị: {exp['target']}.")
    if exp.get("adjustment"):
        parts.append(f"Điều chỉnh: {exp['adjustment']}.")
    if exp.get("missing_fields"):
        parts.append(f"Thông tin còn thiếu (nếu CLARIFY thì phải hỏi đúng cái này): {', '.join(exp['missing_fields'])}.")
    return " ".join(parts)


def _last_user_input(transcript: list[dict[str, str]]) -> str:
    """Toàn hội thoại, nhấn mạnh câu người dùng cuối — đủ ngữ cảnh cho judge."""
    lines = [f"{'Người dùng' if m['role'] == 'user' else 'Trợ lý'}: {m['content']}" for m in transcript]
    return "\n".join(lines)


def _coerce_topics(val: Any) -> list[str]:
    """reference_contexts đòi list[str]; canonical_goal đôi khi là list (case ràng buộc) → làm phẳng."""
    if not val:
        return []
    items = val if isinstance(val, list) else [val]
    out: list[str] = []
    for it in items:
        out.extend(_coerce_topics(it) if isinstance(it, list) else [str(it)])
    return [s for s in out if s]


def build_records(cases: list[dict], model: Any, *, map_rooms: bool) -> list[dict[str, Any]]:
    """Chạy pipeline mọi case → bản ghi giàu (đủ cho cả RAGAS lẫn kiểm tra bằng mắt)."""
    records: list[dict[str, Any]] = []
    # Pass agent LIVE chạy TUẦN TỰ và mỗi lượt tốn vài giây model, nên 200 case là hàng chục
    # phút. Không in tiến độ thì cả pass là một hộp đen: không biết còn bao lâu, cũng không
    # phân biệt được "đang chạy chậm" với "đã treo".
    started = time.time()
    for i, case in enumerate(cases, 1):
        t0 = time.time()
        try:
            result, transcript = run_case(case, model, map_rooms=map_rooms)
        except Exception as exc:  # noqa: BLE001 - một case lỗi không nên chặn cả eval
            result, transcript = ReasoningResult(None, None, "no_action", reply=f"[LỖI PIPELINE] {exc}"), []
        elapsed = time.time() - started
        eta = elapsed / i * (len(cases) - i)
        print(
            f"      [{i:>3}/{len(cases)}] {case.get('id', ''):<8} {time.time() - t0:5.1f}s"
            f"  → {result.outcome:<15} (còn ~{eta / 60:.0f} phút)",
            flush=True,
        )
        exp = case.get("expected", {})
        expected_decision = exp.get("decision", "")
        got = result.outcome
        deterministic_ok = got in _ACCEPT.get(expected_decision, set())
        records.append(
            {
                "id": case.get("id", f"case-{i}"),
                "category": case.get("category", "unknown"),
                "user_input": _last_user_input(transcript),
                "transcript": transcript,
                "response": render_response(result),
                "reference": render_reference(case),
                "retrieved_contexts": list(_HOME_CONTEXTS),
                "reference_topics": _coerce_topics(exp.get("canonical_goal")),
                "expected_decision": expected_decision,
                "got_outcome": got,
                "deterministic_decision_ok": deterministic_ok,
            }
        )
    return records


# ---------------------------------------------------------------------------
# RAGAS judge (LLM + embeddings) từ settings dự án
# ---------------------------------------------------------------------------
def build_judge(judge_model: str | None, embed_model: str):
    _ensure_ragas_importable()
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from src.config import get_settings

    s = get_settings()
    if not s.openai_api_key:
        raise SystemExit(
            "[LỖI] Không có OPENAI_API_KEY (kiểm .env). RAGAS cần LLM judge thật. "
            "Dùng --offline để chỉ dựng samples mà không chấm."
        )
    from src.nlu.model_client import is_reasoning_model

    base_url = s.llm_base_url or None
    model_name = judge_model or s.model_name
    # Họ model SUY LUẬN (gpt-5*/o-series) CHỈ nhận temperature mặc định. RAGAS lại tự đặt
    # temperature=0.01 cho mỗi lời gọi judge, nên nếu MODEL_NAME thuộc họ này thì MỌI lời
    # gọi judge trả 400 và toàn bộ metric ra `nan` — chấm điểm im lặng biến mất trong khi
    # script vẫn in ra một báo cáo trông như bình thường.
    #   • không truyền temperature khi dựng ChatOpenAI, VÀ
    #   • bật `bypass_temperature` để RAGAS không ghi đè lại ở tầng wrapper.
    # `is_reasoning_model` dùng lại đúng bộ nhận diện của runtime (§4: một nguồn sự thật).
    reasoning = is_reasoning_model(model_name)
    kwargs = {"model": model_name, "api_key": s.openai_api_key, "base_url": base_url, "timeout": 120}
    if not reasoning:
        kwargs["temperature"] = 0.0
    else:
        # Judge chỉ cần phán đoán ngắn; effort thấp giữ chi phí/độ trễ ở mức chạy được 200 case.
        kwargs["reasoning_effort"] = s.llm_reasoning_effort or "low"
    llm = ChatOpenAI(**kwargs)
    emb = OpenAIEmbeddings(model=embed_model, api_key=s.openai_api_key, base_url=base_url)
    return (
        LangchainLLMWrapper(llm, bypass_temperature=reasoning),
        LangchainEmbeddingsWrapper(emb),
        model_name,
    )


# Outcome mà agent thật sự TẠO RA câu trả lời/kế hoạch grounded → mới có gì để chấm RAG metric.
# CLARIFY/CANCEL/NO_ACTION là "chưa trả lời" → faithfulness/relevancy/recall vô nghĩa (sẽ ra 0 giả).
_GROUNDED_OUTCOMES = {"candidate_plan", "answer"}


def _behavioral_metrics(llm):
    """Metric hành vi — áp cho MỌI case. AspectCritic/RubricsScore là REFERENCE-FREE ở phần
    definition (không nhận cột reference), nên judge phải chấm từ chính hội thoại + response."""
    from ragas.metrics import AspectCritic, RubricsScore

    decision_appropriate = AspectCritic(
        name="decision_appropriate",
        llm=llm,
        strictness=1,  # 1 lần chấm (gpt-4o-mini không trả n=3 ổn định) — tránh cảnh báo & tiết kiệm phí
        definition=(
            "Xét YÊU CẦU của người dùng trong hội thoại và [QUYẾT ĐỊNH: ...] của trợ lý. Quyết định "
            "có PHÙ HỢP không? Trợ lý PHẢI: hỏi lại làm rõ khi yêu cầu thiếu thông tin (không rõ thiết "
            "bị/phòng); đề xuất hành động khi đã đủ rõ; huỷ khi người dùng huỷ; trả lời trực tiếp khi "
            "người dùng hỏi trạng thái; và KHÔNG được tự bịa thiết bị/phòng không có trong nhà, KHÔNG "
            "over-act khi còn mơ hồ. Trả 1 nếu quyết định phù hợp, 0 nếu không."
        ),
    )
    response_quality = RubricsScore(
        name="response_quality",
        llm=llm,
        rubrics={
            "score1_description": "Sai quyết định: over-act khi thiếu thông tin, hoặc bịa thiết bị/phòng không có thật.",
            "score2_description": "Đúng hướng nhưng lệch trọng tâm hoặc grounding sai một phần.",
            "score3_description": "Quyết định đúng nhưng câu trả lời chung chung/thiếu tự nhiên.",
            "score4_description": "Quyết định đúng, bám ngữ cảnh nhà thật, câu hỏi/hành động rõ ràng.",
            "score5_description": "Đúng, súc tích, tiếng Việt tự nhiên, grounded, hỏi đúng cái còn thiếu khi cần.",
        },
    )
    return [decision_appropriate, response_quality]


def _rag_metrics(llm, emb):
    """Metric RAG cổ điển — CHỈ áp cho case agent tạo câu trả lời/kế hoạch grounded."""
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
        ResponseRelevancy,
    )

    return [
        Faithfulness(llm=llm),
        ResponseRelevancy(llm=llm, embeddings=emb),
        LLMContextPrecisionWithReference(llm=llm),
        LLMContextRecall(llm=llm),
    ]


def _samples_for(records: list[dict[str, Any]]):
    from ragas.dataset_schema import SingleTurnSample

    return [
        SingleTurnSample(
            user_input=r["user_input"],
            response=r["response"],
            reference=r["reference"],
            retrieved_contexts=r["retrieved_contexts"],
            reference_contexts=_coerce_topics(r.get("reference_topics")) or None,
        )
        for r in records
    ]


def score_single_turn(records: list[dict[str, Any]], llm, emb, *, with_rag: bool) -> dict[str, Any]:
    """Pass A (hành vi, MỌI case) — tín hiệu chính. Pass B (RAG, chỉ case grounded) chỉ khi with_rag.

    Lưu ý: metric RAG cổ điển (faithfulness/context) sinh ra cho câu trả lời TUYÊN BỐ kiểm chứng
    được với tài liệu; đầu ra của agent này là KẾ HOẠCH HÀNH ĐỘNG (mệnh lệnh), nên faithfulness
    thường ~0 dù kế hoạch đúng — chỉ bật với --with-rag khi đo đường hỏi-đáp kiến thức."""
    _ensure_ragas_importable()
    from ragas import EvaluationDataset, evaluate

    behav = evaluate(dataset=EvaluationDataset(samples=_samples_for(records)), metrics=_behavioral_metrics(llm))
    behav_df = behav.to_pandas().reset_index(drop=True)

    grounded = [r for r in records if r["got_outcome"] in _GROUNDED_OUTCOMES]
    rag_df = None
    if with_rag and grounded:
        rag = evaluate(dataset=EvaluationDataset(samples=_samples_for(grounded)), metrics=_rag_metrics(llm, emb))
        rag_df = rag.to_pandas().reset_index(drop=True)

    return {"behav_df": behav_df, "rag_df": rag_df, "n_grounded": len(grounded)}


def score_goal_accuracy(records: list[dict[str, Any]], llm) -> Any:
    """Pass đa lượt riêng: agent có đạt mục tiêu người dùng qua nhiều lượt không.

    Reference lấy từ chính record (reference_topics = canonical_goal đã làm phẳng, hoặc reference)
    → chạy được cả khi nạp từ --from-samples, không phụ thuộc dataset gốc."""
    _ensure_ragas_importable()
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import MultiTurnSample
    from ragas.messages import AIMessage, HumanMessage  # ragas dùng message type RIÊNG, không phải langchain
    from ragas.metrics import AgentGoalAccuracyWithReference

    samples = []
    for r in records:
        msgs = []
        for m in r["transcript"]:
            msgs.append(HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(content=m["content"]))
        if msgs and isinstance(msgs[-1], HumanMessage):
            msgs.append(AIMessage(content=r["response"]))  # nối quyết định cuối của agent
        topics = _coerce_topics(r.get("reference_topics"))
        ref = "; ".join(topics) if topics else r["reference"]
        samples.append(MultiTurnSample(user_input=msgs, reference=ref))
    dataset = EvaluationDataset(samples=samples)
    return evaluate(dataset=dataset, metrics=[AgentGoalAccuracyWithReference(llm=llm)])


# ---------------------------------------------------------------------------
def _model(offline: bool):
    if offline:
        from src.core.reasoning import FakeReasoningModel

        return FakeReasoningModel()
    from src.nlu.model_client import build_nlu_model_client

    client = build_nlu_model_client()
    if client is None:
        raise SystemExit("[LỖI] Không dựng được model client (thiếu OPENAI_API_KEY / LLM_DISABLED?). Dùng --offline.")
    return client


def main() -> None:
    ap = argparse.ArgumentParser(description="RAGAS eval toàn bộ agent (decision + reply + grounding)")
    ap.add_argument("--dataset", default=str(_DATASET_DEFAULT), help="File JSON {metadata, cases}")
    ap.add_argument("--limit", type=int, default=12, help="Số case đầu (0 = tất cả). Mặc định 12 để giới hạn phí.")
    ap.add_argument("--category", default=None, help="Chỉ chạy một category")
    ap.add_argument("--offline", action="store_true", help="agent=FakeReasoningModel, KHÔNG chấm — chỉ dựng+dump samples")
    ap.add_argument("--with-goal-accuracy", action="store_true", help="Thêm pass đa lượt AgentGoalAccuracy (tốn thêm phí)")
    ap.add_argument("--with-rag", action="store_true", help="Thêm metric RAG cổ điển (faithfulness/context) — chỉ có nghĩa cho đường hỏi-đáp kiến thức, không cho action-plan")
    ap.add_argument("--no-room-map", action="store_true", help="Tắt lớp ánh xạ phòng dataset→registry")
    ap.add_argument("--judge-model", default=None, help="Model cho LLM judge (mặc định = settings.model_name)")
    ap.add_argument("--embed-model", default="text-embedding-3-small", help="Model embeddings cho ResponseRelevancy")
    ap.add_argument(
        "--out",
        default=str(_ROOT / "eval" / "results" / "report_ragas.json"),
        help="File JSON kết quả",
    )
    ap.add_argument(
        "--samples-out",
        default=str(_ROOT / "eval" / "results" / "report_ragas_samples.json"),
        help="File dump samples thô",
    )
    ap.add_argument("--from-samples", default=None, help="Chấm lại từ file samples đã dump (BỎ QUA chạy agent — không tốn phí agent)")
    args = ap.parse_args()

    if args.from_samples:
        records = json.loads(Path(args.from_samples).read_text(encoding="utf-8"))
        print(f"[1/3] Nạp {len(records)} bản ghi từ {args.from_samples} (không chạy lại agent)...")
    else:
        data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
        cases = data.get("cases", [])
        if args.category:
            cases = [c for c in cases if c.get("category") == args.category]
        if args.limit and args.limit > 0:
            cases = cases[: args.limit]
        if not cases:
            raise SystemExit("[LỖI] Không có case nào (kiểm --dataset/--category/--limit).")

        print(f"[1/3] Chạy pipeline {len(cases)} case (agent={'Fake(offline)' if args.offline else 'LIVE'})...")
        model = _model(args.offline)
        records = build_records(cases, model, map_rooms=not args.no_room_map)
        Path(args.samples_out).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"      Đã dump samples → {args.samples_out}")

    det = sum(1 for r in records if r["deterministic_decision_ok"])
    print(f"      Decision accuracy (tất định, không cần LLM judge): {det}/{len(records)} = {det / len(records):.1%}")

    if args.offline:
        print("\n[offline] Bỏ qua chấm RAGAS (cần LLM judge thật). Xem samples ở file trên để soi hạ tầng.")
        return

    print("[2/3] Dựng RAGAS judge (LLM + embeddings)...")
    llm, emb, judge_name = build_judge(args.judge_model, args.embed_model)
    print(f"      judge LLM = {judge_name}, embeddings = {args.embed_model}")

    print("[3/3] Chấm RAGAS — pass A (hành vi, mọi case)" + (" + pass B (RAG)" if args.with_rag else "") + "...")
    st = score_single_turn(records, llm, emb, with_rag=args.with_rag)
    behav_df = st["behav_df"]
    rag_df = st["rag_df"]

    def _mean_cols(df) -> dict[str, float]:
        drop = {"user_input", "response", "reference", "retrieved_contexts", "reference_contexts"}
        out: dict[str, float] = {}
        for c in df.columns:
            if c in drop or df[c].dtype.kind not in "fi":
                continue
            out[c] = float(df[c].mean(skipna=True))
        return out

    overall = _mean_cols(behav_df)
    if rag_df is not None:
        overall.update(_mean_cols(rag_df))

    # Gộp decision_appropriate theo category (behav_df cùng thứ tự records).
    cat_scores: dict[str, dict[str, float]] = {}
    cats = [r["category"] for r in records]
    behav_metric_cols = list(_mean_cols(behav_df).keys())
    for cat in sorted(set(cats)):
        idx = [i for i, c in enumerate(cats) if c == cat]
        sub = behav_df.iloc[idx]
        cat_scores[cat] = {c: float(sub[c].mean(skipna=True)) for c in behav_metric_cols}

    goal_acc = None
    if args.with_goal_accuracy:
        print("[+] Pass đa lượt: AgentGoalAccuracyWithReference...")
        try:  # goal-accuracy KHÔNG được làm mất điểm hành vi đã chấm (tốn phí) — báo lỗi rồi vẫn ghi report
            ga = score_goal_accuracy(records, llm)
            gdf = ga.to_pandas()
            gcol = [c for c in gdf.columns if "goal" in c.lower()]
            if gcol:
                goal_acc = float(gdf[gcol[0]].mean(skipna=True))
        except Exception as exc:  # noqa: BLE001
            print(f"    [CẢNH BÁO] goal-accuracy lỗi, bỏ qua: {exc}")

    report = {
        "dataset": args.dataset,
        "n_cases": len(records),
        "n_grounded_for_rag": st["n_grounded"],
        "judge_model": judge_name,
        "embed_model": args.embed_model,
        "deterministic_decision_accuracy": det / len(records),
        "ragas_overall": overall,
        "ragas_by_category": cat_scores,
        "agent_goal_accuracy": goal_acc,
        "confusion": {
            f"{exp}→{got}": v
            for (exp, got), v in Counter((r["expected_decision"], r["got_outcome"]) for r in records).items()
        },
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 64)
    print("KẾT QUẢ RAGAS — TOÀN BỘ AGENT")
    print("=" * 64)
    print(f"Decision accuracy (tất định) : {report['deterministic_decision_accuracy']:.1%}  ({det}/{len(records)})")
    print("Hành vi (LLM-judge, mọi case):")
    for name in _mean_cols(behav_df):
        print(f"  {name:34s}: {overall[name]:.3f}")
    if args.with_rag:
        print(f"RAG grounding (thử nghiệm — chỉ {st['n_grounded']}/{len(records)} case grounded; ~0 là bình thường với action-plan):")
        if rag_df is not None:
            for name in _mean_cols(rag_df):
                print(f"  {name:34s}: {overall[name]:.3f}")
        else:
            print("  (không có case grounded trong slice này → n/a)")
    if goal_acc is not None:
        print(f"Đa lượt:\n  {'agent_goal_accuracy':34s}: {goal_acc:.3f}")
    print("\ndecision_appropriate theo category:")
    for cat, sc in cat_scores.items():
        dc = sc.get("decision_appropriate")
        print(f"  {cat:32s}: {dc:.3f}" if dc is not None else f"  {cat}: n/a")
    print(f"\nĐã ghi báo cáo đầy đủ → {args.out}")


if __name__ == "__main__":
    main()
