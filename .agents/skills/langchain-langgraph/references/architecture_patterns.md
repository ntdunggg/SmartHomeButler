# Architecture Patterns Reference

This file contains advanced architecture patterns for LangChain & LangGraph.
Read `SKILL.md` first for foundational concepts.

---

## Pattern 1: RAG with LangGraph

```python
from langgraph.graph import StateGraph, START, END
from langchain_anthropic import ChatAnthropic
from langchain_voyageai import VoyageAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from typing import TypedDict, Annotated

class RAGState(TypedDict):
    question: str
    context: Annotated[list[Document], "retrieved documents"]
    answer: str

# Initialize components
llm = ChatAnthropic(model="claude-sonnet-4-6")
embeddings = VoyageAIEmbeddings(model="voyage-3-large")
vectorstore = PineconeVectorStore(index_name="docs", embedding=embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

async def retrieve(state: RAGState) -> dict:
    """Retrieve relevant documents."""
    docs = await retriever.ainvoke(state["question"])
    return {"context": docs}

async def generate(state: RAGState) -> dict:
    """Generate answer from context, using Anthropic prompt caching for large contexts."""
    prompt = ChatPromptTemplate.from_template(
        """Answer based on the context below. If you cannot answer, say so.

        Context: {context}

        Question: {question}

        Answer:"""
    )
    context_text = "\n\n".join(doc.page_content for doc in state["context"])
    response = await llm.ainvoke(
        prompt.format(context=context_text, question=state["question"])
    )
    return {"answer": response.content}

builder = StateGraph(RAGState)
builder.add_node("retrieve", retrieve)
builder.add_node("generate", generate)
builder.add_edge(START, "retrieve")
builder.add_edge("retrieve", "generate")
builder.add_edge("generate", END)

rag_chain = builder.compile()
result = await rag_chain.ainvoke({"question": "What is the main topic?"})
```

---

## Pattern 2: Custom Agent with Structured Tools

```python
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

class SearchInput(BaseModel):
    query: str = Field(description="Search query")
    filters: dict = Field(default={}, description="Optional filters")

class EmailInput(BaseModel):
    recipient: str = Field(description="Email recipient")
    subject: str = Field(description="Email subject")
    content: str = Field(description="Email body")

async def search_database(query: str, filters: dict = {}) -> str:
    """Search internal database for information."""
    return f"Results for '{query}' with filters {filters}"

async def send_email(recipient: str, subject: str, content: str) -> str:
    """Send an email to specified recipient."""
    return f"Email sent to {recipient}"

tools = [
    StructuredTool.from_function(
        coroutine=search_database,
        name="search_database",
        description="Search internal database",
        args_schema=SearchInput
    ),
    StructuredTool.from_function(
        coroutine=send_email,
        name="send_email",
        description="Send an email",
        args_schema=EmailInput
    )
]

llm = ChatAnthropic(model="claude-sonnet-4-6")
agent = create_react_agent(llm, tools)
```

---

## Pattern 3: Multi-Step Workflow with StateGraph

```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Literal

class WorkflowState(TypedDict):
    text: str
    entities: list
    analysis: str
    summary: str

async def extract_entities(state: WorkflowState) -> dict:
    """Extract key entities from text."""
    response = await llm.ainvoke(
        f"Extract key entities from the following as a JSON list:\n{state['text']}"
    )
    return {"entities": response.content}

async def analyze_entities(state: WorkflowState) -> dict:
    """Analyze extracted entities."""
    response = await llm.ainvoke(
        f"Analyze these entities and provide insights:\n{state['entities']}"
    )
    return {"analysis": response.content}

async def generate_summary(state: WorkflowState) -> dict:
    """Generate final summary."""
    response = await llm.ainvoke(
        f"Summarize entities: {state['entities']}\nAnalysis: {state['analysis']}"
    )
    return {"summary": response.content}

builder = StateGraph(WorkflowState)
builder.add_node("extract", extract_entities)
builder.add_node("analyze", analyze_entities)
builder.add_node("summarize", generate_summary)
builder.add_edge(START, "extract")
builder.add_edge("extract", "analyze")
builder.add_edge("analyze", "summarize")
builder.add_edge("summarize", END)

workflow = builder.compile()
```

---

## Pattern 4: Multi-Agent Supervisor

Routes tasks across specialized sub-agents. Each sub-agent is a compiled `create_react_agent`.

```python
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from typing import TypedDict, Literal, Annotated
from langgraph.graph.message import add_messages

llm = ChatAnthropic(model="claude-sonnet-4-6")

class MultiAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    next_agent: str

# Create specialized agents (tools omitted for brevity)
researcher = create_react_agent(llm, research_tools)
writer = create_react_agent(llm, writing_tools)
reviewer = create_react_agent(llm, review_tools)

async def supervisor(state: MultiAgentState) -> dict:
    """Decide which specialist agent to call next."""
    response = await llm.ainvoke(
        f"""Based on the conversation, which agent should handle this?
Options: researcher, writer, reviewer, FINISH

Messages: {state['messages']}

Reply with only the agent name."""
    )
    return {"next_agent": response.content.strip().lower()}

def route_to_agent(state: MultiAgentState) -> str:
    next_agent = state.get("next_agent", "")
    if next_agent == "finish":
        return END
    return next_agent if next_agent in ["researcher", "writer", "reviewer"] else END

builder = StateGraph(MultiAgentState)
builder.add_node("supervisor", supervisor)
builder.add_node("researcher", researcher)
builder.add_node("writer", writer)
builder.add_node("reviewer", reviewer)
builder.add_edge(START, "supervisor")
builder.add_conditional_edges("supervisor", route_to_agent,
    {"researcher": "researcher", "writer": "writer", "reviewer": "reviewer", END: END})

for agent_name in ["researcher", "writer", "reviewer"]:
    builder.add_edge(agent_name, "supervisor")  # always return to supervisor

multi_agent = builder.compile()
```

---

## Pattern 5: State with Custom Reducers

Multiple concurrent agents updating shared state safely.

```python
from typing import Annotated, TypedDict
from operator import add
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages

def merge_dicts(left: dict, right: dict) -> dict:
    return {**left, **right}

class ResearchState(TypedDict):
    messages: Annotated[list, add_messages]   # smart merge
    findings: Annotated[dict, merge_dicts]    # merge dicts
    sources: Annotated[list[str], add]        # append lists
    current_step: str                          # overwrite (no reducer)
    errors: Annotated[int, lambda a, b: a + b]  # count errors

def researcher(state: ResearchState) -> dict:
    return {
        "findings": {"topic_a": "New finding"},
        "sources": ["source1.com"],
        "current_step": "researching"
    }

def writer(state: ResearchState) -> dict:
    return {
        "messages": [("assistant", f"Report from {len(state['sources'])} sources")],
        "current_step": "writing"
    }
```

---

## Pattern 6: Map-Reduce with `Send`

Fan-out to parallel nodes, then aggregate results. Powered by `Send` objects returned from a conditional edge.

```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from typing import TypedDict, Annotated
from operator import add

class OverallState(TypedDict):
    topics: list[str]
    summaries: Annotated[list[str], add]  # reducer accumulates from each parallel branch

class TopicState(TypedDict):
    topic: str
    summary: str

def generate_topics(state: OverallState) -> list[Send]:
    """Fan out: create one parallel Send per topic."""
    return [Send("process_topic", {"topic": t}) for t in state["topics"]]

async def process_topic(state: TopicState) -> dict:
    """Each branch runs independently and concurrently."""
    result = await llm.ainvoke(f"Summarize this topic in one sentence: {state['topic']}")
    return {"summaries": [result.content]}  # add reducer merges these

async def combine(state: OverallState) -> dict:
    merged = "\n".join(state["summaries"])
    final = await llm.ainvoke(f"Combine these summaries:\n{merged}")
    return {"summaries": [final.content]}

builder = StateGraph(OverallState)
builder.add_node("process_topic", process_topic)
builder.add_node("combine", combine)
builder.add_conditional_edges(START, generate_topics, ["process_topic"])
builder.add_edge("process_topic", "combine")
builder.add_edge("combine", END)

graph = builder.compile()
result = await graph.ainvoke({"topics": ["AI", "Climate", "Economics"]})
```

---

## Pattern 7: Human-in-the-Loop (HITL) — Approve / Reject

Standard HITL: pause inside a node, route based on human decision.

```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict, Literal, Optional

class ApprovalState(TypedDict):
    action_details: str
    status: Optional[Literal["pending", "approved", "rejected"]]

def approval_node(state: ApprovalState) -> Command[Literal["proceed", "cancel"]]:
    # Pause here; payload appears in result["__interrupt__"] for the caller
    decision = interrupt({
        "question": "Approve this action?",
        "details": state["action_details"]
    })
    return Command(goto="proceed" if decision else "cancel")

def proceed_node(state: ApprovalState): return {"status": "approved"}
def cancel_node(state: ApprovalState): return {"status": "rejected"}

builder = StateGraph(ApprovalState)
builder.add_node("approval", approval_node)
builder.add_node("proceed", proceed_node)
builder.add_node("cancel", cancel_node)
builder.add_edge(START, "approval")
builder.add_edge("proceed", END)
builder.add_edge("cancel", END)
graph = builder.compile(checkpointer=MemorySaver())

config = {"configurable": {"thread_id": "approval-123"}}

# Run until interrupted
initial = graph.invoke(
    {"action_details": "Transfer $500", "status": "pending"}, config=config
)
print(initial["__interrupt__"])  # Show payload to user

# Resume with True (approve) or False (reject)
resumed = graph.invoke(Command(resume=True), config=config)
print(resumed["status"])  # "approved"
```

---

## Pattern 8: Interrupt Inside a Tool (Tool-Level HITL)

Best for "approve before action" patterns within tool-calling agents.

```python
import sqlite3
from typing import TypedDict
from langchain.tools import tool
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langgraph.graph.message import add_messages
from typing import Annotated

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient. Always asks for human approval first."""
    # Pause; payload appears as __interrupt__ in the calling graph
    response = interrupt({
        "action": "send_email",
        "to": to, "subject": subject, "body": body,
        "message": "Approve sending this email?"
    })
    if response.get("action") == "approve":
        final_to = response.get("to", to)
        final_subject = response.get("subject", subject)
        final_body = response.get("body", body)
        # Your real email send logic here
        return f"Email sent to {final_to} with subject '{final_subject}'"
    return "Email cancelled by user"

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

llm = ChatAnthropic(model="claude-sonnet-4-6").bind_tools([send_email])

def agent_node(state: AgentState) -> dict:
    result = llm.invoke(state["messages"])
    return {"messages": [result]}

builder = StateGraph(AgentState)
builder.add_node("agent", agent_node)
builder.add_edge(START, "agent")
builder.add_edge("agent", END)

checkpointer = SqliteSaver(sqlite3.connect("tool-approval.db"))
graph = builder.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "email-workflow-1"}}
initial = graph.invoke(
    {"messages": [{"role": "user", "content": "Send an email to alice@example.com about the meeting"}]},
    config=config
)
print(initial["__interrupt__"])  # Show to human for approval

# Resume with approval and optional field overrides
resumed = graph.invoke(
    Command(resume={"action": "approve", "subject": "Updated: Meeting Tomorrow"}),
    config=config
)
```

---

## Pattern 9: Multiple Interrupts in One Node

A node can call `interrupt()` multiple times — for multi-step human reviews.

```python
from langgraph.types import interrupt, Command
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict

class ReviewState(TypedDict):
    draft: str
    title_approved: bool
    content_approved: bool

def review_node(state: ReviewState) -> dict:
    # First interrupt: review title
    title_ok = interrupt({
        "step": 1,
        "question": "Do you approve the title?",
        "title": "AI in 2026"
    })

    if not title_ok:
        return {"title_approved": False, "content_approved": False}

    # Second interrupt: review full content
    content_ok = interrupt({
        "step": 2,
        "question": "Do you approve the full content?",
        "content": state["draft"]
    })

    return {"title_approved": True, "content_approved": content_ok}

builder = StateGraph(ReviewState)
builder.add_node("review", review_node)
builder.add_edge(START, "review")
builder.add_edge("review", END)
graph = builder.compile(checkpointer=MemorySaver())

config = {"configurable": {"thread_id": "multi-review-1"}}
# First pause (title)
graph.invoke({"draft": "Some content..."}, config=config)
# Resume step 1
graph.invoke(Command(resume=True), config=config)
# Resume step 2
graph.invoke(Command(resume=True), config=config)
```

---

## Pattern 10: Durable Execution — Wrapping Side-Effects with `@task` Inside Nodes

When using the **Graph API** (not Functional API), wrap side-effects inside nodes using `task()` from `langgraph.func` to protect them from re-execution on resume.

```python
from typing import TypedDict, NotRequired
import uuid
import requests
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import task
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    urls: list[str]
    results: NotRequired[list[str]]

@task
def _fetch_url(url: str) -> str:
    """Side-effecting operation — result is memoized in checkpoint on resume."""
    return requests.get(url).text[:200]

def call_api_node(state: State) -> dict:
    """Node that fetches URLs in parallel using @task for durability."""
    # Fan out requests concurrently
    futures = [_fetch_url(url) for url in state["urls"]]
    # Collect results (blocks until all done; idempotent on resume)
    results = [f.result() for f in futures]
    return {"results": results}

builder = StateGraph(State)
builder.add_node("call_api", call_api_node)
builder.add_edge(START, "call_api")
builder.add_edge("call_api", END)

graph = builder.compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": str(uuid.uuid4())}}
graph.invoke({"urls": ["https://example.com", "https://httpbin.org/get"]}, config)
```

**Why**: if the process crashes mid-node, `_fetch_url`'s completed results are replayed from the checkpoint; incomplete ones are retried.

---

## Pattern 11: Cross-Thread Long-Term Memory Store (with Semantic Search)

Share structured knowledge between sessions and users using `InMemoryStore` (dev) or `PostgresStore` (prod) with optional vector embeddings for semantic retrieval.

```python
from langgraph.store.memory import InMemoryStore
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

# Provide an embedding function for semantic search
def embed(texts: list[str]) -> list[list[float]]:
    # Replace with a real embedding model, e.g. OpenAIEmbeddings
    from langchain_openai import OpenAIEmbeddings
    model = OpenAIEmbeddings()
    return model.embed_documents(texts)

# InMemoryStore with vector index (use PostgresStore in production)
store = InMemoryStore(index={"embed": embed, "dims": 1536})

class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    user_id: str

llm = ChatAnthropic(model="claude-sonnet-4-6")

def chat_node(state: ChatState, store: InMemoryStore) -> dict:
    user_id = state["user_id"]
    namespace = (user_id, "preferences")

    # 1. Semantic search for relevant memories
    query = state["messages"][-1].content
    memories = store.search(namespace, query=query, limit=3)
    memory_context = "\n".join(m.value.get("fact", "") for m in memories)

    # 2. Direct key lookup for known preferences
    lang_pref = store.get(namespace, "language")
    lang = lang_pref.value["lang"] if lang_pref else "English"

    # 3. Generate response with memory context
    system = f"User language: {lang}. Relevant context:\n{memory_context}"
    response = llm.invoke([HumanMessage(content=system)] + state["messages"])

    # 4. Save new facts back to store
    store.put(namespace, "last_topic", {
        "fact": f"User asked about: {query[:100]}",
        "timestamp": "2026-03-16"
    })

    return {"messages": [response]}

builder = StateGraph(ChatState)
builder.add_node("chat", chat_node)
builder.add_edge(START, "chat")
builder.add_edge("chat", END)
# Pass both checkpointer (short-term) and store (long-term)
app = builder.compile(checkpointer=MemorySaver(), store=store)
```

---

## Pattern 12: Procedural Memory — Self-Updating Agent Instructions

The agent learns and updates its own system prompt over time.

```python
from langgraph.store.memory import InMemoryStore
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_anthropic import ChatAnthropic
from typing import TypedDict, Annotated

store = InMemoryStore()
llm = ChatAnthropic(model="claude-sonnet-4-6")
INSTRUCTIONS_NS = ("agent_instructions",)

def call_model(state: dict, store: InMemoryStore) -> dict:
    """Use the stored instructions when responding."""
    item = store.get(INSTRUCTIONS_NS, "agent_a")
    instructions = item.value["instructions"] if item else "Be helpful and concise."
    system_msg = {"role": "system", "content": instructions}
    response = llm.invoke([system_msg] + state["messages"])
    return {"messages": [response]}

def update_instructions(state: dict, store: InMemoryStore) -> dict:
    """Periodically update instructions based on conversation quality."""
    item = store.search(INSTRUCTIONS_NS)
    current = item[0].value["instructions"] if item else "Be helpful."
    prompt = f"""Current instructions: {current}
Conversation: {state['messages']}
Suggest improved instructions that would make the agent more helpful. Reply with only the new instructions."""
    output = llm.invoke(prompt)
    store.put(INSTRUCTIONS_NS, "agent_a", {"instructions": output.content})
    return {}  # no state change needed

builder = StateGraph({"messages": Annotated[list, add_messages]})
builder.add_node("call_model", call_model)
builder.add_node("update_instructions", update_instructions)
builder.add_edge(START, "call_model")
# Conditionally update instructions every N turns (simplified here)
builder.add_edge("call_model", END)
app = builder.compile(checkpointer=MemorySaver(), store=store)
```

---

## Pattern 13: Conditional Branching (Query Router)

Route to different agent paths based on intent classification.

```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class RouterState(TypedDict):
    query: str
    query_type: str
    result: str

def classifier(state: RouterState) -> dict:
    query = state["query"].lower()
    if any(word in query for word in ["code", "program", "function"]):
        return {"query_type": "coding"}
    elif any(word in query for word in ["search", "find", "lookup"]):
        return {"query_type": "search"}
    else:
        return {"query_type": "chat"}

def coding_agent(state: RouterState) -> dict:
    return {"result": "Here's the code..."}

def search_agent(state: RouterState) -> dict:
    return {"result": "Search results..."}

def chat_agent(state: RouterState) -> dict:
    return {"result": "Here's my answer..."}

def route_query(state: RouterState) -> str:
    return state["query_type"]  # must match a node name or END

graph = StateGraph(RouterState)
graph.add_node("classifier", classifier)
graph.add_node("coding", coding_agent)
graph.add_node("search", search_agent)
graph.add_node("chat", chat_agent)

graph.add_edge(START, "classifier")
graph.add_conditional_edges("classifier", route_query,
    {"coding": "coding", "search": "search", "chat": "chat"})
graph.add_edge("coding", END)
graph.add_edge("search", END)
graph.add_edge("chat", END)

app = graph.compile()
```