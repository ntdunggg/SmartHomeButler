# langgraph-fundamentals

> Consult this file when writing **any** LangGraph code. Covers StateGraph, state schemas, nodes, edges, Command, Send, invoke, streaming, and error handling.

## Table of Contents
- [Core Concepts](#core-concepts)
- [Design Methodology](#design-methodology)
- [When to Use LangGraph](#when-to-use-langgraph)
- [State Management](#state-management)
- [Nodes](#nodes)
- [Edges](#edges)
- [Command Pattern](#command-pattern)
- [Send API (Parallel)](#send-api-parallel)
- [Running Graphs: invoke and stream](#running-graphs-invoke-and-stream)
- [Error Handling](#error-handling)
- [Common Mistakes and Fixes](#common-mistakes-and-fixes)

---

## Core Concepts

LangGraph models agent workflows as **directed graphs**:

- **StateGraph**: The main class for building stateful graphs
- **Nodes**: Functions that perform work and update state
- **Edges**: Define execution order (static or conditional)
- **START/END**: Special nodes marking entry and exit points
- **State with Reducers**: Control how state updates are merged

Graphs must be `compile()`d before execution.

---

## Design Methodology

Follow these 5 steps when building a new graph:

1. **Plan discrete steps** — draw the workflow flowchart, each step corresponds to a node
2. **Determine each step's responsibility** — classify nodes: LLM step, data step, action step, or user input step
3. **Design your state** — state is shared memory for all nodes, stores raw data
4. **Build your nodes** — implement each step as a function that takes state and returns partial updates
5. **Wire it together** — connect nodes with edges, add conditional routing, compile with Checkpointer if needed

---

## When to Use LangGraph

| Use LangGraph When | Use Alternatives When |
|-----------------|-------------|
| Need fine-grained control over agent orchestration | Quick prototypes → LangChain Agent |
| Building complex workflows with branching/loops | Simple stateless workflows → LangChain direct |
| Need human-in-the-loop, persistence | Need out-of-the-box features → Deep Agents |

---

## State Management

| Need | Solution | Example |
|------|---------|------|
| Overwrite value | No Reducer (default) | Simple fields like counters |
| Append to list | Reducer (operator.add / concat) | Message history, logs |
| Custom logic | Custom Reducer function | Complex merging |

### State Schema and Reducers

**Python:**
```python
from typing_extensions import TypedDict, Annotated
import operator

class State(TypedDict):
    name: str  # Default: overwrite on update
    messages: Annotated[list, operator.add]  # Append to list
    total: Annotated[int, operator.add]  # Sum integers
```

**TypeScript:**
```typescript
import { StateSchema, ReducedValue, MessagesValue } from "@langchain/langgraph";
import { z } from "zod";

const State = new StateSchema({
  name: z.string(),  // Default: overwrite
  messages: MessagesValue,  // Messages built-in
  items: new ReducedValue(
    z.array(z.string()).default(() => []),
    { reducer: (current, update) => current.concat(update) }
  ),
});
```

---

## Nodes

Node function signatures:

| Signature | When to Use |
|------|---------| 
| `def node(state: State)` | Simple nodes that only need state |
| `def node(state: State, config: RunnableConfig)` | Need thread_id, tags, or configurable values |
| `def node(state: State, runtime: Runtime[Context])` | Need runtime context, store, or stream_writer |

```python
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

def plain_node(state: State):
    return {"results": "done"}

def node_with_config(state: State, config: RunnableConfig):
    thread_id = config["configurable"]["thread_id"]
    return {"results": f"Thread: {thread_id}"}

def node_with_runtime(state: State, runtime: Runtime[Context]):
    user_id = runtime.context.user_id
    return {"results": f"User: {user_id}"}
```

---

## Edges

| Need | Edge Type | When to Use |
|------|--------|---------| 
| Always go to same node | `add_edge()` | Fixed, deterministic flow |
| Route based on state | `add_conditional_edges()` | Dynamic branching |
| Update state AND route | `Command` | Combine logic in a single node |
| Fan out to multiple nodes | `Send` | Parallel processing with dynamic inputs |

### Basic Graph (Linear Execution)

```python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class State(TypedDict):
    input: str
    output: str

def process_input(state: State) -> dict:
    return {"output": f"Processed: {state['input']}"}

def finalize(state: State) -> dict:
    return {"output": state["output"].upper()}

graph = (
    StateGraph(State)
    .add_node("process", process_input)
    .add_node("finalize", finalize)
    .add_edge(START, "process")
    .add_edge("process", "finalize")
    .add_edge("finalize", END)
    .compile()
)

result = graph.invoke({"input": "hello"})
print(result["output"])  # "PROCESSED: HELLO"
```

### Conditional Edges (Dynamic Routing)

```python
from typing import Literal

class State(TypedDict):
    query: str
    route: str
    result: str

def classify(state: State) -> dict:
    if "weather" in state["query"].lower():
        return {"route": "weather"}
    return {"route": "general"}

def route_query(state: State) -> Literal["weather", "general"]:
    return state["route"]

graph = (
    StateGraph(State)
    .add_node("classify", classify)
    .add_node("weather", lambda s: {"result": "Sunny, 72F"})
    .add_node("general", lambda s: {"result": "General response"})
    .add_edge(START, "classify")
    .add_conditional_edges("classify", route_query, ["weather", "general"])
    .add_edge("weather", END)
    .add_edge("general", END)
    .compile()
)
```

---

## Command Pattern

Command combines state updates and routing in a single return value:
- **`update`**: State updates to apply (similar to returning a dict from a node)
- **`goto`**: Node name to navigate to next
- **`resume`**: Value to resume after `interrupt()` — see `langgraph-human-in-the-loop.md`

```python
from langgraph.types import Command
from typing import Literal

class State(TypedDict):
    count: int
    result: str

def node_a(state: State) -> Command[Literal["node_b", "node_c"]]:
    """Update state AND decide next node in a single return."""
    new_count = state["count"] + 1
    if new_count > 5:
        return Command(update={"count": new_count}, goto="node_c")
    return Command(update={"count": new_count}, goto="node_b")
```

> ⚠️ **Warning**: `Command` only adds **dynamic** edges — static edges defined with `add_edge` will still execute. If `node_a` returns `Command(goto="node_c")` and you also have `graph.add_edge("node_a", "node_b")`, then **both** `node_b` and `node_c` will run.

---

## Send API (Parallel)

Fan out to parallel workers: return `[Send("worker", {...})]` from a conditional edge. Result fields need a Reducer.

```python
from langgraph.types import Send
from typing import Annotated
import operator

class OrchestratorState(TypedDict):
    tasks: list[str]
    results: Annotated[list, operator.add]  # Reducer required
    summary: str

def orchestrator(state: OrchestratorState):
    """Fan out tasks to workers."""
    return [Send("worker", {"task": task}) for task in state["tasks"]]

def worker(state: dict) -> dict:
    return {"results": [f"Completed: {state['task']}"]}

def synthesize(state: OrchestratorState) -> dict:
    return {"summary": f"Processed {len(state['results'])} tasks"}

graph = (
    StateGraph(OrchestratorState)
    .add_node("worker", worker)
    .add_node("synthesize", synthesize)
    .add_conditional_edges(START, orchestrator, ["worker"])
    .add_edge("worker", "synthesize")
    .add_edge("synthesize", END)
    .compile()
)

result = graph.invoke({"tasks": ["Task A", "Task B", "Task C"]})
```

---

## Running Graphs: invoke and stream

### invoke Basics

```python
result = graph.invoke({"input": "hello"})
# With config (for persistence, tags, etc.)
result = graph.invoke({"input": "hello"}, {"configurable": {"thread_id": "1"}})
```

### stream Mode Selection

| Mode | Streamed Content | Use Case |
|------|---------|---------| 
| `values` | Full state after each step | Monitor full state |
| `updates` | State deltas | Track incremental updates |
| `messages` | LLM tokens + metadata | Chat UI |
| `custom` | User-defined data | Progress indicators |

### Streaming LLM Tokens

```python
for chunk in graph.stream(
    {"messages": [HumanMessage("Hello")]},
    stream_mode="messages"
):
    token, metadata = chunk
    if hasattr(token, "content"):
        print(token.content, end="", flush=True)
```

### Custom Streaming Data

```python
from langgraph.config import get_stream_writer

def my_node(state):
    writer = get_stream_writer()
    writer("Processing step 1...")
    # do work
    writer("Complete!")
    return {"result": "done"}

for chunk in graph.stream({"data": "test"}, stream_mode="custom"):
    print(chunk)
```

---

## Error Handling

| Error Type | Who Fixes | Strategy | Example |
|---------|---------|------|------|
| Transient (network, rate limits) | System | `RetryPolicy(max_attempts=3)` | `add_node(..., retry_policy=...)` |
| LLM-recoverable (tool failure) | LLM | `ToolNode(tools, handle_tool_errors=True)` | Error returned as ToolMessage |
| User-fixable (missing info) | Human | `interrupt({...})` | Collect missing data (see HITL reference) |
| Unexpected errors | Developer | Let it bubble up | `raise` |

```python
from langgraph.types import RetryPolicy

# Retry policy
workflow.add_node(
    "search_documentation",
    search_documentation,
    retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0)
)

# Tool node error handling
from langgraph.prebuilt import ToolNode
tool_node = ToolNode(tools, handle_tool_errors=True)
workflow.add_node("tools", tool_node)
```

---

## Common Mistakes and Fixes

### ❌ Forgetting to Call compile()
```python
# Wrong
builder.invoke({"input": "test"})  # AttributeError!

# Correct
graph = builder.compile()
graph.invoke({"input": "test"})
```

### ❌ List Field Without Reducer
```python
# Wrong: list will be overwritten
class State(TypedDict):
    messages: list  # No Reducer!
# Node 1 returns: {"messages": ["A"]}
# Node 2 returns: {"messages": ["B"]}
# Final: {"messages": ["B"]}  # "A" is lost!

# Correct: use Annotated with operator.add
class State(TypedDict):
    messages: Annotated[list, operator.add]
# Final: {"messages": ["A", "B"]}
```

### ❌ Node Returns Entire State Instead of Partial Update
```python
# Wrong: returning the entire state object
def my_node(state: State) -> State:
    state["field"] = "updated"
    return state  # Don't do this!

# Correct: return only a dict with updates
def my_node(state: State) -> dict:
    return {"field": "updated"}
```

### ❌ Infinite Loop (Missing Exit Condition)
```python
# Wrong: loops forever
builder.add_edge("node_a", "node_b")
builder.add_edge("node_b", "node_a")

# Correct
def should_continue(state):
    return END if state["count"] > 10 else "node_b"
builder.add_conditional_edges("node_a", should_continue)
```

### ❌ Other Common Mistakes
```python
# Router must return node names that exist in the graph
builder.add_node("my_node", func)  # Add node before referencing in edges
builder.add_conditional_edges("node_a", router, ["my_node"])

# Command return type needs Literal declaration for routing targets (Python)
def node_a(state) -> Command[Literal["node_b", "node_c"]]:
    return Command(goto="node_b")

# START is just an entry — cannot route back to it
builder.add_edge("node_a", START)  # Wrong!
builder.add_edge("node_a", "entry")  # Use a named entry node instead

# Send parallel results need a Reducer
class State(TypedDict):
    results: Annotated[list, operator.add]  # List Reducer, not string
```
