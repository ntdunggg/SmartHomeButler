# Research References and Implementation Map

This document is the traceability index for the research papers below. Research supports design decisions; the Smart Home specification, deterministic policy, live state, authorization, and execution contracts remain authoritative.

## Status legend

- **Implemented**: observable behavior exists in production code and is covered by deterministic tests.
- **Partial**: the core idea is present, but important limitations remain.
- **Serving-layer guidance**: relevant to model infrastructure, not something the application should emulate without runtime/provider support.

## Reference map

| Source | Main evidence used here | Project mapping | Status |
|---|---|---|---|
| [Vijayvargiya et al., *Ambig-SWE: Interactive Agents to Overcome Underspecificity in Software Engineering* (2026)](https://arxiv.org/abs/2502.13069) | Separate detection of underspecificity, targeted clarification, and use of the answer; interaction can recover substantial performance otherwise lost to missing requirements. | `understand`/sufficiency gate, targeted clarification, no execution while required slots remain unresolved. | **Implemented** |
| [Laban et al., *LLMs Get Lost in Multi-Turn Conversation* (2025)](https://arxiv.org/abs/2505.06120) | Requirements dispersed across turns increase unreliability; early assumptions and premature answers become sticky. | Canonical Requirement Ledger, deterministic recap, explicit continuation/correction/topic-switch tests, stale-context rejection. | **Implemented** |
| [Naik et al., *Agent-Based Detection and Resolution of Incompleteness and Ambiguity in Interactions with Large Language Models* (2025)](https://arxiv.org/abs/2507.03726) | Classify -> resolve -> answer is more robust than answering immediately, with a latency trade-off. | Explicit ambiguity/sufficiency/context-resolution stages before planning. | **Implemented** |
| [Zhou and Han, *A Simple Yet Strong Baseline for Long-Term Conversational Memory of LLM Agents* (2025)](https://arxiv.org/abs/2511.17208) | Event-like, self-contained memory units with source attribution support long-range recall without replaying complete histories. | `TurnRecord` -> `MemoryEvent` -> `ProfileFact`, event/turn links, actor/time/source provenance. | **Implemented**, persistence remains incomplete for all stores |
| [Lumer et al., *Don't Break the Cache: An Evaluation of Prompt Caching for Long-Horizon Agentic Tasks* (2026)](https://arxiv.org/abs/2601.06007) | Stable prefixes and cache-boundary control can reduce cost/latency; dynamic tool results should not destabilize reusable prompt prefixes. | Keep stable instructions/tool schemas separate from bounded dynamic memory evidence; measure cache hit rate in deployment. | **Serving-layer guidance** |
| [Cao, He, and Tan, *HiGMem: A Hierarchical and LLM-Guided Memory System for Long-Term Conversational Agents* (2026)](https://arxiv.org/abs/2604.18349) | Read concise event summaries first and expand only selected raw turns; optimize recall, precision, and context cost together. | `retrieve_memory`: event ranking -> conditional turn expansion -> bounded evidence payload; optional graph evidence remains secondary. | **Implemented** |
| [Heo et al., *Cross-Model KV Cache Transfer in LLM Families* (2026)](https://arxiv.org/abs/2608.03893) | Cross-model cache reuse may reduce re-prefill cost, but depends on matched model-family internals and calibrated mappings. | Candidate serving optimization only if the inference platform exposes compatible KV-cache transfer and quality gates. | **Serving-layer guidance** |
| [Dolinin and Kiourt, *Multi-Agent-Based Smart-Home Energy Management with Adaptive Reasoning* (2026)](https://doi.org/10.3390/app16041896) | Central orchestration, device specialists, contextual inputs, memory/preferences, and adaptive energy reasoning form a practical Smart Home pattern. | Manager -> Specialists -> optimizer, with stronger deterministic validation and execution boundaries than the paper requires. | **Implemented** |
| Das Neves, Zerva, and Gianola, *A Proposal for Handling Query Ambiguity for Process Mining Tasks* (2025) | Domain retrieval and structured alternatives can reduce ambiguity; unresolved multiple interpretations should be clarified. | Registry-backed disambiguation and targeted candidate clarification instead of silent target selection. | **Implemented** |

## Long-conversation improvements derived from the references

The current implementation applies four explicit controls:

1. **Topical gate before contextual ranking.** Semantic relevance must be positive before room, actor, or recency may boost an event. This prevents a recent event from an old topic leaking into a new request merely because both happened in the same room.
2. **Hierarchical expansion with hard limits.** Event summaries are primary evidence. Factless high-relevance events may expand raw turns, but expansion is deduplicated and capped globally.
3. **Bounded prompt payload.** The memory payload has a deterministic character budget. Events are admitted first, selected turns second, and graph-neighbor evidence last.
4. **Provenance survives compression.** Prompt evidence preserves event/turn IDs, actor, timestamp, conversation, and source-turn links instead of flattening memory to unattributed prose.

These controls intentionally avoid replaying complete chat history. The optimization target is successful task completion with inspectable evidence, not the smallest possible prompt per request.

## Known limitations and next evidence required

- `EventStore`, `TurnStore`, `MemoryGraph`, and some profiles are still process-local in the V1 path. Cross-process and restart-safe long-term memory requires repository adapters plus tenant-isolation tests.
- Character budgeting is deterministic and provider-independent, but not an exact tokenizer count. Production telemetry should record input tokens, selected evidence counts, truncation rate, retrieval precision/recall, and re-fetch rate.
- Prompt/KV cache claims must be measured at the serving provider. Do not claim cache savings from application-level prompt structure alone.
- Long-conversation evaluation must score continuation, correction, topic switch, stale-target leakage, artifact/evidence provenance, and context cost separately; aggregate decision accuracy can hide these failures.

## Verification contract

Any future memory/context change should demonstrate:

- no cross-user memory retrieval;
- no unrelated same-room/topic leakage;
- bounded, deterministic prompt evidence;
- provenance retained for every admitted event and raw turn;
- no regression to targeted clarification, authorization, deterministic safety, or `refresh -> reground -> revalidate -> execute`.
