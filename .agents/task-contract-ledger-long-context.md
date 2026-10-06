# PO Task Contract — Ledger semantics and long-context release gate

## Objective

Deliver the four requested increments in order, while preserving existing safety,
grounding, persistence, and dispatch invariants.

## Acceptance criteria

1. Compound-goal preservation
   - A compound `SemanticGoal` persists its canonical room, semantic dimension(s),
     per-target actions, per-target parameters, and desired outcomes into the
     `RequirementLedger` without reconstructing them from raw chat.
   - A later continuation can read those fields after an intervening turn and after
     SQL-store reload.
   - Existing single-action goals remain backward compatible.

2. First-class constraint state
   - Numeric bounds are represented as typed ledger state with dimension, min/max,
     value, and scope; consumers do not need to parse opaque constraint strings.
   - Group exclusions are represented as typed ledger state and can exclude a
     semantic group without enumerating benchmark-specific devices.
   - No-change is represented explicitly in ledger state and cannot be mistaken for
     an actionable device command.
   - Legacy constraint tokens remain readable during migration, and safety behavior
     is not weakened.

3. Long-context evaluation
   - Deterministic eval coverage includes 10-, 20-, and 50-turn conversations;
     references farther than 8 turns; branch return; persistence across process/store
     restart; and concurrent updates to one conversation.
   - Assertions score concrete room/device/capability/action/constraint state, not
     decision type alone.
   - Fixtures use valid registry rooms, aliases, devices, and capabilities.

4. Production-model release gate
   - `live-semantic-200` is run with the configured production model, with live LLM
     enabled and offline/fake mode disabled.
   - The report records model identity, command/configuration, total cases, semantic
     correctness, target correctness, stale-target leakage, and failures.
   - No improvement claim is made unless the live result is compared with a recorded
     baseline under the same model and scorer. If credentials/network/model access
     are unavailable, the result is explicitly BLOCKED rather than inferred from
     deterministic tests.

## Constraints

- Keep Understanding non-executing and Planning grounded through Specialists.
- Keep Memory as evidence only.
- Preserve `refresh -> reground -> revalidate -> execute` for every dispatch.
- Do not special-case eval IDs or exact benchmark sentences.
- Preserve unrelated worktree changes.

