# langchain-rag

> Consult this file when building **any** RAG (Retrieval Augmented Generation) system. Covers document loaders, RecursiveCharacterTextSplitter, embedding models (OpenAI), and vector stores (Chroma, FAISS, Pinecone).

## Table of Contents
- [RAG Pipeline Overview](#rag-pipeline-overview)
- [Vector Store Selection](#vector-store-selection)
- [Full RAG Pipeline Example](#full-rag-pipeline-example)
- [Document Loaders](#document-loaders)
- [Text Splitting](#text-splitting)
- [Vector Store Operations](#vector-store-operations)
- [Retrieval Strategies](#retrieval-strategies)
- [RAG + Agent Integration](#rag--agent-integration)
- [Common Mistakes and Fixes](#common-mistakes-and-fixes)

---

## RAG Pipeline Overview

**Processing flow:**
1. **Indexing**: Load → Split → Embed → Store
2. **Retrieval**: Query → Embed → Search → Return documents
3. **Generation**: Documents + Query → LLM → Response

**Key components:**
- **Document Loaders**: Ingest data from files, web, databases
- **Text Splitters**: Split documents into chunks
- **Embedding Models**: Convert text to vectors
- **Vector Stores**: Store and search embeddings

---

## Vector Store Selection

| Vector Store | Use Case | Persistence |
|---------|---------|--------|
| **InMemory** | Testing | Memory only |
| **FAISS** | Local, high performance | Disk |
| **Chroma** | Development | Disk |
| **Pinecone** | Production, managed | Cloud |

---

## Full RAG Pipeline Example

**Python:**
```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# 1. Load documents
docs = [
    Document(page_content="LangChain is a framework for LLM apps.", metadata={}),
    Document(page_content="RAG = Retrieval Augmented Generation.", metadata={}),
]

# 2. Split documents
splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
splits = splitter.split_documents(docs)

# 3. Create embeddings and store
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = InMemoryVectorStore.from_documents(splits, embeddings)

# 4. Create retriever
retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

# 5. Use in RAG
model = ChatOpenAI(model="gpt-4.1")
query = "What is RAG?"
relevant_docs = retriever.invoke(query)

context = "\n\n".join([doc.page_content for doc in relevant_docs])
response = model.invoke([
    {"role": "system", "content": f"Use this context:\n\n{context}"},
    {"role": "user", "content": query},
])
```

**TypeScript:**
```typescript
import { ChatOpenAI, OpenAIEmbeddings } from "@langchain/openai";
import { MemoryVectorStore } from "@langchain/classic/vectorstores/memory";
import { RecursiveCharacterTextSplitter } from "@langchain/textsplitters";
import { Document } from "@langchain/core/documents";

// 1. Load documents
const docs = [
  new Document({ pageContent: "LangChain is a framework for LLM apps.", metadata: {} }),
  new Document({ pageContent: "RAG = Retrieval Augmented Generation.", metadata: {} }),
];

// 2. Split documents
const splitter = new RecursiveCharacterTextSplitter({ chunkSize: 500, chunkOverlap: 50 });
const splits = await splitter.splitDocuments(docs);

// 3. Create embeddings and store
const embeddings = new OpenAIEmbeddings({ model: "text-embedding-3-small" });
const vectorstore = await MemoryVectorStore.fromDocuments(splits, embeddings);

// 4. Create retriever
const retriever = vectorstore.asRetriever({ k: 4 });

// 5. Use in RAG
const model = new ChatOpenAI({ model: "gpt-4.1" });
const query = "What is RAG?";
const relevantDocs = await retriever.invoke(query);

const context = relevantDocs.map(doc => doc.pageContent).join("\n\n");
const response = await model.invoke([
  { role: "system", content: `Use this context:\n\n${context}` },
  { role: "user", content: query },
]);
```

---

## Document Loaders

### Load PDF
```python
from langchain_community.document_loaders import PyPDFLoader

loader = PyPDFLoader("./document.pdf")
docs = loader.load()
print(f"Loaded {len(docs)} pages")
```

### Load Web Page
```python
from langchain_community.document_loaders import WebBaseLoader

loader = WebBaseLoader("https://docs.langchain.com")
docs = loader.load()
```

### Load Directory
```python
from langchain_community.document_loaders import DirectoryLoader, TextLoader

loader = DirectoryLoader(
    "path/to/documents",
    glob="**/*.txt",  # File pattern
    loader_cls=TextLoader
)
docs = loader.load()
```

---

## Text Splitting

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,        # Characters per chunk
    chunk_overlap=200,      # Overlap for context continuity
    separators=["\n\n", "\n", " ", ""],  # Split hierarchy
)

splits = splitter.split_documents(docs)
```

---

## Vector Store Operations

### Chroma (Persistent Local Storage)

```python
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

# Create and save
vectorstore = Chroma.from_documents(
    documents=splits,
    embedding=OpenAIEmbeddings(),
    persist_directory="./chroma_db",
    collection_name="my-collection",
)

# Load from disk
vectorstore = Chroma(
    persist_directory="./chroma_db",
    embedding_function=OpenAIEmbeddings(),
    collection_name="my-collection",
)
```

### FAISS (High-Performance Local Storage)

```python
from langchain_community.vectorstores import FAISS

vectorstore = FAISS.from_documents(splits, embeddings)
vectorstore.save_local("./faiss_index")

# Load (requires allow_dangerous_deserialization)
loaded = FAISS.load_local(
    "./faiss_index",
    embeddings,
    allow_dangerous_deserialization=True
)
```

---

## Retrieval Strategies

### Similarity Search

```python
# Basic search
results = vectorstore.similarity_search(query, k=5)

# With scores
results_with_score = vectorstore.similarity_search_with_score(query, k=5)
for doc, score in results_with_score:
    print(f"Score: {score}, Content: {doc.page_content}")
```

### MMR Search (Balance Relevance and Diversity)

```python
retriever = vectorstore.as_retriever(
    search_type="mmr",
    search_kwargs={"fetch_k": 20, "lambda_mult": 0.5, "k": 5},
)
```

### Metadata Filtering

```python
docs = [
    Document(
        page_content="Python programming guide",
        metadata={"language": "python", "topic": "programming"}
    ),
]

# Filter search by metadata
results = vectorstore.similarity_search(
    "programming",
    k=5,
    filter={"language": "python"}  # Only return Python documents
)
```

---

## RAG + Agent Integration

Use RAG as a tool for the agent:

```python
from langchain.agents import create_agent
from langchain.tools import tool

@tool
def search_docs(query: str) -> str:
    """Search documentation for relevant information."""
    docs = retriever.invoke(query)
    return "\n\n".join([d.page_content for d in docs])

agent = create_agent(
    model="gpt-4.1",
    tools=[search_docs],
)

result = agent.invoke({
    "messages": [{"role": "user", "content": "How do I create an agent?"}]
})
```

---

## Common Mistakes and Fixes

### ❌ Chunk Size Too Small or Too Large
```python
# Wrong: too small (loses context) or too large (exceeds limits)
splitter = RecursiveCharacterTextSplitter(chunk_size=50)
splitter = RecursiveCharacterTextSplitter(chunk_size=10000)

# Correct: 500-1500 is usually appropriate
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
```

### ❌ No Chunk Overlap
```python
# Wrong: no overlap — context breaks at boundaries
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=0)

# Correct: 10-20% overlap
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
```

### ❌ Using InMemory Vector Store for Production
```python
# Wrong: lost on restart
vectorstore = InMemoryVectorStore.from_documents(docs, embeddings)

# Correct
vectorstore = Chroma.from_documents(docs, embeddings, persist_directory="./chroma_db")
```

### ❌ Different Embedding Models for Indexing and Querying
```python
# Wrong: incompatible!
vectorstore = Chroma.from_documents(docs, OpenAIEmbeddings(model="text-embedding-3-small"))
retriever = vectorstore.as_retriever(embeddings=OpenAIEmbeddings(model="text-embedding-3-large"))

# Correct: same model
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = Chroma.from_documents(docs, embeddings)
retriever = vectorstore.as_retriever()  # Uses same embeddings
```

### ❌ Forgetting Deserialization Parameter When Loading FAISS
```python
# Wrong: will raise error
loaded_store = FAISS.load_local("./faiss_index", embeddings)

# Correct
loaded_store = FAISS.load_local("./faiss_index", embeddings, allow_dangerous_deserialization=True)
```

### ❌ Embedding Dimension Mismatch
```python
# Wrong: index has 1536 dims, but using 512-dim embeddings
pc.create_index(name="idx", dimension=1536, metric="cosine")
vectorstore = PineconeVectorStore.from_documents(
    docs, OpenAIEmbeddings(model="text-embedding-3-small", dimensions=512), index=pc.Index("idx")
)  # Error: dimension mismatch!

# Correct: match dimensions
embeddings = OpenAIEmbeddings()  # Default 1536
```
