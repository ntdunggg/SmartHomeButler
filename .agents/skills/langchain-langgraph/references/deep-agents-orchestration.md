# deep-agents-orchestration

> Consult this file when using sub-agents, task planning, or human approval in Deep Agents. Covers SubAgentMiddleware, TodoList for planning, and HITL interrupts.

---

## Sub-Agents (Task Delegation)

| Use Sub-Agents When | Use Main Agent When |
|---------------|--------------| 
| Task requires specialized tools | General tools are sufficient |
| Want to isolate complex work | Single-step operation |
| Need to keep main agent context clean | Context bloat is acceptable |

**How it works:** Main agent has `task` tool → creates new sub-agent → sub-agent executes autonomously → returns final report

**Default sub-agent:** "general-purpose" — automatically available, shares the same tools/configuration as the main agent.

### Custom Sub-Agents

**Python:**
```python
from deepagents import create_deep_agent
from langchain.tools import tool

@tool
def search_papers(query: str) -> str:
    """Search academic papers."""
    return f"Found 10 papers about {query}"

agent = create_deep_agent(
    subagents=[
        {
            "name": "researcher",
            "description": "Conduct web research and compile findings",
            "system_prompt": "Search thoroughly, return concise summary",
            "tools": [search_papers],
        }
    ]
)
# Main agent delegates: task(agent="researcher", instruction="Research AI trends")
```

### Sub-Agent with HITL

```python
from langgraph.checkpoint.memory import MemorySaver

agent = create_deep_agent(
    subagents=[
        {
            "name": "code-deployer",
            "description": "Deploy code to production",
            "system_prompt": "You deploy code after tests pass.",
            "tools": [run_tests, deploy_to_prod],
            "interrupt_on": {"deploy_to_prod": True},  # Requires approval
        }
    ],
    checkpointer=MemorySaver()  # Required for interrupts
)
```

---

## TodoList (Task Planning)

| Use TodoList When | Skip TodoList When |
|---------------|---------------|
| Complex multi-step tasks | Simple single-action tasks |
| Long-running operations | Quick operations (< 3 steps) |

**Tool signature:**
```
write_todos(todos: list[dict]) -> None
```

Each todo item has:
- `content`: task description
- `status`: one of `"pending"`, `"in_progress"`, `"completed"`

### Example Usage

```python
from deepagents import create_deep_agent

agent = create_deep_agent()  # TodoListMiddleware included by default

result = agent.invoke({
    "messages": [{"role": "user", "content": "Create a REST API: design models, implement CRUD, add auth, write tests"}]
}, config={"configurable": {"thread_id": "session-1"}})

# Agent automatically plans via write_todos:
# [
#   {"content": "Design data models", "status": "in_progress"},
#   {"content": "Implement CRUD endpoints", "status": "pending"},
#   {"content": "Add authentication", "status": "pending"},
#   {"content": "Write tests", "status": "pending"}
# ]
```

### Accessing Todos from Final State

```python
result = agent.invoke({...}, config={"configurable": {"thread_id": "session-1"}})

todos = result.get("todos", [])
for todo in todos:
    print(f"[{todo['status']}] {todo['content']}")
```

---

## Human-in-the-Loop (Approval Workflows)

| Use HITL When | Skip HITL When |
|------------|-----------| 
| High-risk operations (DB writes, deployments) | Read-only operations |
| Compliance requires human oversight | Fully automated workflows |

### Configuring Tools That Require Approval

**Python:**
```python
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver

agent = create_deep_agent(
    interrupt_on={
        "write_file": True,  # Allow all decisions
        "execute_sql": {"allowed_decisions": ["approve", "reject"]},
        "read_file": False,  # No interrupt needed
    },
    checkpointer=MemorySaver()  # Required for HITL
)
```

**TypeScript:**
```typescript
import { createDeepAgent } from "deepagents";
import { MemorySaver } from "@langchain/langgraph";

const agent = await createDeepAgent({
  interruptOn: {
    write_file: true,
    execute_sql: { allowedDecisions: ["approve", "reject"] },
    read_file: false,
  },
  checkpointer: new MemorySaver()  // Required
});
```

### Full Approval Workflow

```python
from langgraph.types import Command

config = {"configurable": {"thread_id": "session-1"}}

# Step 1: Agent proposes write_file — execution pauses
result = agent.invoke({
    "messages": [{"role": "user", "content": "Write config to /prod.yaml"}]
}, config=config)

# Step 2: Check interrupt status
state = agent.get_state(config)
if state.next:
    print("Pending operation")

# Step 3: Approve and resume
result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)
```

### Rejection with Feedback

```python
result = agent.invoke(
    Command(resume={"decisions": [{"type": "reject", "message": "Run tests first"}]}),
    config=config,
)
```

### Editing Actions Before Execution

```python
result = agent.invoke(
    Command(resume={"decisions": [{
        "type": "edit",
        "edited_action": {
            "name": "execute_sql",
            "args": {"query": "DELETE FROM users WHERE last_login < '2020-01-01' LIMIT 100"},
        },
    }]}),
    config=config,
)
```

---

## Configuration Boundaries

**Can configure:**
- Sub-agent names, tools, models, system prompts
- Which tools require approval
- Allowed decision types per tool
- TodoList content and structure

**Cannot configure:**
- Tool names (`task`, `write_todos`)
- HITL protocol (approve/edit/reject structure)
- Checkpointer requirement for skipping interrupts
- Making sub-agents stateful (they are ephemeral)

---

## Common Mistakes and Fixes

### ❌ Missing Checkpointer
```python
# Wrong
agent = create_deep_agent(interrupt_on={"write_file": True})

# Correct
agent = create_deep_agent(interrupt_on={"write_file": True}, checkpointer=MemorySaver())
```

### ❌ Sub-Agents Are Stateless — Missing Complete Instructions
```python
# Wrong: sub-agent doesn't remember prior calls
# task(agent='research', instruction='Find data')
# task(agent='research', instruction='What did you find?')  # Starts fresh!

# Correct: provide complete instructions upfront
# task(agent='research', instruction='Find data on AI, save to /research/, return summary')
```

### ❌ Interrupts Occur Between invoke() Calls
```python
result = agent.invoke({...}, config=config)       # Step 1: triggers interrupt
if "__interrupt__" in result:                      # Step 2: check interrupt
    result = agent.invoke(                         # Step 3: resume
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config,
    )
```

### ❌ Forgetting thread_id for Resuming
```python
# Wrong: cannot resume without thread_id
agent.invoke({"messages": [...]})

# Correct
config = {"configurable": {"thread_id": "session-1"}}
agent.invoke({...}, config=config)
# Resume with Command using the same config
agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)
```
