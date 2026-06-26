# Implementation Guideline

This guideline explains how to keep implementation work on the local assistant
split into small, consistent parts. It is a shared process document, not a status
report.

Use it with:

- [Architecture Guideline](ARCHITECTURE_GUIDELINE.md)
- [Project Documentation Guideline](PROJECT_DOCUMENTATION_GUIDELINE.md)
- [Testing Guideline](TESTING_GUIDELINE.md)

Current project state belongs in `docs/IMPLEMENTATION_PLAN.md` and `ROADMAP.md`,
not in this guideline.

## Purpose

Implementation documentation has two layers:

- This guideline: the reusable method for keeping work small, consistent,
  reviewable, and documented.
- The implementation plan / roadmap: the current state — what exists, what is
  missing, and which small parts come next.

Do not put completion status in this guideline. Link to the plan instead.

## Small Part Rules

Each implementation part should be a complete, narrow functional slice — usually
**one capability = one service function + one `cli.py` command + tests**.

A good part:

- has one user-visible or maintainer-visible goal
- touches the fewest layers needed (e.g. `notes/` module + `cli.py` + a test)
- leaves the assistant runnable, with `pytest` green
- includes validation and error handling appropriate to the risk
- updates the README/plan and adds a report

Avoid parts that create empty layers, move files without preserving behavior, or
mix unrelated goals (a provider change plus a UI redesign plus packaging).

## Consistency Checklist

Before starting a part:

1. Read `README.md`, `docs/IMPLEMENTATION_PLAN.md`, `ROADMAP.md`, and recent
   `docs/reports/`.
2. Identify the affected boundary:
   - `cli.py` command
   - `config.py` settings
   - a `notes/`, `state/`, or `tools/` service module
   - `providers/` (local/remote model)
   - `db.py` schema
   - `ui.py`
   - tests
   - documentation
3. Confirm the part has a clear done state.
4. Match existing naming: commands are verbs, services return dataclasses, env
   vars are `ASSISTANT_*`.

During implementation:

1. Keep domain logic out of `cli.py` and `ui.py`.
2. Pass `Settings` (or its values) and a `sqlite3.Connection` between layers;
   never read `os.environ` in a service.
3. Keep external process calls in `tools/runner.py`; keep model calls in
   `providers/`.
4. Keep validation close to the service that owns the rule; map errors to
   `typer.Exit(1)` at the command boundary.
5. Use test doubles / temp dirs instead of real models or network in tests.
6. Preserve the run-logging lifecycle (`start_run` / `log_event` / `finish_run`).
7. Prefer extending existing patterns over new abstractions.

Before finishing:

1. Run `pytest` (and the specific test file for the changed layer).
2. Update `docs/IMPLEMENTATION_PLAN.md` and the README command reference.
3. Add a report in `docs/reports/`.
4. Confirm the README describes the real command workflow.

## Documentation Consistency

Keep these in sync:

- `README.md`: install, run, and per-command usage.
- `docs/IMPLEMENTATION_PLAN.md`: current state and next parts.
- `ROADMAP.md`: longer-term direction.
- `docs/reports/`: completed work with checks and residual risks.
- shared `docs/guidelines/`: reusable rules only.

When a part changes behavior, update the plan and README in the same change.

## Report Format

Every completed part should create a report:

```text
docs/reports/report_{timestamp}.md   e.g. report_2026-06-26_14-30-00.md
```

Each report includes: task summary, files changed, behavior added/changed,
checks run (e.g. `pytest` result), and remaining risks or follow-ups.

## Recommended Part Sequence

A default sequence for a new capability:

1. Add the pure logic to the right module (`notes/`, `state/`, `tools/`,
   `providers/`).
2. Expose it as one thin `cli.py` command with run logging.
3. Add tests: unit tests for the logic, a `CliRunner` test for the command.
4. Add settings/env-var support if the capability needs configuration.
5. Surface it in `ui.py` only after the CLI and service are stable.
6. Harden failure handling (graceful degradation, clear messages).

Choose the next smallest part that reduces real risk.

## Acceptance Standard

A part is complete when:

- the assistant still runs through its documented commands
- `pytest` passes (or skips are justified)
- changed behavior is documented in the README/plan
- the report captures remaining risks
