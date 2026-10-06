# langchain-fundamentals

> Consult this file when writing any LangChain agent code. Covers `create_agent()`, tool definitions (`@tool` / `tool()`), persistence with Checkpointer, middleware patterns, and structured output.

## Table of Contents
- [Creating Agents with create_agent](#creating-agents-with-create_agent)
- [Defining Tools](#defining-tools)
- [Middleware for Controlling Agent Behavior](#middleware-for-controlling-agent-behavior)
- [Structured Output](#structured-output)
- [Model Configuration](#model-configuration)
- [Common Mistakes and Fixes](#common-mistakes-and-fixes)

---

## Creating Agents with create_agent

`create_agent()` is the recommended way to build agents. It handles the agent loop, tool execution, and state management.

**All other alternatives are outdated.**

| Parameter | Purpose | Example |
|------|------|------|
| `model` | LLM to use | `"anthropic:claude-sonnet-4-5"` or model instance |
| `tools` | List of tools | `[search, calculator]` |
| `system_prompt` / `systemPrompt` | Agent instructions | `"You are a helpful assistant"` |
| `checkpointer` | State persistence | `MemorySaver()` |
| `middleware` | Processing hooks | `[HumanInTheLoopMiddleware(...)]` |

### Basic Agent

**Python:**
```python
from langchain.agents import create_agent
from langchain_core.tools import tool

@tool
def get_weather(location: str) -> str:
    """Get current weather for a location.

    Args:
        location: City name
    """
    return f"Weather in {location}: Sunny, 72F"

agent = create_agent(
    model="anthropic:claude-sonnet-4-5",
    tools=[get_weather],
    system_prompt="You are a helpful assistant."
)

result = agent.invoke({
    "messages": [{"role": "user", "content": "What's the weather in Paris?"}]
})
print(result["messages"][-1].content)
```

**TypeScript:**
```typescript
import { createAgent } from "langchain";
import { tool } from "@langchain/core/tools";
import { z } from "zod";

const getWeather = tool(
  async ({ location }) => `Weather in ${location}: Sunny, 72F`,
  {
    name: "get_weather",
    description: "Get current weather for a location.",
    schema: z.object({ location: z.string().describe("City name") }),
  }
);

const agent = createAgent({
  model: "anthropic:claude-sonnet-4-5",
  tools: [getWeather],
  systemPrompt: "You are a helpful assistant.",
});

const result = await agent.invoke({
  messages: [{ role: "user", content: "What's the weather in Paris?" }],
});
console.log(result.messages[result.messages.length - 1].content);
```

### Agent with Persistence

```python
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
agent = create_agent(
    model="anthropic:claude-sonnet-4-5",
    tools=[search],
    checkpointer=checkpointer,
)

config = {"configurable": {"thread_id": "user-123"}}
agent.invoke({"messages": [{"role": "user", "content": "My name is Alice"}]}, config=config)
result = agent.invoke({"messages": [{"role": "user", "content": "What's my name?"}]}, config=config)
# Agent remembers: "Your name is Alice"
```

---

## Defining Tools

Tools are functions that agents can call. Use the `@tool` decorator (Python) or `tool()` function (TypeScript).

**Python:**
```python
from langchain_core.tools import tool

@tool
def add(a: float, b: float) -> float:
    """Add two numbers.

    Args:
        a: First number
        b: Second number
    """
    return a + b
```

**TypeScript:**
```typescript
import { tool } from "@langchain/core/tools";
import { z } from "zod";

const add = tool(
  async ({ a, b }) => a + b,
  {
    name: "add",
    description: "Add two numbers.",
    schema: z.object({
      a: z.number().describe("First number"),
      b: z.number().describe("Second number"),
    }),
  }
);
```

---

## Middleware for Controlling Agent Behavior

Middleware intercepts the agent loop to add human approval, error handling, logging, etc.

Key imports:
```python
from langchain.agents.middleware import HumanInTheLoopMiddleware, wrap_tool_call
```
```typescript
import { humanInTheLoopMiddleware, createMiddleware } from "langchain";
```

Key patterns:
- **HITL**: `middleware=[HumanInTheLoopMiddleware(interrupt_on={"dangerous_tool": True})]` — requires `checkpointer` + `thread_id`
- **Resume after interrupt**: `agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config=config)`
- **Custom middleware**: `@wrap_tool_call` decorator (Python) or `createMiddleware({ wrapToolCall: ... })` (TypeScript)

> For detailed middleware examples, see `langchain-middleware.md`

---

## Structured Output

Use `response_format` or `with_structured_output()` to get typed, validated agent responses.

**Python:**
```python
from langchain.agents import create_agent
from pydantic import BaseModel, Field

class ContactInfo(BaseModel):
    name: str
    email: str
    phone: str = Field(description="Phone number with area code")

# Option 1: Agent with structured output
agent = create_agent(model="gpt-4.1", tools=[search], response_format=ContactInfo)
result = agent.invoke({"messages": [{"role": "user", "content": "Find contact for John"}]})
print(result["structured_response"])  # ContactInfo(name='John', ...)

# Option 2: Model-level structured output (no agent needed)
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-4.1")
structured_model = model.with_structured_output(ContactInfo)
response = structured_model.invoke("Extract: John, john@example.com, 555-1234")
```

**TypeScript:**
```typescript
import { ChatOpenAI } from "@langchain/openai";
import { z } from "zod";

const ContactInfo = z.object({
  name: z.string(),
  email: z.string().email(),
  phone: z.string().describe("Phone number with area code"),
});

const model = new ChatOpenAI({ model: "gpt-4.1" });
const structuredModel = model.withStructuredOutput(ContactInfo);
const response = await structuredModel.invoke("Extract: John, john@example.com, 555-1234");
```

---

## Model Configuration

`create_agent` accepts a model string or a model instance (for custom settings):

```python
from langchain_anthropic import ChatAnthropic
agent = create_agent(model=ChatAnthropic(model="claude-sonnet-4-5", temperature=0), tools=[...])
```

---

## Common Mistakes and Fixes

### ❌ Missing or Vague Tool Description
```python
# Wrong: vague description
@tool
def bad_tool(input: str) -> str:
    """Does stuff."""
    return "result"

# Correct: clear, specific description with Args
@tool
def search(query: str) -> str:
    """Search the web for current information about a topic.

    Use this when you need recent data or facts.

    Args:
        query: The search query (2-10 words recommended)
    """
    return web_search(query)
```

### ❌ No Checkpointer (Agent Doesn't Remember)
```python
# Wrong: no persistence
agent = create_agent(model="anthropic:claude-sonnet-4-5", tools=[search])
agent.invoke({"messages": [{"role": "user", "content": "I'm Bob"}]})
agent.invoke({"messages": [{"role": "user", "content": "What's my name?"}]})
# Agent doesn't remember!

# Correct: add Checkpointer and thread_id
agent = create_agent(
    model="anthropic:claude-sonnet-4-5",
    tools=[search],
    checkpointer=MemorySaver(),
)
config = {"configurable": {"thread_id": "session-1"}}
agent.invoke({...}, config=config)
```

### ❌ Infinite Loop (Missing Iteration Limit)
```python
# Wrong: may loop infinitely
result = agent.invoke({"messages": [("user", "Do research")]})

# Correct: set recursion_limit in config
result = agent.invoke(
    {"messages": [("user", "Do research")]},
    config={"recursion_limit": 10},
)
```

### ❌ Incorrectly Accessing Results
```python
# Wrong: accessing result.content directly
result = agent.invoke({"messages": [{"role": "user", "content": "Hello"}]})
print(result.content)  # AttributeError!

# Correct: access messages from result dict
print(result["messages"][-1].content)  # Last message content
```
