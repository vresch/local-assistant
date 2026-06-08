# Local Assistant

Phase 1 MVP for a CLI-first, local-first personal AI assistant.

## Install

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

Ask a question using retrieved notes. If `ASSISTANT_LLAMA_MODEL_PATH` points to a local GGUF model,
the answer is synthesized with llama.cpp; otherwise it falls back to an extractive answer:

```bash
uv run assistant ask "What did I decide about search?"
```

Skip model synthesis even when a model is configured:

```bash
uv run assistant ask "What did I decide about search?" --no-model
```

Show a read-only terminal dashboard for stored notes, recent runs, and LLM usage:

```bash
uv run assistant dashboard
```

Clean indexed note data from the SQLite database:

```bash
uv run assistant clean-db
```

Clear indexed data and old run logs:

```bash
uv run assistant clean-db --include-logs
```

Run a registered tool through `uv`:

```bash
uv run assistant run hello
```

Tools are loaded from `tools/registry.yaml` by default:

```yaml
tools:
  hello:
    description: Print a smoke-test message.
    command: ["python", "-c", "print('hello from assistant tool')"]
    requires_approval: false
```

The `requires_approval` flag is recorded in logs, but interactive approval is not implemented in Phase 1.

## Configuration

Environment variables:

- `ASSISTANT_NOTES_DIR`: notes directory, defaults to `~/notes`
- `ASSISTANT_DB_PATH`: SQLite database path, defaults to `~/.local/share/local-assistant/assistant.db`
- `ASSISTANT_REGISTRY_PATH`: tool registry path, defaults to `./tools/registry.yaml`
- `ASSISTANT_DEBUG_LOG_PATH`: debug log file path, defaults to `~/.local/share/local-assistant/debug.log`
- `ASSISTANT_HOME`: base directory for default local state
- `ASSISTANT_LLAMA_MODEL_PATH`: optional path to a local GGUF model for `assistant ask`
- `ASSISTANT_LLAMA_CONTEXT_SIZE`: llama.cpp context window, defaults to `4096`
- `ASSISTANT_LLAMA_MAX_TOKENS`: max generated answer tokens, defaults to `256`
- `ASSISTANT_LLAMA_TEMPERATURE`: generation temperature, defaults to `0.2`

## Data

The SQLite database stores:

- `documents`
- `chunks`
- `chunks_fts`
- `runs`
- `run_events`

Markdown files are skipped on subsequent indexing runs when their content hash has not changed.

## Tests

```bash
uv run pytest
```
