"""Layer 5 — Agent Harness, Safety & Execution (spec §44-53).

Trust boundary giữa reasoning system và physical environment. Deterministic
Validator → Policy Gate → Authorization → Refresh → Re-ground → Revalidate →
Execute → Observe → Audit. LLM KHÔNG gọi thẳng device API (invariant 5).
"""
