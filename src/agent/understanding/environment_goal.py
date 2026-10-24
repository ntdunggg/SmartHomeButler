"""Deterministic repair for model-authored environmental goals.

The language model proposes that the user wants an environmental change.  This
module turns that proposal into a capability-level outcome using fresh sensor
evidence.  It never selects a device or emits a command: grounding remains the
manager/specialists' job and execution remains behind the deterministic harness.

This is deliberately sensor/capability based rather than an utterance-to-scene
table.  A paraphrase can take the same path as long as the model identifies an
environmental concern and the runtime supplies corroborating live evidence.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.core.interfaces import DeviceSelector
from src.nlu.normalizer import NormalizedUtterance
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import DesiredOutcome, RuntimeContext, SemanticGoal, SensorReading

_EXTERNAL_SCOPE = ("ngoài trời", "ngoài đường", "thời tiết", "dự báo")
_ENVIRONMENT_CAPABILITIES = frozenset(
    {"temperature", "brightness", "fan_speed", "preset_mode", "hvac_mode"}
)


def _fresh(readings: Iterable[SensorReading], sensor_type: str, room: str | None) -> SensorReading | None:
    candidates = [
        reading
        for reading in readings
        if reading.sensor_type == sensor_type
        and reading.source == "live"
        and not reading.is_stale
        and (reading.confidence is None or reading.confidence >= 0.5)
    ]
    if not candidates:
        return None
    if room:
        scoped = [reading for reading in candidates if reading.room == room]
        if scoped:
            return scoped[0]
    global_readings = [reading for reading in candidates if not reading.room]
    return (global_readings or candidates)[0]


def _model_capabilities(goal: SemanticGoal) -> set[str]:
    """Capability hints from structured fields only, never from raw phrases."""
    capabilities: set[str] = set()
    for outcome in goal.desired_outcomes:
        capabilities.update(str(key) for key in outcome.relative_change)
        capabilities.update(str(key) for key in outcome.target_state)
        domain = (outcome.selector.domain or "").lower()
        labels = " ".join(outcome.selector.labels).lower()
        structured = f"{domain} {labels}"
        if "light" in structured or "bright" in structured:
            capabilities.add("brightness")
        if "temperature" in structured or "climate" in structured:
            capabilities.add("temperature")
        if "air" in structured or "purif" in structured:
            capabilities.add("preset_mode")
    return capabilities & _ENVIRONMENT_CAPABILITIES


def _named_sensor_types(nu: NormalizedUtterance) -> set[str]:
    # Reuse the shared sensor vocabulary used by state queries. It classifies a
    # physical dimension; it does not map an utterance to a device or scene.
    from src.agent.state_query import matched_sensor_types

    return set(matched_sensor_types(nu))


def _canonical_outcome(
    *, sensor_type: str, reading: SensorReading, room: str,
) -> DesiredOutcome | None:
    value = float(reading.value)
    selector = DeviceSelector(area=room)
    evidence = f"live:{reading.slug}={value:g}{reading.unit}"

    if sensor_type == "temperature":
        if value >= 28:
            return DesiredOutcome(
                selector=selector,
                perceived_state="hot",
                relative_change={"temperature": "decrease"},
                rationale=evidence,
            )
        if value <= 22:
            return DesiredOutcome(
                selector=selector,
                perceived_state="cold",
                relative_change={"temperature": "increase"},
                rationale=evidence,
            )
        return None

    if sensor_type == "pm25" and value >= 55:
        return DesiredOutcome(
            selector=selector,
            perceived_state="poor_air_quality",
            rationale=evidence,
        )

    if sensor_type == "humidity" and value >= 70:
        return DesiredOutcome(
            selector=selector,
            perceived_state="humid",
            target_state={"hvac_mode": "dry"},
            rationale=evidence,
        )

    if sensor_type == "sunlight":
        if value >= 70:
            return DesiredOutcome(
                selector=selector,
                perceived_state="too_bright",
                relative_change={"brightness": "decrease"},
                rationale=evidence,
            )
        if value <= 20:
            return DesiredOutcome(
                selector=selector,
                perceived_state="too_dark",
                relative_change={"brightness": "increase"},
                rationale=evidence,
            )
    return None


def repair_environment_goal(
    goal: SemanticGoal,
    *,
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
) -> SemanticGoal:
    """Canonicalize a corroborated environmental proposal, otherwise return it unchanged."""
    # Activity/routine goals intentionally coordinate several independent domains.
    # Sensor repair is only for a pure environmental complaint; applying it to an
    # activity would replace the complete plan with one currently salient sensor.
    if (
        not goal.desired_outcomes
        or goal.utterance_type == UtteranceType.ROUTINE_INTENT
        or goal.is_cancellation
        or nu.has_negation
    ):
        return goal
    normalized = nu.normalized.lower()
    if any(marker in normalized for marker in _EXTERNAL_SCOPE):
        return goal

    room = (
        nu.matched_rooms[0]
        if nu.matched_rooms
        else (ctx.speaker_location or ctx.focus_room)
    )
    if not room or room not in ctx.rooms:
        return goal

    sensor_types = _named_sensor_types(nu)
    capabilities = _model_capabilities(goal)
    if "brightness" in capabilities:
        sensor_types.add("sunlight")
    if "temperature" in capabilities:
        sensor_types.add("temperature")
    if capabilities & {"fan_speed", "preset_mode"}:
        sensor_types.add("pm25")
    if "hvac_mode" in capabilities:
        sensor_types.add("humidity")

    # One utterance should be repaired from one most-relevant observed dimension.
    # Named dimensions win; stable order only breaks ties in multi-sensor requests.
    for sensor_type in ("pm25", "humidity", "temperature", "sunlight"):
        if sensor_type not in sensor_types:
            continue
        reading = _fresh(ctx.sensors, sensor_type, room)
        if reading is None:
            continue
        outcome = _canonical_outcome(sensor_type=sensor_type, reading=reading, room=room)
        if outcome is None:
            continue
        return goal.model_copy(
            update={
                "utterance_type": UtteranceType.ENVIRONMENT_REQUEST,
                "goal_description": goal.goal_description or nu.raw,
                "desired_outcomes": [outcome],
                "target_area": room,
                "target_device_ids": [],
                "action_hint": None,
                "polarity": "affirmative",
                "excluded_device_ids": [],
                "target_devices_deterministic": False,
                "user_defined_routine": False,
                "routine_constraint_only": False,
                "routine_source_event_id": None,
            }
        )
    return goal
