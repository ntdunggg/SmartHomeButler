# deep-agents-core

> Consult this file when building **any** Deep Agents application. Covers `create_deep_agent()`, Harness architecture, SKILL.md format, and configuration options.

## Table of Contents
- [Overview](#overview)
- [When to Use Deep Agents](#when-to-use-deep-agents)
- [Middleware Selection](#middleware-selection)
- [Basic Example](#basic-example)
- [Full Configuration](#full-configuration)
- [Built-in Tools](#built-in-tools)
- [SKILL.md Format](#skillmd-format)
- [Skills vs Memory](#skills-vs-memory)
- [Configuration Boundaries](#configuration-boundaries)
- [Common Mistakes and Fixes](#common-mistakes-and-fixes)

---

## Overview

Deep Agents is an opinionated agent framework built on top of LangChain/LangGraph with built-in middleware:

- **Task Planning**: TodoListMiddleware breaks down complex tasks
- **Context Management**: Filesystem tools with pluggable backends
- **Task Delegation**: SubAgent middleware for spawning specialized agents
- **Long-term Memory**: Persistent cross-thread storage via Store
- **Human-in-the-loop**: Approval workflows for sensitive operations
- **Skills**: On-demand loading of specialized capabilities

The Agent Harness provides these capabilities automatically — you just configure, no need to implement.

---

## When to Use Deep Agents

| Use Deep Agents When | Use LangChain's create_agent When |
|-------------------|----------------------------------|
| Multi-step tasks requiring planning | Simple single-purpose tasks |
| Large contexts requiring file management | Context fits in a single prompt |
| Need specialized sub-agents | A single agent is sufficient |
| Persistent memory across sessions | Temporary single-session work |

---

## Middleware Selection

| Need… | Middleware | Notes |
|--------|--------|------|
| Track complex tasks | TodoListMiddleware | Enabled by default |
| Manage file context | FilesystemMiddleware | Configure backend |
| Delegate work | SubAgentMiddleware | Add custom sub-agents |
| Add human approval | HumanInTheLoopMiddleware | Requires Checkpointer |
| Load Skills | SkillsMiddleware | Provide skills directory |
| Access memory | MemoryMiddleware | Requires Store instance |

---

## Basic Example

**Python:**
```python
from deepagents import create_deep_agent
from langchain.tools import tool

@tool
def get_weather(city: str) -> str:
    """Get the weather for a given city."""
    return f"It is always sunny in {city}"

agent = create_deep_agent(
    model="claude-sonnet-4-5-20250929",
    tools=[get_weather],
    system_prompt="You are a helpful assistant"
)

config = {"configurable": {"thread_id": "user-123"}}
result = agent.invoke({
    "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}]
}, config=config)
```

**TypeScript:**
```typescript
import { createDeepAgent } from "deepagents";
import { tool } from "@langchain/core/tools";
import { z } from "zod";

const getWeather = tool(
  async ({ city }) => `It is always sunny in ${city}`,
  { name: "get_weather", description: "Get weather for a city", schema: z.object({ city: z.string() }) }
);

const agent = await createDeepAgent({
  model: "claude-sonnet-4-5-20250929",
  tools: [getWeather],
  systemPrompt: "You are a helpful assistant"
});

const config = { configurable: { thread_id: "user-123" } };
const result = await agent.invoke({
  messages: [{ role: "user", content: "What's the weather in Tokyo?" }]
}, config);
```

---

## Full Configuration

All options including sub-agents, Skills, and persistence:

**Python:**
```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

agent = create_deep_agent(
    name="my-assistant",
    model="claude-sonnet-4-5-20250929",
    tools=[custom_tool1, custom_tool2],
    system_prompt="Custom instructions",
    subagents=[research_agent, code_agent],
    backend=FilesystemBackend(root_dir=".", virtual_mode=True),
    interrupt_on={"write_file": True},
    skills=["./skills/"],
    checkpointer=MemorySaver(),
    store=InMemoryStore()
)
```

**TypeScript:**
```typescript
import { createDeepAgent, FilesystemBackend } from "deepagents";
import { MemorySaver, InMemoryStore } from "@langchain/langgraph";

const agent = await createDeepAgent({
  name: "my-assistant",
  model: "claude-sonnet-4-5-20250929",
  tools: [customTool1, customTool2],
  systemPrompt: "Custom instructions",
  subagents: [researchAgent, codeAgent],
  backend: new FilesystemBackend({ rootDir: ".", virtualMode: true }),
  interruptOn: { write_file: true },
  skills: ["./skills/"],
  checkpointer: new MemorySaver(),
  store: new InMemoryStore()
});
```

---

## Built-in Tools

Every Deep Agent has access to:

1. **Planning**: `write_todos` — track multi-step tasks
2. **Filesystem**: `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
3. **Delegation**: `task` — spawn specialized sub-agents

---

## SKILL.md Format

Skills use **progressive disclosure** — the agent only loads content when relevant.

### Directory Structure
```
skills/
└── my-skill/
    ├── SKILL.md        # Required: main skill file
    ├── examples.py     # Optional: supporting files
    └── templates/      # Optional: templates
```

### SKILL.md Format
```markdown
---
name: my-skill
description: Clear, specific description of what this skill does
---

# Skill Name

## Overview
Brief explanation of the skill's purpose.

## When to Use
Conditions under which this skill applies.

## Instructions
Step-by-step guidance for the agent.
```

---

## Skills vs Memory

| Skills | Memory (AGENTS.md) |
|--------|-------------------|
| Loaded on demand | Always loaded at startup |
| Task-specific instructions | General preferences |
| Large documents | Compact context |
| SKILL.md in directories | Single AGENTS.md file |

---

## Configuration Boundaries

**Can configure:**
- Model selection and parameters
- Additional custom tools
- System prompt customization
- Backend storage strategy
- Which tools require approval
- Custom sub-agents with specialized tools

**Cannot configure:**
- Core middleware removal (TodoList, Filesystem, SubAgent are always present)
- write_todos, task, or filesystem tool names
- SKILL.md frontmatter format

---

## Common Mistakes and Fixes

### ❌ Interrupts Without Checkpointer
```python
# Wrong
agent = create_deep_agent(interrupt_on={"write_file": True})

# Correct
agent = create_deep_agent(interrupt_on={"write_file": True}, checkpointer=MemorySaver())
```

### ❌ StoreBackend Without Store
```python
# Wrong
agent = create_deep_agent(backend=lambda rt: StoreBackend(rt))

# Correct
agent = create_deep_agent(backend=lambda rt: StoreBackend(rt), store=InMemoryStore())
```

### ❌ Not Using thread_id for Conversations
```python
# Wrong: each call is independent
agent.invoke({"messages": [{"role": "user", "content": "Hi"}]})
agent.invoke({"messages": [{"role": "user", "content": "What did I say?"}]})

# Correct
config = {"configurable": {"thread_id": "user-123"}}
agent.invoke({"messages": [...]}, config=config)
agent.invoke({"messages": [...]}, config=config)
```

### ❌ SKILL.md Missing Frontmatter
```markdown
<!-- Wrong: missing frontmatter -->
# My Skill
This is my skill...

<!-- Correct: includes YAML frontmatter -->
---
name: my-skill
description: Python testing best practices with pytest fixtures and mocking
---
# My Skill
This is my skill...
```

### ❌ Skills Without Backend
```python
# Wrong: skills cannot load without a proper backend
agent = create_deep_agent(skills=["./skills/"])

# Correct: use FilesystemBackend to load local skills
agent = create_deep_agent(
    backend=FilesystemBackend(root_dir=".", virtual_mode=True),
    skills=["./skills/"]
)
```

### ❌ Vague Skill Description
```markdown
<!-- Wrong: vague description -->
---
name: helper
description: Helpful skill
---

<!-- Correct: specific description -->
---
name: python-testing
description: Python testing best practices with pytest fixtures, mocking, and async patterns
---
```

### ❌ Custom Sub-Agents Don't Inherit Skills
```python
# Wrong: custom sub-agents don't inherit the main agent's skills
agent = create_deep_agent(
    skills=["/main-skills/"],
    subagents=[{"name": "helper", ...}]  # no skills
)

# Correct: explicitly provide skills (generic sub-agents inherit automatically)
agent = create_deep_agent(
    skills=["/main-skills/"],
    subagents=[{"name": "helper", "skills": ["/helper-skills/"], ...}]
)
```
