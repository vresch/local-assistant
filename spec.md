# Local-First Personal AI Assistant — Specification

## 1. Summary

The goal is to build a local-first personal AI assistant controlled through a CLI.

The assistant should use the user’s local knowledge base, local tools, and local execution environment as its core system. Remote LLMs may be used selectively for deeper reasoning, research, or complex tasks, but they should not own memory, orchestration, or execution.

The assistant is not just a chat interface. It is a personal operating layer over:

* Notes
* Tools
* Scripts
* Local models
* Remote models
* Logs
* User workflows

Core philosophy:

```text
Local system owns context.
Remote models rent intelligence.
```

---

# 2. Goals

## Primary Goals

Build a CLI-first assistant that can:

1. Search and summarize local notes.
2. Answer questions using local knowledge.
3. Execute local Python tools through `uv`.
4. Route tasks between local logic, local LLMs, and remote LLMs.
5. Log all actions and decisions.
6. Remain simple, inspectable, and extensible.

## Non-Goals for Phase 1

The first version should avoid:

* Autonomous long-running agents
* Multi-agent frameworks
* Background workers
* Complex planning frameworks
* Premature vector databases
* Heavy orchestration abstractions
* Full task-management systems

---

# 3. System Principles

## 3.1 Local-First

All important state should live locally:

* User input
* Retrieved context
* Notes index
* Tool execution logs
* Assistant decisions
* Generated summaries
* Configuration

Remote APIs are optional external workers.

## 3.2 Notes as Primary Memory

The user’s long-term memory is the local Markdown notes directory:

```bash
~/notes
```

These notes originate from Evernote exports and are accessed through Logseq.

The assistant should treat this repository as the primary knowledge source.

## 3.3 Python as Action Layer

Instead of building many custom tools early, local actions should be delegated to Python scripts managed by `uv`.

Example:

```bash
assistant run analyze-expenses
assistant run transcribe-audio
assistant run generate-report
```

Internally:

```bash
uv run ...
```

## 3.4 Assistant-First Design

The assistant is the product.

LLMs are reasoning engines used by the assistant, not the foundation of the system.

The core value comes from:

```text
Knowledge + Execution + Routing
```

---

# 4. Initial Architecture

```text
assistant/
├─ cli/
├─ orchestrator/
├─ notes/
├─ providers/
├─ tools/
├─ logs/
└─ config/
```

## 4.1 Components

### CLI

Primary user interface.

Required commands:

```bash
assistant ask "..."
assistant search "..."
assistant run "..."
assistant research "..."
```

### Orchestrator

Decides how to handle a user request.

Responsibilities:

* Parse intent
* Retrieve relevant notes
* Decide whether local context is enough
* Decide whether to use a local model
* Decide whether to escalate to a remote model
* Decide whether to execute a tool
* Ask for approval when needed
* Record decisions in logs

The orchestrator should not implement:

* Model inference
* Note indexing internals
* Tool logic
* Storage internals

### Notes Module

Handles:

* Markdown discovery
* Parsing
* Chunking
* SQLite storage
* FTS5 indexing
* Search
* Source references

### Providers Module

Handles model clients.

Initial providers:

* Local LLM via `llama.cpp` server
* Optional `llama-cpp-python`
* Remote LLM provider

### Tools Module

Handles local executable tools.

Initial strategy:

```bash
uv run <tool>
```

Each tool should have metadata:

* Name
* Description
* Input schema, if needed
* Command
* Risk level
* Approval requirement

### Logs Module

Records:

* User input
* Retrieved notes
* Routing decision
* Model used
* Tool executed
* Tool output
* Final response
* Errors

### Config Module

Stores:

* Notes path
* SQLite path
* Provider settings
* Model routing settings
* Tool registry path
* Logging preferences

---

# 5. Data Storage

## 5.1 Notes Index

Use:

```text
SQLite + FTS5
```

Avoid vector databases in Phase 1.

Reason:

* Simpler
* Local
* Transparent
* Fast enough for Markdown notes
* Easy to debug

## 5.2 Suggested Tables

### documents

```sql
CREATE TABLE documents (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  title TEXT,
  modified_at TEXT,
  content_hash TEXT
);
```

### chunks

```sql
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  heading TEXT,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);
```

### chunks_fts

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  content,
  heading,
  content='chunks',
  content_rowid='id'
);
```

### runs

```sql
CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  command TEXT NOT NULL,
  user_input TEXT NOT NULL,
  route TEXT,
  model TEXT,
  status TEXT,
  summary TEXT
);
```

### run_events

```sql
CREATE TABLE run_events (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);
```

---

# 6. CLI Commands

## 6.1 `assistant search`

Search local notes.

Example:

```bash
assistant search "sound healing"
```

Behavior:

1. Search SQLite FTS index.
2. Return matching chunks.
3. Include source file paths.
4. Include headings when available.

Output:

```text
Found 7 results:

1. ~/notes/sound-healing/session-structure.md
   Heading: Body Scan
   Snippet: ...

2. ~/notes/business/sontera.md
   Heading: Offers
   Snippet: ...
```

---

## 6.2 `assistant ask`

Ask a question using local notes and optional local model reasoning.

Example:

```bash
assistant ask "What do my notes say about sound healing?"
```

Purpose:

`assistant ask` turns a natural-language question into a grounded answer using the local notes index. It should behave like a retrieval-based question answering command, not a general chatbot.

Flow:

```text
User input
↓
Normalize question
↓
Search SQLite FTS5 notes index
↓
Retrieve top matching chunks
↓
Build grounded context from chunks
↓
Local synthesis
↓
Answer with sources
↓
Log run
```

Expected answer style:

* Direct answer
* Supporting notes
* Source references
* Optional next action

Command shape:

```bash
assistant ask "QUESTION" [--limit N] [--no-model]
```

Arguments and options:

* `QUESTION`: Required natural-language question.
* `--limit N`: Optional maximum number of chunks to retrieve. Default: `5`.
* `--no-model`: Optional flag to disable local model synthesis and use deterministic extractive synthesis.

Retrieval behavior:

1. Use the existing SQLite FTS5 index.
2. Search against chunk content and headings.
3. Retrieve the top ranked chunks, ordered by FTS rank.
4. Include document path, heading, chunk index, and snippet for each source.
5. Do not use vector search in Phase 1.
6. Do not call a remote model in Phase 1.

Synthesis behavior:

1. The answer must be grounded only in retrieved chunks.
2. If a local model provider is configured, pass the question and retrieved chunks into a simple prompt template.
3. If no local model provider is configured, produce an extractive answer from the strongest matching chunks.
4. If the retrieved chunks do not contain enough evidence, say so directly.
5. Do not invent facts, sources, or user preferences.
6. Prefer concise answers over broad summaries.

Prompt contract for optional local model:

```text
You answer questions using only the provided local notes.
If the notes do not contain enough information, say that clearly.
Return:
1. Direct answer
2. Supporting notes
3. Sources
4. Optional next action

Question:
{question}

Local notes:
{retrieved_chunks}
```

Output format:

```text
Answer:
...

Supporting notes:
- ...
- ...

Sources:
1. ~/notes/path/to/file.md - Heading - chunk 3
2. ~/notes/another-file.md - chunk 1

Next action:
...
```

Empty-result behavior:

If no relevant chunks are found, return:

```text
Answer:
I could not find relevant notes for that question.

Sources:
None

Next action:
Try rephrasing the question or run `assistant index` if your notes changed.
```

Logging requirements:

Each `assistant ask` run must log:

* Original question
* Normalized query
* Route: `local_answer`
* Retrieved source references
* Whether local model synthesis was used
* Final answer summary
* Errors, if any

Success criteria:

* Answers are based on local notes only.
* Sources are always shown when chunks are used.
* Unsupported questions produce an explicit insufficient-context response.
* The command works without a configured model.
* The command writes a local run log.

---

## 6.3 `assistant run`

Execute a registered local tool.

Example:

```bash
assistant run revenue-report
```

Flow:

```text
User input
↓
Lookup tool
↓
Check approval requirement
↓
Execute uv command
↓
Capture stdout/stderr
↓
Return result
↓
Log run
```

Example tool registry entry:

```yaml
tools:
  revenue-report:
    description: Generate monthly revenue report
    command: "uv run python tools/revenue_report.py"
    requires_approval: false
    risk: low
```

---

## 6.4 `assistant research`

Use remote LLMs selectively for deeper tasks.

Example:

```bash
assistant research "best architecture for local-first AI agents"
```

Flow:

```text
User input
↓
Search local notes
↓
Decide whether remote research is justified
↓
Call remote model
↓
Local synthesis
↓
Store summary
↓
Log run
```

Remote models should be used only when the local system cannot reasonably answer.

---

# 7. Routing Logic

The orchestrator should classify each request.

## 7.1 Route Types

```text
local_search
local_answer
local_tool
local_llm
remote_llm
approval_required
clarification_required
```

## 7.2 Simple Routing Rules

| User Intent                  | Default Route            |
| ---------------------------- | ------------------------ |
| Search notes                 | `local_search`           |
| Ask about personal knowledge | `local_answer`           |
| Execute known command        | `local_tool`             |
| Summarize retrieved notes    | `local_llm`              |
| Deep research                | `remote_llm`             |
| External/current information | `remote_llm`             |
| Destructive local action     | `approval_required`      |
| Ambiguous request            | `clarification_required` |

## 7.3 Escalation Rules

Use a remote model only when:

* The task requires deep synthesis
* The local model is insufficient
* The task needs web-scale knowledge
* The task is complex coding or architecture work
* The value justifies the cost

Do not use remote models for:

* Basic note search
* Simple summaries
* Local task lookup
* Simple command execution
* Routine status questions

---

# 8. Local Model Strategy

## Recommended Default

Use:

```text
llama.cpp server
```

Reasons:

* Lightweight
* Efficient
* Infrastructure-friendly
* OpenAI-compatible API
* Easy to swap providers

## Alternative

Use:

```text
llama-cpp-python
```

Useful when:

* Python integration becomes central
* You want tighter control inside the Python app
* You prefer a single-process architecture

## Suggested Provider Interface

```python
class ModelProvider:
    def complete(self, messages: list[dict], **kwargs) -> str:
        ...
```

---

# 9. Tool Execution Strategy

Use Python scripts as the universal action interface.

Tool execution should be explicit and logged.

Example structure:

```text
tools/
├─ analyze_expenses.py
├─ transcribe_audio.py
├─ generate_report.py
└─ registry.yaml
```

Example command:

```bash
uv run python tools/analyze_expenses.py
```

Tools should return structured output where possible:

```json
{
  "status": "ok",
  "summary": "...",
  "artifacts": []
}
```

---

# 10. Logging and Observability

Every command should create a run record.

Minimum logged data:

* Timestamp
* Command
* User input
* Route selected
* Notes retrieved
* Model used
* Tool executed
* Errors
* Final response summary

This is important because the assistant should become debuggable and improvable over time.

---

# 11. Phase 1 Scope

## Must Have

```bash
assistant search "..."
assistant ask "..."
assistant run "..."
```

Capabilities:

* Index `~/notes`
* Search Markdown notes with SQLite FTS5
* Retrieve chunks
* Summarize retrieved context
* Execute registered Python tools through `uv`
* Log all actions

## Should Have

```bash
assistant research "..."
```

Capabilities:

* Escalate to remote model manually or by simple routing rule
* Store research summaries locally

## Should Not Have Yet

* Autonomous loops
* Multi-agent execution
* Background jobs
* Complex planners
* Vector DB
* Web UI
* Fine-tuning
* Agent frameworks

---

# 12. First Milestones

## Milestone 1: Note Search

Command:

```bash
assistant search "sound healing"
```

Success criteria:

* Finds relevant Markdown files
* Returns useful snippets
* Shows source paths
* Runs locally

## Milestone 2: Local Answering

Command:

```bash
assistant ask "What do my notes say about sound healing?"
```

Success criteria:

* Searches notes
* Retrieves relevant chunks
* Produces concise answer
* Includes sources

## Milestone 3: Personal Priorities Query

Command:

```bash
assistant ask "What business ideas am I currently pursuing?"
```

Success criteria:

* Retrieves notes across different topics
* Synthesizes them into a useful answer
* Does not hallucinate unsupported ideas

## Milestone 4: Tool Execution

Command:

```bash
assistant run example-tool
```

Success criteria:

* Looks up tool in registry
* Runs via `uv`
* Captures output
* Logs execution

## Milestone 5: Research Mode

Command:

```bash
assistant research "best architecture for local-first AI assistants"
```

Success criteria:

* Uses local context first
* Escalates to remote model only when justified
* Stores research result locally

---

# 13. Recommended Implementation Order

## Step 1: Project Skeleton

```text
assistant/
├─ cli/
├─ notes/
├─ orchestrator/
├─ providers/
├─ tools/
├─ logs/
└─ config/
```

## Step 2: SQLite FTS Indexer

Build:

```bash
assistant index
assistant search "..."
```

## Step 3: Ask Command

Build:

```bash
assistant ask "..."
```

Use a simple prompt template over retrieved chunks.

## Step 4: Tool Registry

Build:

```bash
assistant run <tool-name>
```

Use `uv run` commands from `tools/registry.yaml`.

## Step 5: Provider Abstraction

Add:

* Local model provider
* Remote model provider

## Step 6: Routing

Start with simple rules.

Do not build a complex planner yet.

## Step 7: Logs

Add persistent run logs early.

Logs will make the system easier to debug and improve.

---

# 14. Minimal MVP Definition

The MVP is complete when this works:

```bash
assistant index
assistant search "sound healing"
assistant ask "What do my notes say about sound healing?"
assistant run example-tool
```

And every command writes a local log entry.

---

# 15. Design Constraints

The system should be:

* Local-first
* CLI-first
* Inspectable
* Simple
* Modular
* Provider-agnostic
* Tool-friendly
* Log-driven
* Easy to extend

Avoid premature abstraction.

The first working version should be boring, reliable, and easy to debug.
