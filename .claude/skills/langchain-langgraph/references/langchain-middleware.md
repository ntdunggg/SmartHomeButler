# langchain-middleware

> Consult this file when you need Human-in-the-loop approval, custom middleware, or structured output. Covers `HumanInTheLoopMiddleware` for human approval of dangerous tool calls, creating custom middleware with hooks, `Command` resume patterns, and Pydantic/Zod structured output.

**Requirement:** All HITL workflows require Checkpointer + thread_id configuration.

---

## Human-in-the-Loop

### Basic HITL Setup

**Python — Pause and request approval before sending email:**
```python
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver
from langchain.tools import tool

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email."""
    return f"Email sent to {to}"

agent = create_agent(
    model="gpt-4.1",
    tools=[send_email],
    checkpointer=MemorySaver(),  # Required for HITL
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                "send_email": {"allowed_decisions": ["approve", "edit", "reject"]},
            }
        )
    ],
)
```

**TypeScript:**
```typescript
import { createAgent, humanInTheLoopMiddleware } from "langchain";
import { MemorySaver } from "@langchain/langgraph";
import { tool } from "@langchain/core/tools";
import { z } from "zod";

const sendEmail = tool(
  async ({ to, subject, body }) => `Email sent to ${to}`,
  {
    name: "send_email",
    description: "Send an email",
    schema: z.object({ to: z.string(), subject: z.string(), body: z.string() }),
  }
);

const agent = createAgent({
  model: "anthropic:claude-sonnet-4-5",
  tools: [sendEmail],
  checkpointer: new MemorySaver(),
  middleware: [
    humanInTheLoopMiddleware({
      interruptOn: { send_email: { allowedDecisions: ["approve", "edit", "reject"] } },
    }),
  ],
});
```

---

### Run, Detect Interrupt, Resume

**Python:**
```python
from langgraph.types import Command

config = {"configurable": {"thread_id": "session-1"}}

# Step 1: Agent runs until it needs to call a tool
result1 = agent.invoke({
    "messages": [{"role": "user", "content": "Send email to john@example.com"}]
}, config=config)

# Detect interrupt
if "__interrupt__" in result1:
    print(f"Awaiting approval: {result1['__interrupt__']}")

# Step 2: Human approval
result2 = agent.invoke(
    Command(resume={"decisions": [{"type": "approve"}]}),
    config=config
)
```

**TypeScript:**
```typescript
import { Command } from "@langchain/langgraph";

const config = { configurable: { thread_id: "session-1" } };

// Step 1
const result1 = await agent.invoke({
  messages: [{ role: "user", content: "Send email to john@example.com" }]
}, config);

// Detect interrupt
if (result1.__interrupt__) {
  console.log(`Awaiting approval: ${result1.__interrupt__}`);
}

// Step 2: Human approval
const result2 = await agent.invoke(
  new Command({ resume: { decisions: [{ type: "approve" }] } }),
  config
);
```

---

### Editing Tool Arguments

```python
# Human edits arguments — edited_action must include name + args
result2 = agent.invoke(
    Command(resume={
        "decisions": [{
            "type": "edit",
            "edited_action": {
                "name": "send_email",
                "args": {
                    "to": "alice@company.com",  # Corrected email
                    "subject": "Project Meeting - Updated",
                    "body": "...",
                },
            },
        }]
    }),
    config=config
)
```

### Rejection with Feedback

```python
result2 = agent.invoke(
    Command(resume={
        "decisions": [{
            "type": "reject",
            "feedback": "Cannot delete customer data without manager approval",
        }]
    }),
    config=config
)
```

### Configuring Different Strategies per Tool

```python
agent = create_agent(
    model="gpt-4.1",
    tools=[send_email, read_email, delete_email],
    checkpointer=MemorySaver(),
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                "send_email": {"allowed_decisions": ["approve", "edit", "reject"]},
                "delete_email": {"allowed_decisions": ["approve", "reject"]},  # No edit
                "read_email": False,  # No HITL for reads
            }
        )
    ],
)
```

---

## Configuration Boundaries

**Can configure:**
- Which tools require approval (per-tool policies)
- Allowed decisions per tool (approve, edit, reject)
- Custom middleware hooks: `before_model`, `after_model`, `wrap_tool_call`, `before_agent`, `after_agent`
- Tool-specific middleware (applies only to certain tools)

**Cannot configure:**
- Interrupt after tool execution (must be before)
- Skipping the Checkpointer requirement for HITL

---

## Common Mistakes and Fixes

### ❌ Missing Checkpointer
```python
# Wrong
agent = create_agent(model="gpt-4.1", tools=[send_email], middleware=[HumanInTheLoopMiddleware({...})])

# Correct
agent = create_agent(
    model="gpt-4.1", tools=[send_email],
    checkpointer=MemorySaver(),  # Required
    middleware=[HumanInTheLoopMiddleware({...})]
)
```

### ❌ Missing thread_id
```python
# Wrong
agent.invoke(input)  # No config!

# Correct
agent.invoke(input, config={"configurable": {"thread_id": "user-123"}})
```

### ❌ Wrong Resume Syntax
```python
# Wrong
agent.invoke({"resume": {"decisions": [...]}})

# Correct
from langgraph.types import Command
agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)
```
