---
name: Acceptance Review
description: Evaluate completed Smart Home work against the Product Owner Task Contract and observable acceptance criteria.
context: fork
agent: product-owner
background: false
disable-model-invocation: true
---

Review acceptance for: **$ARGUMENTS**

Read the Task Contract, implementation summary, and QA evidence. Do not inspect implementation style unless it affects observable product behavior or a stated constraint.

Verify each acceptance criterion individually and mark it PASS, FAIL, or NOT PROVEN.

Pay special attention to:

- correct room/device grounding, not only decision type;
- whether clarification is targeted and necessary;
- correction, negation, topic-switch and stale-context behavior;
- user feedback/preferences not being mistaken for immediate control actions;
- safety behavior preserved;
- claims of unseen generalization backed by blind evidence.

Return ACCEPTED only when all required criteria are PASS. Otherwise return REJECTED or NEEDS CLARIFICATION and name the exact missing criterion/evidence.
