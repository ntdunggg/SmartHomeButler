---
paths:
  - "tests/**/*.py"
  - "src/evaluation/**/*.py"
  - "**/*eval*.py"
---

# Testing and Evaluation Rules

- Tests prove behavior; they are not edited merely to match a faulty implementation.
- Preserve blind/holdout integrity: do not copy unseen test utterances into prompts, rules, or production code.
- For grounding changes, verify target room/device, not only decision enum/type.
- Separate deterministic and live-model evidence.
- Record before/after metrics for live-model behavior claims.
- Prefer semantic families of tests and paraphrases over one exact example.
