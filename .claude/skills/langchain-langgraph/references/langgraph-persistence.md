# langgraph-persistence

> Consult this file when LangGraph needs state persistence, conversation memory, history browsing, or configuring subgraph Checkpointer scoping. Covers Checkpointers, thread_id, time travel, Store, and subgraph persistence patterns.

## Table of Contents
- [Overview](#overview)
- [Checkpointer Selection](#checkpointer-selection)
- [Checkpointer Setup](#checkpointer-setup)
- [Thread Management](#thread-management)
- [State History and Time Travel](#state-history-and-time-travel)
- [Subgraph Checkpointer Scoping](#subgraph-checkpointer-scoping)
- [Long-term Memory (Store)](#long-term-memory-store)
- [Common Mistakes and Fixes](#common-mistakes-and-fixes)

---

## Overview

LangGraph's persistence layer enables durable execution by checkpointing graph state at every super-step:

- **Checkpointer**: Saves/loads graph state at every super-step
- **Thread ID**: Identifies independent checkpoint sequences (conversations)
- **Store**: Cross-thread memory for user preferences, facts

**Two types of memory:**
- **Short-term** (checkpointer): thread-scoped conversation history
- **Long-term** (store): cross-thread user preferences, facts

---

## Checkpointer Selection

| Checkpointer | Use Case | Production Ready |
|-------------|---------|---------|
| `InMemorySaver` | Testing, development | No |
| `SqliteSaver` | Local development | Partial |
| `PostgresSaver` | Production | Yes |

---

## Checkpointer Setup

### Basic Persistence (Development)

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict, Annotated
import operator

class State(TypedDict):
    messages: Annotated[list, operator.add]

def add_message(state: State) -> dict:
    return {"messages": ["Bot response"]}

checkpointer = InMemorySaver()

graph = (
    StateGraph(State)
    .add_node("respond", add_message)
    .add_edge(START, "respond")
    .add_edge("respond", END)
    .compile(checkpointer=checkpointer)  # Pass at compile time
)

# Always provide thread_id
config = {"configurable": {"thread_id": "conversation-1"}}

result1 = graph.invoke({"messages": ["Hello"]}, config)
print(len(result1["messages"]))  # 2

result2 = graph.invoke({"messages": ["How are you?"]}, config)
print(len(result2["messages"]))  # 4 (previous + new)
```

### Production PostgreSQL

```python
from langgraph.checkpoint.postgres import PostgresSaver

with PostgresSaver.from_conn_string(
    "postgresql://user:pass@localhost/db"
) as checkpointer:
    checkpointer.setup()  # Create tables on first use only
    graph = builder.compile(checkpointer=checkpointer)
```

**TypeScript:**
```typescript
import { PostgresSaver } from "@langchain/langgraph-checkpoint-postgres";

const checkpointer = PostgresSaver.fromConnString(
  "postgresql://user:pass@localhost/db"
);
await checkpointer.setup(); // Create tables on first use only

const graph = builder.compile({ checkpointer });
```

---

## Thread Management

Different `thread_id` values maintain independent state:

```python
alice_config = {"configurable": {"thread_id": "user-alice"}}
bob_config = {"configurable": {"thread_id": "user-bob"}}

graph.invoke({"messages": ["Hi from Alice"]}, alice_config)
graph.invoke({"messages": ["Hi from Bob"]}, bob_config)

# Alice's state is isolated from Bob's
```

---

## State History and Time Travel

```python
config = {"configurable": {"thread_id": "session-1"}}

result = graph.invoke({"messages": ["start"]}, config)

# Browse checkpoint history
states = list(graph.get_state_history(config))

# Replay from a past checkpoint
past = states[-2]
result = graph.invoke(None, past.config)  # None = resume from checkpoint

# Or fork: update state at a past checkpoint, then resume
fork_config = graph.update_state(past.config, {"messages": ["edited"]})
result = graph.invoke(None, fork_config)
```

### Manual State Update

```python
config = {"configurable": {"thread_id": "session-1"}}

# Modify state before resuming
graph.update_state(config, {"data": "manually_updated"})

# Resume with the updated state
result = graph.invoke(None, config)
```

---

## Subgraph Checkpointer Scoping

When compiling subgraphs, the `checkpointer` parameter controls persistence behavior.

| Feature | `checkpointer=False` | `None` (default) | `True` |
|-----|---------------------|--------------|--------|
| Interrupts (HITL) | No | Yes | Yes |
| Multi-turn memory | No | No | Yes |
| Multiple invocations (different subgraphs) | Yes | Yes | Caution (possible namespace conflicts) |
| Multiple invocations (same subgraph) | Yes | Yes | No |
| State inspection | No | Caution (current invocation only) | Yes |

**When to use each mode:**

- **`checkpointer=False`** — Subgraph needs no interrupt or persistence. Simplest, no checkpoint overhead.
- **`None` (default / omit `checkpointer`)** — Subgraph needs `interrupt()` but not multi-turn memory. Each invocation starts fresh, but can pause/resume.
- **`checkpointer=True`** — Subgraph needs to remember state across invocations (multi-turn conversation).

```python
# No interrupt needed — opt out of checkpointing
subgraph = subgraph_builder.compile(checkpointer=False)

# Needs interrupt but not cross-invocation persistence (default)
subgraph = subgraph_builder.compile()

# Needs cross-invocation persistence (stateful)
subgraph = subgraph_builder.compile(checkpointer=True)
```

> ⚠️ **Warning**: Stateful subgraphs (`checkpointer=True`) do not support multiple invocations of the same subgraph instance within a single node — those invocations write to the same checkpoint namespace and will conflict.

### Parallel Subgraph Namespace Isolation

When multiple **different** stateful subgraphs run in parallel, wrap each in a `StateGraph` with a unique node name for stable namespace isolation:

```python
from langgraph.graph import MessagesState, StateGraph

def create_sub_agent(model, *, name, **kwargs):
    """Wrap agent with unique node name for namespace isolation."""
    agent = create_agent(model=model, name=name, **kwargs)
    return (
        StateGraph(MessagesState)
        .add_node(name, agent)  # Unique name -> stable namespace
        .add_edge("__start__", name)
        .compile()
    )

fruit_agent = create_sub_agent("gpt-4.1-mini", name="fruit_agent", tools=[fruit_info], checkpointer=True)
veggie_agent = create_sub_agent("gpt-4.1-mini", name="veggie_agent", tools=[veggie_info], checkpointer=True)
```

---

## Long-term Memory (Store)

**Short-term vs Long-term Memory:**
- Checkpointer (short-term): thread-scoped, conversation history
- Store (long-term): **cross-thread**, user preferences, facts

```python
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()

# Save user preferences (available across all threads)
store.put(("alice", "preferences"), "language", {"preference": "short responses"})

# Use Store in a node — access via runtime
from langgraph.runtime import Runtime

def respond(state, runtime: Runtime):
    prefs = runtime.store.get((state["user_id"], "preferences"), "language")
    return {"response": f"Using preference: {prefs.value}"}

# Compile with both checkpointer and store
graph = builder.compile(checkpointer=checkpointer, store=store)

# Two threads access the same long-term memory
graph.invoke({"user_id": "alice"}, {"configurable": {"thread_id": "thread-1"}})
graph.invoke({"user_id": "alice"}, {"configurable": {"thread_id": "thread-2"}})  # Same preferences!
```

### Store Basic Operations

```python
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()

store.put(("user-123", "facts"), "location", {"city": "San Francisco"})  # Put
item = store.get(("user-123", "facts"), "location")  # Get
results = store.search(("user-123", "facts"), filter={"city": "San Francisco"})  # Search
store.delete(("user-123", "facts"), "location")  # Delete
```

---

## Common Mistakes and Fixes

### ❌ Missing thread_id
```python
# Wrong: state won't persist!
graph.invoke({"messages": ["Hello"]})
graph.invoke({"messages": ["What did I say?"]})  # Doesn't remember!

# Correct: always provide thread_id
config = {"configurable": {"thread_id": "session-1"}}
graph.invoke({"messages": ["Hello"]}, config)
graph.invoke({"messages": ["What did I say?"]}, config)  # Remembers!
```

### ❌ Using InMemorySaver in Production
```python
# Wrong: data lost on process restart
checkpointer = InMemorySaver()

# Correct: use persistent storage for production
from langgraph.checkpoint.postgres import PostgresSaver
with PostgresSaver.from_conn_string("postgresql://...") as checkpointer:
    checkpointer.setup()
    graph = builder.compile(checkpointer=checkpointer)
```

### ❌ update_state Doesn't Bypass Reducers
```python
from langgraph.types import Overwrite

# State has Reducer: items: Annotated[list, operator.add]
# Current state: {"items": ["A", "B"]}

# update_state goes through the Reducer
graph.update_state(config, {"items": ["C"]})  # Result: ["A", "B", "C"] — appended!

# To replace instead of append, use Overwrite
graph.update_state(config, {"items": Overwrite(["C"])})  # Result: ["C"] — replaced
```

### ❌ Accessing Store Directly in Nodes
```python
# Wrong: Store is not available in the node
def my_node(state):
    store.put(...)  # NameError! store is not defined

# Correct: access Store via the Runtime object
from langgraph.runtime import Runtime

def my_node(state, runtime: Runtime):
    runtime.store.put(...)  # Correct Store instance
```

### ❌ Running Same Stateful Subgraph in Parallel Within a Single Node
Stateful subgraphs (`checkpointer=True`) cannot be invoked multiple times within a single node — this causes namespace conflicts.
Use different subgraph objects for each parallel instance, or use a namespace wrapper.
