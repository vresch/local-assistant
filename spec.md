# Local-First Personal AI Assistant Specification

This file owns product behavior, architecture boundaries, storage contracts, and command
semantics. Delivery status belongs in [ROADMAP.md](ROADMAP.md); setup and day-to-day
usage belong in [assistant/README.md](assistant/README.md).

## Table Of Contents

- [1. Purpose](#1-purpose)
- [2. Phase 1 Contract](#2-phase-1-contract)
  - [Must Have](#must-have)
  - [Optional In Phase 1](#optional-in-phase-1)
  - [Out Of Scope For Phase 1](#out-of-scope-for-phase-1)
  - [MVP Definition](#mvp-definition)
- [3. Design Principles](#3-design-principles)
  - [Local-First](#local-first)
  - [Notes As Primary Memory](#notes-as-primary-memory)
  - [Python As Action Layer](#python-as-action-layer)
  - [Assistant-First Design](#assistant-first-design)
- [4. Architecture](#4-architecture)
  - [Component Boundaries](#component-boundaries)
- [5. Data Storage](#5-data-storage)
  - [Core Tables](#core-tables)
  - [Phase 2 Metadata Extensions](#phase-2-metadata-extensions)
  - [Logging Tables](#logging-tables)
  - [Task State Tables](#task-state-tables)
- [6. CLI Commands](#6-cli-commands)
  - [`assistant index`](#assistant-index)
  - [`assistant search`](#assistant-search)
  - [`assistant ask`](#assistant-ask)
  - [`assistant run`](#assistant-run)
  - [`assistant research`](#assistant-research)
  - [`assistant ui`](#assistant-ui)
- [7. Routing](#7-routing)
  - [Route Types](#route-types)
  - [Default Rules](#default-rules)
  - [Remote Escalation Rules](#remote-escalation-rules)
- [8. Providers](#8-providers)
- [9. Logging And Observability](#9-logging-and-observability)
- [10. Design Constraints](#10-design-constraints)

## 1. Purpose

Build a CLI-first personal AI assistant that uses local notes, local tools, and local logs as its core system.

Remote LLMs may be used selectively for deeper reasoning, research, or complex tasks, but they must not own memory, orchestration, execution, or default behavior.

Core philosophy:

```text
Local system owns context.
Remote models rent intelligence.
```

The assistant is a personal operating layer over:

* Markdown notes
* Python tools and scripts
* SQLite search
* Local and optional remote model providers
* Local logs
* User workflows

## 2. Phase 1 Contract

Phase 1 builds the boring, inspectable local core.

### Must Have

Commands:

```bash
assistant index
assistant search "query"
assistant ask "question"
assistant run <tool>
```

Capabilities:

* Index Markdown notes from `~/notes`.
* Store searchable chunks in SQLite + FTS5.
* Search notes with source paths and snippets.
* Answer questions from retrieved local notes.
* Execute registered Python tools through `uv`.
* Log all commands locally.
* Work without configured remote LLM support.

Tests:

* Core Markdown chunking behavior.
* SQLite/FTS search behavior.
* Indexing behavior.
* Tool lookup and command execution boundaries where practical.

### Optional In Phase 1

Commands:

```bash
assistant research "query"
assistant ui
```

Optional behavior must remain local-first:

* `assistant research` may use remote LLMs only when explicitly configured.
* `assistant ui` should be read-only if included.

### Out Of Scope For Phase 1

Avoid:

* Autonomous long-running agents
* Multi-agent frameworks
* Background workers
* Complex planning frameworks
* Vector databases
* Web UI
* Fine-tuning
* Remote behavior enabled by default

### MVP Definition

The MVP is complete when this works:

```bash
assistant index
assistant search "sound healing"
assistant ask "What do my notes say about sound healing?"
assistant run example-tool
```

Every command must write a local log entry.

## 3. Design Principles

### Local-First

All important state should live locally:

* User input
* Retrieved context
* Notes index
* Tool execution logs
* Assistant decisions
* Generated summaries
* Configuration

Remote APIs are optional external workers.

### Notes As Primary Memory

The user's long-term memory is the local Markdown notes directory:

```bash
~/notes
```

These notes originate from Evernote exports and are accessed through Logseq. The assistant should treat this repository as the primary knowledge source.

### Python As Action Layer

Local actions should be delegated to Python scripts managed by `uv`.

Examples:

```bash
assistant run analyze-expenses
assistant run transcribe-audio
assistant run generate-report
```

Internally:

```bash
uv run ...
```

Phase 3 extends tools with a v2 manifest while keeping the registry-to-runner shape:

```yaml
tools:
  report:
    command: ["python", "tools/report.py"]
    risk: medium
    permissions: ["read"]
    timeout_seconds: 60
    args:
      - name: month
        type: str
        required: true
        flag: "--month"
```

Tool args are passed as `assistant run <tool> --arg name=value`. The CLI validates and renders them into the command list in manifest order. `--dry-run` prints and logs the resolved command without executing. `--approve` is required for medium/high risk tools or any tool with `requires_approval: true`.

### Assistant-First Design

The assistant is the product. LLMs are reasoning engines used by the assistant, not the foundation of the system.

The core value comes from:

```text
Knowledge + Execution + Routing
```

## 4. Architecture

Target package shape:

```text
assistant/
  cli.py
  config.py
  db.py
  orchestrator.py
  notes/
    chunker.py
    indexer.py
    search.py
  providers/
    local.py
    remote.py
  tools/
    registry.py
    runner.py
  logs/
    logger.py
  ui.py
```

### Component Boundaries

| Component | Owns | Must Not Own |
| --- | --- | --- |
| CLI | Typer commands, option parsing, user-facing output | Storage internals, model inference |
| Orchestrator | Routing, approval decisions, high-level flow | Note indexing internals, tool implementation |
| Notes | Markdown discovery, parsing, chunking, SQLite/FTS storage, search | Model calls, tool execution |
| Providers | Local/remote model clients behind a small interface | Memory, routing, storage |
| Tools | Tool registry, validation, `uv` execution | Search, model calls |
| Logs | Run records and run events | Business logic |
| Config | Paths, provider settings, registry paths, logging preferences | Runtime decisions |

Provider support is optional. The Phase 1 core must work without any configured model provider.

Local model providers are configured explicitly:

```bash
ASSISTANT_LOCAL_PROVIDER=llama-cpp-python
ASSISTANT_LOCAL_MODEL=/path/to/model.gguf
ASSISTANT_LOCAL_CONTEXT_SIZE=4096
ASSISTANT_LOCAL_MAX_TOKENS=256
ASSISTANT_LOCAL_TEMPERATURE=0.2
```

`llama.cpp-server` is also supported through its OpenAI-compatible chat endpoint:

```bash
ASSISTANT_LOCAL_PROVIDER=llama.cpp-server
ASSISTANT_LOCAL_BASE_URL=http://127.0.0.1:8080
ASSISTANT_LOCAL_MODEL=local
```

Older `ASSISTANT_LLAMA_*` settings remain compatibility aliases for the in-process
`llama-cpp-python` provider. `assistant ask --no-model` always disables provider use.

## 5. Data Storage

Use SQLite + FTS5 for Phase 1. Avoid vector databases.

Reasons:

* Simple
* Local
* Transparent
* Fast enough for Markdown notes
* Easy to debug

### Core Tables

```sql
CREATE TABLE documents (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  title TEXT,
  modified_at TEXT,
  content_hash TEXT
);

CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  heading TEXT,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  content,
  heading,
  content='chunks',
  content_rowid='id'
);
```

### Phase 2 Metadata Extensions

Phase 2 extends the core tables instead of replacing them. Migrations must be idempotent so existing local databases can be opened safely.

Implemented additions:

```sql
ALTER TABLE documents ADD COLUMN file_size INTEGER;
ALTER TABLE documents ADD COLUMN tags_json TEXT;

ALTER TABLE chunks ADD COLUMN heading_path TEXT;
ALTER TABLE chunks ADD COLUMN token_count INTEGER;
ALTER TABLE chunks ADD COLUMN start_line INTEGER;
ALTER TABLE chunks ADD COLUMN end_line INTEGER;
```

These fields support better ranking, filtering, source display, and incremental indexing while keeping SQLite + FTS5 as the retrieval system.

Phase 2 search supports chunk IDs, title and heading-path display, tag/path/since filters, deterministic BM25-based ranking with title and heading boosts, and direct chunk inspection:

```bash
assistant search "query" --limit 10 --tag business --path projects --since 2026-01-01
assistant show <chunk-id>
```

### Logging Tables

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

CREATE TABLE run_events (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);
```

### Task State Tables

Task state is local assistant state, not indexed note content.

```sql
CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 3,
  source TEXT,
  related_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE task_events (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
```

## 6. CLI Commands

### `assistant index`

Index Markdown notes from the configured notes directory.

Behavior:

1. Discover Markdown files under `notes_path`.
2. Parse each note.
3. Split content into chunks.
4. Store document and chunk metadata.
5. Populate SQLite FTS5.
6. Log indexed, skipped, changed, and failed files.

Success criteria:

* Re-running index is safe.
* Changed files are reflected in search.
* Source paths remain stable and inspectable.

### `assistant search`

Search local notes.

Example:

```bash
assistant search "sound healing"
```

Behavior:

1. Search SQLite FTS5 against chunk content and headings.
2. Return matching chunks ordered by rank.
3. Include source file path, heading when available, chunk index, and snippet.
4. Log the query and result count.

Output shape:

```text
Found 7 results:

1. ~/notes/sound-healing/session-structure.md
   Heading: Body Scan
   Chunk: 3
   Snippet: ...
```

### `assistant ask`

Answer a question using local notes and optional local model synthesis.

Command shape:

```bash
assistant ask "QUESTION" [--limit N] [--no-model]
```

Behavior:

1. Normalize the question.
2. Search SQLite FTS5.
3. Retrieve the top matching chunks.
4. Build grounded context from retrieved chunks.
5. Produce an answer with sources.
6. Log the run.

Retrieval rules:

* Use SQLite FTS5 only in Phase 1.
* Include document path, heading, chunk index, and snippet for each source.
* Do not call a remote model from `assistant ask`.

Synthesis rules:

* Answer only from retrieved chunks.
* If a local model provider is configured, pass the question and chunks into a simple prompt.
* If no local model provider is configured, produce an extractive answer from the strongest chunks.
* If evidence is insufficient, say so directly.
* Do not invent facts, sources, or user preferences.

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

Output shape:

```text
Answer:
...

Supporting notes:
- ...

Sources:
1. ~/notes/path/to/file.md - Heading - chunk 3
```

Empty-result output:

```text
Answer:
I could not find relevant notes for that question.

Sources:
None

Next action:
Try rephrasing the question or run `assistant index` if your notes changed.
```

### `assistant run`

Execute a registered local tool.

Example:

```bash
assistant run revenue-report
```

Behavior:

1. Look up the tool in the registry.
2. Validate arguments if a schema exists.
3. Check risk and approval requirements.
4. Execute the configured command through `uv`.
5. Capture stdout, stderr, exit code, and artifacts where available.
6. Log the run and result.

Example registry entry:

```yaml
tools:
  revenue-report:
    description: Generate monthly revenue report
    command: ["python", "tools/revenue_report.py"]
    requires_approval: false
    risk: low
```

Tools should return structured output where possible:

```json
{
  "status": "ok",
  "summary": "...",
  "artifacts": []
}
```

### `assistant research`

Optional extension for deeper tasks using remote LLMs.

Example:

```bash
assistant research "best architecture for local-first AI assistants"
```

Rules:

* Use local context first.
* Use remote LLMs only when explicitly configured.
* Store research summaries locally.
* Log the route, model, sources, and summary.

### `assistant ui`

Optional local terminal UI for inspecting stored notes, recent runs, and assistant state.

Rules:

* Use `Textual`.
* Keep it read-only in Phase 1.
* Do not require network access.

## 7. Routing

The orchestrator classifies user requests into simple routes.

### Route Types

```text
local_search
local_answer
local_tool
local_llm
remote_llm
approval_required
clarification_required
```

### Default Rules

| User Intent | Default Route |
| --- | --- |
| Search notes | `local_search` |
| Ask about personal knowledge | `local_answer` |
| Execute known command | `local_tool` |
| Summarize retrieved notes | `local_llm` |
| Deep research | `remote_llm` |
| External/current information | `remote_llm` |
| Destructive local action | `approval_required` |
| Ambiguous request | `clarification_required` |

### Remote Escalation Rules

Use a remote model only when:

* The task requires deep synthesis.
* The local model is insufficient.
* The task needs web-scale or current information.
* The task is complex coding or architecture work.
* The value justifies the cost.

Do not use remote models for:

* Basic note search.
* Routine note answers.
* Local task lookup.
* Simple command execution.
* Routine status questions.

## 8. Providers

Provider support is optional and must be disabled unless configured.

Recommended local default:

```text
llama.cpp server
```

Alternative:

```text
llama-cpp-python
```

Suggested provider interface:

```python
class ModelProvider:
    def complete(self, messages: list[dict], **kwargs) -> str:
        ...
```

## 9. Logging And Observability

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

Logs make the assistant debuggable and improvable over time.

## 10. Design Constraints

The system should remain:

* Local-first
* CLI-first
* Inspectable
* Simple
* Modular
* Provider-agnostic
* Tool-friendly
* Log-driven
* Easy to extend

Avoid premature abstraction. The first working version should be boring, reliable, and easy to debug.
