"""User-defined routine memory built on EMem/HiGMem event -> turn evidence.

This module stores an explicit conditional instruction (``when X, do Y``) as a
free-form ``user_instruction`` event.  The event summary is the semantic anchor;
its facts contain normalized device/capability outcomes and its ``source_turn_ids``
point back to raw evidence.  Trigger labels are user text, not a closed scene or
routine taxonomy.

Parsing is deliberately narrow and deterministic: only explicit conditional
instructions, their immediately following continuation/correction, and explicit
deletion are written.  A normal command is never promoted to a routine merely
because it occurred once.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.agent.memory.event_extractor import embed_query
from src.agent.memory.event_store import EventStore
from src.agent.memory.turn_store import TurnStore
from src.agent.perception.context_builder import build_perception
from src.agent.schemas import MemoryEvent, MemoryFact, SemanticGoal, TurnRecord
from src.agent.text import strip_diacritics
from src.core.interfaces import DeviceSelector
from src.domain.enums import Capability
from src.iot.registry import spec_for
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import DesiredOutcome
from src.nlu.understanding import retry_device_grounding, understand

_CONDITIONAL = re.compile(
    r"^\s*(?:tôi\s+thích\s+)?(?:khi|nếu)\s+(?:tôi\s+)?(?:nói\s+)?(.+)$",
    re.IGNORECASE,
)
_ACTION_START = re.compile(
    r"\b(đừng|không|bật|tắt|mở|đóng|khóa|khoá|chốt|đặt|để|tăng|giảm)\b",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(r"^\s*(?:và|nhưng\s+nhớ)\b\s*(.+)$", re.IGNORECASE)
_DELETE = re.compile(r"\bquên\b.+?\bkhi\s+(?:tôi\s+)?(?:nói\s+)?(.+)$", re.IGNORECASE)
_CORRECTION = re.compile(r"^\s*không\b.*?(\d{1,3})", re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"\s+và\s+(?=(?:đừng|không|bật|tắt|mở|đóng|khóa|khoá|chốt|đặt|để)\b)", re.IGNORECASE)
_TRAILING_PARTICLES = re.compile(r"\b(?:thôi|đây|nhé|nha|ạ|à|đi)\b[\s.!?]*$", re.IGNORECASE)
_STOP_TOKENS = frozenset({"toi", "noi", "khi", "thi", "thoi", "day", "nhe", "nha", "nho", "gio"})


@dataclass(frozen=True, slots=True)
class RoutineCapture:
    kind: str  # stored | updated | corrected | deleted
    trigger: str
    event_id: str | None = None


def _clean_trigger(text: str) -> str:
    cleaned = text.strip(" \t,.;:!?")
    cleaned = re.sub(r"\s+thì\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = _TRAILING_PARTICLES.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _tokens(text: str) -> frozenset[str]:
    folded = strip_diacritics(text.lower())
    raw = re.findall(r"[a-z0-9]+", folded)
    return frozenset(t for t in raw if len(t) > 1 and t not in _STOP_TOKENS)


def _split_conditional(text: str) -> tuple[str, str] | None:
    match = _CONDITIONAL.match(text)
    if match is None:
        return None
    rest = match.group(1).strip()
    if "," in rest:
        trigger, body = rest.split(",", 1)
    else:
        then = re.search(r"\s+thì\s+", rest, flags=re.IGNORECASE)
        if then is not None:
            trigger, body = rest[: then.start()], rest[then.end() :]
        else:
            action = _ACTION_START.search(rest)
            if action is None or action.start() == 0:
                return None
            trigger, body = rest[: action.start()], rest[action.start() :]
    trigger = _clean_trigger(trigger)
    body = body.strip(" \t,.;:!?")
    return (trigger, body) if trigger and body else None


def _conversation_fact(conversation_id: str) -> MemoryFact:
    return MemoryFact(subject="routine", relation="conversation_id", value=conversation_id)


def _trigger_fact(trigger: str) -> MemoryFact:
    return MemoryFact(subject="routine", relation="trigger", value=trigger)


def _fact_value(*, capability: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"capability": capability, "action": action, "params": dict(params)}


def _capability_and_params(goal: SemanticGoal, device_id: str, clause: str) -> tuple[str, dict[str, Any]]:
    action = goal.action_hint or ""
    if action == "lock" or action == "unlock":
        return Capability.LOCK.value, {}
    if action in {"open", "close"}:
        return Capability.POSITION.value, {}
    if action in {"turn_on", "turn_off"}:
        return Capability.ON_OFF.value, {}

    params = dict(goal.parameters or {})
    spec = spec_for(device_id)
    caps = set(spec.capabilities) if spec is not None else set()
    folded = strip_diacritics(clause.lower())
    if "temperature" in params or "nhiet" in folded or " do" in f" {folded}":
        cap, key, value = Capability.TEMPERATURE, "temperature", params.get("temperature")
    elif "am luong" in folded or "volume" in folded:
        cap, key, value = Capability.VOLUME, "volume", params.get("percent")
    elif "muc" in folded or "quat" in folded:
        cap, key, value = Capability.FAN_SPEED, "fan_speed", params.get("level")
    elif Capability.BRIGHTNESS in caps:
        cap, key, value = Capability.BRIGHTNESS, "brightness", params.get("percent")
    elif Capability.POSITION in caps:
        cap, key, value = Capability.POSITION, "position", params.get("percent")
    elif Capability.VOLUME in caps:
        cap, key, value = Capability.VOLUME, "volume", params.get("percent")
    else:
        numeric_caps = [c for c in caps if c in {Capability.TEMPERATURE, Capability.FAN_SPEED}]
        cap = numeric_caps[0] if len(numeric_caps) == 1 else Capability.ON_OFF
        key = cap.value
        value = params.get("temperature") if cap == Capability.TEMPERATURE else params.get("level")
    return cap.value, ({key: value} if value is not None else {})


def _action_facts(
    body: str,
    *,
    now: datetime | None = None,
    focus_room: str | None = None,
    anchor_device_ids: tuple[str, ...] = (),
) -> list[MemoryFact]:
    facts: list[MemoryFact] = []
    for clause in _CLAUSE_SPLIT.split(body):
        clause = re.sub(r"^\s*(?:và|nhưng\s+nhớ|nhớ)\s+", "", clause, flags=re.IGNORECASE).strip()
        if not clause:
            continue
        nu, ctx, _ = build_perception(clause, now=now, focus_room=focus_room)
        goal = understand(nu, ctx).goal
        if goal is None or goal.action_hint is None:
            continue
        action_hint = goal.action_hint
        if not goal.target_device_ids and anchor_device_ids:
            anchored = [device_id for device_id in anchor_device_ids if spec_for(device_id) is not None]
            if anchored:
                goal = goal.model_copy(update={"target_device_ids": anchored})
        if not goal.target_device_ids and focus_room:
            retry_targets = retry_device_grounding(
                nu,
                action_hint=action_hint,
                room=focus_room,
            )
            if len(retry_targets) == 1:
                goal = goal.model_copy(update={"target_device_ids": retry_targets})
        if not goal.target_device_ids:
            continue
        relation = "forbidden_action" if goal.negated else "desired_action"
        for device_id in goal.target_device_ids:
            capability, params = _capability_and_params(goal, device_id, clause)
            # A numeric SET powers an ON_OFF device in the execution layer.  Preserve
            # that state transition explicitly in the routine's capability outcomes.
            spec = spec_for(device_id)
            if relation == "desired_action" and action_hint == "set" and spec and Capability.ON_OFF in spec.capabilities:
                facts.append(
                    MemoryFact(
                        subject=device_id,
                        relation="desired_action",
                        value=_fact_value(capability="on_off", action="turn_on", params={}),
                    )
                )
            facts.append(
                MemoryFact(
                    subject=device_id,
                    relation=relation,
                    value=_fact_value(capability=capability, action=action_hint, params=params),
                )
            )
    return facts


def _event_conversation(event: MemoryEvent) -> str | None:
    for fact in event.facts:
        if fact.relation == "conversation_id":
            return str(fact.value)
    return None


def _event_trigger(event: MemoryEvent) -> str | None:
    for fact in event.facts:
        if fact.relation == "trigger" and isinstance(fact.value, str):
            return fact.value
    return None


def _routine_events(store: EventStore, *, actor: str | None = None) -> list[MemoryEvent]:
    return [
        event
        for event in store.all_events()
        if event.event_type == "user_instruction"
        and (not actor or not event.actors or actor in event.actors)
    ]


def _latest_for_conversation(store: EventStore, conversation_id: str, actor: str | None) -> MemoryEvent | None:
    matches = [e for e in _routine_events(store, actor=actor) if _event_conversation(e) == conversation_id]
    return matches[-1] if matches else None


def _turn_id(turn_store: TurnStore, conversation_id: str) -> str:
    return f"{conversation_id}-instruction-t{len(turn_store.for_conversation(conversation_id)) + 1}"


def _write_turn(
    turn_store: TurnStore, *, conversation_id: str, actor: str | None, text: str, now: datetime | None,
) -> str:
    turn_id = _turn_id(turn_store, conversation_id)
    turn_store.add(
        TurnRecord(
            turn_id=turn_id,
            conversation_id=conversation_id,
            speaker=actor or "user",
            text=text,
            timestamp=now,
        )
    )
    return turn_id


def _event_id(conversation_id: str, actor: str | None, trigger: str) -> str:
    digest = hashlib.sha256(f"{conversation_id}|{actor or 'household'}|{trigger}".encode()).hexdigest()[:12]
    return f"routine-{digest}"


def _upsert(
    store: EventStore,
    *,
    conversation_id: str,
    actor: str | None,
    trigger: str,
    action_facts: list[MemoryFact],
    source_turn_id: str,
    now: datetime | None,
) -> MemoryEvent:
    event_id = _event_id(conversation_id, actor, trigger)
    existing = store.get(event_id)
    old_actions = [
        fact for fact in (existing.facts if existing else [])
        if fact.relation in {"desired_action", "forbidden_action"}
    ]
    # Latest explicit instruction for the same device/capability/relation wins.
    merged: dict[tuple[str, str, str], MemoryFact] = {}
    for fact in [*old_actions, *action_facts]:
        value = fact.value if isinstance(fact.value, dict) else {}
        key = (fact.subject, fact.relation, str(value.get("capability", "")))
        merged[key] = fact
    all_actions = list(merged.values())
    rooms = sorted({spec.room for f in all_actions if (spec := spec_for(f.subject)) is not None and spec.room})
    summary_parts = [
        f"{f.relation}:{f.subject}:{(f.value or {}).get('action', '')}:{(f.value or {}).get('params', {})}"
        for f in all_actions
    ]
    summary = f"Khi {trigger}: " + "; ".join(summary_parts)
    source_ids = list(existing.source_turn_ids) if existing else []
    if source_turn_id not in source_ids:
        source_ids.append(source_turn_id)
    _embedded = embed_query(f"{trigger} {summary}")
    event = MemoryEvent(
        event_id=event_id,
        event_type="user_instruction",
        actors=[actor] if actor else [],
        location=rooms,
        start_time=existing.start_time if existing and existing.start_time else now,
        summary=summary,
        facts=[_conversation_fact(conversation_id), _trigger_fact(trigger), *all_actions],
        source_turn_ids=source_ids,
        embedding=list(_embedded.vector),
        embedding_space=_embedded.space,
    )
    store.add(event)
    return event


def capture_routine_turn(
    *,
    event_store: EventStore,
    turn_store: TurnStore,
    conversation_id: str,
    actor: str | None,
    text: str,
    now: datetime | None = None,
    focus_room: str | None = None,
    anchor_device_ids: tuple[str, ...] = (),
) -> RoutineCapture | None:
    """Capture an explicit routine instruction/update/delete, otherwise return None."""
    deletion = _DELETE.search(text)
    if deletion is not None:
        trigger = _clean_trigger(deletion.group(1))
        match = match_routine_event(event_store.all_events(), trigger, actor=actor, conversation_id=conversation_id)
        if match is not None:
            event_store.remove(match.event_id)
        _write_turn(turn_store, conversation_id=conversation_id, actor=actor, text=text, now=now)
        return RoutineCapture("deleted", trigger, match.event_id if match else None)

    conditional = _split_conditional(text)
    if conditional is not None:
        trigger, body = conditional
        actions = _action_facts(
            body,
            now=now,
            focus_room=focus_room,
            anchor_device_ids=anchor_device_ids,
        )
        if not actions:
            return None
        turn_id = _write_turn(turn_store, conversation_id=conversation_id, actor=actor, text=text, now=now)
        event = _upsert(
            event_store,
            conversation_id=conversation_id,
            actor=actor,
            trigger=trigger,
            action_facts=actions,
            source_turn_id=turn_id,
            now=now,
        )
        return RoutineCapture("stored", trigger, event.event_id)

    continuation = _CONTINUATION.match(text)
    if continuation is not None:
        latest = _latest_for_conversation(event_store, conversation_id, actor)
        latest_focus = latest.location[0] if latest is not None and len(latest.location) == 1 else focus_room
        actions = _action_facts(continuation.group(1), now=now, focus_room=latest_focus)
        continuation_trigger = _event_trigger(latest) if latest is not None else None
        if latest is None or not continuation_trigger or not actions:
            return None
        turn_id = _write_turn(turn_store, conversation_id=conversation_id, actor=actor, text=text, now=now)
        event = _upsert(
            event_store,
            conversation_id=conversation_id,
            actor=actor,
            trigger=continuation_trigger,
            action_facts=actions,
            source_turn_id=turn_id,
            now=now,
        )
        return RoutineCapture("updated", continuation_trigger, event.event_id)

    correction = _CORRECTION.match(text)
    if correction is not None:
        latest = _latest_for_conversation(event_store, conversation_id, actor)
        correction_trigger = _event_trigger(latest) if latest is not None else None
        if latest is None or not correction_trigger:
            return None
        new_value = int(correction.group(1))
        revised: list[MemoryFact] = []
        changed = False
        for fact in latest.facts:
            if fact.relation not in {"desired_action", "forbidden_action"} or not isinstance(fact.value, dict):
                continue
            value = dict(fact.value)
            params = dict(value.get("params") or {})
            numeric_keys = [k for k, v in params.items() if isinstance(v, int | float) and not isinstance(v, bool)]
            if numeric_keys and not changed:
                params[numeric_keys[0]] = new_value
                value["params"] = params
                fact = MemoryFact(subject=fact.subject, relation=fact.relation, value=value)
                changed = True
            revised.append(fact)
        if not changed:
            return None
        turn_id = _write_turn(turn_store, conversation_id=conversation_id, actor=actor, text=text, now=now)
        event_store.remove(latest.event_id)
        event = _upsert(
            event_store,
            conversation_id=conversation_id,
            actor=actor,
            trigger=correction_trigger,
            action_facts=revised,
            source_turn_id=turn_id,
            now=now,
        )
        return RoutineCapture("corrected", correction_trigger, event.event_id)
    return None


def match_routine_event(
    events: list[MemoryEvent],
    text: str,
    *,
    actor: str | None = None,
    conversation_id: str | None = None,
) -> MemoryEvent | None:
    """Find one unambiguous trigger match using normalized user-language tokens."""
    query = _tokens(text)
    if not query:
        return None
    scored: list[tuple[float, MemoryEvent]] = []
    for event in events:
        if event.event_type != "user_instruction":
            continue
        if actor and event.actors and actor not in event.actors:
            continue
        if conversation_id and _event_conversation(event) not in {None, conversation_id}:
            # Explicitly taught routines are household/user memory, but an in-progress
            # correction/deletion is scoped by conversation. Trigger use itself may cross
            # sessions, so callers omit conversation_id for normal recall.
            continue
        trigger = _event_trigger(event)
        trigger_tokens = _tokens(trigger or "")
        if not trigger_tokens:
            continue
        overlap = len(query & trigger_tokens)
        if overlap == 0:
            continue
        coverage = overlap / len(trigger_tokens)
        precision = overlap / len(query)
        if coverage == 1.0 or precision == 1.0:
            scored.append((0.7 * coverage + 0.3 * precision, event))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][1].event_id != scored[1][1].event_id:
        return None
    return scored[0][1]


def _target_state(action: str, capability: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "turn_on":
        return {"power": "on"}
    if action == "turn_off":
        return {"power": "off"}
    if action == "lock":
        return {"locked": True}
    if action == "unlock":
        return {"locked": False}
    if action == "open":
        return {"position": 100}
    if action == "close":
        return {"position": 0}
    return dict(params) or {capability: True}


def goal_from_routine(event: MemoryEvent, *, raw_utterance: str, base_goal: SemanticGoal | None) -> SemanticGoal:
    """Rewrite a matched routine event into capability outcomes for normal planning/validation."""
    trigger = _event_trigger(event) or raw_utterance
    desired: list[DesiredOutcome] = []
    targets: list[str] = []
    forbidden: list[str] = []
    relative_actions: dict[str, str] = {}
    desired_fact_count = 0
    for fact in event.facts:
        if fact.relation not in {"desired_action", "forbidden_action"} or not isinstance(fact.value, dict):
            continue
        device_id = fact.subject
        spec = spec_for(device_id)
        if spec is None:
            continue
        if fact.relation == "forbidden_action":
            forbidden.append(device_id)
            continue
        value = fact.value
        desired_fact_count += 1
        fact_action = str(value.get("action", ""))
        if fact_action in {"increase", "decrease"} and not dict(value.get("params") or {}):
            relative_actions[device_id] = fact_action
        desired.append(
            DesiredOutcome(
                selector=DeviceSelector(
                    device_id=device_id,
                    area=spec.room,
                    domain=spec.device_type.value,
                ),
                target_state=_target_state(
                    str(value.get("action", "")),
                    str(value.get("capability", "")),
                    dict(value.get("params") or {}),
                ),
                perceived_state="user_defined_routine",
                cardinality="one",
                rationale=f"Explicit instruction for trigger: {trigger}",
            )
        )
        if device_id not in targets:
            targets.append(device_id)
    rooms = sorted({spec.room for d in targets if (spec := spec_for(d)) is not None and spec.room})
    update: dict[str, Any] = {
        "intent": f"routine:{trigger}",
        "raw_utterance": raw_utterance,
        "goal_description": f"Thực hiện hướng dẫn đã lưu khi {trigger}",
        "utterance_type": UtteranceType.ROUTINE_INTENT,
        "action_hint": None,
        "target_device_ids": targets,
        "target_area": rooms[0] if len(rooms) == 1 else None,
        "desired_outcomes": desired,
        "excluded_device_ids": sorted(set(forbidden)),
        "polarity": "affirmative",
        "references_resolved": True,
        "target_devices_deterministic": True,
        "user_defined_routine": True,
        "routine_constraint_only": not desired,
        "routine_source_event_id": event.event_id,
        "confidence": 1.0,
    }
    # Một routine chỉ gồm các delta đơn trên mỗi thiết bị có thể replay như
    # explicit semantic action. Nó vẫn đi qua build_subgoals/specialist/validator; memory
    # không phát lệnh thiết bị trực tiếp. Routine phức hợp tiếp tục dùng desired_outcomes.
    if relative_actions and len(relative_actions) == desired_fact_count == len(targets):
        update["action_hint"] = next(iter(relative_actions.values()))
        update["target_actions"] = relative_actions
        update["desired_outcomes"] = []
    if base_goal is not None:
        # A constraint-only rule can safely decorate the model-authored routine
        # decomposition. Positive learned outcomes replace it with exact user evidence.
        if not desired and base_goal.desired_outcomes:
            update["desired_outcomes"] = list(base_goal.desired_outcomes)
            update["target_area"] = update["target_area"] or base_goal.target_area or (event.location[0] if len(event.location) == 1 else None)
        return base_goal.model_copy(update=update)
    return SemanticGoal.model_validate(update)
