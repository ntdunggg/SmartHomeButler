# Advanced Features Reference

This file covers Memory Management, Anthropic-specific features (Extended Thinking, Server Tools, Prompt Caching, MCP), Streaming, Testing, and Performance Optimization.

---

## Memory Management

### Short-Term Memory (Checkpointers)

```python
from langgraph.checkpoint.memory import MemorySaver  # dev
# from langgraph.checkpoint.postgres import PostgresSaver  # production
from langgraph.prebuilt import create_react_agent

checkpointer = MemorySaver()
agent = create_react_agent(llm, tools, checkpointer=checkpointer)

config = {"configurable": {"thread_id": "session-abc123"}}
result1 = await agent.ainvoke({"messages": [("user", "My name is Alice")]}, config)
result2 = await agent.ainvoke({"messages": [("user", "What's my name?")]}, config)
# Agent remembers: "Your name is Alice"
```

### Production Memory with PostgreSQL

```python
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string(
    "postgresql://user:pass@localhost/langgraph"
)
agent = create_react_agent(llm, tools, checkpointer=checkpointer)
```

### Long-Term Memory Store (InMemoryStore with Semantic Search)

```python
from langgraph.store.memory import InMemoryStore

def embed(texts: list[str]) -> list[list[float]]:
    """Provide your embedding function here."""
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings().embed_documents(texts)

# Index config enables semantic search (use PostgresStore in production)
store = InMemoryStore(index={"embed": embed, "dims": 1536})

user_id = "my-user"
namespace = (user_id, "preferences")

# Store a memory item
store.put(namespace, "language-pref", {
    "rules": ["User likes short, direct language", "User only speaks English & Python"],
    "updated": "2026-03-16"
})

# Retrieve by key
item = store.get(namespace, "language-pref")
print(item.value)

# Semantic search within namespace — returns most relevant items
results = store.search(
    namespace,
    query="what language does the user prefer?",
    filter={"updated": "2026-03-16"},  # optional metadata filter
    limit=5
)
for r in results:
    print(r.value)
```

### Three Types of Long-Term Memory

| Type | What it stores | Example Namespace |
|---|---|---|
| **Semantic** | Facts about users / world | `(user_id, "facts")` |
| **Episodic** | Past experience / few-shot examples | `(user_id, "examples")` |
| **Procedural** | Agent instructions / rules | `("agent_instructions",)` |

---

## Anthropic Extended Thinking

Extended thinking makes Claude reason step-by-step internally before responding. Best for complex planning, math, code, and multi-step reasoning.

### Basic Extended Thinking

```python
from langchain_anthropic import ChatAnthropic

# Sonnet 4.6 will use interleaved thinking automatically when `thinking` is set
llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    thinking={"type": "enabled", "budget_tokens": 8000}
    # For claude-opus-4-6, use: thinking={"type": "adaptive"}
)

response = llm.invoke("Solve this logic puzzle step by step: ...")
# response.content contains both thinking blocks and text blocks

# Access individual content blocks
for block in response.content:
    if block.type == "thinking":
        print("Thinking:", block.thinking[:200], "...")
    elif block.type == "text":
        print("Answer:", block.text)
```

### Extended Thinking with Tools (LangGraph Agent)

```python
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

@tool
def run_calculation(expression: str) -> str:
    """Safely evaluate a mathematical expression."""
    import ast, operator as op
    # ... safe eval ...
    return "Result: ..."

# Extended thinking interleaves reasoning with tool calls
llm_with_thinking = ChatAnthropic(
    model="claude-sonnet-4-6",
    thinking={"type": "enabled", "budget_tokens": 10000}
)

agent = create_react_agent(
    llm_with_thinking,
    tools=[run_calculation],
    checkpointer=MemorySaver()
)

result = await agent.ainvoke(
    {"messages": [("user", "Solve this complex optimization problem: ...")]},
    config={"configurable": {"thread_id": "thinking-agent-1"}}
)
```

**Key model rules:**
- `claude-opus-4-6`: Use `thinking={"type": "adaptive"}` (manual `budget_tokens` mode is deprecated)
- `claude-sonnet-4-6`: Supports both `enabled` (with `budget_tokens`) and `adaptive`; also supports `interleaved` thinking with tools
- `budget_tokens` must be less than `max_tokens`
- Claude Opus 4.6 supports up to **128k output tokens**

---

## Anthropic Prompt Caching

Dramatically reduces cost (90% cheaper reads) and latency for repeated large contexts. Cache TTL is 5 minutes by default (1-hour available).

### Automatic Caching (Simplest)

```python
from langchain_anthropic import ChatAnthropic

# Add cache_control at the top level — system automatically caches last cacheable block
llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    model_kwargs={"cache_control": {"type": "ephemeral"}}
)
```

### Explicit Cache Breakpoints

Use this when you want to cache a large system prompt or document context:

```python
import anthropic

client = anthropic.Anthropic()

# Cache a large system prompt + first user turn
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "You are an AI assistant for a complex legal document system. " * 500,
            "cache_control": {"type": "ephemeral"}  # ← Cache breakpoint here
        }
    ],
    messages=[{"role": "user", "content": "Summarize the key clauses."}]
)

# Check cache performance
print(response.usage.cache_read_input_tokens)   # tokens read from cache
print(response.usage.cache_creation_input_tokens)  # tokens written to cache
```

### Prompt Caching in LangGraph (RAG with large context)

```python
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

# Use cache_control on the system message with a large document context
llm = ChatAnthropic(model="claude-sonnet-4-6")

large_document = "..." * 5000  # Large document context

messages = [
    SystemMessage(
        content=[
            {
                "type": "text",
                "text": f"You are an expert. Reference document:\n{large_document}",
                "cache_control": {"type": "ephemeral"}
            }
        ]
    ),
    HumanMessage(content="What does section 3.2 say about liability?")
]

response = llm.invoke(messages)
```

**Pricing multipliers:**
- Cache write (5-min): **1.25×** base input price
- Cache write (1-hour): **2×** base input price
- Cache read: **0.1×** base input price (90% savings!)

---

## Anthropic Server Tools

Server tools execute on Anthropic's servers — no client implementation needed. Just declare them in the API request.

### Web Search Tool

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=[{
        "type": "web_search_20250305",  # versioned tool type
        "name": "web_search",
        "max_uses": 5  # limit searches per request
    }],
    messages=[{"role": "user", "content": "What are the latest LangGraph features in 2026?"}]
)

# Claude automatically searches, incorporates results, and cites sources
print(response.content)
```

### Web Search in LangGraph Agent (via `langchain-anthropic`)

```python
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

# Pass server tools via model_kwargs
llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    model_kwargs={
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    }
)

# Note: for full LangGraph integration, use client tools (@tool) alongside
# server tools, or handle server-tool responses in a custom node.
```

**Important**: Web search requires your organization admin to enable it in the Claude Console.

---

## Anthropic MCP Connector

Connect to remote MCP servers directly from the Messages API — no MCP client implementation needed.

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    # MCP server connection details
    mcp_servers=[{
        "type": "url",
        "url": "https://your-mcp-server.example.com/mcp",
        "name": "my-mcp-server",
        # Optional OAuth token
        # "authorization_token": "Bearer your-token"
    }],
    tools=[{
        "type": "mcp",
        "name": "my-mcp-server",  # matches mcp_servers[].name
        # Enable all tools:
        "tool_configuration": {"enabled": True}
        # Or allowlist specific tools:
        # "tool_configuration": {"allowed_tools": ["tool_a", "tool_b"]}
        # Or denylist:
        # "tool_configuration": {"excluded_tools": ["dangerous_tool"]}
    }],
    messages=[{"role": "user", "content": "Use the MCP tools to analyze this data."}]
)
```

**Key limitations:**
- Only **tool calls** from MCP are currently supported (not resources/prompts)
- MCP server must be **publicly exposed via HTTP** (no local STDIO)
- Not supported on Amazon Bedrock or Google Vertex

---

## Callback System & LangSmith

### Enable LangSmith Tracing

```python
import os

# Set environment variables (or put in .env)
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "your-ls-api-key"
os.environ["LANGSMITH_PROJECT"] = "my-agent-project"

# All LangChain/LangGraph operations are automatically traced from this point
from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-sonnet-4-6")
```

### Custom Callback Handler

```python
from langchain_core.callbacks import BaseCallbackHandler
from typing import Any, Dict, List

class CustomCallbackHandler(BaseCallbackHandler):
    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs) -> None:
        print(f"LLM started with {len(prompts)} prompts")

    def on_llm_end(self, response, **kwargs) -> None:
        usage = getattr(response, 'llm_output', {}).get('token_usage', {})
        print(f"LLM completed. Tokens used: {usage}")

    def on_llm_error(self, error: Exception, **kwargs) -> None:
        print(f"LLM error: {error}")

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs) -> None:
        print(f"Tool started: {serialized.get('name')}")

    def on_tool_end(self, output: str, **kwargs) -> None:
        print(f"Tool completed: {output[:100]}...")

result = await agent.ainvoke(
    {"messages": [("user", "query")]},
    config={"callbacks": [CustomCallbackHandler()]}
)
```

---

## Streaming Responses

### Stream LLM Tokens

```python
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(model="claude-sonnet-4-6")

async for chunk in llm.astream("Tell me a story about AI agents"):
    print(chunk.content, end="", flush=True)
```

### Stream Agent Events (v2 — preferred)

```python
# astream_events with version="v2" provides richer, structured events
async for event in agent.astream_events(
    {"messages": [("user", "Search for Python tutorials")]},
    version="v2"
):
    event_type = event["event"]

    if event_type == "on_chat_model_stream":
        content = event["data"]["chunk"].content
        if content:
            print(content, end="", flush=True)

    elif event_type == "on_tool_start":
        print(f"\n[🔧 Using tool: {event['name']}]")

    elif event_type == "on_tool_end":
        print(f"[✅ Tool done: {event['name']}]")
```

### Stream Graph State Updates

```python
# Stream intermediate state from a StateGraph
async for chunk in app.astream(
    {"messages": [("user", "Analyze market trends")]},
    config={"configurable": {"thread_id": "stream-123"}}
):
    for node_name, state_update in chunk.items():
        print(f"[{node_name}] → {list(state_update.keys())}")
```

### HITL with v2 Interrupt Streaming

```python
from langgraph.types import Command

config = {"configurable": {"thread_id": "hitl-stream-1"}}

# Initial stream — pauses at interrupt
async for chunk in graph.astream({"input": "do something"}, config=config, version="v2"):
    if "__interrupt__" in chunk:
        interrupt_payload = chunk["__interrupt__"]
        print("Graph paused! Payload:", interrupt_payload)
        # Show to user, collect decision...

# Resume with human decision
async for chunk in graph.astream(Command(resume=True), config=config, version="v2"):
    print("Resumed:", chunk)
```

---

## Testing Strategies

### Unit Testing Nodes in Isolation

```python
import pytest

def test_classifier_routes_coding():
    """Test query classifier returns correct route."""
    from src.nodes.classifier import classifier_node

    state = {"query": "write me a Python function", "query_type": "", "result": ""}
    result = classifier_node(state)
    assert result["query_type"] == "coding"

def test_classifier_routes_search():
    state = {"query": "search for the latest news", "query_type": "", "result": ""}
    result = classifier_node(state)
    assert result["query_type"] == "search"
```

### Integration Testing with Mocked LLM

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from langchain_core.messages import AIMessage, ToolCall

@pytest.mark.asyncio
async def test_agent_uses_search_tool():
    """Test agent routes to the correct tool."""
    mock_tool_call = AIMessage(
        content="",
        tool_calls=[ToolCall(name="search_database", args={"query": "documents"}, id="1")]
    )

    with patch.object(llm, "ainvoke", return_value=mock_tool_call):
        result = await agent.ainvoke({
            "messages": [("user", "search for documents")]
        })
        # Verify the tool was called
        messages = result["messages"]
        tool_calls = [m for m in messages if hasattr(m, "tool_calls") and m.tool_calls]
        assert any(tc["name"] == "search_database" for m in tool_calls for tc in m.tool_calls)

@pytest.mark.asyncio
async def test_memory_persistence():
    """Test short-term memory persists across invocations."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import create_react_agent
    from langchain_anthropic import ChatAnthropic

    agent = create_react_agent(ChatAnthropic(model="claude-sonnet-4-6"), tools=[],
                               checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-thread-persist"}}

    await agent.ainvoke({"messages": [("user", "Remember: the code is 12345")]}, config)
    result = await agent.ainvoke({"messages": [("user", "What was the code?")]}, config)
    assert "12345" in result["messages"][-1].content

@pytest.mark.asyncio
async def test_interrupt_pauses_graph():
    """Test that interrupt() halts graph execution at the right point."""
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt, Command
    from langgraph.checkpoint.memory import MemorySaver
    from typing import TypedDict

    class S(TypedDict):
        value: str

    def my_node(state: S) -> dict:
        answer = interrupt("Confirm?")
        return {"value": f"confirmed={answer}"}

    g = StateGraph(S)
    g.add_node("n", my_node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    graph = g.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "interrupt-test-1"}}
    initial = graph.invoke({"value": ""}, config)
    assert "__interrupt__" in initial  # graph paused

    resumed = graph.invoke(Command(resume=True), config)
    assert resumed["value"] == "confirmed=True"
```

---

## Performance Optimization

### 1. LLM Response Caching with Redis

```python
from langchain_community.cache import RedisCache
from langchain_core.globals import set_llm_cache
import redis

redis_client = redis.Redis.from_url("redis://localhost:6379")
set_llm_cache(RedisCache(redis_client))
# Identical prompts return cached responses without calling the API
```

### 2. Async Batch Processing

```python
import asyncio
from langchain_core.documents import Document

async def process_documents(documents: list[Document]) -> list:
    """Process all documents in parallel."""
    tasks = [process_single(doc) for doc in documents]
    return await asyncio.gather(*tasks)

async def process_single(doc: Document) -> dict:
    chunks = text_splitter.split_documents([doc])
    embeddings_result = await embeddings_model.aembed_documents(
        [c.page_content for c in chunks]
    )
    return {"doc_id": doc.metadata.get("id"), "embeddings": embeddings_result}
```

### 3. Parallel Tasks in Functional API

```python
from langgraph.func import entrypoint, task
from langgraph.checkpoint.memory import InMemorySaver
import asyncio

@task
async def fetch_data(source: str) -> str:
    """Each @task can run concurrently."""
    await asyncio.sleep(0.5)  # simulated I/O
    return f"Data from {source}"

@entrypoint(checkpointer=InMemorySaver())
async def parallel_workflow(sources: list[str]) -> dict:
    """Fan out to multiple tasks simultaneously."""
    futures = [fetch_data(s) for s in sources]
    results = [await f for f in futures]  # or use asyncio.gather on futures
    return {"results": results}
```

### 4. Connection Pooling for Vector Stores

```python
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone

# Reuse the Pinecone client across requests (avoids re-auth overhead)
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
index = pc.Index("my-index")
vectorstore = PineconeVectorStore(index=index, embedding=embeddings)
```

### 5. Token Budget Management with Extended Thinking

```python
from langchain_anthropic import ChatAnthropic

# For simpler tasks: smaller budget = faster + cheaper
llm_fast = ChatAnthropic(
    model="claude-sonnet-4-6",
    thinking={"type": "enabled", "budget_tokens": 1024},  # minimal thinking
    max_tokens=2048
)

# For complex reasoning: larger budget = higher quality
llm_deep = ChatAnthropic(
    model="claude-sonnet-4-6",
    thinking={"type": "enabled", "budget_tokens": 32000},
    max_tokens=64000
)
```

### 6. Durability Mode Selection

```python
# For long-running, batch-style graphs where crashes are unlikely:
result = graph.invoke(input, config, durability="exit")

# Balanced (default for most production cases):
result = graph.invoke(input, config, durability="async")

# For critical workflows where every checkpoint matters:
result = graph.invoke(input, config, durability="sync")
```

---

## LangSmith Observability (Production)

LangSmith is the standard for debugging, evaluating, and monitoring LangGraph agents in production.

```python
import os
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "ls__..."
os.environ["LANGSMITH_PROJECT"] = "my-production-agent"

# What LangSmith captures automatically:
# - Every LLM call (inputs, outputs, latency, token usage)
# - Every tool invocation (args, results, errors)
# - Full graph execution traces (node-by-node)
# - Interrupt/resume events for HITL flows
# - Checkpointer state snapshots

# Add custom metadata to traces
from langchain_core.tracers.context import tracing_v2_enabled

with tracing_v2_enabled(project_name="experiment-001", tags=["v2", "production"]):
    result = await agent.ainvoke(
        {"messages": [("user", "Complex user query")]},
        config={
            "configurable": {"thread_id": "user-abc"},
            "metadata": {"user_tier": "premium", "region": "us-east"}
        }
    )
```

**Key LangSmith capabilities:**
- **Trace visualization**: See the full execution DAG with timing
- **Evaluation**: Compare agent versions on benchmark datasets
- **Monitoring**: Set alerts on error rates, latency, token costs
- **Deployment (LangSmith Deployment)**: Host your `langgraph.json` graphs as scalable services
- **Agent Studio**: Visual prototyping and HITL testing UI