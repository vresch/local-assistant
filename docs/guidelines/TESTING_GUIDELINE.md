# Testing Guideline

How to test the local assistant. Tests use `pytest` and live in
`assistant/tests/`. Run them with:

```bash
cd assistant
uv run python -m pytest -q
uv run python -m pytest tests/test_voice_note_ingest.py -q   # one file
```

## What to Test

Cover every layer, from the inside out:

- **Pure logic** (chunker, metadata, links, search ranking, parsers): plain
  functions with literal inputs and expected outputs. Fast, no DB.
- **Service / workflow layer** (indexer, workflows, tasks, orchestrator): use a
  real temporary SQLite database via `connect(tmp_path / "assistant.db")` and a
  `tmp_path` notes dir. Assert on returned dataclasses and on rows queried back
  from the connection.
- **CLI commands**: drive `assistant.cli.app` with Typer's `CliRunner`, passing
  configuration through `env=` (`ASSISTANT_NOTES_DIR`, `ASSISTANT_DB_PATH`,
  `ASSISTANT_REGISTRY_PATH`, `ASSISTANT_DEBUG_LOG_PATH`, `ASSISTANT_LLAMA_MODEL_PATH=""`).
  Assert on `result.exit_code` and on files/rows produced — not on exact console
  text, which Rich soft-wraps to terminal width.
- **Providers**: never call a real model or network in tests. Inject a fake
  provider or response object so the local/remote paths are exercised offline.

## Conventions

- Use `tmp_path` for all filesystem state; never write into the real notes dir
  or `~`.
- Build fixtures inline and keep each test readable top to bottom.
- Prefer asserting on structured results (dataclasses, DB rows, written files)
  over scraping stdout.
- Test error paths too: bad input should raise `ValueError`/`KeyError` at the
  service layer and exit `1` at the CLI.
- Keep tests deterministic — pass explicit timestamps/ids rather than relying on
  `datetime.now()` where the value is asserted.

## Organization

One test module per behavior area, named `test_<area>.py` (e.g.
`test_search.py`, `test_note_workflows.py`, `test_integration.py`). Integration
tests that exercise `index -> search -> ask` through the CLI belong in
`test_integration.py`.

Every new capability ships with tests in the same change: unit tests for the
logic and at least one `CliRunner` test for the command.
