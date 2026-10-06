---
paths:
  - "src/agent/**/*.py"
---

# Agent Architecture Rules

- Respect the 5-layer separation: Perception/Understanding/Ledger -> Memory/Preference -> Planning/Specialists -> Harness -> Feedback/Knowledge.
- Understanding never executes.
- Specialists propose actions; they do not bypass the harness.
- LLM output is untrusted proposal data until deterministic validation accepts it.
- Preserve the single dispatch trust boundary: `refresh -> reground -> revalidate -> execute`.
- Memory cannot override fresher runtime/device state.
- RL/preferences cannot weaken authorization, policy, validation, or safety.
