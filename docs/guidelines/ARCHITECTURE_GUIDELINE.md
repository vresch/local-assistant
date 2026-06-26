# Architecture Guideline

Use this guideline to extend the local assistant: a CLI-first, local-first
personal AI over Markdown notes. It indexes notes into SQLite FTS5, answers from
retrieved notes, optionally calls a local model, and runs registered local
tools.

The goal is a consistent layered shape:

```text
storage (SQLite FTS5) and domain functions
service / workflow layer
shared settings model
Typer CLI and Textual UI presentation layers
console_script entry point (assistant)
```

## Core Principle

Local-first and explicit. Default behavior never reaches the network. `ask`
answers only from indexed notes; remote reasoning is opt-in via `research` and
only when a provider is configured. Keep each capability (index, search, ask,
research, capture, tasks, run) behind its own service so boundaries stay clear.

## Standard Structure

The package lives under `assistant/src/assistant/`:

```text
assistant/
├── README.md
├── pyproject.toml
├── docs/
│   ├── guidelines/
│   ├── IMPLEMENTATION_PLAN.md
│   └── reports/
├── src/assistant/
│   ├── cli.py            # Typer app: one command per capability
│   ├── ui.py             # Textual database/workflow browser
│   ├── config.py         # Settings model + get_settings()
│   ├── db.py             # connection + schema + cleanup
│   ├── orchestrator.py   # ask: retrieve -> synthesize
│   ├── research.py       # research: local-first, optional remote
│   ├── notes/            # indexer, chunker, search, metadata, links, workflows
│   ├── providers/        # local.py, remote.py model providers
│   ├── state/            # tasks.py and other local state
│   ├── tools/            # registry.py, runner.py for registered local tools
│   └── logs/             # logger.py (run logging), debug.py
└── tests/
```

When a capability is added, place pure logic in the matching `notes/`,
`providers/`, `state/`, or `tools/` module and expose it through one `cli.py`
command. Do not grow `cli.py` with domain logic.

## Entry Point

The CLI is a Typer app exposed as the `assistant` console script
(`pyproject.toml` `[project.scripts]`). Commands are thin: they read settings,
open a connection, start a run, call a service, log the result, and print.

```python
@app.command()
def capture(text: str) -> None:
    settings = get_settings()
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "capture", text, "notes.capture")
        result = capture_note(conn, settings.notes_dir, text)
        finish_run(conn, run_id, "succeeded", f"path={result.path}")
```

Do not put indexing, retrieval, chunking, or model calls directly in command
bodies. Delegate to a service or workflow function.

## Settings Model

Configuration lives in one frozen `Settings` dataclass built by `get_settings()`
from `ASSISTANT_*` environment variables (and an optional env file). Pass
`Settings` (or the specific values it holds) into services. Never read
`os.environ` inside a service or notes module.

Key settings include `db_path`, `notes_dir`, `research_dir`, `registry_path`,
`debug_log_path`, `local_provider`/`local_model`, and the remote provider fields.
Tests override these with env vars (`ASSISTANT_NOTES_DIR`, `ASSISTANT_DB_PATH`,
etc.), so keep all paths settings-driven.

## CLI Layer (Typer)

The CLI owns option definitions and conversion to service calls.

Responsibilities:

- declare commands, arguments, and `typer.Option` flags with help text
- accept paths as `pathlib.Path`
- validate output formats / inputs at the command boundary (`typer.BadParameter`)
- open the DB connection and own the run-logging lifecycle
- render results with Rich; print machine output with `markup=False`

The CLI should not: run queries directly, chunk or embed text, call providers,
or build SQL. It maps user input to a service and formats the result.

## Service / Workflow Layer

Services own application behavior and are importable without Typer.

Responsibilities:

- validate domain inputs and raise `ValueError`/`KeyError` for the CLI to map
- read and write the SQLite store through `db.py` connections
- compose pure helpers (chunker, metadata, links, search)
- return dataclasses (e.g. `CaptureResult`, `NoteSummary`, `IndexStats`)

`notes/workflows.py`, `notes/indexer.py`, `notes/search.py`, `state/tasks.py`,
and `orchestrator.py` are service-layer modules. They take a `sqlite3.Connection`
and settings-derived paths, never a Typer context.

## Providers (External Models)

Wrap model backends behind `providers/local.py` and `providers/remote.py`.

- build providers from `Settings`; fail with a clear message when unconfigured
- keep the local path fully offline; remote is only reached on explicit opt-in
- a missing or misconfigured provider must degrade gracefully (extractive
  local-note answer), not crash `ask`
- isolate provider-specific request/response shaping in the provider module so a
  backend can be swapped without touching the orchestrator

## Registered Local Tools

External commands run through the tool registry, not ad-hoc `subprocess` calls.

- define tools in the registry (`tools/registry.py`); build argv as a list
- execute via `tools/runner.py` with a timeout; never use a shell string
- respect `risk` and `requires_approval` — medium/high risk requires `--approve`
- support `--dry-run` to print the resolved command without executing

## UI Layer (Textual)

`ui.py` is a read-and-act presentation layer over the same services.

The UI may display indexed documents, runs, logs, tasks, and search results, and
call services at screen-level boundaries. It must not build SQL, call providers
directly, or duplicate service logic. Run blocking work (search, model calls) in
Textual workers and update widgets from the app thread. See the
[UX/UI Design Guideline](UX_UI_DESIGN_GUIDELINE.md).

## Run Logging

Every command records a run. Use `start_run`, `log_event`, `update_run_route`,
and `finish_run` from `logs/logger.py`, and `get_debug_logger` for debug output.
Log the `llm` decision (provider/model or `none`) even when no model is used, so
the dashboard and `save-llm-summary` stay accurate. This run trail is part of the
architecture, not optional instrumentation.

## Exit Codes

Keep CLI exit codes stable:

- `0`: success
- `1`: user input / validation error (`typer.Exit(1)` after a friendly message)
- non-zero from `run`: propagate the tool's return code

Map `ValueError`/`KeyError` to exit `1` with a readable stderr message; let
unexpected exceptions surface after the run is marked failed.

## Documentation Contract

Keep these current:

- `README.md`: how to install and run every command
- `docs/IMPLEMENTATION_PLAN.md`: staged roadmap and current state
- `docs/reports/`: one report per completed implementation part

## Agent Workflow

When extending the assistant:

1. Read `docs/IMPLEMENTATION_PLAN.md` and recent `docs/reports/`.
2. Inspect the affected module under `src/assistant/`.
3. Implement one complete slice: service logic + one CLI command + tests.
4. Keep CLI/UI thin; keep SQL and model calls in services/providers.
5. Run `pytest`.
6. Update the README and module plan, then write a report.
