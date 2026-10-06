"""Orchestrator LangGraph 5 tầng (spec §56) — hệ multi-agent mới.

Đây là orchestrator MỚI, TÁCH khỏi `src/agent/graph.py` cũ (pipeline cũ giữ nguyên để
51 file test hiện có vẫn xanh — ràng buộc rebuild). Luồng theo §56:

    collect_runtime_context → understand → sufficiency_gate
      ├─ CLARIFY/ABSTAIN → END (hỏi lại)
      └─ PROCEED → update_ledger → retrieve_memory → infer_preferences
                 → manager_plan → specialists+aggregate → energy_optimizer
                 → deterministic_validate → policy_gate
                     ├─ REJECT  → END
                     ├─ CONFIRM → END (WAITING_FOR_USER_APPROVAL)
                     └─ PROCEED → refresh → reground → execute → observe → audit
                                → feedback → {memory_update ∥ rl_update} → END

Vòng feedback (§54, §56): sau execute, `observe` cô đọng cái ĐÃ xảy ra thật, `audit` ghi
vết, rồi `feedback` diễn giải tín hiệu có cấu trúc; hai nhánh cập nhật ĐỘC LẬP (§54):
`memory_update` ghi Turn+Event (write path §25), `rl_update` cập nhật Q preference (§32).

Bất biến kiến trúc §73 được cài đặt ở từng node tương ứng (understanding không execute;
planner ground qua specialists; memory không override live state; RL không đụng safety;
LLM không gọi device API; safety+refresh trước execute).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.cognitive.ledger import LedgerStore
from src.agent.cognitive.ledger_updater import numeric_bounds_for, update_ledger
from src.agent.feedback.interpreter import interpret_feedback
from src.agent.feedback.memory_update import commit_event, commit_feedback_event, record_turn
from src.agent.feedback.rl_update import apply_feedback
from src.agent.harness.audit import build_audit
from src.agent.harness.authorization import authorize
from src.agent.harness.executor import execute_plan
from src.agent.harness.gateway import DeviceGateway, InMemoryGateway
from src.agent.harness.policy import decide
from src.agent.harness.refresh import refresh_state, reground, revalidate
from src.agent.harness.validator import validate_plan
from src.agent.knowledge.rag import KnowledgeBase, answer_knowledge
from src.agent.knowledge.rag import get_default_knowledge_base as _default_knowledge_base
from src.agent.memory.event_store import EventStore
from src.agent.memory.graph_memory import MemoryGraph
from src.agent.memory.hierarchical_retriever import retrieve_memory
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.routine_memory import capture_routine_turn, goal_from_routine, match_routine_event
from src.agent.memory.turn_store import TurnStore
from src.agent.perception.context_builder import build_perception
from src.agent.perception.sensor_adapter import occupancy_for_room, outdoor_sensor_by_type
from src.agent.planning.aggregator import aggregate
from src.agent.planning.energy_optimizer import optimize
from src.agent.planning.manager import build_subgoals
from src.agent.preference.preference_store import PreferenceStore
from src.agent.preference.state_encoder import encode_state
from src.agent.schemas import DesiredOutcome, SemanticGoal, SufficiencyDecision
from src.agent.specialists.registry import gather_proposals
from src.agent.state import AgentState
from src.agent.state_query import (
    answer_state_query,
    asks_about_home_state,
    matched_sensor_types,
    resolve_state_query_devices,
)
from src.agent.text import Pattern, TextView, strip_diacritics
from src.agent.tools.environment import (
    EnvironmentUnavailableError,
    fetch_environment_sync,
    geocode_city,
)
from src.agent.understanding.clarification import build_clarification
from src.agent.understanding.goal_author import author_goal
from src.agent.understanding.preference_feedback import interpret_preference_feedback
from src.agent.understanding.semantic_cache import SemanticGoalCache
from src.agent.understanding.semantic_resolver import resolve_semantics
from src.agent.understanding.turn_intent import Modifier, TurnIntent, build_continuation_goal, classify
from src.agent.weather_query import detect as detect_weather_question
from src.agent.weather_query import format_answer as format_weather_answer
from src.config import get_settings
from src.context.location_deixis import has_location_deixis, resolve_deictic_room, strip_deixis
from src.context.salience import SalienceStack
from src.core.reasoning import FakeReasoningModel
from src.domain.enums import Role
from src.iot.registry import ROOMS, spec_for
from src.nlu.context import hydrate_focus_room
from src.nlu.exclusion_clause import split_exclusion_clause
from src.nlu.normalizer import analyze, is_closing, is_deferral
from src.nlu.ontology import UtteranceType
from src.nlu.understanding import (
    asks_knowledge,
    mentioned_device_types,
    unavailable_target,
    understand,
)
from src.planning.grounding import devices_by

logger = logging.getLogger("agent.pipeline")


@dataclass(slots=True)
class PipelineDeps:
    """Phụ thuộc có thể inject (test/production dùng store & gateway riêng)."""

    # Each dependency bundle owns its stores. Production keeps one bundle per
    # household in pipeline_bridge; tests/evaluations get isolation by default.
    ledger_store: LedgerStore = field(default_factory=LedgerStore)
    event_store: EventStore = field(default_factory=EventStore)
    turn_store: TurnStore = field(default_factory=TurnStore)
    preference_store: PreferenceStore = field(default_factory=PreferenceStore)
    preference_repo: Any = None  # PreferenceRepository tuỳ chọn cho persistent reading
    profile_store: ProfileStore = field(default_factory=ProfileStore)
    knowledge_base: KnowledgeBase = field(default_factory=_default_knowledge_base)
    gateway_factory: Any = None  # (live_states) -> DeviceGateway
    # Parity với graph cũ: khi không có client live, dùng reasoning model offline để
    # mục tiêu open-ended vẫn được author; không biến mọi câu suy diễn thành no_plan.
    model_client: Any = field(default_factory=FakeReasoningModel)
    # httpx.Client đồng bộ (test inject MockTransport). None = tự mở client thật khi cần.
    environment_client: Any = None
    semantic_cache: SemanticGoalCache = field(
        default_factory=lambda: SemanticGoalCache(
            ttl_seconds=get_settings().semantic_cache_ttl_seconds,
            max_entries=get_settings().semantic_cache_max_entries,
        )
    )

    def make_gateway(self, live_states: dict[str, dict] | None) -> DeviceGateway:
        if self.gateway_factory is not None:
            return self.gateway_factory(live_states)
        return InMemoryGateway(live_states)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def _collect_runtime_context(state: AgentState) -> dict[str, Any]:
    nu, ctx, power = build_perception(
        state.get("user_message", ""),
        now=state.get("now"),
        timezone=state.get("timezone", "Asia/Ho_Chi_Minh"),
        focus_room=state.get("focus_room"),
        speaker_location=state.get("speaker_location"),
        speaker_location_source=state.get("speaker_location_source", ""),
        speaker_location_confidence=state.get("speaker_location_confidence", 0.0),
        speaker_home_room=state.get("speaker_home_room"),
        speaker_private_room=state.get("speaker_private_room"),
        recent_dialogue=state.get("recent_dialogue"),
        live_device_states=state.get("live_device_states"),
        live_sensors=state.get("live_sensors"),
    )
    # An implicit environmental request ("nóng quá") is scoped by a unique live
    # presence observation.  Presence is measured evidence, not an LLM/default-room
    # guess; if zero or multiple rooms are active we keep the scope unknown.
    if ctx.focus_room is None and ctx.speaker_location is None:
        occupied = sorted(
            {
                sensor.room
                for sensor in ctx.sensors
                if sensor.sensor_type == "presence"
                and not sensor.is_stale
                and sensor.source == "live"
                and sensor.value >= 1
                and sensor.room in ctx.rooms
            }
        )
        if len(occupied) == 1:
            ctx = ctx.model_copy(update={"focus_room": occupied[0]})
    return {"normalized": nu, "runtime_context": ctx, "power_load": power}


def _asks_about_state(nu) -> bool:
    """Câu hỏi nhắm vào trạng thái nhà mình (thiết bị/phòng/cảm biến)? — tín hiệu tất định.

    Ngoài đích cụ thể (alias/phòng/loại thiết bị/cảm biến), câu hỏi tổng quan về cả nhà
    ("trong nhà có bao nhiêu thiết bị đang bật?") cũng đọc được từ snapshot sống — xem
    `asks_about_home_state`."""
    return bool(
        nu.matched_device_ids
        or nu.matched_rooms
        or mentioned_device_types(nu)
        or matched_sensor_types(nu)
        or asks_about_home_state(nu)
    )


def _answer_weather(ask, ctx, deps: PipelineDeps) -> str:
    """Trả lời câu hỏi thời tiết/AQI ngoài trời bằng dữ liệu Open-Meteo LIVE.

    Không nêu thành phố → toạ độ nhà trong Settings. Nêu thành phố → geocode trước.
    Lỗi mạng mà KHÔNG nêu thành phố → lùi về cảm biến ngoài trời trong snapshot (giữ được
    hành vi offline cũ). Lỗi mà CÓ nêu thành phố → nói rõ chưa lấy được, không đoán."""
    settings = get_settings()
    client = deps.environment_client
    place_label = ""
    latitude, longitude = settings.environment_latitude, settings.environment_longitude

    if ask.city:
        place = geocode_city(ask.city, client=client, settings=settings)
        if place is None:
            return f"Mình chưa tìm được địa điểm '{ask.city}' để tra thời tiết."
        latitude, longitude, place_label = place.latitude, place.longitude, place.label

    try:
        snapshot = fetch_environment_sync(
            latitude=latitude, longitude=longitude, client=client, settings=settings
        )
    except EnvironmentUnavailableError:
        if ask.city:
            return f"Mình chưa lấy được dữ liệu thời tiết cho {place_label or ask.city} lúc này."
        fallback = answer_state_query(_weather_fallback_nu(ask), ctx)
        return fallback or "Mình chưa lấy được dữ liệu thời tiết ngoài trời lúc này."

    return format_weather_answer(snapshot, ask, place_label=place_label)


def _weather_fallback_nu(ask):
    """NU tổng hợp để `answer_state_query` đọc cảm biến ngoài trời khi Open-Meteo lỗi."""
    probe = {
        "summary": "nhiệt độ ngoài trời",
        "temperature": "nhiệt độ ngoài trời",
        "rain": "trời có mưa không",
        "aqi": "chất lượng không khí",
        "humidity": "độ ẩm ngoài trời",
        "wind": "gió ngoài trời",
        "uv": "chỉ số uv",
        "sunlight": "nắng ngoài trời",
    }.get(ask.dimension, "nhiệt độ ngoài trời")
    return analyze(probe)


def _is_actionable(goal) -> bool:
    """Mục tiêu có ĐỦ để lập kế hoạch không? Lệnh tường minh có thiết bị, hoặc có
    desired_outcome (chiều tiện nghi). Huỷ cũng coi là 'actionable' (có nhánh xử lý riêng)."""
    if goal is None:
        return False
    if getattr(goal, "is_cancellation", False):
        return True
    if getattr(goal, "user_defined_routine", False):
        return True
    if goal.action_hint is not None and goal.target_device_ids:
        return True
    return bool(goal.desired_outcomes)


# Câu hoàn toàn ngoài phạm vi nhà thông minh (thơ văn, tán gẫu không liên quan môi trường/thiết
# bị...). `goal_author._finalize` đã tự PROMOTE UNKNOWN/SOCIAL_UTTERANCE → ENVIRONMENT_REQUEST
# bất cứ khi nào model suy ra được desired_outcomes — nên nếu goal vẫn còn mang một trong hai
# nhãn này VÀ không actionable, đó là tín hiệu ĐÃ ĐƯỢC model xác nhận: không có nhu cầu nhà
# thông minh nào cả, không phải "thiếu phòng/thiết bị". Hỏi lại phòng trong trường hợp này vô
# nghĩa (không có phòng nào cứu được yêu cầu "viết một bài thơ") — phải từ chối + định hướng lại.
_OUT_OF_SCOPE_UTTERANCE_TYPES = (UtteranceType.UNKNOWN, UtteranceType.SOCIAL_UTTERANCE)

_OUT_OF_SCOPE_REPLY_VI = (
    "Mình chỉ hỗ trợ điều khiển và theo dõi thiết bị nhà thông minh thôi, chưa giúp được yêu cầu "
    "này. Mình điều khiển được đèn, điều hoà, rèm, loa/TV và các thiết bị trong nhà — bạn thử nói "
    "'bật đèn phòng khách' hoặc hỏi trạng thái thiết bị nhé."
)

_MIXED_SCOPE_REPLY_VI = (
    "Mình chỉ xử lý phần nhà thông minh; phần yêu cầu còn lại nằm ngoài phạm vi nên mình không thể thực hiện."
)

# Cụm yêu cầu có ngữ nghĩa ngoài smart-home đủ mạnh để làm hard boundary. Đây là taxonomy
# hành vi (sáng tác/dịch/toán/tra cứu/tư vấn), không phải danh sách benchmark sentence. Nó còn
# dùng cho mixed-domain: giữ phần thiết bị hợp lệ, nhưng buộc từ chối phần còn lại.
_OUT_OF_SCOPE_COMPONENT = Pattern(
    r"\b((?:viết|soạn|sáng tác).{0,80}(?:thơ|truyện|email|code|mã|bài viết)|"
    r"(?:kể).{0,80}(?:chuyện|lịch sử)|review|đánh giá.{0,40}(?:phim|sách)|"
    r"dịch.{0,80}(?:sang tiếng|câu)|tóm tắt.{0,80}(?:sách|lịch sử|văn bản)|"
    r"giải.{0,80}(?:phương trình|bài toán|\d+\s*(?:nhân|chia|cộng|trừ))|"
    r"gợi ý.{0,80}(?:quán|du lịch|lịch trình)|tin bóng đá|"
    r"(?:tư vấn|lời khuyên).{0,80}(?:cổ phiếu|bitcoin|pháp lý|hợp đồng)|"
    r"giá (?:vàng|cổ phiếu|bitcoin)|công thức (?:nấu|làm|phở|bánh))\b"
)
_SYSTEM_CAPABILITY_QUESTION = Pattern(
    r"\b(?:hệ thống|bạn|trợ lý|mình).{0,40}(?:làm được gì|hỗ trợ gì|điều khiển được gì|"
    r"có thể làm gì|có khả năng gì)\b"
)


def _requests_out_of_scope_component(nu) -> bool:
    return _OUT_OF_SCOPE_COMPONENT.search(TextView(raw=nu.normalized or "", folded=nu.folded or "")) is not None


def _asks_system_capability(nu) -> bool:
    return _SYSTEM_CAPABILITY_QUESTION.search(TextView(raw=nu.normalized or "", folded=nu.folded or "")) is not None


def _has_smart_home_anchor(nu) -> bool:
    return bool(
        nu.matched_device_ids
        or nu.matched_rooms
        or mentioned_device_types(nu)
        or matched_sensor_types(nu)
        or asks_about_home_state(nu)
    )


def _is_out_of_domain(goal, nu=None) -> bool:
    """Goal không actionable VÀ model tự gắn nhãn ngoài-lệnh (UNKNOWN/SOCIAL_UTTERANCE) →
    từ chối + định hướng, phân biệt với goal môi trường mơ hồ (ENVIRONMENT_REQUEST rỗng, ví dụ
    "hôm nay trời đẹp thật") — trường hợp đó hỏi lại phòng vẫn còn ý nghĩa nên giữ nguyên hành vi cũ.

    Nhưng NHÃN do model tự chọn không đủ làm hàng rào: hàng rào chỉ nhận đúng hai nhãn, nên một
    yêu cầu ngoài phạm vi mà model gắn nhãn KHÁC là thoát sạch. Đo thật §2026-08-30: "thủ đô nước
    Pháp là gì" ra INFORMATION_QUESTION rồi rơi xuống "Bạn muốn mình làm ở phòng nào ạ?".

    Với riêng CÂU HỎI, có tín hiệu TẤT ĐỊNH thay cho nhãn: một câu hỏi về nhà mình luôn neo vào
    thiết bị/phòng/cảm biến, hoặc là câu hỏi phạm vi cả nhà — đúng thứ `_asks_about_state` đã
    định nghĩa. Không neo vào gì thì KHÔNG có phòng nào cứu được câu hỏi, nên hỏi lại phòng là
    vô nghĩa y hệt trường hợp UNKNOWN.

    CỐ Ý không áp luật mỏ neo cho các nhãn khác: "ngột ngạt quá", "ồn quá", "tôi sắp về nhà"
    cũng không có mỏ neo nào nhưng đều là yêu cầu thật, chỉ thiếu phòng — hỏi lại mới đúng.
    """
    if goal is None or _is_actionable(goal):
        return False
    if goal.utterance_type in _OUT_OF_SCOPE_UTTERANCE_TYPES:
        return True
    if goal.utterance_type is UtteranceType.INFORMATION_QUESTION and nu is not None:
        return not _asks_about_state(nu)
    return False


def _is_inferred_goal(goal: SemanticGoal | None) -> bool:
    """Phân biệt goal suy diễn với lệnh explicit bằng artifact đã ground.

    `action_hint is None` một mình không đủ làm provenance. Lệnh explicit chỉ
    được coi là explicit khi có cả action và entity thật; mọi trường hợp còn lại
    là inferred/fuzzy và không được tự kèm security action."""
    if goal is not None and goal.user_defined_routine and goal.routine_source_event_id:
        # The trigger is implicit, but every device/action came from an explicit
        # user instruction with Event -> Turn provenance.  Keep authorization and
        # policy gates; do not discard security actions as model-invented inference.
        return False
    return not (
        goal is not None
        and goal.action_hint is not None
        and bool(goal.target_device_ids)
        and goal.utterance_type == UtteranceType.DEVICE_COMMAND
    )


def _social_goal(text: str) -> SemanticGoal:
    """Represent a social/closing turn without inventing a device objective."""
    return SemanticGoal(
        intent="social",
        raw_utterance=text,
        goal_description=text,
        utterance_type=UtteranceType.SOCIAL_UTTERANCE,
        confidence=1.0,
    )


def _preservation_constraint_goal(nu, ledger) -> SemanticGoal | None:
    """Parse a side-device state constraint without replacing the active goal."""
    if not (ledger.current_goal and ledger.confirmed_facts.get("room")):
        return None
    folded = nu.folded
    preserve_state = bool(
        re.search(r"(?<!\w)giu(?!\w).*(?<!\w)(?:nguyen|bat|tat)(?!\w)", folded)
        or re.search(r"(?<!\w)van\s+phai(?!\w)", folded)
    )
    if not preserve_state:
        return None

    room = ledger.confirmed_facts["room"]
    targets = [slug for slug in nu.matched_device_ids if (spec := spec_for(slug)) is not None and spec.room == room]
    if not targets:
        types = tuple(mentioned_device_types(nu))
        targets = (
            [spec.slug for spec in devices_by(device_types=types, area=room, exclude_security=False)] if types else []
        )
    if len(targets) != 1:
        return None

    slug = targets[0]
    if re.search(r"(?<!\w)giu(?!\w).*(?<!\w)bat(?!\w)", folded):
        token = f"keep_on:{slug}"
    elif re.search(r"(?<!\w)giu(?!\w).*(?<!\w)tat(?!\w)", folded):
        token = f"keep_off:{slug}"
    else:
        token = f"avoid:{slug}"
    no_change_device_ids = targets if token.startswith("avoid:") else []
    return SemanticGoal(
        intent="preserve_device_state",
        raw_utterance=nu.raw,
        goal_description=nu.raw,
        utterance_type=UtteranceType.DEVICE_COMMAND,
        confidence=1.0,
        target_area=room,
        target_device_ids=targets,
        no_change_device_ids=no_change_device_ids,
        explicit_constraints=[token],
        references_resolved=True,
        target_devices_deterministic=True,
    )


def _wanted_capability(nu) -> str | None:
    """Capability số mà câu nhắm tới ("quạt"→fan_speed, "âm lượng"→volume...), else None.

    Dùng lọc anaphora theo capability: "tăng quạt của nó" → thực thể nổi bật có fan_speed, không
    phải đèn vừa nhắc. Tái dùng bộ dò capability-noun của understanding (một nguồn luật)."""
    from src.nlu.normalizer import TextView
    from src.nlu.understanding import _mentioned_capability

    cap = _mentioned_capability(TextView(raw=nu.normalized, folded=nu.folded))
    return cap.value if cap is not None else None


def _push_salience(
    ledger,
    device_ids: list[str],
    *,
    turn: int,
    room: str | None,
    kind: str,
    action: str = "",
):
    """Đẩy các thiết bị VỪA tác động/nhắc vào Salience Stack của ledger (§14). Ghi tại chỗ (mutate)."""
    if not device_ids:
        return
    stack = SalienceStack.from_list(ledger.salience)
    stack.push_many(
        [d for d in device_ids if spec_for(d) is not None],
        turn=turn,
        room=room,
        kind=kind,
        action=action,
    )
    ledger.salience = stack.to_list()


_ORDINAL_WORDS = {
    "nhất": 0,
    "một": 0,
    "hai": 1,
    "ba": 2,
    "tư": 3,
    "bốn": 3,
    "năm": 4,
}


def _ordinal_index(text: str) -> int | None:
    """Chỉ số 0-based trong "cái thứ N/cái đầu/cái cuối", hoặc None."""
    normalized = (text or "").lower()
    if re.search(r"(?<!\w)cái\s+cuối(?!\w)", normalized):
        return -1
    if re.search(r"(?<!\w)cái\s+đầu(?!\w)", normalized):
        return 0
    match = re.search(r"(?<!\w)cái\s+thứ\s+(\d+|nhất|một|hai|ba|tư|bốn|năm)(?!\w)", normalized)
    if match is None:
        return None
    token = match.group(1)
    return int(token) - 1 if token.isdigit() and int(token) > 0 else _ORDINAL_WORDS.get(token)


def _ordinal_salience_device(prior_ledger, nu) -> str | None:
    index = _ordinal_index(nu.normalized or "")
    if index is None:
        return None
    hit = SalienceStack.from_list(prior_ledger.salience).ordinal(index)
    return hit.device_id if hit is not None else None


def _deferred_reference_device(prior_ledger, nu) -> str | None:
    """Mô tả "cái ... vừa nói/để lát nữa" → thiết bị deferred gần nhất."""
    text = nu.normalized or ""
    if not nu.has_reference or not re.search(r"\b(vừa nói|để\s+lát nữa|lát nữa)\b", text):
        return None
    hit = SalienceStack.from_list(prior_ledger.salience).most_recent(predicate=lambda entry: entry.kind == "deferred")
    return hit.device_id if hit is not None else None


_DEFERRED_TASK_CANCELLATION = re.compile(
    r"\b(?:thôi\s*,?\s*)?(?:bỏ|hủy|huỷ)\s+(?:công\s+)?việc\s+(?:đó|này|đấy)\b|"
    r"\bkhông\s+cần\s+(?:công\s+)?việc\s+(?:đó|này|đấy)(?:\s+nữa)?\b"
)


def _deferred_cancellation_target(prior_ledger, nu) -> tuple[bool, str | None]:
    """Resolve a cancellation that refers to a previously deferred *task*.

    This is deliberately narrower than ordinary cancellation: a command such as
    ``hủy lệnh vừa rồi`` must keep its existing route. Here, task deixis
    (``bỏ việc đó``) can only consume a deferred anchor. More than one such
    anchor is ambiguous and must be clarified rather than picking the newest.
    """
    text = nu.normalized or ""
    if not _DEFERRED_TASK_CANCELLATION.search(text):
        return False, None
    deferred_ids = list(
        dict.fromkeys(
            str(entry.get("device_id"))
            for entry in prior_ledger.salience
            if entry.get("kind") == "deferred" and entry.get("device_id")
        )
    )
    if not deferred_ids:
        return False, None
    return True, deferred_ids[0] if len(deferred_ids) == 1 else None


def _option_answered(prior_ledger, nu) -> str | None:
    """Lựa chọn DUY NHẤT mà lượt này chỉ tới, khi người dùng trả lời bằng phần phân biệt.

    Câu hỏi đã chào "Phòng ngủ bố mẹ hay Phòng ngủ con"; người dùng đáp gọn "bố mẹ". Không
    có bước này, lượt đó không khớp phòng nào và hệ thống hỏi lại y nguyên — vòng lặp mà
    người dùng nhìn thấy. Chỉ nhận khi câu KHÔNG có động từ hành động (đúng là câu trả lời,
    không phải lệnh mới) và khớp ĐÚNG MỘT lựa chọn — mơ hồ thì vẫn phải hỏi lại.
    """
    if nu.has_action_verb or nu.matched_rooms or nu.matched_device_ids:
        return None
    options = [str(o) for o in (prior_ledger.pending_clarification or {}).get("options") or []]
    if len(options) < 2:
        return None
    answer = strip_diacritics((nu.normalized or "").strip().lower())
    if not answer:
        return None
    hits = [o for o in options if answer in strip_diacritics(o.lower())]
    return hits[0] if len(hits) == 1 else None


def _rebind_room_correction(prior_ledger, new_room: str, raw_utterance: str) -> SemanticGoal | None:
    """Rebind a previous explicit command to a corrected room by registry type.

    Appending the new room to the old sentence leaves both old and new room aliases
    present ("máy lọc phòng con ... phòng bố mẹ") and preserves the stale device.
    Rebinding by the already-confirmed device type makes the correction a proper
    ledger delta and records the rejected old target.
    """
    current = prior_ledger.current_goal or {}
    action_hint = current.get("action_hint")
    old_ids = list(prior_ledger.confirmed_facts.get("devices") or [])
    if not action_hint or not old_ids:
        return None
    rebound: list[str] = []
    for old_id in old_ids:
        old_spec = spec_for(old_id)
        if old_spec is None:
            return None
        candidates = devices_by(device_types=(old_spec.device_type,), area=new_room, exclude_security=False)
        if len(candidates) > 1:
            # Same type, several instances ("Đèn ngủ" and "Đèn bàn làm việc" are both lights).
            # Type alone cannot say which one the user meant, but the registry carries finer
            # identity: narrow to the counterpart that shares the old device's name, else its
            # semantic roles. Bailing out here instead would drop the turn into the slot-fill
            # concatenation, which re-emits the old sentence and acts on BOTH rooms.
            narrowed = [s for s in candidates if s.name == old_spec.name]
            if len(narrowed) != 1 and old_spec.semantic_roles:
                narrowed = [s for s in candidates if set(s.semantic_roles) & set(old_spec.semantic_roles)]
            candidates = narrowed
        if len(candidates) != 1:
            return None
        if candidates[0].slug not in rebound:
            rebound.append(candidates[0].slug)
    return SemanticGoal(
        intent=f"room correction: {current.get('intent') or action_hint}",
        raw_utterance=raw_utterance,
        goal_description=raw_utterance,
        utterance_type=UtteranceType.DEVICE_COMMAND,
        action_hint=str(action_hint),
        target_device_ids=rebound,
        target_area=new_room,
        parameters=dict(current.get("parameters") or {}),
        is_correction=True,
        target_devices_deterministic=True,
        confidence=1.0,
    )


def _salient_anaphora_device(prior_ledger, nu) -> str | None:
    """Thiết bị nổi bật GẦN NHẤT tương thích capability của câu, cho anaphora ("nó"/"tắt đi"/"bật
    lên"). None nếu ngăn xếp rỗng hoặc không có thực thể tương thích (để tầng trên hỏi lại)."""
    stack = SalienceStack.from_list(prior_ledger.salience)
    if len(stack) == 0:
        return None
    cap = _wanted_capability(nu)
    # Với điều chỉnh số, bỏ qua thiết bị vừa bị tắt/đóng/khoá. Hành
    # động cuối là bằng chứng hội thoại tất định khi runner chỉ planning (chưa
    # mutate live state); nếu không, "giảm nó" bám TV vừa tắt thay vì loa còn hoạt động.
    active_predicate = None
    numeric_anaphora = bool(
        nu.has_reference and nu.has_action_verb and re.search(r"(?<!\w)\d+(?!\w)", nu.normalized or "")
    )
    if cap is not None or numeric_anaphora:

        def _not_recently_deactivated(entry):
            return entry.action not in {"turn_off", "close", "lock"}

        active_predicate = _not_recently_deactivated
    hit = stack.most_recent(capability=cap, predicate=active_predicate)
    if hit is None and cap is not None:
        # Câu nêu capability nhưng KHÔNG thực thể nổi bật nào hỗ trợ → không đoán bừa sang thiết bị
        # khác dimension; nhường tầng trên (clarify). Chỉ khi không nêu capability mới lấy đỉnh.
        return None
    resolved = hit or stack.top()
    return resolved.device_id if resolved is not None else None


def _last_device_from_ledger(ledger) -> str | None:
    """Thiết bị đã CHỐT ở lượt trước (spec §14 tier-3, §17) — mỏ neo cho anaphora đa lượt.

    Chỉ carry khi ledger chốt ĐÚNG MỘT thiết bị: "tắt nó đi"/"giảm bớt độ sáng nữa" mới có
    một đích rõ để bind. Nhiều thiết bị → None (để câu chỉ định lại/hỏi lại, không đoán bừa)."""
    if ledger is None:
        return None
    devices = ledger.confirmed_facts.get("devices") or []
    return devices[0] if len(devices) == 1 and spec_for(devices[0]) is not None else None


def _names_capability(nu) -> bool:
    """Câu tự NÊU một capability số (độ sáng/nhiệt độ/âm lượng...)? → understand() ground được,
    không cần kế thừa modifier. Tái dùng bộ dò capability-noun của understanding (không lặp luật)."""
    from src.nlu.normalizer import TextView
    from src.nlu.understanding import _mentioned_capability

    return _mentioned_capability(TextView(raw=nu.normalized, folded=nu.folded)) is not None


def _is_context_refinement(nu) -> bool:
    """Câu này chỉ BỔ SUNG/SỬA ngữ cảnh (nêu phòng) cho mục tiêu lượt trước, không phải lệnh mới?

    Nêu PHÒNG mà KHÔNG mang động từ hành động của riêng nó và KHÔNG dùng tham chiếu ("nó/cái đó").
    Đây là tinh chỉnh vị trí ("à ở phòng ngủ con mà") hoặc trả lời phòng cho câu hỏi ("phòng khách")
    — phải chạy lại mục tiêu lượt trước với phòng mới, không hiểu như câu độc lập rỗng nghĩa. Câu có
    động từ ("bật đèn phòng ngủ") là LỆNH MỚI; câu có tham chiếu đi qua nhánh anaphora (A1)."""
    return bool(nu.matched_rooms) and not nu.has_action_verb and not nu.has_reference


def _persist_pending(
    deps: PipelineDeps,
    state: AgentState,
    goal,
    missing: list[str],
    question: str | None = None,
    nu=None,
    options: list[str] | None = None,
    evidence_trace: list[dict[str, Any]] | None = None,
) -> None:
    """Lưu mục tiêu ĐANG TREO khi clarify vào ledger (§17: ledger là state hội thoại canonical).

    Nhờ vậy lượt sau trả lời phòng ("phòng khách") có mốc raw_utterance để slot-fill lại. Ngoài
    missing_information, ta còn lưu TƯỜNG MINH câu hỏi treo (§15) để lượt sau nhận diện tất định
    "đây là câu trả lời clarification" và giữ dấu vết. Không có goal → bỏ qua (câu rỗng không pending)."""
    if goal is None:
        return
    conv = state.get("conversation_id", "")
    ledger = deps.ledger_store.load(conv)
    turn = ledger.last_updated_turn + 1
    # Một lượt chỉ TRẢ LỜI/THU HẸP câu hỏi đang treo ("phòng ngủ" cho "Bật đèn ở phòng
    # nào?") KHÔNG được thay thế chính câu lệnh nó đang trả lời. Nếu thay, mốc slot-fill
    # biến mất và lượt sau ("phòng ngủ con") không còn gì để ghép → hội thoại quay vòng.
    # Lượt mang động từ hành động của riêng nó LÀ lệnh mới và được phép thay.
    prior_pending = (ledger.pending_clarification or {}).get("raw_utterance") or ""
    answers_prior_question = bool(prior_pending) and nu is not None and not nu.has_action_verb
    pending = {
        "fields": list(missing),
        "question": question or "",
        "turn": turn,
        "raw_utterance": prior_pending if answers_prior_question else goal.raw_utterance,
        # Các lựa chọn ĐÃ CHÀO. Người dùng hay trả lời bằng phần PHÂN BIỆT của lựa chọn
        # ("bố mẹ" cho "Phòng ngủ bố mẹ") — giữ danh sách để lượt sau nhận ra câu trả lời
        # đó thay vì bỏ qua rồi hỏi lại y nguyên.
        "options": list(options or []),
    }
    merged = update_ledger(
        ledger,
        goal,
        turn=turn,
        conversation_id=conv,
        missing_information=missing,
        pending_clarification=pending,
        evidence_trace=evidence_trace,
    )
    deps.ledger_store.save(merged)


def _record_query_salience(
    deps: PipelineDeps,
    state: AgentState,
    nu,
    *,
    device_ids: list[str] | None = None,
) -> None:
    """Cập nhật MỎ NEO anaphora (§14 tier-3) sau khi TRẢ LỜI một câu hỏi trạng thái.

    Một câu HỎI cũng là một lần "nhắc" thiết bị: referent gần nhất của "nó"/"bật lên" ở lượt
    sau phải là thiết bị VỪA được hỏi, KHÔNG phải thiết bị của lệnh cũ trước lúc chuyển chủ đề
    (§73 cô lập ngữ cảnh; topic_switch_stale). Trước bản vá, câu hỏi trả lời rồi return luôn nên
    ledger vẫn neo thiết bị lệnh cũ → "tắt nó đi" lôi nhầm thiết bị cũ trở lại.

    - Câu hỏi NÊU một hoặc nhiều thiết bị điều khiển được → neo về thiết bị/nhóm đó; giữ
      current_goal TỐI THIỂU để `has_grounded_prior` còn True cho lượt tiếp nối cụt.
    - Câu hỏi CHỈ nêu cảm biến/số đo (không thiết bị) → XOÁ mỏ neo: referent gần nhất không điều
      khiển được nên "nó" lượt sau MƠ HỒ → clarify, thay vì hồi sinh thiết bị cũ (TS-001).
    Constraints/rejected_assumptions durable (§18) được GIỮ NGUYÊN."""
    conv = state.get("conversation_id", "")
    ledger = deps.ledger_store.load(conv)
    source_ids = nu.matched_device_ids if device_ids is None else device_ids
    named = list(dict.fromkeys(d for d in source_ids if spec_for(d) is not None))
    # Câu ghép có dismissal/deferral trước câu hỏi:
    # "Thôi chuyện loa để sau. Camera ... thế nào?". Chỉ mệnh đề hỏi
    # cuối là query referent; nếu dùng matched_device_ids toàn câu sẽ thấy 2 thiết bị
    # và xoá salience. Re-analyze câu cuối là phân tách cấu trúc, không map case.
    clauses = [part.strip() for part in re.split(r"[.!?]+", nu.raw or "") if part.strip()]
    if len(clauses) > 1:
        tail_nu = analyze(clauses[-1])
        tail_named = [d for d in tail_nu.matched_device_ids if spec_for(d) is not None]
        if tail_named:
            named = tail_named
    merged = ledger.model_copy(deep=True)
    merged.last_updated_turn = ledger.last_updated_turn + 1
    merged.desired_outcomes = []
    merged.pending_clarification = {}
    if named:
        specs = [spec_for(device_id) for device_id in named]
        valid_specs = [spec for spec in specs if spec is not None]
        merged.confirmed_facts["devices"] = list(named)
        rooms = {spec.room for spec in valid_specs}
        if len(rooms) == 1:
            merged.confirmed_facts["room"] = next(iter(rooms))
        else:
            merged.confirmed_facts.pop("room", None)
        label = valid_specs[0].name if len(valid_specs) == 1 else "nhóm thiết bị"
        merged.current_goal = {
            "goal_description": f"tham chiếu {label}",
            "intent": "state_reference",
            "utterance_type": UtteranceType.INFORMATION_QUESTION.value,
            "action_hint": None,
            "negated": False,
            "raw_utterance": nu.raw or nu.normalized or "",
        }
        # Câu hỏi cũng là một lần "nhắc" thiết bị → đẩy lên Salience Stack. Nhóm mới thay
        # toàn bộ salience cũ để topic-switch không hồi sinh một referent đơn đã lỗi thời.
        if len(named) > 1:
            merged.salience = []
        _push_salience(
            merged,
            list(named),
            turn=merged.last_updated_turn,
            room=next(iter(rooms)) if len(rooms) == 1 else None,
            kind="query" if len(named) == 1 else "group",
        )
    else:
        # Không có thiết bị điều khiển rõ (chỉ cảm biến/số đo) → không còn mỏ neo để "nó" bám
        # vào; xoá cả confirmed_facts LẪN Salience Stack để lượt sau hỏi lại thay vì hồi sinh
        # thiết bị cũ (TS-001).
        merged.confirmed_facts.pop("devices", None)
        merged.current_goal = {}
        merged.salience = []
    deps.ledger_store.save(merged)


def _isolated_topic_ledger(ledger):
    """Create the current-turn ledger view for a genuine topic switch.

    Durable user constraints remain available to the trust boundary, but target,
    salience, pending clarification, and prior-goal evidence are not exposed to
    understanding or planning for the new topic.
    """
    isolated = ledger.model_copy(deep=True)
    isolated.current_goal = {}
    isolated.confirmed_facts = {}
    isolated.desired_outcomes = []
    isolated.preferences_in_scope = []
    isolated.missing_information = []
    isolated.pending_clarification = {}
    # A genuine topic switch must not inherit an ordinary target/reference from
    # the previous command.  A deferred item is different: it is an explicit
    # future-facing discourse anchor ("để máy rửa bát lát nữa") and must remain
    # addressable after an unrelated command in between.
    isolated.salience = [entry for entry in ledger.salience if entry.get("kind") == "deferred"]
    isolated.evidence = []
    return isolated


def _record_conversational_feedback(deps: PipelineDeps, state: AgentState, ledger, signal) -> dict[str, Any]:
    """Persist a conversational preference/acceptance through the real EventStore path."""
    conv = state.get("conversation_id", "")
    turn = ledger.last_updated_turn + 1
    turn_id = f"{conv}-t{turn}"
    actor = state.get("user_id") or "user"
    raw = state.get("user_message", "") or ""
    record_turn(
        deps.turn_store,
        turn_id=turn_id,
        conversation_id=conv,
        text=raw,
        speaker=actor,
    )

    chosen_value = signal.value
    if chosen_value is None and signal.dimension:
        for outcome in ledger.desired_outcomes:
            value = (outcome.get("target_state") or {}).get(signal.dimension)
            if isinstance(value, int | float) and not isinstance(value, bool):
                chosen_value = value
                break
    if chosen_value is None and signal.dimension:
        params = (ledger.current_goal or {}).get("parameters") or {}
        value = params.get(signal.dimension)
        if value is None:
            value = params.get("percent") or params.get("level") or params.get("value")
        if isinstance(value, int | float) and not isinstance(value, bool):
            chosen_value = value
    event = commit_feedback_event(
        deps.event_store,
        event_id=f"{conv}-fb{turn}",
        conversation_id=conv,
        actors=[actor],
        kind=signal.kind,
        dimension=signal.dimension,
        corrected_value=signal.value,
        subject=signal.subject,
        chosen_value=chosen_value,
        source_turn_ids=[turn_id],
        location=[signal.room] if signal.room else None,
        summary=raw,
    )
    advanced = ledger.model_copy(deep=True)
    advanced.last_updated_turn = turn
    deps.ledger_store.save(advanced)
    if event is not None:
        deps.profile_store.consolidate_from_events(deps.event_store.by_actor(actor))
    return {
        "turn_id": turn_id,
        "event_id": event.event_id if event is not None else None,
        "feedback_event_id": event.event_id if event is not None else None,
        "profiles_promoted": 0,
    }


def _record_deferred_salience(deps: PipelineDeps, state: AgentState, nu) -> None:
    """Lưu thiết bị bị hoãn làm mỏ neo mô tả; không biến hoãn thành action."""
    named = [d for d in nu.matched_device_ids if spec_for(d) is not None]
    if len(named) != 1:
        return
    conv = state.get("conversation_id", "")
    ledger = deps.ledger_store.load(conv)
    merged = ledger.model_copy(deep=True)
    merged.last_updated_turn = ledger.last_updated_turn + 1
    spec = spec_for(named[0])
    _push_salience(
        merged,
        named,
        turn=merged.last_updated_turn,
        room=spec.room if spec else None,
        kind="deferred",
    )
    deps.ledger_store.save(merged)


def _refine_env_goal(current_goal: dict, outcomes: list[dict], room: str) -> SemanticGoal | None:
    """Tái dựng mục tiêu CẢM NHẬN (env) lượt trước từ ledger + neo phòng SỬA vào selector/target_area.

    Dùng cho refine-room ("à ở phòng ngủ con mà"): giữ NGUYÊN relative_change/perceived_state đã lưu
    (không nhờ author offline dựng lại — dễ mất chiều), chỉ đổi phòng. Trả None nếu ledger không có outcome."""
    rebuilt: list[DesiredOutcome] = []
    for od in outcomes:
        data = dict(od)
        data["selector"] = {**(data.get("selector") or {}), "area": room}
        rebuilt.append(DesiredOutcome.model_validate(data))
    if not rebuilt:
        return None
    return SemanticGoal(
        intent=current_goal.get("intent") or "refine",
        raw_utterance=current_goal.get("raw_utterance", ""),
        goal_description=current_goal.get("goal_description", ""),
        utterance_type=UtteranceType(current_goal.get("utterance_type", UtteranceType.ENVIRONMENT_REQUEST.value)),
        confidence=0.8,
        desired_outcomes=rebuilt,
        target_area=room,
    )


def _ledger_recap(ledger: Any) -> dict[str, Any] | None:
    """RECAP tất định (spec §5/§17; "LLMs Get Lost in Multi-Turn Conversation" §7.1: chèn lại
    tường minh những gì đã CHỐT thay vì bắt model tự dò trong lịch sử thô — mitigation này đo
    được cải thiện reliability rõ rệt so với để model tự lục lại raw dialogue).

    Tóm tắt NGẮN, chỉ field đã xác nhận trong Ledger phiên hội thoại NÀY (phòng/thiết bị/mục
    tiêu lượt trước) — không phải bằng chứng dài hạn (đó là `_fetch_memory_evidence`, khác
    nguồn, tách riêng để LLM phân biệt "vừa nói phiên này" với "nhớ từ trước"). None nếu
    Ledger rỗng (lượt đầu tiên, không có gì để recap)."""
    facts = ledger.confirmed_facts or {}
    cg = ledger.current_goal or {}
    if not facts and not cg and not ledger.desired_outcomes:
        return None
    recap: dict[str, Any] = {}
    if facts.get("room"):
        recap["room"] = facts["room"]
    if facts.get("devices"):
        recap["devices"] = list(facts["devices"])
    if cg.get("raw_utterance"):
        recap["prior_utterance"] = cg["raw_utterance"]
    if cg.get("action_hint"):
        recap["prior_action"] = cg["action_hint"]
    if ledger.desired_outcomes:
        recap["prior_outcomes"] = [
            {k: v for k, v in o.items() if k in ("relative_change", "target_state", "selector") and v}
            for o in ledger.desired_outcomes
        ]
    return recap or None


def _fetch_memory_evidence(
    deps: PipelineDeps, *, query_text: str, room: str | None, actor: str | None, now: Any
) -> dict[str, Any] | None:
    """Truy hồi bằng chứng EMem/HiGMem (spec §22, §26) SỚM — TRƯỚC khi author goal.

    `retrieve_memory` (node `retrieve_memory`, Layer 3) chạy SAU `update_ledger`, tức sau khi
    `understand`/`author_goal` đã xong — bằng chứng dài hạn vì vậy KHÔNG BAO GIỜ tới được prompt
    author goal (chỉ tới planner). Gọi lại đây, NHẸ HƠN (không graph expansion — để dành cho node
    đầy đủ ở Layer 3), để câu mới lạ/tiếp nối cũng được author CÓ bằng chứng quá khứ liên quan."""
    if not (query_text or "").strip():
        return None
    if not deps.event_store.all_events():
        return None
    evidence = retrieve_memory(
        event_store=deps.event_store,
        turn_store=deps.turn_store,
        query_text=query_text,
        room=room,
        actor=actor,
        now=now,
        top_k=3,
    )
    payload = evidence.to_prompt_payload()
    has_evidence = any(payload[key] for key in ("events", "expanded_turns", "graph_events"))
    return payload if has_evidence else None


def _rerun_goal(
    deps: PipelineDeps,
    *,
    text: str,
    state: AgentState,
    focus_room: str | None,
    last_device_id: str | None,
    ledger: Any = None,
):
    """Suy lại goal trên MỘT câu tái dựng (mục tiêu lượt trước + phòng mới) — §5/§17 refine/slot-fill.

    Chạy lại đúng đường suy luận (understand tất định → author LLM/Fake) để re-trigger cả suy luận lẫn
    grounding với phòng mới. Trả (goal|None, nu, ctx) để pipeline tiếp tục trên chúng."""
    nu2, ctx2, _ = build_perception(
        text,
        now=state.get("now"),
        timezone=state.get("timezone", "Asia/Ho_Chi_Minh"),
        focus_room=focus_room,
        speaker_location=state.get("speaker_location"),
        recent_dialogue=state.get("recent_dialogue"),
        live_device_states=state.get("live_device_states"),
        live_sensors=state.get("live_sensors"),
    )
    model = deps.model_client
    nlu_model = model if hasattr(model, "understand") else None
    u = understand(nu2, ctx2, model_client=nlu_model, last_device_id=last_device_id)
    if u.goal is not None and u.goal.action_hint is not None and u.goal.target_device_ids:
        return u.goal, nu2, ctx2
    authored = (
        author_goal(
            model,
            nu2,
            ctx2,
            utterance=text,
            confirmed_so_far=_ledger_recap(ledger) if ledger is not None else None,
            memory_evidence=_fetch_memory_evidence(
                deps,
                query_text=text,
                room=focus_room or ctx2.speaker_location,
                actor=state.get("user_id"),
                now=state.get("now"),
            ),
            semantic_cache=deps.semantic_cache,
        )
        if (text and not u.abstained)
        else None
    )
    return (authored if authored is not None else u.goal), nu2, ctx2


def _make_understand_node(deps: PipelineDeps):
    def _understand(state: AgentState) -> dict[str, Any]:
        nu = state["normalized"]
        ctx = state["runtime_context"]
        outside_scope_requested = _requests_out_of_scope_component(nu)
        has_smart_home_anchor = _has_smart_home_anchor(nu)

        # Scope boundary phải thắng social-closing. "Soạn email cảm ơn" chứa "cảm ơn" nhưng
        # là một yêu cầu sáng tác, không phải người dùng đang cảm ơn agent.
        if outside_scope_requested and not has_smart_home_anchor:
            return {
                "semantic_goal": None,
                "final_status": "answered",
                "reply": _OUT_OF_SCOPE_REPLY_VI,
                "route_kind": "answer",
            }

        # Câu XÃ GIAO/HOÃN VIỆC (§10-11): không mang mục tiêu, không nên rơi vào vòng hỏi lại.
        # Tất định, kiểm tra TRƯỚC ledger/authoring — hai loại câu này không cần ngữ cảnh gì.
        if is_closing(nu):
            return {
                "semantic_goal": _social_goal(nu.raw),
                "final_status": "answered",
                "reply": "Dạ không có gì, cần gì cứ gọi mình nhé!",
                "route_kind": "answer",
            }
        if is_deferral(nu):
            # Hoãn một thiết bị cụ thể là no-op, nhưng vẫn là bằng chứng tham
            # chiếu cho lượt sau ("cái máy ... để lát nữa"). Chỉ lưu salience,
            # tuyệt đối không dựng goal/plan ngay bây giờ.
            _record_deferred_salience(deps, state, nu)
            return {
                "semantic_goal": None,
                "final_status": "answered",
                "reply": "Dạ được, mình chưa làm gì bây giờ nhé.",
                "route_kind": "answer",
            }

        # Ledger lượt trước = canonical conversation state (§P4/§17). Nạp SỚM để (a) bind
        # anaphora đa lượt, (b) scope explicit routine instructions by the last confirmed room.
        prior_ledger = deps.ledger_store.load(state.get("conversation_id", ""))

        # Huỷ một VIỆC ĐÃ HOÃN ("thôi, bỏ việc đó đi") phải thu hồi đúng mỏ neo
        # deferred, nếu không lượt sau "giờ bật cái máy để lát nữa" sẽ hồi sinh tác
        # vụ người dùng vừa bỏ. Không đi qua cancellation thông thường: "hủy lệnh
        # vừa rồi" vẫn thuộc luồng huỷ lệnh hiện có; nhiều việc hoãn thì hỏi rõ thay
        # vì tự chọn mục mới nhất.
        is_deferred_cancellation, deferred_target = _deferred_cancellation_target(prior_ledger, nu)
        if is_deferred_cancellation:
            if deferred_target is None:
                return {
                    "semantic_goal": None,
                    "final_status": "answered",
                    "reply": "Bạn muốn bỏ việc đã hoãn nào ạ?",
                    "route_kind": "answer",
                }
            merged = prior_ledger.model_copy(deep=True)
            merged.salience = [
                entry
                for entry in merged.salience
                if not (entry.get("kind") == "deferred" and entry.get("device_id") == deferred_target)
            ]
            merged.last_updated_turn = prior_ledger.last_updated_turn + 1
            deps.ledger_store.save(merged)
            return {
                "semantic_goal": None,
                "final_status": "answered",
                "reply": "Dạ, mình đã bỏ việc đã hoãn.",
                "route_kind": "answer",
            }

        # Explicit conditional instructions are written immediately as Event +
        # normalized EDU facts, with raw Turn provenance.  They are declarations,
        # not commands to execute at teaching time.
        routine_capture = capture_routine_turn(
            event_store=deps.event_store,
            turn_store=deps.turn_store,
            conversation_id=state.get("conversation_id", ""),
            actor=state.get("user_id"),
            text=state.get("user_message", "") or nu.raw or "",
            now=state.get("now"),
            focus_room=prior_ledger.confirmed_facts.get("room"),
            anchor_device_ids=tuple(prior_ledger.confirmed_facts.get("devices") or ()),
        )
        if routine_capture is not None:
            reply = {
                "stored": "Dạ, mình đã lưu hướng dẫn này.",
                "updated": "Dạ, mình đã bổ sung hướng dẫn đó.",
                "corrected": "Dạ, mình đã sửa hướng dẫn theo giá trị mới.",
                "deleted": "Dạ, mình đã xoá hướng dẫn đó.",
            }[routine_capture.kind]
            return {
                "semantic_goal": None,
                "final_status": "answered",
                "reply": reply,
                "route_kind": "answer",
            }

        # `prior_ledger` loaded above is reused for context resolution below.

        # DEIXIS VỊ TRÍ (§14, Context-Transducer slice 2): "bật đèn ngủ ở đây", "máy lọc ở đây",
        # "cùng phòng đó" — câu chỉ vị trí bằng đại từ, phân giải phòng từ presence → vị trí hội
        # thoại → phòng chốt gần nhất, rồi RE-ANALYZE để ground thiết bị ĐÚNG phòng (khử mơ hồ "đèn
        # ngủ" giữa 2 phòng). Chỉ khi câu CHƯA nêu phòng tường minh (không đè ngữ cảnh thật).
        utterance_text = state.get("user_message", "") or nu.raw or ""
        if not nu.matched_rooms and has_location_deixis(utterance_text):
            presence_rooms = [
                s.room
                for s in ctx.sensors
                if s.sensor_type == "presence"
                and s.source == "live"
                and not s.is_stale
                and s.value >= 1
                and s.room
            ]
            deictic_room = resolve_deictic_room(
                utterance_text,
                presence_rooms=presence_rooms,
                conversation_location=ctx.speaker_location,
                ledger_room=prior_ledger.confirmed_facts.get("room"),
                valid_rooms=ROOMS,
            )
            if deictic_room:
                # VIẾT LẠI thành lệnh tường minh (Resolve, paper §3): BỎ cụm deixis (gỡ đại từ "đó/
                # đây" để không còn has_reference chặn slot-fill) rồi chèn tên phòng đã phân giải →
                # room_explicit=True, ground được cả thiết bị chỉ nêu LOẠI ("máy lọc"→type-in-room).
                rewritten = f"{strip_deixis(utterance_text)} {deictic_room}".strip()
                nu = analyze(
                    rewritten,
                    focus_room=deictic_room,
                    speaker_home_room=state.get("speaker_home_room"),
                    speaker_private_room=state.get("speaker_private_room"),
                )
                ctx = ctx.model_copy(update={"focus_room": deictic_room})

        # Classify before any ledger-derived anchor/room/memory is exposed.  A topic
        # switch is a hard context boundary for this turn, not merely a hint to avoid
        # the continuation builder later in the pipeline.
        turn_intent: TurnIntent | None = None
        continuation_provenance: str | None = None
        cont_modifier = None
        feedback_continuation = False
        conversational_memory_written: dict[str, Any] | None = None
        preservation_goal: SemanticGoal | None = None
        if state.get("semantic_goal") is None:
            turn_intent, cont_modifier = classify(
                nu,
                prior_ledger,
                has_pending_clarification=bool(prior_ledger.pending_clarification),
            )
            preservation_goal = _preservation_constraint_goal(nu, prior_ledger)
            if preservation_goal is not None:
                turn_intent = TurnIntent.CONTINUATION
        semantic_ledger = (
            _isolated_topic_ledger(prior_ledger) if turn_intent == TurnIntent.TOPIC_SWITCH else prior_ledger
        )

        # Mỏ neo anaphora: ưu tiên tín hiệu client cấp, rồi Salience Stack (nổi bật gần nhất TƯƠNG
        # THÍCH capability của câu — §14), rồi mới thiết bị đơn đã chốt (đường cũ, giữ để không hồi
        # quy khi ngăn xếp trống). Stack bao gồm cả thiết bị được NHẮC trong câu hỏi, không chỉ lệnh.
        last_device_id = (
            state.get("last_device_id")
            or _ordinal_salience_device(semantic_ledger, nu)
            or _deferred_reference_device(semantic_ledger, nu)
            or _salient_anaphora_device(semantic_ledger, nu)
            or _last_device_from_ledger(semantic_ledger)
        )

        # Room-carry đa lượt (§14 tier-3): phòng đã chốt lượt trước làm focus MẶC ĐỊNH khi câu
        # này KHÔNG nêu phòng → lệnh chỉnh trong-phòng ("giảm bớt độ sáng nữa") ground đúng phòng.
        # Guard: chỉ khi client chưa cấp focus + câu không nêu phòng (không đè ngữ cảnh thật).
        last_room = semantic_ledger.confirmed_facts.get("room")
        if last_room and not nu.matched_rooms and ctx.focus_room is None and last_room in ctx.rooms:
            ctx = hydrate_focus_room(
                ctx,
                last_room,
                live_device_states=state.get("live_device_states"),
            )

        # Goal ứng viên: do service/LLM author sẵn (giống kiến trúc repo) HOẶC backend
        # nlu.understand (tất định khi offline). "LLM proposes semantic" (spec §P1).
        goal = state.get("semantic_goal")

        if goal is None and preservation_goal is not None:
            turn = prior_ledger.last_updated_turn + 1
            merged = update_ledger(
                prior_ledger,
                preservation_goal,
                turn=turn,
                conversation_id=state.get("conversation_id", ""),
            )
            _push_salience(
                merged,
                list(preservation_goal.target_device_ids),
                turn=turn,
                room=preservation_goal.target_area,
                kind="constraint",
            )
            deps.ledger_store.save(merged)
            return {
                "semantic_goal": preservation_goal,
                "final_status": "answered",
                "reply": "Dạ, mình sẽ giữ nguyên ràng buộc đó.",
                "route_kind": "answer",
                "turn_intent": TurnIntent.CONTINUATION.value,
            }

        # KHAI BÁO SỞ THÍCH / PHẢN HỒI CHẤP NHẬN (§34): câu này chỉ NHỚ, không phải mệnh lệnh
        # cho lượt này ("tôi thường để điều hoà 25 độ khi ngủ", "ừ mức này được"). Tất định,
        # kiểm tra SỚM (như is_closing/is_deferral ở trên) — nếu không, understand()/author_goal
        # bên dưới sẽ biến nó nhầm thành một kế hoạch hành động ngay. Bỏ qua khi có clarify đang
        # treo (câu này nhiều khả năng là câu TRẢ LỜI cho clarify, không phải khai báo mới) hoặc
        # khi caller đã tự cấp goal (không phải pipeline tự hiểu).
        # HUỶ là tín hiệu tất định mạnh (§5) — KHÔNG bao giờ được diễn giải thành "chấp nhận sở
        # thích": "hủy lệnh vừa rồi" sau khi trợ lý vừa hành động dễ bị classify_preference_feedback
        # bắt nhầm thành accept (assistant_acted=True) rồi trả lời xã giao, nuốt mất ý huỷ.
        if goal is None and turn_intent != TurnIntent.TOPIC_SWITCH:
            assistant_acted = bool(prior_ledger.confirmed_facts.get("devices")) or bool(prior_ledger.desired_outcomes)
            prior_dimension = None
            for prior_outcome in prior_ledger.desired_outcomes:
                for field in ("target_state", "relative_change"):
                    values = prior_outcome.get(field) or {}
                    if values:
                        prior_dimension = next(iter(values))
                        break
                if prior_dimension:
                    break
            if prior_dimension is None:
                params = (prior_ledger.current_goal or {}).get("parameters") or {}
                prior_dimension = next(
                    (key for key in ("temperature", "brightness", "fan_speed", "volume", "position") if key in params),
                    None,
                )
                prior_ids = list(prior_ledger.confirmed_facts.get("devices") or [])
                prior_specs = [spec for device_id in prior_ids if (spec := spec_for(device_id)) is not None]
                cap_sets = [{cap.value for cap in spec.capabilities} for spec in prior_specs]
                cap_values = set.intersection(*cap_sets) if cap_sets else set()
                if prior_dimension is None and "percent" in params:
                    prior_dimension = next(
                        (cap for cap in ("brightness", "position", "volume") if cap in cap_values),
                        None,
                    )
                if prior_dimension is None and "level" in params:
                    prior_dimension = next(
                        (cap for cap in ("fan_speed", "volume", "brightness") if cap in cap_values),
                        None,
                    )
            pf_signal = interpret_preference_feedback(
                nu,
                assistant_acted=assistant_acted,
                prior_devices=list(prior_ledger.confirmed_facts.get("devices") or []),
                prior_room=prior_ledger.confirmed_facts.get("room"),
                prior_dimension=prior_dimension,
            )
            if pf_signal is not None:
                memory_written = _record_conversational_feedback(deps, state, prior_ledger, pf_signal)
                conversational_memory_written = memory_written
            else:
                memory_written = None
            if pf_signal is not None and pf_signal.route == "store_preference":
                return {
                    "semantic_goal": None,
                    "final_status": "answered",
                    "reply": "Dạ mình đã ghi nhớ điều này, lần sau sẽ áp dụng nhé.",
                    "route_kind": "answer",
                    "memory_written": memory_written,
                }
            if pf_signal is not None and pf_signal.route == "accept":
                return {
                    "semantic_goal": None,
                    "final_status": "answered",
                    "reply": "Dạ mình hiểu, cảm ơn bạn đã phản hồi.",
                    "route_kind": "answer",
                    "memory_written": memory_written,
                }
            if pf_signal is not None and pf_signal.route == "correction_only":
                return {
                    "semantic_goal": None,
                    "final_status": "answered",
                    "reply": "Dạ, mình đã ghi nhận mức bạn muốn cho lần sau.",
                    "route_kind": "answer",
                    "memory_written": memory_written,
                }
            if pf_signal is not None and pf_signal.route in {"reject", "correction"}:
                if pf_signal.value is not None:
                    cont_modifier = Modifier(
                        kind="absolute",
                        value=pf_signal.value,
                        dimension=pf_signal.dimension or "",
                        is_correction=True,
                        evidence=[f"feedback:{pf_signal.route}"],
                    )
                elif pf_signal.direction is not None:
                    cont_modifier = Modifier(
                        kind="relative",
                        direction=pf_signal.direction,
                        dimension=pf_signal.dimension or "",
                        is_correction=True,
                        evidence=[f"feedback:{pf_signal.route}"],
                    )
                if cont_modifier is not None:
                    turn_intent = TurnIntent.CONTINUATION
                    feedback_continuation = True
                else:
                    return {
                        "semantic_goal": None,
                        "final_status": "answered",
                        "reply": "Dạ, mình đã ghi nhận rằng mức vừa rồi không phù hợp.",
                        "route_kind": "answer",
                        "memory_written": memory_written,
                    }

        # HUỶ (§5): lượt huỷ ý định đang treo ("hủy lệnh vừa rồi", "thôi bỏ đi"). understand() cố
        # tình trả goal=None cho câu KHÔNG phải DEVICE_COMMAND, để tầng trên xử lý — nên pipeline
        # tự dựng goal huỷ TỐI THIỂU (is_cancellation) cho bridge route "cancelled" (parity graph
        # cũ). update_ledger tự xoá mục tiêu đang treo nhưng GIỮ constraint durable (§18).
        if goal is None and turn_intent == TurnIntent.CANCELLATION:
            goal = SemanticGoal(
                intent="cancellation",
                goal_description=nu.raw or "huỷ",
                utterance_type=UtteranceType.CANCELLATION,
                raw_utterance=nu.raw or "",
                confidence=0.95,
                is_cancellation=True,
            )

        if goal is None:
            # Câu hỏi THỜI TIẾT / AQI ngoài trời (mặc định vị trí nhà, hoặc thành phố người
            # dùng nêu) → gọi Open-Meteo LIVE, trả lời ngay. Đặt TRƯỚC understand()/author vì
            # đây không phải lệnh điều khiển: để nó chạy qua goal-authoring thì "dự báo thời
            # tiết ngày mai" bị hiểu thành mục tiêu môi trường rồi hỏi "phòng nào ạ?".
            weather_ask = detect_weather_question(nu)
            if weather_ask is not None:
                reply = _answer_weather(weather_ask, ctx, deps)
                if reply:
                    return {
                        "semantic_goal": None,
                        "final_status": "answered",
                        "reply": reply,
                        "route_kind": "answer",
                    }

            reasoning_model = deps.model_client
            # nlu.understand chỉ nhận model có `.understand()` (backend NLU); ReasoningModel
            # dùng cho AUTHOR goal open-ended ở dưới.
            nlu_model = reasoning_model if hasattr(reasoning_model, "understand") else None
            u = understand(nu, ctx, model_client=nlu_model, last_device_id=last_device_id)

            # Strong standalone outside-scope request: stop before open-ended authoring can
            # reinterpret "review phim" as a watch-movie routine or "soạn email" as an
            # environmental goal. Mixed requests keep going so their smart-home clause survives.
            if outside_scope_requested and not has_smart_home_anchor:
                return {
                    "semantic_goal": None,
                    "final_status": "answered",
                    "reply": _OUT_OF_SCOPE_REPLY_VI,
                    "route_kind": "answer",
                }

            if u.utterance_type == UtteranceType.SOCIAL_UTTERANCE and u.goal is None:
                return {
                    "semantic_goal": _social_goal(nu.raw),
                    "final_status": "answered",
                    "reply": "Chào bạn! Mình có thể giúp điều khiển và theo dõi các thiết bị trong nhà.",
                    "route_kind": "answer",
                }

            # Câu hỏi KIẾN THỨC (an toàn/bảo dưỡng/cách dùng/dải giá trị) → Knowledge RAG (§27, P5):
            # tách khỏi state-query (snapshot sống) và memory. Chỉ khi KHÔNG phải lệnh điều khiển
            # (goal None) và câu MANG marker kiến thức (asks_knowledge) — §27 "chỉ gọi khi thật cần".
            if u.goal is None and asks_knowledge(nu):
                kb_reply = answer_knowledge(nu, deps.knowledge_base)
                if kb_reply:
                    return {
                        "semantic_goal": None,
                        "final_status": "answered",
                        "reply": kb_reply,
                        "route_kind": "answer",
                    }

            # Câu HỎI (không phải mệnh lệnh): trả lời từ snapshot sống — tất định, KHÔNG lập
            # kế hoạch (parity với graph cũ; spec: understanding không execute). Chỉ nhận
            # STATE_QUERY khi luật tất định cũng thấy đây là câu hỏi có đích cụ thể.
            if u.utterance_type == UtteranceType.INFORMATION_QUESTION and u.goal is None:
                if _asks_about_state(nu) and not asks_knowledge(nu):
                    prior_device_ids = list(semantic_ledger.confirmed_facts.get("devices") or [])
                    query_devices = resolve_state_query_devices(nu, ctx, prior_device_ids=prior_device_ids)
                    # Cập nhật mỏ neo anaphora về referent VỪA hỏi (§14) — trước khi return, để
                    # "tắt nó đi" lượt sau bám thiết bị vừa hỏi, không lôi thiết bị lệnh cũ (§73).
                    _record_query_salience(
                        deps,
                        state,
                        nu,
                        device_ids=[device.device_id for device in query_devices],
                    )
                    reply = answer_state_query(nu, ctx, devices=query_devices)
                    if reply:
                        return {
                            "semantic_goal": None,
                            "final_status": "answered",
                            "reply": reply,
                            "route_kind": "answer",
                        }
                # Chỉ câu hỏi NĂNG LỰC thật mới nhận catalog chung. Câu hỏi ngoài domain không
                # có state anchor phải từ chối rõ, không trả một câu né tránh và càng không được
                # rơi vào sensor do va chạm bỏ dấu ("mua" cổ phiếu ≠ "mưa").
                if not _asks_system_capability(nu):
                    return {
                        "semantic_goal": None,
                        "final_status": "answered",
                        "reply": _OUT_OF_SCOPE_REPLY_VI,
                        "route_kind": "answer",
                    }
                return {
                    "semantic_goal": None,
                    "final_status": "answered",
                    "reply": (
                        "Mình điều khiển được đèn, điều hoà, rèm, loa/TV và các thiết bị trong nhà. "
                        "Bạn thử nói 'bật đèn phòng khách' hoặc hỏi trạng thái thiết bị nhé."
                    ),
                    "route_kind": "answer",
                }

            exclusion_split = split_exclusion_clause(nu.raw)
            positive_clause_nu = (
                analyze(
                    exclusion_split.positive_clause,
                    focus_room=ctx.focus_room,
                    speaker_home_room=ctx.speaker_home_room,
                    speaker_private_room=ctx.speaker_private_room,
                )
                if exclusion_split is not None
                else None
            )
            open_positive_clause = bool(
                positive_clause_nu is not None
                and not positive_clause_nu.has_action_verb
                and not positive_clause_nu.matched_device_ids
                and positive_clause_nu.folded.strip(" ,.;:!?—-")
                not in {"va", "con", "nhung"}
            )
            if (
                u.goal is not None
                and u.goal.action_hint is not None
                and u.goal.target_device_ids
                and not open_positive_clause
            ):
                # Lệnh TƯỜNG MINH (có thiết bị rõ): goal tất định (rẻ, offline), không cần LLM.
                goal = u.goal
            elif (
                u.goal is not None
                and u.goal.action_hint is not None
                and not nu.matched_rooms
                and not open_positive_clause
            ):
                # Lệnh điều khiển TƯỜNG MINH (có động từ tất định) NHƯNG không phân giải được thiết
                # bị cụ thể, KHÔNG nêu phòng ("tắt đèn", "bật tivi") = thiếu ĐÍCH thực thi.
                # Vẫn giữ goal tất định để Semantic Resolver có thể retry theo phòng đã
                # chốt trong ledger; `last_device_id` không được làm ta rơi sang LLM khi câu
                # tự nêu một LOẠI thiết bị khác mỏ neo ("TV âm lượng 20" sau loa).
                # Đây là
                # under-specification tất định — KHÔNG để LLM đoán bừa thiết bị fallback (§P2 "code
                # decides"): giữ goal tất định (cụt) để sufficiency gate hỏi lại đúng thiết bị/phòng,
                # thay vì over-ground vào thiết bị mặc định của phòng khách.
                goal = u.goal
            elif turn_intent == TurnIntent.CLARIFICATION_ANSWER:
                # Câu slot-only ("phòng khách", "cửa chính") đang trả lời câu hỏi treo.
                # Không author nó như một goal độc lập bằng LLM: nhánh refinement bên dưới sẽ
                # ghép câu gốc + slot rồi chạy lại parser tất định. Author trước vừa tốn một
                # model call, vừa có thể làm mục tiêu mới đè/méo context đang treo.
                goal = u.goal
            else:
                # Câu MỚI LẠ / mơ hồ OPEN-ENDED (không có động từ điều khiển tất định): LLM author
                # goal → FR-16 generalize sang paraphrase & unseen (Fake offline, MODEL_NAME prod).
                # Model lỗi → None → rơi về goal tất định (thường None) → CLARIFY. LLM proposes, code decides.
                utt = (state.get("user_message", "") or nu.raw or "").strip()
                # Câu rỗng / abstain: không có gì để diễn giải → KHÔNG author (tránh goal rỗng).
                authored = (
                    author_goal(
                        reasoning_model,
                        nu,
                        ctx,
                        utterance=utt,
                        confirmed_so_far=_ledger_recap(semantic_ledger),
                        memory_evidence=(
                            None
                            if turn_intent == TurnIntent.TOPIC_SWITCH
                            else _fetch_memory_evidence(
                                deps,
                                query_text=utt,
                                room=ctx.focus_room or ctx.speaker_location,
                                actor=state.get("user_id"),
                                now=state.get("now"),
                            )
                        ),
                        semantic_cache=deps.semantic_cache,
                    )
                    if (utt and not u.abstained)
                    else None
                )
                goal = authored if authored is not None else (None if u.abstained else u.goal)

        # HiGMem read path for explicit routines: inspect the event anchor first;
        # exact action facts are rewritten into capability outcomes and then still
        # pass through specialists, validator, authorization and policy.  Only
        # trigger-like utterances (no new device/action) may activate a routine.
        if (
            turn_intent != TurnIntent.TOPIC_SWITCH
            and turn_intent != TurnIntent.CLARIFICATION_ANSWER
            and not nu.has_action_verb
            and not nu.matched_device_ids
            and not nu.has_cancellation
        ):
            routine_event = match_routine_event(
                deps.event_store.all_events(),
                state.get("user_message", "") or nu.raw or "",
                actor=state.get("user_id"),
            )
            if routine_event is not None:
                goal = goal_from_routine(
                    routine_event,
                    raw_utterance=state.get("user_message", "") or nu.raw or "",
                    base_goal=goal,
                )

        # Kế thừa mục tiêu tiếp nối (§5, §17) — SAU authoring, làm FALLBACK/ĐÈ có kiểm soát:
        #   • modifier MANG GIÁ TRỊ (tuyệt đối/½: "à 25 độ", "60 thôi", "mở lại một nửa") → ĐÈ, vì
        #     understand() không lấy được con số từ ngữ cảnh (phải kế thừa đích + set giá trị mới).
        #   • modifier TƯƠNG ĐỐI ("mạnh hơn") → chỉ kế thừa khi understand/author KHÔNG ra mục tiêu
        #     dùng được (không thiết bị & không outcome) — giữ nguyên đường capability-carry đã có
        #     ("giảm bớt độ sáng" vẫn do understand xử lý, không bị đè → không hồi quy).
        if cont_modifier is not None and turn_intent == TurnIntent.EXCLUSION_REFINEMENT:
            # Loại trừ thiết bị ("trừ X ra", "X thì thôi") luôn ĐÈ — câu này tự thân không mang
            # động từ điều khiển nên understand()/author_goal không thể tự dựng ra mục tiêu dùng
            # được; kế thừa Ledger là đường DUY NHẤT.
            cont = build_continuation_goal(prior_ledger, cont_modifier, ctx)
            if cont is not None and _is_actionable(cont):
                goal = cont
                continuation_provenance = f"{turn_intent.value}: {'; '.join(cont_modifier.evidence)}"
        elif cont_modifier is not None and turn_intent == TurnIntent.CONTINUATION:
            value_carrying = cont_modifier.kind in ("absolute", "fraction", "bound")
            # Câu tự NÊU capability ("giảm bớt độ sáng", "giảm nhiệt độ") đã được understand() ground
            # đúng qua capability-carry → KHÔNG đè. Chỉ modifier THUẦN (không nêu capability) mới cần
            # kế thừa chiều/đích từ mục tiêu trước. Modifier mang GIÁ TRỊ luôn đè (understand không lấy số).
            if feedback_continuation or value_carrying or not _names_capability(nu):
                cont = build_continuation_goal(prior_ledger, cont_modifier, ctx)
                if cont is not None and _is_actionable(cont):
                    goal = cont
                    continuation_provenance = f"{turn_intent.value}: {'; '.join(cont_modifier.evidence)}"

        # A correction that explicitly supplies a different room is a registry
        # rebind of the previous device type, not string concatenation with the old
        # room.  This also prevents the rejected old device from remaining salient.
        #
        # The cue is STRUCTURAL, not lexical: a bare refinement turn that names one room
        # while the ledger already CONFIRMED a different room is a replacement, whatever
        # words carry it ("à ở phòng ngủ bố mẹ mà", "không, phòng bếp", "phòng bếp cơ").
        # Matching only correction keywords let those turns fall through to the slot-fill
        # concatenation below, which re-emits the old sentence — old room alias included —
        # and acts on BOTH rooms. Answering a clarify that has no confirmed room is NOT a
        # correction and must keep the concatenation path (`confirmed_room` is empty then,
        # because the updater pops a room it just reported as missing).
        confirmed_room = (prior_ledger.confirmed_facts or {}).get("room") or ""
        replaces_confirmed_room = bool(
            turn_intent != TurnIntent.TOPIC_SWITCH
            and confirmed_room
            and len(nu.matched_rooms) == 1
            and nu.matched_rooms[0] != confirmed_room
            and not nu.has_action_verb
            and not nu.has_reference
        )
        room_correction_cue = bool(
            nu.has_correction
            or replaces_confirmed_room
            or re.search(r"^\s*không\b.*\b(?:bảo|ý\s+(?:tôi|mình))\b", nu.normalized or "")
        )
        if turn_intent != TurnIntent.TOPIC_SWITCH and room_correction_cue and len(nu.matched_rooms) == 1:
            rebound = _rebind_room_correction(
                prior_ledger,
                nu.matched_rooms[0],
                state.get("user_message", "") or nu.raw or "",
            )
            if rebound is not None:
                goal = rebound
                continuation_provenance = "room_correction: registry_type_rebind"

        # Tinh chỉnh/slot-fill đa lượt (§5, §17): câu chỉ NÊU phòng HOẶC đúng MỘT thiết bị (không
        # động từ/tham chiếu) mà tự nó KHÔNG tạo được mục tiêu hành động → chạy lại mục tiêu lượt
        # trước ghép với phần vừa trả lời. Bao trùm cả sửa phòng ("à ở phòng ngủ con mà"), trả lời
        # phòng cho clarify ("phòng khách"), LẪN trả lời THIẾT BỊ cho clarify hỏi thiết bị ("cửa
        # chính") — câu hỏi có thể hỏi phòng (khi 2 thiết bị khác phòng) nhưng người dùng trả lời
        # bằng tên thiết bị vẫn phải resolve được, không hỏi lại vòng hai.
        # Câu refinement (chỉ nêu phòng/thiết bị, không động từ) tự nó KHÔNG đáng tin để author độc
        # lập (author offline dễ ra goal rỗng) → nếu ledger có mục tiêu lượt trước, refine ĐÈ.
        # Trả lời NÊU đúng MỘT thiết bị (dù có kèm phòng, "điều hoà phòng khách") là bằng chứng
        # MẠNH HƠN trả lời chỉ nêu phòng — luôn ưu tiên ghép vào câu cũ để grounding tất định tự
        # neo đúng thiết bị, thay vì đi đường env-refine (chỉ áp phòng, bỏ phí tín hiệu thiết bị).
        device_named = (
            len(nu.matched_device_ids) == 1
            and not nu.has_action_verb
            and not nu.has_reference
            and not re.search(r"(?<!\w)\d+(?!\w)", nu.normalized or "")
        )
        # Guard: EXCLUSION_REFINEMENT ("trừ X ra") cũng khớp `device_named` (nêu đúng 1 thiết bị,
        # không động từ) — nếu đã kế thừa thành công ở trên (continuation_provenance đã set),
        # KHÔNG chạy lại refine ở đây (sẽ ghép "câu cũ + tên thiết bị" và xoá mất phần loại trừ).
        # Mốc để ghép câu trả lời vào: câu hỏi ĐANG TREO là bản ghi có thẩm quyền về "lệnh nào
        # còn chờ trả lời" (§15, §17) — nó sống qua các lượt chỉ THU HẸP ("phòng ngủ"), trong khi
        # `current_goal` bị chính lượt thu hẹp đó ghi đè. Không có pending thì dùng current_goal
        # như cũ (lượt tinh chỉnh một lệnh đã chạy).
        pending_utterance = (prior_ledger.pending_clarification or {}).get("raw_utterance") or ""
        # Trả lời cụt bằng phần phân biệt của một lựa chọn đã chào ("bố mẹ") — coi như đã nêu
        # phòng đó, rồi đi tiếp đúng đường slot-fill bên dưới.
        chosen_option = _option_answered(prior_ledger, nu)
        if turn_intent != TurnIntent.TOPIC_SWITCH and chosen_option and chosen_option in ROOMS and pending_utterance:
            g2, nu2, ctx2 = _rerun_goal(
                deps,
                text=f"{pending_utterance} {chosen_option}",
                state=state,
                focus_room=chosen_option,
                last_device_id=last_device_id,
                ledger=prior_ledger,
            )
            if _is_actionable(g2):
                goal, nu, ctx = g2, nu2, ctx2
                continuation_provenance = "clarification_option: elliptical_answer"
        if (
            turn_intent != TurnIntent.TOPIC_SWITCH
            and (_is_context_refinement(nu) or device_named)
            and continuation_provenance is None
            and (
                prior_ledger.desired_outcomes
                or pending_utterance
                or (prior_ledger.current_goal or {}).get("raw_utterance")
            )
        ):
            cg = dict(prior_ledger.current_goal or {})
            if pending_utterance:
                cg["raw_utterance"] = pending_utterance
            if prior_ledger.desired_outcomes and nu.matched_rooms and not device_named:
                # Mục tiêu CẢM NHẬN treo (env): tái dựng từ ledger + phòng mới (giữ chiều tiện nghi).
                refine_room = nu.matched_rooms[0]
                g2 = _refine_env_goal(cg, prior_ledger.desired_outcomes, refine_room)
                if g2 is not None and _is_actionable(g2):
                    goal = g2
            elif cg.get("raw_utterance"):
                # Lệnh TƯỜNG MINH treo (slot-fill "phòng khách"/"cửa chính"/"điều hoà phòng khách"):
                # chạy lại "câu cũ + phần trả lời" — grounding tất định (type-in-room/alias thiết
                # bị) đòi thông tin NÊU RÕ trong text, nên ghép vào câu thay vì chỉ set focus.
                answer_text = nu.raw if device_named else nu.matched_rooms[0]
                focus = None if device_named else nu.matched_rooms[0]
                combined = f"{cg['raw_utterance']} {answer_text}".strip()
                g2, nu2, ctx2 = _rerun_goal(
                    deps,
                    text=combined,
                    state=state,
                    focus_room=focus,
                    last_device_id=last_device_id,
                    ledger=prior_ledger,
                )
                if _is_actionable(g2):
                    goal, nu, ctx = g2, nu2, ctx2

        # Episodic evidence for CONTEXT RESOLUTION must be scoped more strictly
        # than long-term memory used for goal authoring. Resolve only from events
        # whose source turns belong to this conversation, whose actor is allowed,
        # and whose topic passes the normal relevance threshold. Passing the raw
        # household EventStore here lets an unrelated user/session silently supply
        # a room merely because its event was inserted first.
        resolution_events = []
        if turn_intent != TurnIntent.TOPIC_SWITCH and deps.event_store.all_events():
            resolution_evidence = retrieve_memory(
                event_store=deps.event_store,
                turn_store=deps.turn_store,
                query_text=state.get("user_message", "") or nu.raw or "",
                room=ctx.focus_room or ctx.speaker_location,
                actor=state.get("user_id"),
                conversation_id=state.get("conversation_id", ""),
                now=state.get("now"),
                top_k=3,
            )
            resolution_events = resolution_evidence.events

        # Layer 2 đầy đủ: transducer → context-resolver 6-tier → sufficiency gate → clarify.
        outcome = resolve_semantics(
            nu,
            ctx,
            goal,
            ledger=semantic_ledger,
            events=resolution_events,
        )
        evidence_trace = list(outcome.evidence_trace or [])

        # Ngoài phạm vi nhà thông minh (§4 "hiểu không execute" vẫn áp: đây chỉ là một reply,
        # không phải hành động) — chặn TRƯỚC mọi nhánh clarify bên dưới, vì cả nhánh PROCEED-nhưng-
        # rỗng lẫn nhánh CLARIFY/ABSTAIN đều fallback hỏi "phòng nào" khi không còn field nào thiếu,
        # điều vô nghĩa với một yêu cầu không liên quan nhà thông minh.
        if _is_out_of_domain(outcome.goal, nu):
            return {
                "semantic_goal": outcome.goal,
                "is_inferred_goal": _is_inferred_goal(outcome.goal),
                "final_status": "answered",
                "reply": _OUT_OF_SCOPE_REPLY_VI,
                "route_kind": "answer",
                "evidence_trace": evidence_trace,
            }

        mixed_scope_refusal = _MIXED_SCOPE_REPLY_VI if outside_scope_requested and has_smart_home_anchor else ""

        # Cổng KHÔNG-CÓ-THIẾT-BỊ: người dùng nêu một đích mà registry không hề có (loại thiết
        # bị nhà này không lắp, hoặc một phòng không tồn tại). Hỏi lại ở đây là vô nghĩa — câu
        # "bật quạt trần phòng khách" ĐÃ nêu phòng mà vẫn bị hỏi "phòng nào ạ?", vì cổng
        # thiếu-đích bên dưới không phân biệt được "thiếu thông tin" với "không tồn tại".
        # Đặt TRƯỚC cổng ACTIONABLE và chỉ khi chưa ground được thiết bị nào, để không bao giờ
        # chặn một lệnh vốn thực hiện được.
        if (
            outcome.goal is not None
            and not outcome.goal.target_device_ids
            and (missing := unavailable_target(nu)) is not None
        ):
            return {
                "semantic_goal": outcome.goal,
                "is_inferred_goal": _is_inferred_goal(outcome.goal),
                "final_status": "answered",
                "reply": f"Nhà mình không có {missing} nên chưa điều khiển được. Bạn kiểm tra lại giúp mình nhé.",
                "route_kind": "answer",
                "evidence_trace": evidence_trace,
            }

        # Cổng ACTIONABLE (spec §FR-02/FR-04): mục tiêu mơ hồ không đủ để hành động (không
        # thiết bị cụ thể, không desired_outcome) → HỎI LẠI, không để sinh kế hoạch rỗng rồi
        # reject câm ("tắt đèn"/"đèn"/"mở ra" thiếu ngữ cảnh). Ambiguous → clarify, không no-op.
        if outcome.decision in (
            SufficiencyDecision.PROCEED,
            SufficiencyDecision.RESOLVE_CONTEXT,
        ) and not _is_actionable(outcome.goal):
            fields = ["device"] if (outcome.goal and outcome.goal.action_hint) else ["room"]
            clar = build_clarification(fields, nu=nu, ctx=ctx)
            clar_q = clar.question if clar else "Bạn muốn mình làm gì, ở thiết bị/phòng nào ạ?"
            if mixed_scope_refusal:
                clar_q = f"{clar_q} {mixed_scope_refusal}"
            _persist_pending(
                deps,
                state,
                outcome.goal,
                fields,
                question=clar_q,
                nu=nu,
                options=list(clar.options) if clar else None,
                evidence_trace=evidence_trace,
            )
            return {
                "semantic_goal": outcome.goal,
                "is_inferred_goal": _is_inferred_goal(outcome.goal),
                "sufficiency_decision": SufficiencyDecision.CLARIFY,
                "semantic_analysis": outcome.analysis.to_dict(),
                "requires_clarification": True,
                "clarification_question": clar.question if clar else "Bạn muốn mình làm gì, ở thiết bị/phòng nào ạ?",
                "clarification_reason": "goal_not_actionable",
                "memory_written": conversational_memory_written or {},
                "evidence_trace": evidence_trace,
            }

        result: dict[str, Any] = {
            "semantic_goal": outcome.goal,
            "is_inferred_goal": _is_inferred_goal(outcome.goal),
            "sufficiency_decision": outcome.decision,
            "semantic_analysis": outcome.analysis.to_dict(),
            "evidence_trace": evidence_trace,
            # nu/ctx có thể đã được thay bằng bản refine đa lượt → truyền xuống để cả turn nhất quán.
            "normalized": nu,
            "runtime_context": ctx,
        }
        if mixed_scope_refusal:
            result["scope_refusal"] = mixed_scope_refusal
        if turn_intent is not None:
            result["turn_intent"] = turn_intent.value
        if continuation_provenance is not None:
            result["continuation_provenance"] = continuation_provenance
        if conversational_memory_written is not None:
            result["memory_written"] = conversational_memory_written
        if outcome.decision in (SufficiencyDecision.CLARIFY, SufficiencyDecision.ABSTAIN):
            q = (
                outcome.clarification.question
                if outcome.clarification
                else ("Mình chưa rõ ý bạn, bạn nói cụ thể hơn giúp mình nhé?")
            )
            if mixed_scope_refusal:
                q = f"{q} {mixed_scope_refusal}"
            _persist_pending(
                deps,
                state,
                outcome.goal,
                list(outcome.analysis.missing_information) or ["room"],
                question=q,
                nu=nu,
                options=list(outcome.clarification.options) if outcome.clarification else None,
                evidence_trace=evidence_trace,
            )
            result.update(
                {
                    "requires_clarification": True,
                    "clarification_question": q,
                    "clarification_reason": outcome.reason,
                }
            )
        return result

    return _understand


def _route_after_sufficiency(state: AgentState) -> str:
    # Câu hỏi đã trả lời từ snapshot → nhánh answer (không lập kế hoạch).
    if state.get("route_kind") == "answer":
        return "answer"
    # PROCEED và RESOLVE_CONTEXT đều đi tiếp (RESOLVE_CONTEXT đã áp resolution vào goal).
    decision = state.get("sufficiency_decision")
    if decision in (SufficiencyDecision.PROCEED, SufficiencyDecision.RESOLVE_CONTEXT):
        return "proceed"
    return "clarify"


def _clarify_node(state: AgentState) -> dict[str, Any]:
    return {
        "final_status": "clarification_required",
        "reply": state.get("clarification_question") or "Bạn có thể nói rõ hơn được không ạ?",
    }


def _answer_node(state: AgentState) -> dict[str, Any]:
    """Terminal cho câu hỏi trạng thái/năng lực — reply đã soạn ở understand node."""
    return {"final_status": "answered", "reply": state.get("reply", "")}


def _make_update_ledger_node(deps: PipelineDeps):
    def _update(state: AgentState) -> dict[str, Any]:
        conv = state.get("conversation_id", "")
        goal: SemanticGoal | None = state.get("semantic_goal")
        ledger = deps.ledger_store.load(conv)
        if state.get("turn_intent") == TurnIntent.TOPIC_SWITCH.value:
            ledger = _isolated_topic_ledger(ledger)
        turn = ledger.last_updated_turn + 1
        merged = update_ledger(
            ledger,
            goal,
            turn=turn,
            conversation_id=conv,
            evidence_trace=state.get("evidence_trace") or [],
        )
        # Đẩy thiết bị VỪA tác động vào Salience Stack (§14) — nền cho anaphora lượt sau.
        if goal is not None and not goal.is_cancellation:
            device_ids = list(goal.target_device_ids)
            _push_salience(
                merged,
                device_ids,
                turn=turn,
                room=goal.target_area,
                kind="group" if len(device_ids) > 1 else "command",
                action=goal.action_hint or "",
            )
        deps.ledger_store.save(merged)
        return {"requirement_ledger": merged}

    return _update


def _make_retrieve_memory_node(deps: PipelineDeps):
    def _retrieve(state: AgentState) -> dict[str, Any]:
        goal: SemanticGoal | None = state.get("semantic_goal")
        if goal is None:
            return {"memory_evidence": []}
        if state.get("turn_intent") == TurnIntent.TOPIC_SWITCH.value:
            return {"memory_evidence": [], "profile_evidence": []}
        ctx = state["runtime_context"]
        room = ctx.focus_room or ctx.speaker_location
        # Dựng EMem-G từ event hiện có (§23) để mở rộng quan hệ tuỳ chọn (§26).
        events = deps.event_store.all_events()
        graph = MemoryGraph()
        for ev in events:
            graph.add_event(ev)
        evidence = retrieve_memory(
            event_store=deps.event_store,
            turn_store=deps.turn_store,
            query_text=goal.goal_description or goal.raw_utterance,
            room=room,
            actor=state.get("user_id"),
            now=state.get("now"),
            graph=graph if events else None,
        )
        # Tier-6 Profile/Semantic Memory (§14, §24, §26): stable fact của các thiết bị TRONG SCOPE
        # mục tiêu (thiết bị nêu đích danh, hoặc thiết bị trong phòng đang xét) — bằng chứng bền,
        # tách khỏi RL preference (P5). Chỉ đọc, không override live state (invariant #3).
        subjects: set[str] = set(goal.target_device_ids or [])
        area = goal.target_area or room
        if area:
            subjects |= {d.device_id for d in ctx.devices if d.room == area}
        profiles = deps.profile_store.relevant(subjects) if subjects else []
        return {"memory_evidence": evidence.events, "profile_evidence": [p.model_dump() for p in profiles]}

    return _retrieve


def _make_infer_preferences_node(deps: PipelineDeps):
    def _infer(state: AgentState) -> dict[str, Any]:
        from src.agent.preference.state_encoder import build_canonical_decision_context
        from src.config import get_settings

        ctx = state["runtime_context"]
        power = state["power_load"]
        room = ctx.focus_room or ctx.speaker_location
        # Cảm biến ngoài trời (không có phòng hoặc slug ngoại trời) (Finding 4)
        outside_temp_reading = outdoor_sensor_by_type(ctx.sensors, "temperature")
        occ = occupancy_for_room(ctx.sensors, room) if room else {"occupied": None}
        rl_state = encode_state(
            resident=state.get("user_id"),
            room=room,
            now=ctx.now,
            outside_temp_c=outside_temp_reading.value if outside_temp_reading else None,
            occupied=occ.get("occupied"),
            power_mode=power.mode.value,
        )
        decision_context = build_canonical_decision_context(
            resident=state.get("user_id"),
            room=room,
            now=ctx.now,
            outside_temp_c=outside_temp_reading.value if outside_temp_reading else None,
            occupied=occ.get("occupied"),
            power_mode=power.mode.value,
            sensors=ctx.sensors,
        )

        dists = {}
        # Cờ learning_v2_read kiểm soát quyền đọc preference vào planner
        if get_settings().learning_v2_read:
            repo = getattr(deps, "preference_repo", None)
            if repo is not None:
                household_id = int(state.get("household_id") or 0)
                uid_raw = state.get("user_id")
                user_id = int(uid_raw) if uid_raw is not None and str(uid_raw).isdigit() else None
                for dim in ("temperature", "brightness"):
                    d = repo.distribution(household_id=household_id, user_id=user_id, dimension=dim, state=rl_state)
                    if d is not None:
                        dists[dim] = d
            elif deps.preference_store is not None:
                for dim in deps.preference_store.dimensions():
                    d = deps.preference_store.distribution(dim, rl_state)
                    if d is not None:
                        dists[dim] = d

        # Giữ lại rl_state và decision_context đã mã hoá để rl_update phạt/thưởng ĐÚNG (s,a)
        return {"preference_distribution": dists, "rl_state": rl_state, "decision_context": decision_context}

    return _infer


def _manager_plan_node(state: AgentState) -> dict[str, Any]:
    goal = state.get("semantic_goal")
    if goal is None:
        return {"subgoals": []}
    ctx = state["runtime_context"]
    return {"subgoals": build_subgoals(goal, ctx)}


_BOUND_TARGET_KEYS = {
    "brightness": ("brightness", "percent", "level"),
    "temperature": ("temperature", "value"),
    "fan_speed": ("fan_speed", "level", "value"),
    "volume": ("volume", "percent", "level"),
    "position": ("position", "percent", "level"),
}


def _apply_ledger_bounds(actions, ledger) -> None:
    """Clamp numeric proposal targets to durable user bounds before optimization."""
    for action in actions:
        minimum, maximum = numeric_bounds_for(
            ledger,
            device_id=action.device_id,
            capability=action.capability,
        )
        if minimum is None and maximum is None:
            continue
        target = dict(action.target or {})
        for key in _BOUND_TARGET_KEYS.get(action.capability, (action.capability,)):
            value = target.get(key)
            if not isinstance(value, int | float) or isinstance(value, bool):
                continue
            bounded = float(value)
            if minimum is not None:
                bounded = max(minimum, bounded)
            if maximum is not None:
                bounded = min(maximum, bounded)
            target[key] = int(bounded) if bounded.is_integer() else bounded
        action.target = target


def _specialists_aggregate_node(state: AgentState) -> dict[str, Any]:
    goal = state.get("semantic_goal")
    if goal is None:
        return {"device_proposals": [], "candidate_plans": []}
    ctx = state["runtime_context"]
    excluded = frozenset(goal.excluded_device_ids or [])
    preference = state.get("preference_distribution")
    # Memory Evidence vào planning (spec §35, QC-06): profile_evidence (§24) là prior cho target số.
    profile = state.get("profile_evidence")
    groups = gather_proposals(state["subgoals"], ctx, excluded=excluded, preference=preference, profile=profile)
    flat = [p for g in groups for p in g]
    _apply_ledger_bounds(
        [action for proposal in flat for action in proposal.actions],
        state.get("requirement_ledger"),
    )
    plans = aggregate(groups)
    return {"device_proposals": flat, "candidate_plans": plans}


def _energy_optimizer_node(state: AgentState) -> dict[str, Any]:
    power = state["power_load"]
    # Truyền cả PowerLoad để optimizer áp ràng buộc tải CỨNG ở CRITICAL (§41), không chỉ mode.
    selected, scored = optimize(state.get("candidate_plans", []), power_load=power)
    # Một routine nhiều mục tiêu có thể chứa đúng một bước làm tăng tải (ví dụ bật
    # robot hút bụi) cùng nhiều bước trung tính (chỉnh sáng/âm lượng trên thiết bị đã
    # bật). Ở CRITICAL, không được để bước tăng tải làm cả routine biến thành plan rỗng.
    # Chỉ khi TOÀN BỘ plan đầy đủ đều infeasible, dựng một phương án suy giảm gồm các
    # proposal có công suất đã biết <= 0; safety/validation vẫn chạy nguyên vẹn phía sau.
    if selected is None and power.mode.value == "CRITICAL":
        load_neutral = [
            proposal
            for proposal in state.get("device_proposals", [])
            if not proposal.safety_violation
            and proposal.estimated_power_w is not None
            and proposal.estimated_power_w <= 0
        ]
        if load_neutral:
            fallback_plans = aggregate([[proposal] for proposal in load_neutral])
            for index, plan in enumerate(fallback_plans, start=1):
                plan.plan_id = f"load_neutral_{index}"
            fallback_selected, fallback_scored = optimize(fallback_plans, power_load=power)
            if fallback_selected is not None:
                selected = fallback_selected
                scored = [*scored, *fallback_scored]
    return {"candidate_plans": scored, "selected_plan": selected}


def _validate_node(state: AgentState) -> dict[str, Any]:
    selected = state.get("selected_plan")
    goal = state.get("semantic_goal")
    ctx = state["runtime_context"]
    ledger = state.get("requirement_ledger")
    if selected is None or goal is None:
        return {"validated_plan": [], "validation_errors": []}
    is_inferred = state.get("is_inferred_goal", _is_inferred_goal(goal))
    validated, errors, _dropped = validate_plan(selected.actions, ctx, ledger=ledger, is_inferred_goal=is_inferred)
    return {"validated_plan": validated, "validation_errors": errors}


def _policy_gate_node(state: AgentState) -> dict[str, Any]:
    validated = state.get("validated_plan", [])
    errors = state.get("validation_errors", [])
    # Đồ thị đầy đủ (build_pipeline/run_turn) hiện chỉ chạy trong eval harness/test
    # (không có DB session thật trong AgentState) — đường live production đi qua
    # agent_runner + src/agent/execution.py, nơi ĐÃ gọi resolve_access() trực tiếp
    # (cùng nguồn chân lý per-device với đường API). authorize() ở đây rơi về
    # evaluate() theo vai trò × mức rủi ro khi không có session/user thật, và sẽ
    # tự chuyển sang resolve_access() nếu node này có ngày được cấp session/user.
    auth = authorize(
        validated,
        role=state.get("speaker_role", Role.OWNER.value),
    )
    decision = decide(
        hard_errors=errors, authorization=auth, has_actions=bool(auth.allowed_actions or auth.approval_actions)
    )
    return {
        "authorization": auth,
        "policy_decision": {
            "decision": decision.decision,
            "priority_hit": decision.priority_hit,
            "reasons": decision.reasons,
        },
    }


def _route_after_policy(state: AgentState) -> str:
    decision = (state.get("policy_decision") or {}).get("decision")
    if decision == "PROCEED":
        return "execute"
    if decision == "CONFIRM":
        return "confirm"
    return "reject"


def _confirm_node(state: AgentState) -> dict[str, Any]:
    reasons = (state.get("policy_decision") or {}).get("reasons") or []
    return {
        "final_status": "WAITING_FOR_USER_APPROVAL",
        "reply": "Cần bạn xác nhận trước khi thực hiện. " + (" ".join(reasons)),
    }


def _reject_node(state: AgentState) -> dict[str, Any]:
    reasons = (state.get("policy_decision") or {}).get("reasons") or ["Không thực hiện được yêu cầu."]
    return {"final_status": "rejected", "reply": " ".join(reasons)}


def _make_execute_node(deps: PipelineDeps):
    def _execute(state: AgentState) -> dict[str, Any]:
        auth = state["authorization"]
        actions = auth.allowed_actions
        gateway = deps.make_gateway(state.get("live_device_states"))
        # Trust boundary §50: refresh → re-ground → REVALIDATE → execute (invariant §73.6-7).
        live = refresh_state(gateway, [a.entity_id for a in actions])
        kept, noops = reground(actions, live)
        kept, rejected = revalidate(kept)  # loại action ngoài dải trên state mới, KHÔNG execute
        selected = state.get("selected_plan")
        result = execute_plan(gateway, kept, plan_id=(selected.plan_id if selected else "plan"), noop_actions=noops)
        return {
            "validated_plan": kept,
            "dropped_noops": noops,
            "revalidate_rejected": [a.entity_id for a in rejected],
            "execution_result": result,
            "final_status": result.status,
            "reply": _summarize(result),
        }

    return _execute


def _summarize(result) -> str:
    ok = sum(1 for a in result.actions if a.status == "SUCCESS")
    noop = sum(1 for a in result.actions if a.status == "NO_OP")
    failed = sum(1 for a in result.actions if a.status == "FAILED")
    parts = []
    if ok:
        parts.append(f"đã thực hiện {ok} hành động")
    if noop:
        parts.append(f"{noop} đã đúng trạng thái")
    if failed:
        parts.append(f"{failed} thất bại")
    return "Xong: " + ", ".join(parts) if parts else "Không có hành động nào để thực hiện."


def _make_audit_node(deps: PipelineDeps):
    def _audit(state: AgentState) -> dict[str, Any]:
        goal = state.get("semantic_goal")
        audit = build_audit(
            conversation_id=state.get("conversation_id", ""),
            user=state.get("user_id") or "",
            user_input=state.get("user_message", ""),
            semantic_goal=goal,
            ledger=state.get("requirement_ledger"),
            evidence_trace=state.get("evidence_trace") or [],
            memory_used=[e.event_id for e in state.get("memory_evidence", [])],
            profile_used=state.get("profile_evidence") or [],
            preference_used={k: v.model_dump() for k, v in (state.get("preference_distribution") or {}).items()},
            candidate_plans=state.get("candidate_plans", []),
            selected_plan=state.get("selected_plan"),
            policy_decision=state.get("policy_decision"),
            execution_result=state.get("execution_result"),
            now=state.get("now") or datetime.now(UTC),
        )
        return {"audit": audit}

    return _audit


# ---------------------------------------------------------------------------
# Feedback loop (spec §54, §56) — observe → feedback → {memory_update ∥ rl_update}
# ---------------------------------------------------------------------------
def _observe_node(state: AgentState) -> dict[str, Any]:
    """Quan sát cái ĐÃ xảy ra thật sau execute (spec §56). Không đọc lại device — execute
    đã refresh/reground/ghi nhận; ở đây chỉ cô đọng kết quả cho audit/feedback/memory dùng."""
    result = state.get("execution_result")
    if result is None:
        return {"observation": {"status": "none", "changed": []}}
    changed = [{"device_id": a.device_id, "requested": a.requested, "status": a.status} for a in result.actions]
    return {"observation": {"status": result.status, "changed": changed}}


def _feedback_node(state: AgentState) -> dict[str, Any]:
    """Diễn giải feedback CÓ CẤU TRÚC của lượt (spec §34, §54).

    KHÔNG đoán feedback từ chuỗi câu (spec §34: LLM không tự sinh reward). Chỉ nhận tín
    hiệu caller truyền qua ``state['feedback']`` (accept/reject/correction/unchanged). Không
    có feedback → signal None (rl_update sẽ no-op; memory vẫn ghi độc lập)."""
    fb = state.get("feedback") or {}
    if not fb:
        return {"feedback_signal": None}
    signal = interpret_feedback(
        accepted=fb.get("accepted"),
        rejected=bool(fb.get("rejected", False)),
        corrected_value=fb.get("corrected_value"),
        dimension=fb.get("dimension"),
        unchanged=bool(fb.get("unchanged", False)),
    )
    return {"feedback_signal": signal}


def _turn_number(state: AgentState) -> int:
    ledger = state.get("requirement_ledger")
    return ledger.last_updated_turn if ledger is not None else 1


def _make_memory_update_node(deps: PipelineDeps):
    """Ghi Turn Store + Event Store (spec §20, §25) — ĐỘC LẬP với RL (spec §54).

    Chỉ ghi event từ hành động ĐÃ THỰC THI thật (evidence), không từ kế hoạch đề xuất
    (event_extractor lọc theo SUCCESS/NO_OP). Đây là write path để retrieve_memory ở lượt
    sau có bằng chứng (FR-06/FR-07)."""

    def _memory_update(state: AgentState) -> dict[str, Any]:
        conv = state.get("conversation_id", "")
        turn = _turn_number(state)
        turn_id = f"{conv}-t{turn}"
        speaker = state.get("user_id") or "user"
        record_turn(
            deps.turn_store, turn_id=turn_id, conversation_id=conv, text=state.get("user_message", ""), speaker=speaker
        )

        result = state.get("execution_result")
        event_id: str | None = None
        feedback_event_id: str | None = None
        # Feedback lượt này (đã diễn giải ở node feedback, §34): quyết định có HỌC giá trị đã
        # execute thành preferred không. Bác/sửa → KHÔNG học (giá trị vừa bị từ chối/thay).
        signal = state.get("feedback_signal")
        signal_kind = signal.kind.value if signal is not None else None
        learn = signal_kind in (None, "explicit_accept", "no_correction")
        if result is not None:
            goal = state.get("semantic_goal")
            uid = state.get("user_id")
            actors = [uid] if uid else []
            event = commit_event(
                deps.event_store,
                event_id=f"{conv}-e{turn}",
                conversation_id=conv,
                actors=actors,
                execution=result,
                summary=(goal.goal_description or goal.raw_utterance) if goal else "",
                source_turn_ids=[turn_id],
                learn_preferences=learn,
            )
            event_id = event.event_id if event is not None else None

            # Event feedback CÓ NGHĨA (§21, §10/§11): plan accepted/rejected/correction lưu
            # thành event có cấu trúc — không tan vào raw turn. Chỉ khi có tín hiệu đáng lưu.
            if signal is not None and signal_kind != "no_correction":
                dim = getattr(signal, "dimension", None)
                selected = state.get("selected_plan")
                subject = _subject_for_dimension(selected, dim) if dim else None
                chosen_value = _chosen_action_for(selected, dim) if dim else None
                fb_event = commit_feedback_event(
                    deps.event_store,
                    event_id=f"{conv}-fb{turn}",
                    conversation_id=conv,
                    actors=actors,
                    kind=signal_kind or "feedback",
                    execution=result,
                    dimension=dim,
                    corrected_value=getattr(signal, "corrected_value", None),
                    subject=subject,
                    chosen_value=chosen_value,
                    source_turn_ids=[turn_id],
                )
                feedback_event_id = fb_event.event_id if fb_event is not None else None

        # Consolidation Profile/Semantic Memory (§24, §25): promote (thiết bị, relation) đủ evidence
        # (≥ ngưỡng) từ CÁC event của người dùng thành stable fact. KHÔNG promote từ một lần (§24/§71).
        actor = state.get("user_id")
        promoted = 0
        if (event_id or feedback_event_id) and actor:
            promoted = len(deps.profile_store.consolidate_from_events(deps.event_store.by_actor(actor)))
        return {
            "memory_written": {
                "turn_id": turn_id,
                "event_id": event_id,
                "feedback_event_id": feedback_event_id,
                "profiles_promoted": promoted,
            }
        }

    return _memory_update


def _chosen_action_for(selected, dimension: str) -> int | None:
    """Giá trị số lượt ĐÃ chọn cho một dimension trong selected plan (để RL phạt/thưởng đúng a)."""
    if selected is None:
        return None
    for a in selected.actions:
        if a.capability == dimension:
            v = (a.target or {}).get(dimension)
            if isinstance(v, int | float) and not isinstance(v, bool):
                return int(v)
    return None


def _subject_for_dimension(selected, dimension: str) -> str | None:
    """Thiết bị mang một dimension trong selected plan — để neo event feedback đúng thiết bị."""
    if selected is None:
        return None
    for a in selected.actions:
        if a.capability == dimension:
            return a.device_id
    return None


def _make_rl_update_node(deps: PipelineDeps):
    """Cập nhật Q preference từ feedback (spec §32, §54, §67) — ĐỘC LẬP với memory (spec §54).

    Chỉ chạy khi có feedback_signal + xác định được (dimension, chosen_action, rl_state).
    RL KHÔNG đụng safety/authorization (spec §P6, invariant #4) — chỉ ghi Q-table."""

    def _rl_update(state: AgentState) -> dict[str, Any]:
        signal = state.get("feedback_signal")
        if signal is None or signal.dimension is None:
            return {"rl_update_result": None}
        rl_state = state.get("rl_state")
        if rl_state is None:
            return {"rl_update_result": None}
        chosen = (state.get("feedback") or {}).get("chosen_action")
        if chosen is None:
            chosen = _chosen_action_for(state.get("selected_plan"), signal.dimension)
        if chosen is None:
            return {"rl_update_result": None}
        result = apply_feedback(
            deps.preference_store, dimension=signal.dimension, state=rl_state, chosen_action=chosen, feedback=signal
        )
        return {"rl_update_result": result}

    return _rl_update


# ---------------------------------------------------------------------------
# Graph assembly (spec §56)
# ---------------------------------------------------------------------------
def _timed_node(name: str, node):  # noqa: ANN001, ANN201
    """Wrap one graph node and accumulate deterministic wall-clock telemetry."""

    def _wrapped(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        result = node(state)
        timings = dict(state.get("node_latency_ms") or {})
        timings[name] = round((time.perf_counter() - started) * 1000, 3)
        return {**result, "node_latency_ms": timings}

    return _wrapped


def _model_diagnostics(model: Any) -> dict[str, Any]:
    calls = list(getattr(model, "calls", ()) or ())
    input_tokens = sum(getattr(call, "input_tokens", 0) or 0 for call in calls)
    cached_tokens = sum(getattr(call, "cached_tokens", 0) or 0 for call in calls)
    return {
        "model_calls": len(calls),
        "structured_calls": int(getattr(model, "structured_calls", 0) or 0),
        "schema_repairs": int(getattr(model, "schema_repairs", 0) or 0),
        "input_tokens": input_tokens,
        "output_tokens": sum(getattr(call, "output_tokens", 0) or 0 for call in calls),
        "reasoning_tokens": sum(getattr(call, "reasoning_tokens", 0) or 0 for call in calls),
        "cached_tokens": cached_tokens,
        "prompt_cache_hit_ratio": round(cached_tokens / input_tokens, 4) if input_tokens else 0.0,
        "cache_write_tokens": sum(getattr(call, "cache_write_tokens", 0) or 0 for call in calls),
        "prompt_bytes": sum(getattr(call, "prompt_bytes", 0) or 0 for call in calls),
        "model_latency_ms": round(sum(getattr(call, "latency_ms", 0.0) or 0.0 for call in calls), 3),
        "provider_errors": [
            getattr(call, "provider_error_type", None) for call in calls if getattr(call, "provider_error_type", None)
        ],
    }


def build_pipeline(deps: PipelineDeps | None = None):
    """Compile LangGraph pipeline 5 tầng. Inject deps để test dùng store/gateway riêng."""
    deps = deps or PipelineDeps()
    g = StateGraph(AgentState)

    g.add_node("collect_runtime_context", _timed_node("collect_runtime_context", _collect_runtime_context))
    g.add_node("understand", _timed_node("understand", _make_understand_node(deps)))
    g.add_node("clarify", _timed_node("clarify", _clarify_node))
    g.add_node("answer", _timed_node("answer", _answer_node))
    g.add_node("update_ledger", _timed_node("update_ledger", _make_update_ledger_node(deps)))
    g.add_node("retrieve_memory", _timed_node("retrieve_memory", _make_retrieve_memory_node(deps)))
    g.add_node("infer_preferences", _timed_node("infer_preferences", _make_infer_preferences_node(deps)))
    g.add_node("manager_plan", _timed_node("manager_plan", _manager_plan_node))
    g.add_node("specialists_aggregate", _timed_node("specialists_aggregate", _specialists_aggregate_node))
    g.add_node("energy_optimizer", _timed_node("energy_optimizer", _energy_optimizer_node))
    g.add_node("deterministic_validate", _timed_node("deterministic_validate", _validate_node))
    g.add_node("policy_gate", _policy_gate_node)
    g.add_node("confirm", _confirm_node)
    g.add_node("reject", _reject_node)
    g.add_node("execute", _make_execute_node(deps))
    g.add_node("observe", _observe_node)
    g.add_node("audit", _make_audit_node(deps))
    g.add_node("feedback", _feedback_node)
    g.add_node("memory_update", _make_memory_update_node(deps))
    g.add_node("rl_update", _make_rl_update_node(deps))

    g.add_edge(START, "collect_runtime_context")
    g.add_edge("collect_runtime_context", "understand")
    g.add_conditional_edges(
        "understand",
        _route_after_sufficiency,
        {"proceed": "update_ledger", "clarify": "clarify", "answer": "answer"},
    )
    g.add_edge("clarify", END)
    g.add_edge("answer", END)
    g.add_edge("update_ledger", "retrieve_memory")
    g.add_edge("retrieve_memory", "infer_preferences")
    g.add_edge("infer_preferences", "manager_plan")
    g.add_edge("manager_plan", "specialists_aggregate")
    g.add_edge("specialists_aggregate", "energy_optimizer")
    g.add_edge("energy_optimizer", "deterministic_validate")
    g.add_edge("deterministic_validate", "policy_gate")
    g.add_conditional_edges(
        "policy_gate", _route_after_policy, {"execute": "execute", "confirm": "confirm", "reject": "reject"}
    )
    # Vòng feedback (§54, §56): execute → observe → audit → feedback → memory ∥ rl → END.
    # memory_update và rl_update độc lập; nối tuần tự (mỗi node chỉ đọc state, không phụ
    # thuộc output của nhau) để tránh fan-in nhưng vẫn giữ tính độc lập của §54.
    g.add_edge("execute", "observe")
    g.add_edge("observe", "audit")
    g.add_edge("audit", "feedback")
    g.add_edge("feedback", "memory_update")
    g.add_edge("memory_update", "rl_update")
    g.add_edge("rl_update", END)
    g.add_edge("confirm", END)
    g.add_edge("reject", END)
    return g.compile()


def build_planning_pipeline(deps: PipelineDeps | None = None):
    """Pipeline CHỈ suy luận + lập kế hoạch, DỪNG sau deterministic_validate (không execute).

    Dùng cho cutover có kiểm soát (Phase 3): tầng service (agent_runner) tự lo authorization,
    HITL (Approval trong DB) và execution qua bus như hiện tại — bridge chỉ thay bộ NÃO
    (understanding → planning → validate) từ graph cũ sang pipeline 5 tầng mới. Nhờ vậy
    không phá cơ chế HITL/DB/execution đã có (giữ 435 test cũ).
    """
    deps = deps or PipelineDeps()
    g = StateGraph(AgentState)

    g.add_node("collect_runtime_context", _timed_node("collect_runtime_context", _collect_runtime_context))
    g.add_node("understand", _timed_node("understand", _make_understand_node(deps)))
    g.add_node("clarify", _timed_node("clarify", _clarify_node))
    g.add_node("answer", _timed_node("answer", _answer_node))
    g.add_node("update_ledger", _timed_node("update_ledger", _make_update_ledger_node(deps)))
    g.add_node("retrieve_memory", _timed_node("retrieve_memory", _make_retrieve_memory_node(deps)))
    g.add_node("infer_preferences", _timed_node("infer_preferences", _make_infer_preferences_node(deps)))
    g.add_node("manager_plan", _timed_node("manager_plan", _manager_plan_node))
    g.add_node("specialists_aggregate", _timed_node("specialists_aggregate", _specialists_aggregate_node))
    g.add_node("energy_optimizer", _timed_node("energy_optimizer", _energy_optimizer_node))
    g.add_node("deterministic_validate", _timed_node("deterministic_validate", _validate_node))

    g.add_edge(START, "collect_runtime_context")
    g.add_edge("collect_runtime_context", "understand")
    g.add_conditional_edges(
        "understand",
        _route_after_sufficiency,
        {"proceed": "update_ledger", "clarify": "clarify", "answer": "answer"},
    )
    g.add_edge("clarify", END)
    g.add_edge("answer", END)
    g.add_edge("update_ledger", "retrieve_memory")
    g.add_edge("retrieve_memory", "infer_preferences")
    g.add_edge("infer_preferences", "manager_plan")
    g.add_edge("manager_plan", "specialists_aggregate")
    g.add_edge("specialists_aggregate", "energy_optimizer")
    g.add_edge("energy_optimizer", "deterministic_validate")
    g.add_edge("deterministic_validate", END)
    return g.compile()


def run_turn(state: AgentState, deps: PipelineDeps | None = None) -> AgentState:
    """Chạy một lượt qua pipeline ĐẦY ĐỦ (gồm execute) — tiện cho test/service offline."""
    pipeline = build_pipeline(deps)
    return pipeline.invoke(state, config={"run_name": "smart-home-agent-turn"})


def run_planning(state: AgentState, deps: PipelineDeps | None = None) -> AgentState:
    """Chạy tới hết validate (KHÔNG execute) — dùng cho bridge cutover Phase 3."""
    deps = deps or PipelineDeps()
    started = time.perf_counter()
    compile_started = time.perf_counter()
    pipeline = build_planning_pipeline(deps)
    compile_ms = (time.perf_counter() - compile_started) * 1000
    out = pipeline.invoke(state, config={"run_name": "smart-home-agent-planning"})
    total_ms = (time.perf_counter() - started) * 1000
    diagnostics = {
        "pipeline_total_ms": round(total_ms, 3),
        "graph_compile_ms": round(compile_ms, 3),
        "node_latency_ms": out.get("node_latency_ms") or {},
        **_model_diagnostics(deps.model_client),
        "semantic_cache": deps.semantic_cache.stats(),
    }
    out["diagnostics"] = diagnostics
    # Request-scoped production clients expose this to the API bridge without
    # coupling the public ReasoningResult schema to LangGraph internals.
    try:
        setattr(deps.model_client, "last_pipeline_diagnostics", diagnostics)
    except (AttributeError, TypeError):
        pass
    logger.info("agent_pipeline_metrics %s", diagnostics)
    return out
