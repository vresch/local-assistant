# Local Assistant

CLI-first, local-first personal AI assistant for Markdown notes and local Python tools.

The assistant indexes Markdown notes into SQLite FTS5, answers from retrieved notes,
runs registered Python tools through `uv`, and logs actions locally. Optional local
model synthesis and remote research support stay disabled unless configured.

## Documentation Map

- [README](#local-assistant): install, configure, and use the CLI.
- [Specification](../spec.md): product contract, architecture, data model, and command behavior.
- [Roadmap](../ROADMAP.md): phase status, accepted work, and proposed next work.
- [Agent instructions](../AGENTS.md): implementation constraints for coding agents.

## Contents

- [Documentation Map](#documentation-map)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Daily Workflow](#daily-workflow)
- [Command Reference](#command-reference)
  - [Index](#index)
  - [Search](#search)
  - [Ask](#ask)
  - [Research](#research)
  - [Note Workflows](#note-workflows)
  - [Tasks](#tasks)
  - [Inspect](#inspect)
- [Tool Usage](#tool-usage)
- [Configuration](#configuration)
- [Storage](#storage)
- [Development](#development)

## Quick Start

Requires Python 3.10 or newer and `uv`.

From the package directory:

```bash
cd assistant
uv sync
```

For repo-local development state, load the checked-in development environment:

```bash
set -a
source .env.development
set +a
mkdir -p "$ASSISTANT_NOTES_DIR"
```

This points the assistant at:

- `.local/notes`
- `.local/assistant/assistant.db`
- `tools/registry.yaml`
- `.local/assistant/debug.log`

Index notes and search them:

```bash
uv run assistant index
uv run assistant search "project alpha"
uv run assistant ask "What did I decide about search?"
```

## Installation

Use this when you want to type `assistant ...` from any shell instead of
`uv run assistant ...`.

From the repository root, install the convenience wrapper into
`~/.local/bin/assistant`:

```bash
./local/bin/deploy-assistant
```

The installer checks that `uv` is available, writes an executable wrapper for
the current checkout, and prints the test command.

Check that it is executable and wired correctly:

```bash
ls -l ~/.local/bin/assistant
assistant --help
assistant search "project alpha"
```

If `assistant` is not found, add this to your shell config:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

For a more standard Python CLI install, use the package entry point directly:

```bash
uv tool install --editable ./assistant
```

Use the wrapper installer when you want the command to always run from this
checkout with the repo-local environment and lockfile.

## Daily Workflow

1. Put Markdown notes in `ASSISTANT_NOTES_DIR`, which defaults to `~/notes`.
2. Run `uv run assistant index`.
3. Use `search`, `show`, and `ask` to inspect indexed content.
4. Use `run` to execute registered local tools through `uv`.
5. Review local logs and dashboard output when you need auditability.

The local-only path is:

```bash
uv run assistant index
uv run assistant search "sound healing"
uv run assistant ask "What do my notes say about sound healing?" --no-model
uv run assistant run hello
```

## Command Reference

All commands write local run logs.

| Command | Purpose | Remote behavior |
| --- | --- | --- |
| `assistant index` | Index Markdown notes into SQLite FTS5. | None |
| `assistant search` | Search indexed note chunks. | None |
| `assistant show` | Inspect one indexed chunk. | None |
| `assistant ask` | Answer from retrieved local notes. | Never remote |
| `assistant research` | Research with local notes first. | Optional, only when configured |
| `assistant capture` | Save a quick inbox Markdown note. | None |
| `assistant daily` | Show or append to today's daily Markdown note. | None |
| `assistant backlinks` | Show notes that link to an indexed note. | None |
| `assistant related` | Show locally related notes. | None |
| `assistant summarize` | Summarize one note extractively. | None |
| `assistant task ...` | Track local task state. | None |
| `assistant dashboard` | Show read-only terminal status. | None |
| `assistant ui` | Open the Textual workflow TUI. | Same as selected workflow |
| `assistant run` | Execute registered local tools. | None unless the tool itself does it |

### Index

Index Markdown notes into SQLite:

```bash
uv run assistant index
```

Indexing skips unchanged files by content hash and removes deleted files from the index.

### Search

Search indexed chunks:

```bash
uv run assistant search "project alpha"
```

Filter by limit, tag, path, or modification date:

```bash
uv run assistant search "project alpha" --limit 10 --tag business --path projects --since 2026-01-01
```

Search output includes chunk IDs. Show a specific chunk and its metadata:

```bash
uv run assistant show 42
```

### Ask

Answer from retrieved notes:

```bash
uv run assistant ask "What did I decide about search?"
```

By default, `ask` uses an extractive answer when no local provider is configured.
Force local-only extractive behavior:

```bash
uv run assistant ask "What did I decide about search?" --no-model
```

Use a configured local model provider for one request:

```bash
uv run assistant ask "What did I decide about search?" --model-provider llama-cpp-python
```

Fail instead of falling back when no valid local provider is configured:

```bash
uv run assistant ask "What did I decide about search?" --model-required
```

Supported local providers are `llama-cpp-python` and `llama.cpp-server`.
`assistant ask` never calls a remote provider.

The in-process `llama-cpp-python` provider is an optional extra (it builds a
native library). Install it only when you want local generation:

```bash
uv sync --extra local-llm
```

Without it, `assistant ask` still works and returns extractive local-note
answers. The `llama.cpp-server` provider needs no extra; it talks to a running
server over HTTP.

### Research

Research is optional and remains local-first. It searches local notes first, then may
escalate to a configured remote provider only when remote configuration is present.

```bash
uv run assistant research "best architecture for local-first AI assistants"
```

Force local-only research:

```bash
uv run assistant research "best architecture for local-first AI assistants" --no-remote
```

Force the configured remote provider after local retrieval:

```bash
uv run assistant research "best architecture for local-first AI assistants" --force-remote --limit 8
```

### Note Workflows

Capture a quick thought as a Markdown inbox note:

```bash
uv run assistant capture "Follow up on the local search ranking idea #inbox/to-read"
```

Show today's daily note path, or append an entry:

```bash
uv run assistant daily
uv run assistant daily --text "Reviewed the SQLite link index."
```

Inspect links and related notes after indexing:

```bash
uv run assistant backlinks notes/project-alpha.md
uv run assistant related notes/project-alpha.md
```

Summarize one note locally without a model provider:

```bash
uv run assistant summarize notes/project-alpha.md
```

Notes remain plain Markdown files under `ASSISTANT_NOTES_DIR`. Optional YAML
frontmatter is indexed for title, tags, aliases, type, status, created, and
updated fields. Wikilinks and local Markdown links are indexed for backlink and
related-note discovery.

### Tasks

Task state is local SQLite state, separate from indexed notes and roadmap status.

Add a task:

```bash
uv run assistant task add "Review local provider tests"
```

Add task details:

```bash
uv run assistant task add "Review local provider tests" \
  --description "Confirm fallback behavior and missing model handling" \
  --priority 2 \
  --related-path assistant/tests/test_local_providers.py
```

List, filter, and inspect tasks:

```bash
uv run assistant task list
uv run assistant task list --status open
uv run assistant task show 1
```

Update task state:

```bash
uv run assistant task set 1 --status active
uv run assistant task set 1 --priority 1
uv run assistant task note 1 "Blocked on deciding exact config behavior"
uv run assistant task done 1
uv run assistant task cancel 2
```

Task commands support machine-readable output:

```bash
uv run assistant task add "Review JSON output" --format json
uv run assistant task list --format json
uv run assistant task show 1 --format json
```

### Inspect

Open the read-only terminal dashboard:

```bash
uv run assistant dashboard
```

Open the Textual workflow TUI:

```bash
uv run assistant ui
```

The TUI keeps the CLI as the source of truth. It calls the same search, ask, tool,
database, and logging functions used by CLI commands.

TUI workflows:

- `Ask`: ask from local notes with the configured local model enabled by default, falling back to extractive answers when no local provider is configured; selected sources can be used explicitly.
- `Search`: query indexed notes, filter by limit/tag/path/since, preview chunks, and add chunks to the selected source basket.
- `Sources`: inspect and clear the in-memory selected source basket.
- `Tools`: inspect registered tools, dry-run commands, and run approved tools.
- `Runs`: inspect recent command runs and their event timeline.
- `Logs`: browse local events without making raw storage tables the primary workflow.
- `Storage`: inspect raw indexed documents and chunks when needed.

Useful keys:

```text
/       focus search input
enter   preview selected item
a       toggle selected search result in the source basket
o       show full selected source
r       refresh
d       dry-run selected tool
q       quit
```

Workflow-specific keys only act in their matching tab; for example, source selection
keys are scoped to Search and tool dry-runs are scoped to Tools.

Maintenance commands:

```bash
uv run assistant categorise-notes
uv run assistant save-llm-summary
uv run assistant clean-db
uv run assistant clean-db --include-logs
```

## Tool Usage

Tools are registry-driven and local-first.

- Tool definitions live in `tools/registry.yaml`.
- Commands are stored and executed as `list[str]`, not shell strings.
- `assistant run` validates manifest arguments before execution.
- `--dry-run` prints the resolved command, risk, permissions, and approval requirement.
- Medium/high risk tools, or tools with `requires_approval: true`, require `--approve`.
- Tool runs log command metadata, approval state, return code, duration, summaries, and artifacts.

Run a registered tool:

```bash
uv run assistant run hello
```

Pass typed manifest arguments with repeated `--arg name=value` options:

```bash
uv run assistant run file-search --arg pattern=*.md --arg root=.
```

Preview without executing:

```bash
uv run assistant run note-create --arg path=inbox/idea --arg title="Idea" --dry-run
```

Approve a medium/high risk or approval-required tool:

```bash
uv run assistant run note-create --arg path=inbox/idea --arg title="Idea" --approve
```

Example registry entry:

```yaml
tools:
  report:
    description: Generate a local report.
    command: ["python", "-m", "assistant.tools.project_inspect"]
    requires_approval: false
    risk: low
    permissions: ["read"]
    timeout_seconds: 30
    args:
      - name: month
        type: str
        required: false
        flag: "--month"
        description: Month to report, formatted as YYYY-MM.
```

Built-in tools:

- `hello`: smoke-test command.
- `categorise-notes`: categorise indexed notes with local keyword rules.
- `note-create`: create a Markdown note under `ASSISTANT_NOTES_DIR`.
- `note-append-daily`: append a bullet to `daily/YYYY-MM-DD.md`.
- `file-search`: find files by pattern from a local root.
- `project-inspect`: summarize basic project files from the current working directory.

## Configuration

Configuration is read from environment variables and from the nearest `.env` file found
from the current working directory upward.

Core paths:

| Variable | Default |
| --- | --- |
| `ASSISTANT_HOME` | `~/.local/share/local-assistant` |
| `ASSISTANT_NOTES_DIR` | `~/notes` |
| `ASSISTANT_DB_PATH` | `$ASSISTANT_HOME/assistant.db` |
| `ASSISTANT_REGISTRY_PATH` | `./tools/registry.yaml` |
| `ASSISTANT_DEBUG_LOG_PATH` | `$ASSISTANT_HOME/debug.log` |
| `ASSISTANT_LLM_SUMMARY_PATH` | `$ASSISTANT_HOME/last-llm-request.md` |
| `ASSISTANT_RESEARCH_DIR` | `$ASSISTANT_NOTES_DIR/research` |

Local model settings:

| Variable | Default |
| --- | --- |
| `ASSISTANT_LOCAL_PROVIDER` | unset |
| `ASSISTANT_LOCAL_MODEL` | unset |
| `ASSISTANT_LOCAL_CONTEXT_SIZE` | `4096` |
| `ASSISTANT_LOCAL_MAX_TOKENS` | `256` |
| `ASSISTANT_LOCAL_TEMPERATURE` | `0.2` |
| `ASSISTANT_LOCAL_BASE_URL` | provider default |
| `ASSISTANT_LOCAL_TIMEOUT` | `30` |

Compatibility aliases are still supported:

- `ASSISTANT_LLAMA_MODEL_PATH`
- `ASSISTANT_LLAMA_CONTEXT_SIZE`
- `ASSISTANT_LLAMA_MAX_TOKENS`
- `ASSISTANT_LLAMA_TEMPERATURE`

Remote research settings:

| Variable | Default |
| --- | --- |
| `ASSISTANT_REMOTE_PROVIDER` | unset |
| `ASSISTANT_REMOTE_MODEL` | unset |
| `ASSISTANT_REMOTE_API_KEY` | unset |
| `ASSISTANT_REMOTE_BASE_URL` | `https://api.openai.com/v1` |
| `ASSISTANT_REMOTE_TIMEOUT` | `30` |

Remote behavior is disabled unless configured and only used by optional research flows.

## Storage

SQLite stores:

- `documents`: title, path, tags, file size, content hash, and modification metadata.
- `chunks`: searchable note chunks with heading paths, line ranges, and token estimates.
- `chunks_fts`: SQLite FTS5 search index.
- `tasks`: local task state with status, priority, timestamps, and optional context.
- `task_events`: task notes and task-local events.
- `runs`: command-level execution records.
- `run_events`: detailed events for indexing, search, ask, research, task, and tool runs.

Generated research summaries are written under `ASSISTANT_RESEARCH_DIR`.

## Development

Run tests:

```bash
uv run pytest
```

Run static checks:

```bash
uv run ruff check .
uv run mypy src
```
