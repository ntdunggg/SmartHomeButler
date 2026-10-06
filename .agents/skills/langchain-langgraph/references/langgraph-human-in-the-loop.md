# langgraph-human-in-the-loop

> Consult this file when implementing Human-in-the-Loop patterns, pausing for approval, or handling errors in LangGraph. Covers `interrupt()`, `Command(resume=...)`, approval/validation workflows, and the four-layer error handling strategy.

## Table of Contents
- [Overview and Requirements](#overview-and-requirements)
- [Basic Interrupt and Resume](#basic-interrupt-and-resume)
- [Approval Workflow](#approval-workflow)
- [Validation Loop](#validation-loop)
- [Multiple Parallel Interrupts](#multiple-parallel-interrupts)
- [Side Effects Before Interrupt Must Be Idempotent](#side-effects-before-interrupt-must-be-idempotent)
- [Subgraph Interrupt Re-execution](#subgraph-interrupt-re-execution)
- [Common Mistakes and Fixes](#common-mistakes-and-fixes)

---

## Overview and Requirements

LangGraph's Human-in-the-Loop pattern lets you pause graph execution, present data to a user, and resume after receiving input:

- **`interrupt(value)`** — pauses execution, presents a value to the caller
- **`Command(resume=value)`** — resumes execution, providing the value as the return of `interrupt()`
- **Checkpointer** — required: saves state during the pause
- **Thread ID** — required: identifies which paused execution to resume

**Three prerequisites:**
1. **Checkpointer** — compile with `checkpointer=InMemorySaver()` (dev) or `PostgresSaver` (production)
2. **Thread ID** — pass `{"configurable": {"thread_id": "..."}}` to every `invoke`/`stream` call
3. **JSON-serializable payloads** — values passed to `interrupt()` must be JSON-serializable

---

## Basic Interrupt and Resume

`interrupt(value)` pauses the graph. The value appears under the `__interrupt__` key in the result. `Command(resume=value)` resumes — the resume value becomes the return value of `interrupt()`.

**Key point**: When a graph resumes, the node restarts from the **beginning** — all code before `interrupt()` will re-run.

**Python:**
```python
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class State(TypedDict):
    approved: bool

def approval_node(state: State):
    # Pause and request approval
    approved = interrupt("Do you approve this action?")
    # On resume, Command(resume=...) returns the value here
    return {"approved": approved}

checkpointer = InMemorySaver()
graph = (
    StateGraph(State)
    .add_node("approval", approval_node)
    .add_edge(START, "approval")
    .add_edge("approval", END)
    .compile(checkpointer=checkpointer)
)

config = {"configurable": {"thread_id": "thread-1"}}

# Initial run — hits interrupt and pauses
result = graph.invoke({"approved": False}, config)
print(result["__interrupt__"])
# [Interrupt(value='Do you approve this action?')]

# Resume with human response
result = graph.invoke(Command(resume=True), config)
print(result["approved"])  # True
```

**TypeScript:**
```typescript
import { interrupt, Command, MemorySaver, StateGraph, StateSchema, START, END } from "@langchain/langgraph";
import { z } from "zod";

const State = new StateSchema({
  approved: z.boolean().default(false),
});

const approvalNode = async (state: typeof State.State) => {
  const approved = interrupt("Do you approve this action?");
  return { approved };
};

const checkpointer = new MemorySaver();
const graph = new StateGraph(State)
  .addNode("approval", approvalNode)
  .addEdge(START, "approval")
  .addEdge("approval", END)
  .compile({ checkpointer });

const config = { configurable: { thread_id: "thread-1" } };

let result = await graph.invoke({ approved: false }, config);
console.log(result.__interrupt__);

result = await graph.invoke(new Command({ resume: true }), config);
console.log(result.approved);  // true
```

---

## Approval Workflow

Common pattern: interrupt shows a draft, routes based on human decision.

```python
from langgraph.types import interrupt, Command
from langgraph.graph import StateGraph, START, END
from typing import Literal
from typing_extensions import TypedDict

class EmailAgentState(TypedDict):
    email_content: str
    draft_response: str
    classification: dict

def human_review(state: EmailAgentState) -> Command[Literal["send_reply", "__end__"]]:
    """Use interrupt to pause for human review, and route based on decision."""
    classification = state.get("classification", {})

    # interrupt() must execute first — any code before it will re-run on resume
    human_decision = interrupt({
        "email_id": state.get("email_content", ""),
        "draft_response": state.get("draft_response", ""),
        "urgency": classification.get("urgency"),
        "action": "Please review and approve/edit this response"
    })

    # Handle human decision
    if human_decision.get("approved"):
        return Command(
            update={"draft_response": human_decision.get("edited_response", state.get("draft_response", ""))},
            goto="send_reply"
        )
    else:
        return Command(update={}, goto=END)
```

---

## Validation Loop

Use `interrupt()` in a loop to validate human input, re-prompting on invalid input:

```python
def get_age_node(state):
    prompt = "What is your age?"

    while True:
        answer = interrupt(prompt)

        # Validate input
        if isinstance(answer, int) and answer > 0:
            break
        else:
            # Invalid input — ask again with more specific prompt
            prompt = f"'{answer}' is not a valid age. Please enter a positive number."

    return {"age": answer}

# Usage:
config = {"configurable": {"thread_id": "form-1"}}
first = graph.invoke({"age": None}, config)
# __interrupt__: "What is your age?"

retry = graph.invoke(Command(resume="thirty"), config)
# __interrupt__: "'thirty' is not a valid age..."

final = graph.invoke(Command(resume=30), config)
print(final["age"])  # 30
```

---

## Multiple Parallel Interrupts

When parallel branches each call `interrupt()`, resume all in a single call by mapping each interrupt ID to its resume value:

```python
from typing import Annotated, TypedDict
import operator
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command, interrupt

class State(TypedDict):
    vals: Annotated[list[str], operator.add]

def node_a(state):
    answer = interrupt("question_a")
    return {"vals": [f"a:{answer}"]}

def node_b(state):
    answer = interrupt("question_b")
    return {"vals": [f"b:{answer}"]}

graph = (
    StateGraph(State)
    .add_node("a", node_a)
    .add_node("b", node_b)
    .add_edge(START, "a")
    .add_edge(START, "b")
    .add_edge("a", END)
    .add_edge("b", END)
    .compile(checkpointer=InMemorySaver())
)

config = {"configurable": {"thread_id": "1"}}

# Both parallel nodes hit interrupt() and pause
result = graph.invoke({"vals": []}, config)

# Resume all pending interrupts at once
resume_map = {
    i.id: f"answer for {i.value}"
    for i in result["__interrupt__"]
}
result = graph.invoke(Command(resume=resume_map), config)
# result["vals"] = ["a:answer for question_a", "b:answer for question_b"]
```

---

## Side Effects Before Interrupt Must Be Idempotent

When a graph resumes, the node restarts from the **beginning** — **all** code before `interrupt()` will re-run. In subgraphs, both parent nodes and subgraph nodes re-execute.

**✅ Do:**
- Use **upsert** (not insert) operations before `interrupt()`
- Use **check-then-create** patterns
- Place side effects **after** `interrupt()` whenever possible
- Separate side effects into their own nodes

**❌ Don't:**
- Create new records before `interrupt()` — they'll duplicate on every resume
- Append to lists before `interrupt()` — duplicate entries on every resume

```python
# ✅ Correct: upsert is idempotent — safe before interrupt
def node_a(state: State):
    db.upsert_user(user_id=state["user_id"], status="pending_approval")
    approved = interrupt("Approve this change?")
    return {"approved": approved}

# ✅ Correct: side effect after interrupt — runs only once
def node_a(state: State):
    approved = interrupt("Approve this change?")
    if approved:
        db.create_audit_log(user_id=state["user_id"], action="approved")
    return {"approved": approved}

# ❌ Wrong: insert will create duplicates on every resume!
def node_a(state: State):
    audit_id = db.create_audit_log({  # Runs again on resume!
        "user_id": state["user_id"],
        "action": "pending_approval",
    })
    approved = interrupt("Approve this change?")
    return {"approved": approved}
```

---

## Subgraph Interrupt Re-execution

When a subgraph contains `interrupt()`, resuming re-executes the parent node (that calls the subgraph) AND the subgraph node (that calls `interrupt()`):

```python
def node_in_parent_graph(state: State):
    some_code()  # <-- re-executes on resume
    subgraph_result = subgraph.invoke(some_input)
    # ...

def node_in_subgraph(state: State):
    some_other_code()  # <-- also re-executes on resume
    result = interrupt("What's your name?")
    # ...
```

---

## Common Mistakes and Fixes

### ❌ Missing Checkpointer
```python
# Wrong
graph = builder.compile()

# Correct
graph = builder.compile(checkpointer=InMemorySaver())
```

### ❌ Using Plain Dict Instead of Command to Resume
```python
# Wrong
graph.invoke({"resume_data": "approve"}, config)

# Correct
graph.invoke(Command(resume="approve"), config)
```

### ❌ Resuming with a Different thread_id
```python
# Wrong: creates a new thread instead of resuming
graph.invoke(Command(resume=True), {"configurable": {"thread_id": "different-id"}})

# Correct: use the same config
graph.invoke(Command(resume=True), config)  # Same config object
```

### ❌ Passing Command(update=...) as invoke Input
`Command(resume=...)` is the **only** Command pattern that can be used as `invoke()`/`stream()` input.
Do not pass `Command(update=...)` as input — it will resume from the latest checkpoint and the graph will appear stuck.

### ❌ Non-Idempotent Side Effects Before interrupt()
Non-idempotent operations (insert, append) before `interrupt()` will duplicate on every resume.
Move side effects **after** `interrupt()`, or use upsert/check-then-create patterns.
