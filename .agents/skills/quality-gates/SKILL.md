---
name: Quality Gates
description: Independently run the repository's relevant test, lint, and evaluation gates and compare pre/post evidence for Smart Home behavior changes.
context: fork
agent: qa
background: false
disable-model-invocation: true
---

Verify scope: **$ARGUMENTS**

Do not edit production code.

1. Discover the real quality commands from repository configuration, CI, scripts, and docs. Do not invent command names.
2. Run targeted tests for the changed semantic behavior.
3. Run the full configured unit test suite.
4. Run configured lint/static/type checks that apply.
5. If the change affects LLM understanding, grounding, memory, planning, or multi-turn behavior, run the relevant live eval group against a recorded pre-change baseline.
6. If a broad quality claim is made, run the full configured agent evaluation.
7. Compare target correctness, not only decision-type correctness.
8. Check for regressions in ambiguity detection, targeted clarification, safety gates, approval/resume, negation/correction, and stale-context handling as applicable.
9. Clearly separate deterministic results from live-model results.

Return PASS, PASS WITH RISK, FAIL, or BLOCKED with commands and evidence.
