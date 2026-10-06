# langchain-dependencies

> Consult this file when setting up a new project, asking about package versions, installation issues, or dependency management for LangChain/LangGraph/LangSmith/Deep Agents. Covers required packages, minimum versions, environment requirements, version best practices, and common community toolkits for both Python and TypeScript.

## Table of Contents
- [Environment Requirements](#environment-requirements)
- [Framework Selection](#framework-selection)
- [Core Packages — Python](#core-packages--python)
- [Core Packages — TypeScript](#core-packages--typescript)
- [Minimal Project Templates](#minimal-project-templates)
- [Version Strategy and Upgrades](#version-strategy-and-upgrades)
- [Environment Variables](#environment-variables)
- [Common Mistakes and Fixes](#common-mistakes-and-fixes)

---

## Environment Requirements

| Requirement | Python | TypeScript / Node |
|------|--------|-------------------|
| Minimum Runtime | **Python 3.10+** | **Node.js 20+** |
| LangChain | **1.0+ (LTS)** | **1.0+ (LTS)** |
| LangSmith SDK | >= 0.3.0 | >= 0.3.0 |

---

## Framework Selection

Choose **one** agent orchestration layer — you don't need both.

| Framework | When to Use | Key Additional Package |
|------|---------|-----------| 
| **LangGraph** | Need fine-grained graph control, custom workflows, loops or branching | `langgraph` / `@langchain/langgraph` |
| **Deep Agents** | Need out-of-the-box planning, memory, file context and skills | `deepagents` (depends on LangGraph; installed as transitive dependency) |

Both are built on top of `langchain` + `langchain-core` + `langsmith`.

---

## Core Packages — Python

### Python — Always Required

| Package | Purpose | Minimum Version |
|----|------|---------| 
| `langchain` | Agents, chains, retrieval | 1.0 |
| `langchain-core` | Core types and interfaces (peer dependency) | 1.0 |
| `langsmith` | Tracing, evaluation, datasets | 0.3.0 |

### Python — Orchestration Layer (Choose One)

| Package | When to Use | Minimum Version |
|----|---------|---------| 
| `langgraph` | Building custom graphs directly | 1.0 |
| `deepagents` | Using the Deep Agents framework | latest |

### Python — Model Providers (Choose as Needed)

| Package | Provider |
|----|--------| 
| `langchain-openai` | OpenAI (GPT-4o, o3, …) |
| `langchain-anthropic` | Anthropic (Claude) |
| `langchain-google-genai` | Google (Gemini) |
| `langchain-mistralai` | Mistral |
| `langchain-groq` | Groq (fast inference) |
| `langchain-cohere` | Cohere |
| `langchain-fireworks` | Fireworks AI |
| `langchain-together` | Together AI |
| `langchain-huggingface` | Hugging Face Hub |
| `langchain-ollama` | Ollama (local models) |
| `langchain-aws` | AWS Bedrock |
| `langchain-azure-ai` | Azure AI Foundry |

### Python — Common Tools and Retrieval Packages

| Package | Added Capability | Notes |
|----|---------|------|
| `langchain-tavily` | Tavily web search | Prefer latest version |
| `langchain-text-splitters` | Text chunking tools | Semver, keep up to date |
| `langchain-community` | 1000+ integrations (fallback) | **Not semver — pin to minor series** |
| `faiss-cpu` | FAISS vector store (local) | Via langchain-community |
| `langchain-chroma` | Chroma vector store | Prefer latest version |
| `langchain-pinecone` | Pinecone vector store | Prefer latest version |
| `langchain-qdrant` | Qdrant vector store | Prefer latest version |
| `langchain-weaviate` | Weaviate vector store | Prefer latest version |
| `langsmith[pytest]` | pytest plugin | Requires langsmith >= 0.3.4 |

> ⚠️ **langchain-community stability note**: This package does **not follow semver**. Minor versions may contain breaking changes. When a dedicated integration package exists (e.g., `langchain-chroma` instead of the community Chroma integration), prefer it — dedicated packages are independently versioned and more thoroughly tested.

---

## Core Packages — TypeScript

### TypeScript — Always Required

| Package | Purpose | Minimum Version |
|----|------|---------| 
| `@langchain/core` | Core types and interfaces (peer dependency) | 1.0 |
| `langchain` | Agents, chains, retrieval | 1.0 |
| `langsmith` | Tracing, evaluation, datasets | 0.3.0 |

### TypeScript — Orchestration Layer (Choose One)

| Package | When to Use | Minimum Version |
|----|---------|---------| 
| `@langchain/langgraph` | Building custom graphs directly | 1.0 |
| `deepagents` | Using the Deep Agents framework | latest |

### TypeScript — Model Providers (Choose as Needed)

| Package | Provider |
|----|--------| 
| `@langchain/openai` | OpenAI |
| `@langchain/anthropic` | Anthropic (Claude) |
| `@langchain/google-genai` | Google (Gemini) |
| `@langchain/mistralai` | Mistral |
| `@langchain/groq` | Groq |
| `@langchain/cohere` | Cohere |
| `@langchain/aws` | AWS Bedrock |
| `@langchain/ollama` | Ollama |

### TypeScript — Common Tools and Retrieval Packages

| Package | Added Capability |
|----|---------| 
| `@langchain/tavily` | Tavily web search |
| `@langchain/community` | Broad integrations (use cautiously, prefer dedicated packages) |
| `@langchain/pinecone` | Pinecone vector store |
| `@langchain/qdrant` | Qdrant vector store |

> ⚠️ **`@langchain/core` must be explicitly installed** — in yarn workspaces and monorepos it is a peer dependency and won't always be auto-hoisted.

---

## Minimal Project Templates

### LangGraph — Python

```python
# requirements.txt
langchain>=1.0,<2.0
langchain-core>=1.0,<2.0
langgraph>=1.0,<2.0
langsmith>=0.3.0

# Add your model provider, e.g.:
# langchain-openai
# langchain-anthropic
```

### LangGraph — TypeScript

```json
{
  "dependencies": {
    "@langchain/core": "^1.0.0",
    "langchain": "^1.0.0",
    "@langchain/langgraph": "^1.0.0",
    "langsmith": "^0.3.0"
  }
}
```

### Deep Agents — Python

```python
# requirements.txt
deepagents            # Internally bundles langgraph
langchain>=1.0,<2.0
langchain-core>=1.0,<2.0
langsmith>=0.3.0

# Add your model provider:
# langchain-anthropic
# langchain-openai
```

### LangGraph with Tavily and Vector Store — Python

```python
# requirements.txt
langchain>=1.0,<2.0
langchain-core>=1.0,<2.0
langgraph>=1.0,<2.0
langsmith>=0.3.0

# Web search
langchain-tavily          # Use latest; partner package, semver

# Vector store — choose one:
langchain-chroma          # Use latest
# langchain-pinecone      # Use latest
# langchain-qdrant        # Use latest

langchain-text-splitters  # Use latest
# Your model provider:
# langchain-openai / langchain-anthropic / other
```

---

## Version Strategy and Upgrades

| Package Group | Versioning Scheme | Safe Upgrade Strategy |
|------|---------|------------|
| `langchain`, `langchain-core` | Strict semver (1.0 LTS) | Allow minor: `>=1.0,<2.0` |
| `langgraph` / `@langchain/langgraph` | Strict semver (v1 LTS) | Allow minor: `>=1.0,<2.0` |
| `langsmith` | Strict semver | Allow minor: `>=0.3.0` |
| Dedicated integration packages (e.g., `langchain-tavily`) | Independent versioning | Allow minor updates; use latest |
| `langchain-community` | **Not semver** | Pin minor: `>=0.4.0,<0.5.0` |
| `deepagents` | Follows project releases | Pin to tested version in production |

---

## Environment Variables

```bash
# LangSmith (always recommended for observability)
LANGSMITH_API_KEY=<your-key>
LANGSMITH_PROJECT=<project-name>   # Optional, defaults to "default"

# Model providers — set the one you use
OPENAI_API_KEY=<your-key>
ANTHROPIC_API_KEY=<your-key>
GOOGLE_API_KEY=<your-key>
MISTRAL_API_KEY=<your-key>
GROQ_API_KEY=<your-key>

# Common tool/retrieval services
TAVILY_API_KEY=<your-key>
PINECONE_API_KEY=<your-key>
```

---

## Common Mistakes and Fixes

### ❌ Using LangChain 0.3 (Legacy)
```python
# Wrong: legacy version, no new features, security patches only
langchain>=0.3,<0.4

# Correct: LangChain 1.0 LTS
langchain>=1.0,<2.0
```

### ❌ Unpinned langchain-community Version
```python
# Wrong: allows potentially breaking minor updates
langchain-community>=0.4

# Correct: pin to exact minor series
langchain-community>=0.4.0,<0.5.0
```

### ❌ Using Deprecated Community Import Paths
```python
# Wrong — deprecated community import paths
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.vectorstores import Chroma
from langchain_community.vectorstores import Pinecone

# Correct — use dedicated package imports
from langchain_tavily import TavilySearch            # pip: langchain-tavily
from langchain_chroma import Chroma                  # pip: langchain-chroma
from langchain_pinecone import PineconeVectorStore   # pip: langchain-pinecone
```

### ❌ Missing @langchain/core in TypeScript
```json
// Wrong: may not hoist correctly in yarn workspaces
{ "dependencies": { "@langchain/langgraph": "^1.0.0" } }

// Correct: always explicitly list @langchain/core
{ "dependencies": { "@langchain/core": "^1.0.0", "@langchain/langgraph": "^1.0.0" } }
```

### ❌ Python Version Too Low
```python
# Verify before installing
import sys
assert sys.version_info >= (3, 10), "LangChain 1.0 requires Python 3.10+"
```
