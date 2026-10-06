"""Ledger bền vững (spec §17) — trạng thái hội thoại sống qua restart/worker khác.

Ledger là bản ghi CANONICAL của mạch hội thoại. Khi nó chỉ ở RAM, lượt trả lời clarify
("phòng bếp") mất mốc `raw_utterance` nên hệ thống hỏi lại vòng hai, và các ràng buộc
durable (§73 #8/#9) biến mất. Các test dưới đây mô phỏng restart bằng cách dựng
`PipelineDeps` HOÀN TOÀN MỚI giữa hai lượt của cùng một `conversation_id`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from src.agent.cognitive.ledger import LedgerStore
from src.agent.cognitive.ledger_repository import SqlLedgerStore
from src.agent.cognitive.ledger_updater import update_ledger
from src.agent.pipeline import PipelineDeps
from src.agent.schemas import RequirementLedger, SemanticGoal
from src.domain.models import ConversationLedger
from src.nlu.ontology import UtteranceType
from src.services.pipeline_bridge import _bind_session_deps, reason

_NOW = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


def _turn(message: str, conversation_id: str, session, deps: PipelineDeps):
    return reason(
        message=message,
        conversation_id=conversation_id,
        role="owner",
        household_id=1,
        session=session,
        now=_NOW,
        deps=deps,
    )


def test_slot_fill_survives_a_process_restart(seeded):
    """Lượt 1 hỏi phòng, tiến trình restart, lượt 2 trả lời phòng → vẫn ra kế hoạch."""
    conv = "persist-slotfill"
    first = _turn("Bật đèn", conv, seeded, PipelineDeps())
    assert first.outcome == "clarification"

    # Restart: deps mới hoàn toàn, không còn cache trong tiến trình.
    second = _turn("phòng bếp", conv, seeded, PipelineDeps())

    assert second.outcome == "candidate_plan"
    device_ids = {a.device_id for a in second.candidate_plan.actions}
    assert device_ids == {"den_bep", "den_ban_an"}


def test_durable_constraints_survive_a_process_restart(seeded):
    """Ràng buộc người dùng nêu rõ (§73 #8/#9) không được biến mất khi tiến trình restart."""
    conv = "persist-constraint"
    _turn("Cho mát hơn nhưng đừng bật điều hoà phòng khách", conv, seeded, PipelineDeps())

    stored = SqlLedgerStore(household_id=1).load(conv)
    assert stored.constraints, "ràng buộc phải được ghi xuống DB"

    after_restart = _turn("phòng khách", conv, seeded, PipelineDeps())
    assert after_restart.explicit_constraints == stored.constraints


def test_ledger_row_is_written_for_the_conversation(seeded):
    conv = "persist-row"
    _turn("Bật điều hoà phòng ngủ con", conv, seeded, PipelineDeps())

    row = seeded.execute(
        select(ConversationLedger).where(ConversationLedger.conversation_id == conv)
    ).scalar_one()
    assert row.household_id == 1
    assert row.payload["confirmed_facts"]["devices"] == ["dieu_hoa_phong_con"]


def test_sql_store_round_trips_and_resets(seeded):
    store = SqlLedgerStore(household_id=1)
    ledger = RequirementLedger(
        conversation_id="round-trip",
        constraints=["avoid:dieu_hoa_phong_khach"],
        rejected_assumptions=["use:dieu_hoa_phong_khach"],
        last_updated_turn=3,
    )
    store.save(ledger)

    # Store MỚI = không cache: chứng minh dữ liệu thật sự nằm ở DB.
    reloaded = SqlLedgerStore(household_id=1).load("round-trip")
    assert reloaded.constraints == ["avoid:dieu_hoa_phong_khach"]
    assert reloaded.rejected_assumptions == ["use:dieu_hoa_phong_khach"]
    assert reloaded.last_updated_turn == 3

    store.reset("round-trip")
    assert SqlLedgerStore(household_id=1).load("round-trip") == RequirementLedger(
        conversation_id="round-trip"
    )


def test_compound_and_typed_state_survive_store_restart(seeded):
    store = SqlLedgerStore(household_id=1)
    goal = SemanticGoal(
        intent="compound",
        raw_utterance="Mở rèm bếp rồi chỉnh đèn bàn ăn xuống 50%.",
        utterance_type=UtteranceType.DEVICE_COMMAND,
        confidence=1.0,
        target_area="Phòng bếp",
        target_device_ids=["rem_bep", "den_ban_an"],
        action_hint="open",
        target_actions={"rem_bep": "open", "den_ban_an": "set"},
        target_parameters={"den_ban_an": {"percent": 50}},
        explicit_constraints=["bound:max:brightness:70:den_ban_an"],
    )
    store.save(
        update_ledger(
            store.load("restart-typed"),
            goal,
            turn=1,
            conversation_id="restart-typed",
        )
    )

    reloaded = SqlLedgerStore(household_id=1).load("restart-typed")
    assert reloaded.current_goal["target_area"] == "Phòng bếp"
    assert reloaded.current_goal["dimensions"] == ["brightness", "position"]
    assert reloaded.current_goal["target_actions"]["den_ban_an"] == "set"
    assert reloaded.bounds[-1].dimension == "brightness"
    assert reloaded.bounds[-1].value == 70


def test_concurrent_workers_merge_same_turn_durable_deltas(seeded):
    conversation_id = "concurrent-ledger"
    creator = SqlLedgerStore(household_id=1)
    creator.save(RequirementLedger(conversation_id=conversation_id, last_updated_turn=1))

    worker_a = SqlLedgerStore(household_id=1)
    worker_b = SqlLedgerStore(household_id=1)
    stale_a = worker_a.load(conversation_id)
    stale_b = worker_b.load(conversation_id)

    bound_delta = update_ledger(
        stale_a,
        SemanticGoal(
            intent="bound",
            raw_utterance="Không quá 70%.",
            utterance_type=UtteranceType.DEVICE_COMMAND,
            confidence=1.0,
            target_area="Phòng bếp",
            target_device_ids=["den_ban_an"],
            explicit_constraints=["bound:max:brightness:70:den_ban_an"],
        ),
        turn=2,
    )
    exclusion_delta = update_ledger(
        stale_b,
        SemanticGoal(
            intent="exclude",
            raw_utterance="Trừ đèn bếp ra.",
            utterance_type=UtteranceType.DEVICE_COMMAND,
            confidence=1.0,
            target_area="Phòng bếp",
            target_device_ids=["den_ban_an"],
            excluded_device_ids=["den_bep"],
            action_hint="turn_on",
        ),
        turn=2,
    )

    worker_a.save(bound_delta)
    worker_b.save(exclusion_delta)

    merged = SqlLedgerStore(household_id=1).load(conversation_id)
    assert [(bound.kind, bound.dimension, bound.value) for bound in merged.bounds] == [
        ("max", "brightness", 70.0)
    ]
    assert merged.group_exclusions[-1].excluded_device_ids == ["den_bep"]
    assert "bound:max:brightness:70:den_ban_an" in merged.constraints
    assert "avoid:den_bep" in merged.constraints


def test_unknown_conversation_loads_an_empty_ledger_instead_of_failing(seeded):
    assert SqlLedgerStore().load("never-seen") == RequirementLedger(conversation_id="never-seen")


def test_offline_runs_keep_the_in_memory_store(seeded):
    """Không có session (eval offline / unit test) → KHÔNG ép phụ thuộc DB."""
    deps = PipelineDeps()
    assert isinstance(_bind_session_deps(deps, session=None).ledger_store, LedgerStore)
    bound = _bind_session_deps(deps, session=seeded, household_id=1)
    assert isinstance(bound.ledger_store, SqlLedgerStore)
