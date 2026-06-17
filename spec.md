# Local-First Personal AI Assistant Specification

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
  - [Optional Roadmap Table](#optional-roadmap-table)
- [6. CLI Commands](#6-cli-commands)
  - [`assistant index`](#assistant-index)
  - [`assistant search`](#assistant-search)
  - [`assistant ask`](#assistant-ask)
  - [`assistant run`](#assistant-run)
  - [`assistant research`](#assistant-research)

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
assistant status
```

Optional behavior must remain local-first:

* `assistant research` may use remote LLMs only when explicitly configured.
* `assistant ui` should be read-only if included.
* `assistant status` should only inspect or update local roadmap state.

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

Phase 4 extends tools with a v2 manifest while keeping the registry-to-runner shape:

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
ALTER TABLE documents ADD COLUMN title TEXT;
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

### Optional Roadmap Table

Use only if roadmap/status tracking is implemented in the app instead of a checked-in file.

```sql
CREATE TABLE roadmap_items (
  id INTEGER PRIMARY KEY,
  phase TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL,
  summary TEXT,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL
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
    command: "uv run python tools/revenue_report.py"
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

### `assistant status`

Optional local roadmap control command.

Examples:

```bash
assistant status
assistant status phase-1
assistant status set phase-2 active
assistant status set phase-4 planned
```

Rules:

* Read roadmap items from local storage or a checked-in roadmap file.
* Show phases in priority order.
* Allow explicit status changes.
* Log status changes as local events.
* Do not trigger model calls, remote research, tool execution, or automatic planning.

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

## 10. Status Control

Status control tracks roadmap progress. It is optional for Phase 1 unless explicitly prioritized.

Allowed statuses:

```text
proposed
planned
active
blocked
done
deferred
cancelled
```

Status meanings:

* `proposed`: Captured as an idea, but not committed.
* `planned`: Accepted into the roadmap.
* `active`: Currently being worked on.
* `blocked`: Cannot move forward without a decision, dependency, or external change.
* `done`: Completed and accepted.
* `deferred`: Intentionally postponed.
* `cancelled`: Removed from the roadmap.

Transition rules:

* `proposed` may become `planned`, `deferred`, or `cancelled`.
* `planned` may become `active`, `deferred`, or `cancelled`.
* `active` may become `blocked`, `done`, or `deferred`.
* `blocked` may become `active`, `deferred`, or `cancelled`.
* `done` is terminal unless manually reopened.
* `cancelled` is terminal unless manually restored.

## 11. Roadmap

Recommended order:

| Order | Phase | Initial Status | Outcome |
| --- | --- | --- | --- |
| 1 | Phase 1: Local Retrieval CLI | `done` | Build the local index/search/ask/run/log core. |
| 2 | Phase 2: Better Local Knowledge Quality | `done` | Improve retrieval quality and source usefulness. |
| 3 | Phase 4: Tooling Layer | `done` | Make local tool execution controlled and practical. |
| 4 | Phase 5: Local LLM Support | `done` | Add optional local generation after deterministic behavior works. |
| 5 | Phase 3: Assistant Memory And Task State | `done` | Add lightweight local task state across sessions. |
| 6 | Phase 6: Note Workflows | `proposed` | Add practical note operations. |
| 7 | Phase 7: Project-Aware Mode | `proposed` | Extend indexing/search to local project folders. |
| 8 | Phase 8: TUI Or Minimal UI | `proposed` | Improve ergonomics after commands stabilize. |
| 9 | Phase 9: Reliability And Packaging | `proposed` | Harden the assistant for regular local use. |

Ordering rationale:

* Finish the local CLI core first.
* Improve retrieval quality before adding broader behavior.
* Make tool execution useful before model-dependent workflows.
* Add local LLM support only after the non-LLM core works.
* Add UI and packaging after the command model is proven.

### Phase Details

#### Phase 1: Local Retrieval CLI

See the Phase 1 contract above.

#### Phase 2: Better Local Knowledge Quality

Goal:

Make local search and `assistant ask` more trustworthy, explainable, and efficient without introducing vector search, background workers, or remote dependencies.

Phase 2 should improve the existing retrieval system rather than change the assistant's architecture.

##### Scope

Must have:

* Add richer chunk metadata: title, heading path, tags, modified time.
* Improve ranking with FTS/BM25, exact-title matches, heading matches, and optional recency boost.
* Add search filters: `--tag`, `--path`, `--since`, and `--limit`.
* Reindex only changed files.
* Show source citations consistently in `assistant ask`.
* Add result inspection commands: `assistant show <result-id>` and/or `assistant open <note>`.
* Add tests for metadata extraction, filtering, ranking, and incremental indexing.

Should have:

* Extract Markdown title from the first H1, then fall back to filename.
* Track heading path for chunks, not only the nearest heading.
* Extract simple tags from Markdown frontmatter and inline `#tag` tokens.
* Preserve line ranges where practical for source display.
* Make snippets stable and readable.

Out of scope:

* Vector embeddings.
* Semantic rerankers.
* Background indexing.
* Cross-device sync.
* Automatic note rewriting.
* Assistant-generated long-term memory.
* Remote LLM use for indexing or search.

##### Data Model

Phase 2 should keep the Phase 1 SQLite schema compatible and add metadata incrementally.

Document metadata:

* `title`
* `path`
* `modified_at`
* `indexed_at`
* `content_hash`
* `file_size`
* `tags_json`

Chunk metadata:

* `document_id`
* `chunk_index`
* `content`
* `heading`
* `heading_path`
* `token_count`
* `start_line`
* `end_line`

Tag storage can start as `tags_json` on `documents`. A normalized tag table is not required until filtering or reporting needs it.

##### Indexing Behavior

`assistant index` should become incremental.

Rules:

1. Discover Markdown files under the configured notes path.
2. Compute each file's `content_hash`, `modified_at`, and `file_size`.
3. Skip files whose stored hash and metadata are unchanged.
4. Reindex files that are new or changed.
5. Remove database records for deleted files.
6. Update FTS rows atomically with chunk changes.
7. Report indexed, skipped, removed, and failed counts.

Expected output shape:

```text
Indexed notes:
  New: 3
  Updated: 12
  Skipped: 1842
  Removed: 1
  Failed: 0
```

##### Search Behavior

`assistant search` should support:

```bash
assistant search "query" --limit 10
assistant search "query" --tag business
assistant search "query" --path projects/sontera
assistant search "query" --since 2026-01-01
```

Ranking inputs:

* FTS5/BM25 score.
* Exact title match boost.
* Heading and heading-path match boost.
* Optional recency boost based on `modified_at`.

Ranking must remain deterministic and explainable. Search results should include enough information to understand why a result matched.

Output shape:

```text
Found 7 results:

1. ~/notes/business/sontera.md
   Title: Sontera
   Heading: Offers > Group Sessions
   Modified: 2026-05-18
   Tags: business, sound
   Score: 12.4
   Snippet: ...
```

##### Ask Behavior

`assistant ask` should use the improved metadata in source citations.

Rules:

* Always show source paths when chunks are used.
* Prefer title + heading path over raw filenames in the answer body.
* Keep exact paths available in the `Sources` section.
* If the same document contributes multiple chunks, group citations by document where readable.
* Do not use metadata as evidence unless the chunk content supports the answer.

##### Result Inspection

Add at least one inspect/open command after search results become identifiable.

Possible commands:

```bash
assistant show <result-id>
assistant open <note>
```

`assistant show` should print the full chunk and metadata from the most recent search result set or a stable stored result reference.

`assistant open` may open a note path using the local environment, but should be optional because opening GUI apps may require platform-specific handling.

##### Acceptance Criteria

Phase 2 is complete when:

* Re-running `assistant index` skips unchanged files.
* Changed and deleted notes are reflected correctly.
* Search supports `--limit`, `--tag`, `--path`, and `--since`.
* Search results include title, heading path, modified date, tags when available, and source path.
* `assistant ask` citations are consistent and readable.
* At least one result inspection command exists.
* Tests cover metadata extraction, incremental indexing, filtering, ranking behavior, and citation formatting.
* No remote service is required.

#### Phase 4: Tooling Layer

Status: implemented for the local Phase 4 core.

Phase 4 extends the existing registry-to-runner path rather than replacing it:

```text
YAML registry -> ToolSpec -> validated command list -> run_tool() -> local logs
```

Implemented behavior:

* Tool manifests support `risk`, `permissions`, typed `args`, `timeout_seconds`, and `working_dir`.
* Registry loading remains backwards compatible with old command-only tool entries.
* Registry validation rejects unknown risks, unknown permissions, invalid args, invalid tool names, and duplicate list entries.
* `assistant run <tool> --arg name=value` parses and validates typed arguments.
* Args render into the command list in manifest order without shell interpolation.
* `assistant run <tool> --dry-run` prints the resolved command, risk, permissions, and approval requirement without executing.
* `assistant run <tool> --approve` explicitly authorizes medium/high risk tools and tools with `requires_approval: true`.
* The runner captures stdout, stderr, return code, duration, timeout status, structured JSON output, and artifacts.
* Tool run logs include tool name, resolved command, args, dry-run vs execution, risk, permissions, approval result, return code, duration, structured summary, and artifacts.

Default built-in tools:

* `note-create`: create a Markdown note under `ASSISTANT_NOTES_DIR`; medium risk.
* `note-append-daily`: append a bullet to a daily note; medium risk.
* `file-search`: find files by local filename pattern; low risk.
* `project-inspect`: summarize basic project files from the current working directory; low risk.

Safety constraints:

* Commands and args must stay as `list[str]` through execution.
* Tool args must never be converted into shell strings.
* Write tools must confine writes to their configured local scope.
* Remote behavior remains disabled unless explicitly configured elsewhere.

#### Phase 5: Local LLM Support

Status: implemented for the local Phase 5 core.

Phase 5 adds optional local model synthesis while keeping deterministic local behavior as the default fallback.

Implemented behavior:

* A stable local provider interface exists behind `assistant.providers.local`.
* `assistant ask` can use a configured local model provider for note-grounded synthesis.
* Supported local providers are `llama-cpp-python` and `llama.cpp-server`.
* Local provider settings are configured with `ASSISTANT_LOCAL_PROVIDER`, `ASSISTANT_LOCAL_MODEL`, context size, max tokens, temperature, base URL, and timeout.
* Older `ASSISTANT_LLAMA_*` settings remain compatibility aliases for the in-process `llama-cpp-python` provider.
* `assistant ask --no-model` always forces extractive local-note answers.
* `assistant ask --model-provider <provider>` can select a local provider for one request.
* `assistant ask --model-required` fails fast when a valid local provider is not configured.
* `assistant search` remains purely SQLite/FTS and never requires a model.
* Local model usage, model name, fallback behavior, prompt chunk count, and prompt character count are logged.
* Tests cover provider request/response handling, missing model files, missing dependencies, and local provider prompt behavior.

Safety constraints:

* Remote models are not used by `assistant ask`.
* If no local model is configured, `assistant ask` still returns an extractive answer from retrieved notes.
* If retrieved notes are insufficient, the assistant must say so directly instead of inventing details.
* Local model prompts must be grounded in retrieved note chunks and include source-aware context.

#### Phase 3: Assistant Memory And Task State

Outcome: Add lightweight local task state across sessions.

Scope:

* Track tasks, status, priority, timestamps, and optional context.
* Store task state locally in SQLite.
* Keep task state separate from indexed user notes, logs, roadmap status, and assistant memory facts.
* Do not add planning agents, background workers, reminders, or remote model behavior.

Proposed task table:

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
```

Allowed task statuses:

```text
open
active
blocked
done
cancelled
```

MVP command path:

```bash
assistant task add "Review local provider tests"
assistant task list
assistant task list --status open
assistant task show 12
assistant task set 12 --status active
assistant task set 12 --priority 1
assistant task note 12 "Blocked on config decision"
assistant task done 12
assistant task cancel 12
```

Acceptance criteria:

* Tasks persist across CLI sessions.
* Tasks are stored separately from indexed notes.
* `assistant task add/list/show/set/done/cancel` works.
* Invalid statuses and priorities are rejected.
* Task commands write local log entries.
* No remote model, tool execution, or background process is required.

Implemented behavior:

* Task state lives in `assistant/state/tasks.py`.
* Task storage helpers support create, list, get, update, complete, cancel, and task notes.
* Task notes are stored in `task_events`.
* `assistant task` supports text output by default and JSON output with `--format json`.
* Tests cover task creation, status transitions, filtering, persistence, CLI logging, and JSON output.

#### Phase 6: Note Workflows

Possible work:

* Add `assistant daily`.
* Add `assistant capture "thought"`.
* Add `assistant summarize path/to/note.md`.
* Add backlinks and related-note discovery.
* Detect duplicate or near-duplicate notes.
* Support Markdown frontmatter.

#### Phase 7: Project-Aware Mode

Possible work:

* Add separate indexes for notes and projects.
* Add `assistant project index`.
* Add `assistant project search`.
* Add configurable include/exclude globs.
* Add code-aware chunking.
* Extract README, spec, package, and dependency metadata.
* Add `assistant ask --project "question"`.

#### Phase 8: TUI Or Minimal UI

Possible work:

* Add interactive search results.
* Add source previews.
* Browse recent runs and logs.
* Run approved tools from the UI.
* Select retrieved sources for `assistant ask`.

#### Phase 9: Reliability And Packaging

Possible work:

* Add installable CLI packaging.
* Add config discovery and validation.
* Add database migrations.
* Add backup/export commands.
* Handle index corruption and rebuilds.
* Add logging retention.
* Expand tests around indexing, search, tools, and logs.
* Improve documentation.

## 12. Design Constraints

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
