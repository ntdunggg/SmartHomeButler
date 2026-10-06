# deep-agents-memory

> Consult this file when a Deep Agent needs memory, persistence, or filesystem access. Covers StateBackend (ephemeral), StoreBackend (persistent), FilesystemMiddleware, and CompositeBackend for routing.

## Backend Selection

| Use Case | Backend | Reason |
|---------|---------|--------|
| Ephemeral working files | StateBackend | Default, no configuration needed |
| Local development CLI | FilesystemBackend | Direct disk access |
| Cross-session memory | StoreBackend | Persistent across threads |
| Mixed storage | CompositeBackend | Combine ephemeral + persistent |

`FilesystemMiddleware` provides tools: `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`

---

## Default StateBackend (Ephemeral)

```python
from deepagents import create_deep_agent

agent = create_deep_agent()  # Default: StateBackend
result = agent.invoke({
    "messages": [{"role": "user", "content": "Write notes to /draft.txt"}]
}, config={"configurable": {"thread_id": "thread-1"}})
# /draft.txt is lost when the thread ends
```

---

## CompositeBackend (Mixed Storage)

Routes files to different backends by path prefix:

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()

composite_backend = lambda rt: CompositeBackend(
    default=StateBackend(rt),
    routes={"/memories/": StoreBackend(rt)}
)

agent = create_deep_agent(backend=composite_backend, store=store)

# /draft.txt -> ephemeral (StateBackend)
# /memories/user-prefs.txt -> persistent (StoreBackend)
```

---

## Cross-Session Memory

```python
# Files in /memories/ persist across threads via StoreBackend
config1 = {"configurable": {"thread_id": "thread-1"}}
agent.invoke({"messages": [{"role": "user", "content": "Save to /memories/style.txt"}]}, config=config1)

config2 = {"configurable": {"thread_id": "thread-2"}}
agent.invoke({"messages": [{"role": "user", "content": "Read /memories/style.txt"}]}, config=config2)
# Thread 2 can read the file saved by thread 1
```

---

## FilesystemBackend (Local Development)

```python
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

agent = create_deep_agent(
    backend=FilesystemBackend(root_dir=".", virtual_mode=True),  # Restrict access
    interrupt_on={"write_file": True, "edit_file": True},
    checkpointer=MemorySaver()
)
# Agent can read and write actual files on disk
```

**⚠️ Security Warning: Never use FilesystemBackend in a web server — use StateBackend or a sandbox instead.**

---

## Using Store in Custom Tools

```python
from langchain.tools import tool, ToolRuntime
from langchain.agents import create_agent
from langgraph.store.memory import InMemoryStore

@tool
def get_user_preference(key: str, runtime: ToolRuntime) -> str:
    """Get a user preference from long-term storage."""
    store = runtime.store
    result = store.get(("user_prefs",), key)
    return str(result.value) if result else "Not found"

@tool
def save_user_preference(key: str, value: str, runtime: ToolRuntime) -> str:
    """Save a user preference to long-term storage."""
    store = runtime.store
    store.put(("user_prefs",), key, {"value": value})
    return f"Saved {key}={value}"

store = InMemoryStore()
agent = create_agent(model="gpt-4.1", tools=[get_user_preference, save_user_preference], store=store)
```

---

## Common Mistakes and Fixes

### ❌ StoreBackend Without Store Instance
```python
# Wrong
agent = create_deep_agent(backend=lambda rt: StoreBackend(rt))

# Correct
agent = create_deep_agent(backend=lambda rt: StoreBackend(rt), store=InMemoryStore())
```

### ❌ StateBackend Files Don't Persist Across Threads
```python
# Wrong: thread-2 cannot read files from thread-1
agent.invoke({...}, config={"configurable": {"thread_id": "thread-1"}})  # write
agent.invoke({...}, config={"configurable": {"thread_id": "thread-2"}})  # file not found!

# Correct: use CompositeBackend with StoreBackend routing
```

### ❌ Path Prefix Mismatch
```python
# With routes={"/memories/": StoreBackend(rt)}:
agent.invoke(...)  # /prefs.txt -> ephemeral (no match)
agent.invoke(...)  # /memories/prefs.txt -> persistent (matches route)
```

### ❌ Using InMemoryStore in Production
```python
# Wrong: data lost on restart
store = InMemoryStore()

# Correct: use PostgresStore for production
store = PostgresStore(connection_string="postgresql://...")
```

### ❌ FilesystemBackend Without virtual_mode
```python
# Correct: enable virtual_mode=True to prevent ../ and ~/ path escapes
backend = FilesystemBackend(root_dir="/project", virtual_mode=True)
```

> **CompositeBackend uses longest-prefix-first matching:**
> `routes={"/mem/": StoreBackend(rt), "/mem/temp/": StateBackend(rt)}`
> - `/mem/file.txt` → StoreBackend; `/mem/temp/file.txt` → StateBackend (longer prefix matches)
