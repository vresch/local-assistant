# Project Documentation Guideline

How documentation is organized for the local assistant and how to keep it in
sync.

## Documents and Owners

- `README.md` (in `assistant/`): user-facing install, quick start, and a command
  reference with one section per command. The first thing a new user reads.
- `ROADMAP.md`: longer-term direction and milestones.
- `spec.md`: the product/behavior specification.
- `AGENTS.md`: instructions for agents working in this repo.
- `docs/guidelines/`: reusable rules (architecture, implementation, testing,
  UX/UI, this file). Not project status.
- `docs/IMPLEMENTATION_PLAN.md`: current state and the next small parts.
- `docs/reports/`: one report per completed implementation part.

## Who Reads What

- A new user reads `README.md`.
- A contributor or agent reads `docs/guidelines/` plus the implementation plan
  before starting work.
- A reviewer reads the relevant `docs/reports/` entry to see what changed and
  what risks remain.

## Keeping It in Sync

When a change adds or alters a command:

1. Update the README command reference table and the command's usage section.
2. Update `docs/IMPLEMENTATION_PLAN.md` to reflect the new state.
3. Add a report under `docs/reports/`.

When a change only touches shared process, update the relevant guideline and
leave project-status docs alone. Guidelines describe how to work; the plan and
reports describe what has been done.

Documentation lives in the same change as the code it describes — never as a
deferred follow-up.
