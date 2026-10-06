---
name: Diagnose Grounding
description: Diagnose Smart Home target/room, context-resolution, continuation, correction, negation, or topic-switch failures using evidence before proposing a patch.
context: fork
agent: qa
background: false
disable-model-invocation: true
---

Diagnose: **$ARGUMENTS**

Do not edit production code.

Produce an evidence table for each failing case with:

- utterance and relevant prior turns;
- expected decision and expected target;
- actual decision and actual target;
- registry/alias validity;
- device capability validity;
- ledger/context evidence available before the failing turn;
- first pipeline stage where expected evidence is lost or changed;
- whether the root cause is fixture/registry, stale context, intent classification, semantic resolution, goal authoring, memory retrieval, validation, or another named stage;
- minimal semantic fix hypothesis;
- regression cases that could disprove the hypothesis.

Mandatory order:

1. Reproduce.
2. Registry/alias check.
3. Trace state/evidence across the pipeline.
4. Separate data/fixture problems from logic problems.
5. Only then propose a fix hypothesis.

Reject any hypothesis that depends on special-casing an eval case ID or exact benchmark sentence.
