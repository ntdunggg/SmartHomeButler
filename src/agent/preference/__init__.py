"""Layer 3 (preference) — Preference RL (spec §28-34).

Contextual-bandit / tabular Q-learning trả lời: *trong context này, lựa chọn nào
thường phù hợp với người dùng?* RL chỉ học PREFERENCE — KHÔNG học/override safety,
authorization, permission, device limit (spec §P6, invariant 4).
"""
