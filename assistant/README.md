# Local Assistant

Phase 1 MVP with Phase 2 note metadata and search inspection for a CLI-first, local-first personal AI assistant.

The core Phase 1 path is fully local: index Markdown notes, search them with SQLite FTS5, answer from retrieved notes, run registered Python tools through `uv`, and log actions locally. Optional model-backed commands are available, but the assistant works without a configured model or remote provider.

## Table Of Contents

- [Install](#install)
- [Development Environment](#development-environment)
- [Usage](#usage)
- [Tool Usage](#tool-usage)
- [Configuration](#configuration)
- [Data](#data)
- [Tests](#tests)

## Install

Requires Python 3.10 or newer.

```bash
uv sync
```

## Development Environment

Use the checked-in development env file to keep local testing inside the repo:

```bash
set -a
source .env.development
set +a
mkdir -p "$ASSISTANT_NOTES_DIR"
```

This points the assistant at `.local/notes`, `.local/assistant/assistant.db`, `tools/registry.yaml`, and `.local/assistant/debug.log`.

## Usage

Index Markdown notes from `~/notes`:

```bash
uv run assistant index
```

Search indexed notes:

```bash
uv run assistant search "project alpha"
```

Search supports metadata filters and prints chunk IDs for inspection:

```bash
uv run assistant search "project alpha" --limit 10 --tag business --path projects --since 2026-01-01
uv run assistant show 42
```

Ask a question using retrieved notes. If a local provider is configured, the answer is synthesized with
that provider; otherwise it falls back to an extractive answer:

```bash
uv run assistant ask "What did I decide about search?"
```

Use a specific configured local provider for one request:

```bash
uv run assistant ask "What did I decide about search?" --model-provider llama-cpp-python
```

Skip model synthesis even when a model is configured:

```bash
uv run assistant ask "What did I decide about search?" --no-model
```

Fail instead of falling back when no local provider is configured:

```bash
uv run assistant ask "What did I decide about search?" --model-required
```

Optional extension: research a question using local notes first, with optional remote escalation when configured:

```bash
uv run assistant research "best architecture for local-first AI assistants"
```

Force local-only research or force a configured remote model after local retrieval:

```bash
uv run assistant research "best architecture for local-first AI assistants" --no-remote
uv run assistant research "best architecture for local-first AI assistants" --force-remote --limit 8
```

Show a read-only terminal dashboard for stored notes, recent runs, the latest LLM request summary, and LLM usage:

```bash
uv run assistant dashboard
```

Save a markdown summary of the most recent `assistant ask` run:

```bash
uv run assistant save-llm-summary
```

Clean indexed note data from the SQLite database:

```bash
uv run assistant clean-db
```

Clear indexed data and old run logs:

```bash
uv run assistant clean-db --include-logs
```

## Tool Usage

The tool layer is registry-driven and local-first:

- Tool definitions live in `tools/registry.yaml`.
- Commands are stored and executed as `list[str]`, not shell strings.
- `assistant run` validates manifest args before execution.
- `--dry-run` shows the resolved command, risk, permissions, and approval requirement without running it.
- Medium/high risk tools, and tools with `requires_approval: true`, require `--approve`.
- Tool runs log the resolved command, args, risk, permissions, approval result, return code, duration, structured summary, and artifacts when available.

Run a registered tool through `uv`:

```bash
uv run assistant run hello
```

Pass typed manifest arguments with repeated `--arg name=value` options:

```bash
uv run assistant run file-search --arg pattern=*.md --arg root=.
```

Preview the resolved command, risk, permissions, and approval requirement without executing:

```bash
uv run assistant run note-create --arg path=inbox/idea --arg title="Idea" --dry-run
```

Medium/high risk tools and tools with `requires_approval: true` require explicit approval:

```bash
uv run assistant run note-create --arg path=inbox/idea --arg title="Idea" --approve
```

Tools are loaded from `tools/registry.yaml` by default:

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

Tool manifests support `risk`, `permissions`, typed `args`, `timeout_seconds`, and `working_dir`. Commands and rendered args remain lists and are executed without shell interpolation.

Built-in tools included in the default registry:

- `note-create`: create a Markdown note under `ASSISTANT_NOTES_DIR`; medium risk, requires `--approve`.
- `note-append-daily`: append a bullet to `daily/YYYY-MM-DD.md`; medium risk, requires `--approve`.
- `file-search`: find files by pattern from a local root; low risk.
- `project-inspect`: summarize basic project files from the current working directory; low risk.

## Configuration

Environment variables:

- `ASSISTANT_NOTES_DIR`: notes directory, defaults to `~/notes`
- `ASSISTANT_DB_PATH`: SQLite database path, defaults to `~/.local/share/local-assistant/assistant.db`
- `ASSISTANT_REGISTRY_PATH`: tool registry path, defaults to `./tools/registry.yaml`
- `ASSISTANT_DEBUG_LOG_PATH`: debug log file path, defaults to `~/.local/share/local-assistant/debug.log`
- `ASSISTANT_LLM_SUMMARY_PATH`: saved last LLM request summary path, defaults to `last-llm-request.md` under `ASSISTANT_HOME`
- `ASSISTANT_RESEARCH_DIR`: stored research markdown directory, defaults to `research` under `ASSISTANT_NOTES_DIR`
- `ASSISTANT_HOME`: base directory for default local state
- `ASSISTANT_LOCAL_PROVIDER`: optional local provider, currently `llama-cpp-python` or `llama.cpp-server`
- `ASSISTANT_LOCAL_MODEL`: local model path for `llama-cpp-python`, or model name for server providers
- `ASSISTANT_LOCAL_CONTEXT_SIZE`: local context window, defaults to `4096`
- `ASSISTANT_LOCAL_MAX_TOKENS`: max generated answer tokens, defaults to `256`
- `ASSISTANT_LOCAL_TEMPERATURE`: generation temperature, defaults to `0.2`
- `ASSISTANT_LOCAL_BASE_URL`: local HTTP provider base URL, defaults to `http://127.0.0.1:8080` for `llama.cpp-server`
- `ASSISTANT_LOCAL_TIMEOUT`: local HTTP provider timeout in seconds, defaults to `30`
- `ASSISTANT_LLAMA_MODEL_PATH`: compatibility alias for `ASSISTANT_LOCAL_MODEL` with `llama-cpp-python`
- `ASSISTANT_LLAMA_CONTEXT_SIZE`: compatibility alias for `ASSISTANT_LOCAL_CONTEXT_SIZE`
- `ASSISTANT_LLAMA_MAX_TOKENS`: compatibility alias for `ASSISTANT_LOCAL_MAX_TOKENS`
- `ASSISTANT_LLAMA_TEMPERATURE`: compatibility alias for `ASSISTANT_LOCAL_TEMPERATURE`
- `ASSISTANT_REMOTE_PROVIDER`: optional remote provider, currently `openai-compatible`
- `ASSISTANT_REMOTE_MODEL`: optional remote model for `assistant research`
- `ASSISTANT_REMOTE_API_KEY`: optional remote provider API key
- `ASSISTANT_REMOTE_BASE_URL`: OpenAI-compatible API base URL, defaults to `https://api.openai.com/v1`
- `ASSISTANT_REMOTE_TIMEOUT`: remote request timeout in seconds, defaults to `30`

## Data

The SQLite database stores:

- `documents` with title, file size, tags, content hash, and mtime metadata
- `chunks` with heading paths, line ranges, and approximate token counts
- `chunks_fts`
- `runs`
- `run_events`

Markdown files are skipped on subsequent indexing runs when their content hash has not changed. Deleted files are removed from the index.

## Tests

```bash
uv run pytest
```
