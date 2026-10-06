---
name: cost-optimization
description: Optimize LLM token costs and latency for AI agents and LLM applications. Use this skill when the user wants to reduce API costs, control token usage, implement model routing (cheap vs expensive models), add token budget guardrails to LangGraph graphs, leverage Anthropic prompt caching, compress context windows, or implement batch processing strategies. Trigger whenever you see concerns about cost, "too expensive", "too slow", token limits, or scaling LLM workloads.
---

# LLM Cost Optimization

**Role**: LLM Cost Engineer

You are an expert in reducing the cost and latency of LLM-powered agents without sacrificing quality. You know that the #1 mistake teams make is defaulting to the most capable model for every task. You use tiered model routing, prompt caching, context compression, and smart batching to cut costs by 60-90% on typical workloads.

## When to Use This Skill

- API bills are growing faster than the product
- Agent is slow due to unnecessarily large context
- Single model used for all tasks regardless of complexity
- No caching for repeated prompts or large documents
- No token budget guardrails in production graphs
- Need to scale to 10x more users without 10x cost increase

## Cost Mental Model

```
Total Cost = Σ (input_tokens × input_price + output_tokens × output_price)
           + Σ cache_write_tokens × write_price     ← one-time investment
           - Σ cache_read_tokens × (input_price - read_price)  ← savings

Key levers:
1. Model choice       — up to 95% savings (Haiku vs Opus)
2. Prompt caching     — up to 90% savings on reads
3. Context reduction  — fewer input tokens = less cost
4. Output control     — structured output = no rambling
5. Batching           — 50% discount on Anthropic Batch API
```

## Anthropic Model Pricing Tiers (2026)

| Model | Input | Output | Cache Write | Cache Read | Best For |
|---|---|---|---|---|---|
| `claude-haiku-4-5` | ~$0.80/M | ~$4/M | ~$1/M | ~$0.08/M | Classification, extraction, routing |
| `claude-sonnet-4-6` | ~$3/M | ~$15/M | ~$3.75/M | ~$0.30/M | Most agent tasks, reasoning |
| `claude-opus-4-6` | ~$15/M | ~$75/M | ~$18.75/M | ~$1.50/M | Complex reasoning, planning only |

> **Rule of thumb**: Route 70% of tasks to Haiku, 25% to Sonnet, 5% to Opus.

---

## Strategy 1: Model Routing (Biggest Lever)

Never use one model for everything. Classify task complexity first, then route.

### Simple Router in LangGraph

```python
from langgraph.graph import StateGraph, START, END
from langchain_anthropic import ChatAnthropic
from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages

# Three-tier model pool
haiku  = ChatAnthropic(model="claude-haiku-4-5-20251001")   # ~20x cheaper than Opus
sonnet = ChatAnthropic(model="claude-sonnet-4-6")            # balanced
opus   = ChatAnthropic(model="claude-opus-4-6")              # max capability

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    complexity: Literal["simple", "medium", "complex"]
    task_type: str

# Classifier uses cheapest model to decide routing
async def classify_complexity(state: AgentState) -> dict:
    """Use Haiku to classify — never use Opus to decide which model to use!"""
    prompt = f"""Classify this task complexity. Reply with ONLY one word: simple, medium, or complex.

simple = factual lookup, yes/no, extraction, formatting, classification
medium = multi-step reasoning, code generation, summarization, analysis  
complex = novel research, strategic planning, advanced math, multi-constraint optimization

Task: {state['messages'][-1].content}"""

    response = await haiku.ainvoke(prompt)
    complexity = response.content.strip().lower()
    if complexity not in ("simple", "medium", "complex"):
        complexity = "medium"  # safe default
    return {"complexity": complexity}

async def route_to_model(state: AgentState) -> dict:
    """Execute with the appropriate model tier."""
    model_map = {"simple": haiku, "medium": sonnet, "complex": opus}
    model = model_map[state["complexity"]]
    response = await model.ainvoke(state["messages"])
    return {"messages": [response]}

def complexity_router(state: AgentState) -> str:
    """All tasks go through route_to_model — routing happens inside the node."""
    return "execute"

graph = StateGraph(AgentState)
graph.add_node("classify", classify_complexity)
graph.add_node("execute", route_to_model)
graph.add_edge(START, "classify")
graph.add_edge("classify", "execute")
graph.add_edge("execute", END)

app = graph.compile()
```

### Fine-Grained Task-Type Router

```python
from typing import Literal

TaskType = Literal[
    "extract",      # Pull structured info from text → Haiku
    "classify",     # Label or categorize → Haiku
    "summarize",    # Condense document → Haiku/Sonnet by length
    "analyze",      # Identify patterns, insights → Sonnet
    "generate",     # Write content, code → Sonnet
    "plan",         # Multi-step strategy → Opus
    "reason",       # Complex logic, math → Opus
]

MODEL_FOR_TASK: dict[TaskType, ChatAnthropic] = {
    "extract":   haiku,
    "classify":  haiku,
    "summarize": haiku,    # override to sonnet for docs > 50k tokens
    "analyze":   sonnet,
    "generate":  sonnet,
    "plan":      opus,
    "reason":    opus,
}

def get_model_for_task(task_type: TaskType, context_tokens: int = 0) -> ChatAnthropic:
    model = MODEL_FOR_TASK[task_type]
    # Upgrade summarization for large docs (Haiku may struggle)
    if task_type == "summarize" and context_tokens > 50_000:
        model = sonnet
    return model
```

---

## Strategy 2: Anthropic Prompt Caching (90% Read Savings)

Cache any content that repeats across requests: system prompts, tool definitions, large documents.

### Cache a Large System Prompt

```python
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

llm = ChatAnthropic(model="claude-sonnet-4-6")

# Large, stable system prompt — cache it!
SYSTEM_PROMPT = """You are an expert legal document analyst with 20 years of experience...
[... 2000 tokens of instructions ...]
""" * 50  # simulate large prompt

def make_cached_messages(user_query: str) -> list:
    return [
        SystemMessage(content=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}  # ← cache this block
        }]),
        HumanMessage(content=user_query)
    ]

# First call: cache WRITE (1.25× cost) — pays for itself after 1 reuse
response1 = llm.invoke(make_cached_messages("What does clause 3.2 say?"))

# Subsequent calls: cache READ (0.1× cost = 90% savings!)
response2 = llm.invoke(make_cached_messages("What are the termination conditions?"))
response3 = llm.invoke(make_cached_messages("List all party obligations."))
# response2 and response3 charged at 10% of normal input price for the system prompt
```

### Cache RAG Context (Multi-Breakpoint Strategy)

```python
from langchain_core.messages import SystemMessage, HumanMessage

def build_rag_messages_with_caching(
    system_prompt: str,
    documents: str,         # Large retrieved context
    conversation_history: list,  # Previous turns
    user_query: str
) -> list:
    """
    Three-level cache strategy:
    1. System prompt (stable, always cached)
    2. Documents (changes per-query batch, cached for the batch)
    3. Conversation history is NOT cached (changes every turn)
    """
    return [
        SystemMessage(content=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"}  # Cache block 1
        }]),
        # Inject documents as a human turn with cache breakpoint
        HumanMessage(content=[{
            "type": "text",
            "text": f"Reference documents:\n\n{documents}",
            "cache_control": {"type": "ephemeral"}  # Cache block 2
        }]),
        # Conversation history (dynamic, no cache)
        *conversation_history,
        HumanMessage(content=user_query)
    ]
```

### Track Cache Performance

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": "Analyze this contract."}]
)

usage = response.usage
print(f"Input tokens:          {usage.input_tokens}")
print(f"Cache write tokens:    {usage.cache_creation_input_tokens}")  # first call
print(f"Cache read tokens:     {usage.cache_read_input_tokens}")      # subsequent calls
print(f"Output tokens:         {usage.output_tokens}")

# Effective savings calculation
saved_tokens = usage.cache_read_input_tokens
normal_price_per_token = 3 / 1_000_000   # Sonnet input price
cache_read_price       = 0.3 / 1_000_000  # 10× cheaper
savings = saved_tokens * (normal_price_per_token - cache_read_price)
print(f"Saved on this call:    ${savings:.4f}")
```

---

## Strategy 3: Token Budget Node in LangGraph

Add a token guardian node that monitors cumulative spending and can halt or downgrade the agent.

```python
from typing import TypedDict, Annotated, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage
import tiktoken

class BudgetedAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    total_tokens_used: int
    token_budget: int          # max tokens for this session
    budget_exceeded: bool
    model_tier: str            # current model tier: "economy" | "standard" | "premium"

def count_tokens(text: str, model: str = "claude-sonnet-4-6") -> int:
    """Approximate token count (use tiktoken as proxy for Claude)."""
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))

def token_budget_guardian(state: BudgetedAgentState) -> dict:
    """
    Check token usage after each step. Can:
    1. Downgrade model tier if spending too fast
    2. Halt gracefully if budget exceeded
    3. Emit warning if approaching limit
    """
    used = state.get("total_tokens_used", 0)
    budget = state.get("token_budget", 50_000)
    pct_used = used / budget

    if pct_used >= 1.0:
        # Hard stop — return early exit signal
        return {
            "budget_exceeded": True,
            "messages": [AIMessage(
                content="I've reached the token budget for this session. "
                        "Please start a new conversation or increase the budget."
            )]
        }
    elif pct_used >= 0.8:
        # Downgrade to economy mode
        return {"model_tier": "economy", "budget_exceeded": False}
    elif pct_used >= 0.5:
        # Switch to standard
        return {"model_tier": "standard", "budget_exceeded": False}
    else:
        return {"model_tier": "premium", "budget_exceeded": False}

def track_usage(state: BudgetedAgentState) -> dict:
    """Count tokens in the latest message and accumulate."""
    if not state["messages"]:
        return {}
    last_msg = state["messages"][-1]
    content = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)
    new_tokens = count_tokens(content)
    return {"total_tokens_used": state.get("total_tokens_used", 0) + new_tokens}

def budget_exceeded_router(state: BudgetedAgentState) -> str:
    return END if state.get("budget_exceeded") else "agent"

def tiered_agent(state: BudgetedAgentState) -> dict:
    """Use different models based on current budget tier."""
    tier_models = {
        "economy":  ChatAnthropic(model="claude-haiku-4-5-20251001"),
        "standard": ChatAnthropic(model="claude-sonnet-4-6"),
        "premium":  ChatAnthropic(model="claude-opus-4-6"),
    }
    tier = state.get("model_tier", "standard")
    model = tier_models[tier]
    response = model.invoke(state["messages"])
    return {"messages": [response]}

# Wire it all together
builder = StateGraph(BudgetedAgentState)
builder.add_node("track",    track_usage)
builder.add_node("guardian", token_budget_guardian)
builder.add_node("agent",    tiered_agent)
builder.add_edge(START, "track")
builder.add_edge("track", "guardian")
builder.add_conditional_edges("guardian", budget_exceeded_router, {"agent": "agent", END: END})
builder.add_edge("agent", "track")  # loop back through tracker

app = builder.compile()

# Example invocation with a 20k token budget
result = app.invoke({
    "messages": [("user", "Help me analyze this dataset")],
    "total_tokens_used": 0,
    "token_budget": 20_000,
    "budget_exceeded": False,
    "model_tier": "premium"
})
```

---

## Strategy 4: Context Compression

Don't send the whole conversation — trim, summarize, or compress it.

### Rolling Summary Pattern

```python
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

haiku = ChatAnthropic(model="claude-haiku-4-5-20251001")

async def compress_history(messages: list, keep_last_n: int = 4) -> list:
    """
    Summarize old messages, keep recent ones verbatim.
    Reduces token usage by 60-80% for long conversations.
    """
    if len(messages) <= keep_last_n + 1:
        return messages  # Nothing to compress yet

    # Separate old from recent
    old_messages = messages[:-keep_last_n]
    recent_messages = messages[-keep_last_n:]

    # Summarize old context with cheapest model
    history_text = "\n".join(
        f"{m.type.upper()}: {m.content}" for m in old_messages
        if hasattr(m, "content") and isinstance(m.content, str)
    )
    summary_response = await haiku.ainvoke(
        f"Summarize this conversation history in 3-5 bullet points. "
        f"Preserve key facts, decisions, and context:\n\n{history_text}"
    )
    summary = summary_response.content

    # Replace old messages with a single summary message
    summary_msg = SystemMessage(content=f"[Earlier conversation summary]\n{summary}")
    return [summary_msg] + recent_messages

# Use in your LangGraph node
async def agent_with_compression(state: dict) -> dict:
    compressed = await compress_history(state["messages"], keep_last_n=6)
    response = await sonnet.ainvoke(compressed)
    return {"messages": [response]}
```

### LLMLingua Context Compression (Advanced)

```python
# pip install llmlingua
from llmlingua import PromptCompressor

compressor = PromptCompressor(
    model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
    use_llmlingua2=True,
    device_map="cpu"
)

def compress_retrieved_docs(documents: str, target_ratio: float = 0.3) -> str:
    """
    Compress RAG retrieved documents to 30% of original length.
    Preserves semantic meaning while drastically cutting tokens.
    """
    result = compressor.compress_prompt(
        documents,
        rate=target_ratio,       # Keep 30% of tokens
        force_tokens=["\n", "."] # Always keep sentence boundaries
    )
    return result["compressed_prompt"]

# Before: 10,000 tokens of retrieved docs
# After:  ~3,000 tokens — 70% cost reduction on RAG inputs
```

### Trim Tool Results

```python
def trim_tool_result(result: str, max_chars: int = 2000) -> str:
    """
    Tool results are the #1 source of unexpected token bloat.
    Truncate and signal to the agent that more is available.
    """
    if len(result) <= max_chars:
        return result
    return result[:max_chars] + f"\n\n[... {len(result) - max_chars} chars truncated. Ask for more if needed.]"

# Apply to all ToolNode outputs via custom tool wrapper
from langchain_core.tools import tool

@tool
def search_database(query: str) -> str:
    """Search internal database. Returns top results."""
    raw_result = _do_actual_search(query)  # might return 10,000 chars
    return trim_tool_result(raw_result, max_chars=3000)
```

---

## Strategy 5: Anthropic Batch API (50% Discount)

For offline workloads where you don't need real-time responses.

```python
import anthropic
import json
from pathlib import Path

client = anthropic.Anthropic()

def batch_process_documents(documents: list[dict]) -> str:
    """
    Process documents in batch — 50% cheaper than real-time API.
    Results available within 24 hours.
    Best for: nightly report generation, bulk data extraction, offline analysis.
    """
    # Build batch requests
    requests = []
    for i, doc in enumerate(documents):
        requests.append({
            "custom_id": f"doc-{i}",
            "params": {
                "model": "claude-haiku-4-5-20251001",  # Use cheapest for bulk
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": f"Extract key entities from:\n\n{doc['content']}"
                }]
            }
        })

    # Submit batch
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch submitted: {batch.id}")
    print(f"Estimated cost: ~50% of real-time price")
    return batch.id

def retrieve_batch_results(batch_id: str) -> list[dict]:
    """Poll for results (typically available within 1 hour)."""
    import time

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        print(f"Status: {batch.processing_status} — "
              f"{batch.request_counts.succeeded} done, "
              f"{batch.request_counts.processing} processing")

        if batch.processing_status == "ended":
            break
        time.sleep(60)  # Poll every minute

    # Download results
    results = []
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            results.append({
                "id": result.custom_id,
                "content": result.result.message.content[0].text
            })
    return results
```

---

## Strategy 6: Output Token Control

Unnecessary output tokens are wasted money. Force concise responses.

```python
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

# Strategy A: Explicit max_tokens limits
llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    max_tokens=512   # Never let it ramble more than ~400 words
)

# Strategy B: Structured output eliminates verbose prose
class ExtractionResult(BaseModel):
    entities: list[str] = Field(description="Named entities found (max 10)")
    sentiment: str = Field(description="positive, negative, or neutral")
    key_topic: str = Field(description="Main topic in 5 words or less")

structured_llm = ChatAnthropic(model="claude-haiku-4-5-20251001").with_structured_output(ExtractionResult)
# Structured output uses ~3x fewer output tokens than prose answers

# Strategy C: System prompt output constraints
CONCISE_SYSTEM = """Be extremely concise. 
- Facts only, no preamble
- No "Certainly!" or "Great question!"  
- No repeating what was asked
- Maximum 3 sentences unless code is required
- Use bullet points over paragraphs"""
```

---

## Strategy 7: LangSmith Cost Monitoring

Track actual costs per run to identify expensive nodes.

```python
import os
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "ls__..."
os.environ["LANGSMITH_PROJECT"] = "cost-tracking"

# LangSmith automatically captures:
# - Token usage per LLM call
# - Latency per node
# - Total cost per trace
# Use the LangSmith dashboard → "Cost" column to find expensive nodes

# Add custom cost metadata to traces
config = {
    "configurable": {"thread_id": "user-123"},
    "metadata": {
        "budget_usd": 0.10,      # Track budget per session
        "user_tier": "free",     # Route by user tier
        "expected_tokens": 5000
    }
}
result = await app.ainvoke(user_input, config=config)
```

---

## Cost Reduction Playbook

Use this checklist when optimizing an existing agent:

```
Phase 1 — Quick Wins (1 day, typical 3-5× savings)
──────────────────────────────────────────────────
[ ] Add complexity classifier → route simple tasks to Haiku
[ ] Add cache_control to system prompt if > 1024 tokens
[ ] Set max_tokens on all LLM calls
[ ] Trim tool results to < 3000 chars

Phase 2 — Medium Effort (1 week, 5-10× total savings)
──────────────────────────────────────────────────────
[ ] Add token budget guardian node to the graph
[ ] Implement rolling summary for conversations > 10 turns
[ ] Cache RAG documents for same-session repeated queries
[ ] Switch bulk/offline tasks to Batch API

Phase 3 — Advanced (ongoing, 10-20× total savings)
──────────────────────────────────────────────────
[ ] Structured output for all extraction/classification tasks
[ ] LLMLingua for RAG context compression
[ ] Fine-tune Haiku on domain tasks → replace Sonnet
[ ] Custom embedding model to reduce retrieval pass size
```

## Common Cost Anti-Patterns

| Anti-Pattern | Cost Impact | Fix |
|---|---|---|
| Using Opus for all tasks | **20-95× overspend** | Add complexity router |
| No system prompt caching | **6× overspend** on repeated calls | Add `cache_control` |
| Returning raw tool results | **5-10× extra input tokens** | Trim to 3k chars |
| No `max_tokens` set | **3× extra output tokens** | Set limit explicitly |
| Real-time call for batch work | **2× overspend** | Use Batch API |
| Full history in every prompt | **Grows linearly** | Rolling summary |
| Prose output when structured works | **3× extra output** | `with_structured_output()` |

## Related Skills

Works best with: `langchain-langgraph`, `rag-implementation`, `prompt-engineering`, `instructor`
