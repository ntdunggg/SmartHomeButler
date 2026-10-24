"""EMem-G relational memory (spec §23) + HiGMem recency/graph expansion (§22, §26)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.agent.memory.event_extractor import embed_query
from src.agent.memory.event_retriever import score_events
from src.agent.memory.event_store import EventStore
from src.agent.memory.graph_memory import Entity, MemoryGraph, entities_of
from src.agent.memory.hierarchical_retriever import retrieve_memory
from src.agent.memory.turn_store import TurnStore
from src.agent.schemas import MemoryEvent, MemoryFact


def _event(eid, *, room, actor="user_A", device="den_chum_phong_khach", relation="preferred_brightness", value=70, when=None):
    summary = f"{room} {relation}"
    embedded = embed_query(summary)
    return MemoryEvent(
        event_id=eid,
        actors=[actor],
        location=[room],
        start_time=when,
        summary=summary,
        facts=[MemoryFact(subject=device, relation=relation, value=value)],
        embedding=list(embedded.vector),
        embedding_space=embedded.space,
    )


def test_entities_extracted_from_event():
    ents = entities_of(_event("e1", room="Phòng khách"))
    assert Entity("room", "Phòng khách") in ents
    assert Entity("actor", "user_A") in ents
    assert Entity("device", "den_chum_phong_khach") in ents
    assert Entity("preference", "preferred_brightness") in ents


def test_graph_expands_via_shared_room():
    g = MemoryGraph()
    a = _event("a", room="Phòng khách", device="den_chum_phong_khach")
    b = _event("b", room="Phòng khách", device="dieu_hoa_phong_khach", relation="preferred_temperature", value=24)
    c = _event("c", room="Phòng bếp", device="den_bep")
    for ev in (a, b, c):
        g.add_event(ev)
    # Seed = a → lan sang b (cùng Phòng khách), KHÔNG tới c (khác phòng, khác thiết bị).
    expanded = g.expand(["a"], hops=1)
    assert "b" in expanded and "c" not in expanded


def test_recency_boost_orders_newer_first():
    now = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)
    old = _event("old", room="Phòng khách", when=now - timedelta(days=30))
    new = _event("new", room="Phòng khách", when=now - timedelta(hours=1))
    scored = score_events([old, new], query_text="Phòng khách preferred_brightness", room="Phòng khách", now=now)
    assert scored[0].event.event_id == "new"


def test_hierarchical_retrieval_includes_graph_events():
    store = EventStore()
    graph = MemoryGraph()
    # 'a' khớp trực tiếp qua room. 'b' KHÁC phòng, không khớp query, nhưng dùng CHUNG thiết
    # bị với 'a' → chỉ vào được qua graph expansion (spec §26), không trùng tập chính.
    a = _event("a", room="Phòng khách", device="den_chum_phong_khach")
    b = _event("b", room="Phòng ngủ con", device="den_chum_phong_khach", relation="preferred_temperature", value=24)
    b.summary = "khong lien quan gi"
    unrelated = embed_query(b.summary)
    b.embedding = list(unrelated.vector)
    b.embedding_space = unrelated.space
    for ev in (a, b):
        store.add(ev)
        graph.add_event(ev)
    mem = retrieve_memory(
        event_store=store,
        turn_store=TurnStore(),
        query_text="Phòng khách preferred_brightness",  # khớp a; b chỉ vào qua graph
        room="Phòng khách",
        graph=graph,
    )
    main_ids = {e.event_id for e in mem.events}
    graph_ids = {e.event_id for e in mem.graph_events}
    assert "a" in main_ids
    assert "b" not in main_ids
    assert "b" in graph_ids
