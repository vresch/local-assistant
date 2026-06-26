# Investigation, Review, Planning, Prototyping

How to work on the local assistant in different modes. Each mode ends with a
timestamped report under `docs/reports/`.

## Investigation Mode

Understand a problem or domain before building.

- Survey existing solutions — open-source, free, and commercial — for the
  capability in question (e.g. local embeddings, FTS ranking, model runners).
- Learn the relevant domain and define the scope and constraints, keeping the
  local-first, offline-by-default principle in mind.
- Output: `docs/reports/investigation_{timestamp}.md` with findings, options,
  and a recommendation.

## Review Mode

Assess the current project.

- Read the code and docs; find weak spots, unclear decisions, and
  inconsistencies across the CLI, services, providers, and tests.
- Check that commands stay thin, services own logic, run logging is intact, and
  the README matches real behavior.
- Output: `docs/reports/review_{timestamp}.md` with prioritized issues and
  suggested fixes.

## Planning Mode

Turn a goal into a staged roadmap.

- Break the goal into small parts where each step leaves a working product with a
  new, usable capability (one service + one command + tests).
- Sequence parts to reduce real risk first; align with `ROADMAP.md` and
  `docs/IMPLEMENTATION_PLAN.md`.
- Output: `docs/reports/plan_{timestamp}.md`.

## Prototyping Mode

Produce a minimal working slice to validate an idea.

- Write the smallest runnable script or module that solves the task — enough for
  an MVP, not production hardening.
- Keep it offline and self-contained where possible.
- Output: `docs/reports/prototype_{timestamp}.md` describing the prototype's
  structure, plus the prototype code under `src/` (or a clearly marked scratch
  location).

After any mode, if the work leads to implementation, fold it into the
implementation plan and follow the
[Implementation Guideline](IMPLEMENTATION_GUIDELINE.md).
