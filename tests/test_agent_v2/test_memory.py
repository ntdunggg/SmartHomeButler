"""Event Memory EMem/HiGMem (spec §19-26) — extraction, recall, profile consolidation."""

from __future__ import annotations

import json

from src.agent.memory.event_extractor import extract_event
from src.agent.memory.event_store import EventStore
from src.agent.memory.hierarchical_retriever import retrieve_memory
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, _fetch_memory_evidence
from src.agent.schemas import ExecutedAction, ExecutionResult, TurnRecord


def _exec(brightness=70, temp=24):
    return ExecutionResult(
        plan_id="p",
        status="SUCCESS",
        actions=[
            ExecutedAction(device_id="den_chum_phong_khach", requested={"brightness": brightness}, status="SUCCESS"),
            ExecutedAction(device_id="dieu_hoa_phong_khach", requested={"temperature": temp}, status="SUCCESS"),
        ],
    )


def test_extract_event_derives_facts_and_location():
    ev = extract_event(event_id="e1", conversation_id="c1", actors=["user_A"], execution=_exec(), summary="đón khách")
    assert ev is not None
    relations = {(f.subject, f.relation, f.value) for f in ev.facts}
    assert ("den_chum_phong_khach", "preferred_brightness", 70) in relations
    assert "Phòng khách" in ev.location


def test_event_recall_by_query_and_room():
    store = EventStore()
    store.add(extract_event(event_id="e1", conversation_id="c1", actors=["user_A"], execution=_exec(), summary="đón khách phòng khách"))
    mem = retrieve_memory(
        event_store=store, turn_store=TurnStore(), query_text="chuẩn bị đón khách", room="Phòng khách", actor="user_A"
    )
    assert len(mem.events) == 1
    assert mem.trace  # có dấu vết vì sao nhớ


def test_irrelevant_event_not_recalled():
    store = EventStore()
    store.add(extract_event(event_id="e1", conversation_id="c1", actors=["user_B"], execution=_exec(), summary="ngủ trưa phòng ngủ con"))
    mem = retrieve_memory(
        event_store=store, turn_store=TurnStore(), query_text="mở khoá cửa gara", room="Phòng bếp", actor="user_A"
    )
    assert mem.events == []


def test_memory_not_retrieved_cross_user():
    """QC-06 (§P5 privacy): cùng phòng/truy vấn nhưng khác chủ → KHÔNG trả event của người khác."""
    store = EventStore()
    store.add(extract_event(event_id="e1", conversation_id="c1", actors=["user_A"], execution=_exec(), summary="đón khách phòng khách"))
    # user_B hỏi cùng ngữ cảnh → không được thấy event của user_A.
    mem_b = retrieve_memory(event_store=store, turn_store=TurnStore(), query_text="đón khách", room="Phòng khách", actor="user_B")
    assert mem_b.events == []
    # Chính chủ vẫn thấy.
    mem_a = retrieve_memory(event_store=store, turn_store=TurnStore(), query_text="đón khách", room="Phòng khách", actor="user_A")
    assert len(mem_a.events) == 1


def test_profile_needs_min_evidence():
    ps = ProfileStore()
    evs = [
        extract_event(event_id=f"e{i}", conversation_id="c1", actors=["user_A"], execution=_exec(brightness=70))
        for i in range(3)
    ]
    assert ps.consolidate("user_A", "preferred_brightness", evs[:2]) is None  # <3 → chưa promote
    pf = ps.consolidate("user_A", "preferred_brightness", evs)
    assert pf is not None and pf.value["value"] == 70


def test_consolidate_from_events_promotes_per_device():
    """Tự khám phá + promote theo ĐÚNG (thiết bị, relation) — không trộn đèn với điều hoà (§24)."""
    ps = ProfileStore()
    evs = [
        extract_event(event_id=f"e{i}", conversation_id="c1", actors=["user_A"], execution=_exec(brightness=40, temp=25))
        for i in range(3)
    ]
    promoted = ps.consolidate_from_events(evs)
    facts = {(p.subject, p.fact): p.value["value"] for p in promoted}
    assert facts[("den_chum_phong_khach", "preferred_brightness")] == 40
    assert facts[("dieu_hoa_phong_khach", "preferred_temperature")] == 25


def test_consolidate_from_events_skips_under_threshold():
    """Chưa đủ evidence (<3) → KHÔNG promote (§24/§71 không bịa stable fact từ một-hai lần)."""
    ps = ProfileStore()
    evs = [
        extract_event(event_id=f"e{i}", conversation_id="c1", actors=["user_A"], execution=_exec())
        for i in range(2)
    ]
    assert ps.consolidate_from_events(evs) == []
    assert ps.for_subject("den_chum_phong_khach") == []


def test_profile_relevant_filters_by_subject():
    ps = ProfileStore()
    evs = [extract_event(event_id=f"e{i}", conversation_id="c1", actors=["user_A"], execution=_exec()) for i in range(3)]
    ps.consolidate_from_events(evs)
    subjects = {p.subject for p in ps.relevant({"den_chum_phong_khach"})}
    assert subjects == {"den_chum_phong_khach"}  # không lẫn dieu_hoa dù cùng được promote


def test_higmem_expands_turns_for_factless_event():
    ts = TurnStore()
    ts.add(TurnRecord(turn_id="t1", conversation_id="c1", text="đón khách, chỉnh phòng khách cho ấm cúng"))
    store = EventStore()
    # Event có summary mạnh nhưng KHÔNG fact → cần expand turn.
    ev = extract_event(
        event_id="e1",
        conversation_id="c1",
        actors=["user_A"],
        execution=ExecutionResult(actions=[]),
        summary="đón khách phòng khách",
        location=["Phòng khách"],
        source_turn_ids=["t1"],
    )
    store.add(ev)
    mem = retrieve_memory(
        event_store=store, turn_store=ts, query_text="đón khách phòng khách", room="Phòng khách", expand_threshold=0.2
    )
    assert mem.expanded_turns and mem.expanded_turns[0].turn_id == "t1"


def test_higmem_caps_and_deduplicates_expanded_turns():
    ts = TurnStore()
    for i in range(10):
        ts.add(TurnRecord(turn_id=f"t{i}", conversation_id="c1", text=f"chi tiết {i}"))
    store = EventStore()
    for event_id, source_ids in (("e1", [f"t{i}" for i in range(8)]), ("e2", ["t0", "t8", "t9"])):
        store.add(
            extract_event(
                event_id=event_id,
                conversation_id="c1",
                actors=["user_A"],
                execution=ExecutionResult(actions=[]),
                summary="đón khách phòng khách",
                location=["Phòng khách"],
                source_turn_ids=source_ids,
            )
        )

    mem = retrieve_memory(
        event_store=store,
        turn_store=ts,
        query_text="đón khách phòng khách",
        room="Phòng khách",
        actor="user_A",
        expand_threshold=0.2,
        max_expanded_turns=4,
    )

    assert [turn.turn_id for turn in mem.expanded_turns] == ["t0", "t1", "t2", "t3"]
    assert "turn_expansion_budget=4" in mem.trace


def test_memory_prompt_payload_is_bounded_and_preserves_provenance():
    ts = TurnStore()
    ts.add(TurnRecord(turn_id="t1", conversation_id="c1", speaker="user", text="chi tiết cần giữ"))
    store = EventStore()
    event = extract_event(
        event_id="e1",
        conversation_id="c1",
        actors=["user_A"],
        execution=ExecutionResult(actions=[]),
        summary="đón khách phòng khách",
        location=["Phòng khách"],
        source_turn_ids=["t1"],
    )
    store.add(event)
    mem = retrieve_memory(
        event_store=store,
        turn_store=ts,
        query_text="đón khách phòng khách",
        room="Phòng khách",
        actor="user_A",
        expand_threshold=0.2,
    )

    payload = mem.to_prompt_payload(max_chars=2_000)

    assert payload["events"][0]["event_id"] == "e1"
    assert payload["events"][0]["actors"] == ["user_A"]
    assert payload["events"][0]["source_turn_ids"] == ["t1"]
    assert payload["expanded_turns"][0]["turn_id"] == "t1"
    assert payload["expanded_turns"][0]["conversation_id"] == "c1"
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 2_000


def test_early_memory_context_keeps_selected_turn_evidence():
    ts = TurnStore()
    ts.add(TurnRecord(turn_id="t1", conversation_id="c1", text="đón khách, để ánh sáng ấm"))
    store = EventStore()
    store.add(
        extract_event(
            event_id="e1",
            conversation_id="c1",
            actors=["user_A"],
            execution=ExecutionResult(actions=[]),
            summary="đón khách phòng khách",
            location=["Phòng khách"],
            source_turn_ids=["t1"],
        )
    )
    deps = PipelineDeps(event_store=store, turn_store=ts)

    payload = _fetch_memory_evidence(
        deps,
        query_text="đón khách phòng khách",
        room="Phòng khách",
        actor="user_A",
        now=None,
    )

    assert payload is not None
    assert payload["events"][0]["event_id"] == "e1"
    assert payload["expanded_turns"][0]["turn_id"] == "t1"
