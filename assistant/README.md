# Local Assistant

CLI-first, local-first personal AI assistant for Markdown notes and local Python tools.

Phase 1 is intentionally boring: Markdown files are chunked, indexed into SQLite FTS5,
searched from the CLI, answered from retrieved notes, and every action is logged locally.
Optional model-backed commands exist, but the core workflow works without a configured
LLM or remote provider.

Current implementation status: Phases 1, 2, 4, and 5 are complete. The assistant now
has the local retrieval core, richer note metadata/search, controlled local tool
execution, built-in workflow tools, and optional local LLM synthesis through configured
local providers. Remote behavior remains disabled unless explicitly configured.

## Contents

- [Quick Start](#quick-start)
- [Core Workflow](#core-workflow)
- [Commands](#commands)
- [Tools](#tools)
- [Configuration](#configuration)
- [Storage](#storage)
- [Development](#development)

## Quick Start

Requires Python 3.10 or newer and `uv`.

```bash
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

## Core Workflow

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

## Commands

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

Local model support is optional. `assistant ask` never calls a remote provider, and
falls back to extractive answers from retrieved notes when no local provider is
configured.

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

### Inspect

Open the read-only terminal dashboard:

```bash
uv run assistant dashboard
```

Open the Textual database browser:

```bash
uv run assistant ui
```

Categorise indexed notes with local keyword rules:

```bash
uv run assistant categorise-notes
```

Save a Markdown summary of the most recent `ask` run:

```bash
uv run assistant save-llm-summary
```

Clean indexed note data:

```bash
uv run assistant clean-db
```

Clean indexed note data and old run logs:

```bash
uv run assistant clean-db --include-logs
```

## Tools

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

The local workflow layer is complete: these built-in tools are available through the
same registry, validation, approval, dry-run, and logging path as custom tools.

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

The SQLite database stores:

- `documents`: title, path, tags, file size, content hash, and modification metadata.
- `chunks`: searchable note chunks with heading paths, line ranges, and token estimates.
- `chunks_fts`: SQLite FTS5 search index.
- `runs`: command-level execution records.
- `run_events`: detailed events for indexing, search, ask, research, and tool runs.

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
