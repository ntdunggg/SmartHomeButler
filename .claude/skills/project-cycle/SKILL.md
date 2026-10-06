---
name: Smart Home Project Cycle
description: Run the full four-role delivery workflow for a non-trivial Smart Home feature, bug, refactor, or eval-driven improvement.
disable-model-invocation: true
---

Run a full delivery cycle for: **$ARGUMENTS**

You are the Tech Lead and main session. Use Agent Teams, not a pile of uncoordinated edits.

1. Confirm Agent Teams are available. Create one team for this task.
2. Spawn exactly three teammates using the project agent types `product-owner`, `qa`, and `coder`. Do not spawn a second Tech Lead.
3. Create a shared task list with dependencies:
   - PO Task Contract
   - QA reproduction + baseline
   - Tech Lead root-cause review + patch approval
   - Coder implementation
   - QA verification
   - PO acceptance
   - Tech Lead final decision
4. Instruct teammates to communicate findings directly when they affect another role.
5. Do not let the Coder edit before the PO contract and QA baseline are available, unless the task is an emergency safety fix and you explicitly document why.
6. For grounding/context issues, require registry/alias validation before code diagnosis.
7. Preserve all architecture and git-safety rules from `.claude/rules/`.
8. Wait for QA and PO before declaring done.
9. End with one synthesized delivery report, not four disconnected summaries.

If Agent Teams are unavailable, fall back to sequential custom subagents in the same role order and state that the fallback was used.
