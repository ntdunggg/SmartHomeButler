---
paths:
  - "src/agent/understanding/**/*.py"
  - "src/agent/cognitive/**/*.py"
  - "src/agent/memory/**/*.py"
  - "src/agent/**/turn_intent.py"
---

# Grounding and Multi-turn Rules

- Registry/alias validity must be checked before a logic-bug conclusion.
- A continuation must inherit only explicit, relevant, non-stale evidence from prior turns.
- Topic switch must invalidate stale target context when appropriate.
- Corrections and negations must update/remove prior intent rather than append contradictory state.
- Cross-cutting semantics such as increase/decrease direction must have one source of truth.
- Never special-case eval IDs, exact benchmark sentences, or fixed rooms/devices to make a test pass.
