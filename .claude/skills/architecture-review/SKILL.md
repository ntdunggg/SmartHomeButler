---
name: Architecture Review
description: Review a proposed Smart Home change against the 5-layer architecture, trust boundary, ownership rules, and cross-cutting semantic consistency.
disable-model-invocation: true
---

Review this proposed change: **$ARGUMENTS**

Do not implement it during this review.

Check:

- Which of the 5 architecture layers are touched and why.
- Whether Understanding remains non-executing.
- Whether Planning still grounds through Specialists rather than device APIs.
- Whether Memory remains evidence and cannot override fresher state.
- Whether RL/preferences stay inside safety constraints.
- Whether every dispatch still goes through `refresh -> reground -> revalidate -> execute`.
- Whether authorization, policy, audit, failure handling, and executor remain in the proper boundary.
- Whether the change introduces duplicate semantic logic or a second source of truth.
- Whether behavior belongs in deterministic code, LLM prompting, registry data, memory, or evaluation fixtures.
- What new failure modes or regressions the change could create.

Return APPROVE, APPROVE WITH CONDITIONS, or REJECT with concrete reasons.
