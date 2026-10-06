# framework-selection

> Consult this reference **before** starting any LangChain/LangGraph/Deep Agents project. Use it to decide which framework layer to use: LangChain, LangGraph, Deep Agents, or a combination. **Must be consulted before using other skills.**

LangChain, LangGraph, and Deep Agents are **layered** — not competing. Each layer builds on the one below:

```
┌─────────────────────────────────────────┐
│              Deep Agents                │  ← Highest layer: out-of-the-box
│   (planning, memory, skills, files)     │
├─────────────────────────────────────────┤
│               LangGraph                 │  ← Orchestration layer: graphs, loops, state
│    (nodes, edges, state, persistence)   │
├─────────────────────────────────────────┤
│               LangChain                 │  ← Foundation layer: models, tools, chains
│      (models, tools, prompts, RAG)      │
└─────────────────────────────────────────┘
```

Choosing a higher layer does not cut off access to lower layers — you can use LangGraph graphs inside Deep Agents, and LangChain base components in both.

---

## Decision Guide

Answer the following questions in order:

| Question | Yes → | No → |
|------|------|------|
| Does the task require breaking down sub-tasks, managing files across long sessions, persistent memory, or on-demand skill loading? | **Deep Agents** | ↓ |
| Does the task require complex control flow — loops, dynamic branching, parallel workers, human-in-the-loop, or custom state? | **LangGraph** | ↓ |
| Is this a single-purpose agent that takes input, executes tools, and returns a result? | **LangChain** (`create_agent`) | ↓ |
| Is this a pure model call, chain, or retrieval pipeline with an agent loop? | **LangChain** (LCEL / chain) | — |

---

## Framework Overview

### LangChain — Use When Task-Focused and Self-Contained

**Best for:**
- Single-purpose agents with a fixed toolset
- RAG pipelines and document Q&A
- Model calls, prompt templates, output parsing
- Quick prototypes with simple agent logic

**Not suitable for:**
- Agents that need multi-step planning
- State that needs to persist across multiple sessions
- Control flow that is conditional or iterative

**Next references to consult:** `langchain-fundamentals`, `langchain-rag`, `langchain-middleware`

---

### LangGraph — Use When You Need Control Flow Ownership

**Best for:**
- Agents with branching logic or loops (e.g., retry until correct, reflection)
- Multi-step workflows where different paths depend on intermediate results
- Human-in-the-loop approval at specific steps
- Parallel fan-out / fan-in (Map-Reduce patterns)
- Persistent state across calls within a session

**Not suitable for:**
- When you want planning, file management, and sub-agent delegation handled automatically (use Deep Agents)
- When the workflow is simple enough on its own

**Next references to consult:** `langgraph-fundamentals`, `langgraph-human-in-the-loop`, `langgraph-persistence`

---

### Deep Agents — Use When Tasks Are Open-Ended and Multi-Dimensional

**Best for:**
- Long-running tasks that need to be broken down into a todo list
- Agents that need to read, write, and manage files across sessions
- Delegating sub-tasks to specialized sub-agents
- On-demand loading of domain-specific skills
- Persistent memory that survives across multiple sessions

**Built-in middleware:**

| Middleware | Capability Provided | Always On? |
|--------|-----------|-----------| 
| `TodoListMiddleware` | `write_todos` tool | ✓ |
| `FilesystemMiddleware` | `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep` | ✓ |
| `SubAgentMiddleware` | `task` tool | ✓ |
| `SkillsMiddleware` | On-demand SKILL.md loading from skills directory | Optional |
| `MemoryMiddleware` | Cross-session long-term memory via Store | Optional |
| `HumanInTheLoopMiddleware` | Pause and request approval before sensitive tool calls | Optional |

**Next references to consult:** `deep-agents-core`, `deep-agents-memory`, `deep-agents-orchestration`

---

## Mixing Frameworks

Because the frameworks are layered, they can be combined in the same project. The most common pattern is using Deep Agents as the top-level orchestrator with LangGraph for specialized sub-agents.

| Scenario | Recommended Pattern |
|------|---------| 
| Main agent needs planning and memory, but a sub-task requires precise graph control | Deep Agents orchestrator → LangGraph sub-agent |
| Specialized pipelines (e.g., RAG, reflection loops) called by a broader agent | LangGraph graph wrapped as a tool or sub-agent |
| High-level coordination + domain-specific low-level graphs | Deep Agents + LangGraph compiled graphs as sub-agents |

---

## Quick Reference Comparison

| | LangChain | LangGraph | Deep Agents |
|---|-----------|-----------|-------------|
| **Control Flow** | Fixed (tool loop) | Custom (graph) | Managed (middleware) |
| **Planning** | ✗ | Manual | ✓ TodoListMiddleware |
| **File Management** | ✗ | Manual | ✓ FilesystemMiddleware |
| **Persistent Memory** | ✗ | Requires Checkpointer | ✓ MemoryMiddleware |
| **Sub-Agent Delegation** | ✗ | Manual | ✓ SubAgentMiddleware |
| **Human-in-the-loop** | ✗ | Manual interrupt | ✓ HumanInTheLoopMiddleware |
| **Custom Graph Edges** | ✗ | ✓ Full control | Limited |
| **Setup Complexity** | Low | Medium | Low |
| **Flexibility** | Medium | High | Medium |
