# UX / UI Design Guideline

Keep the assistant's two surfaces — the Typer CLI and the Textual UI —
consistent, intuitive, and local-first.

## CLI Output (Rich)

- Commands are verbs; flags use `--kebab-case` with clear help text.
- Render human output with Rich tables/panels using the shared dashboard styles
  (`DASHBOARD_*` constants in `cli.py`) so colors stay consistent across
  commands.
- Print machine-readable lines (paths, ids, summaries) with `markup=False` so
  values are not reinterpreted as Rich markup.
- Offer `--format json` where a command's output is likely to be piped, matching
  the existing task commands.
- Errors go to the stderr console with a meaningful message, then exit `1`.
  Never print a raw traceback as the primary error for expected failures.
- Long work shows a status spinner only when attached to a terminal
  (`console.is_terminal`); stay quiet when output is redirected.

## Textual UI

- Provide explicit, always-visible key bindings; no hidden shortcuts.
- Show full, context-dependent information on screen — current view, counts,
  selection, and active filters.
- Use a command palette for quick navigation between views.
- Standard keys: `ctrl+q` to quit, `esc` to close the current screen/dialog.
- Run blocking work (search, indexing, model calls) in Textual workers; report
  completion with a toast/notification carrying the result status.
- Keep styling in TCSS, organized so themes and colors can change independently
  of layout.

## Shared Principles

- Consistency first: the same concept (tags, status, paths, run results) looks
  the same in both surfaces.
- Local-first is visible: make it obvious when an action stays local versus when
  it would reach a remote provider (and that remote is opt-in).
- Error messages are specific and actionable — say what failed and what to do.
