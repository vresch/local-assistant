# Roadmap

Source: `spec.md`

## Status Values

- `proposed`: Captured as an idea, but not committed.
- `planned`: Accepted into the roadmap.
- `active`: Currently being worked on.
- `blocked`: Cannot move forward without a decision, dependency, or external change.
- `done`: Completed and accepted.
- `deferred`: Intentionally postponed.
- `cancelled`: Removed from the roadmap.

## Recommended Order

| Order | Phase | Status | Outcome |
| --- | --- | --- | --- |
| 1 | Phase 1: Local Retrieval CLI | `done` | Build the local index/search/ask/run/log core. |
| 2 | Phase 2: Better Local Knowledge Quality | `done` | Improve retrieval quality and source usefulness. |
| 3 | Phase 4: Tooling Layer | `done` | Make local tool execution controlled and practical. |
| 4 | Phase 5: Local LLM Support | `done` | Add optional local generation after deterministic behavior works. |
| 5 | Phase 3: Assistant Memory And Task State | `proposed` | Add lightweight local state across sessions. |
| 6 | Phase 6: Note Workflows | `proposed` | Add practical note operations. |
| 7 | Phase 7: Project-Aware Mode | `proposed` | Extend indexing/search to local project folders. |
| 8 | Phase 8: TUI Or Minimal UI | `proposed` | Improve ergonomics after commands stabilize. |
| 9 | Phase 9: Reliability And Packaging | `proposed` | Harden the assistant for regular local use. |

## Ordering Rationale

- Finish the local CLI core first.
- Improve retrieval quality before adding broader behavior.
- Make tool execution useful before model-dependent workflows.
- Add local LLM support only after the non-LLM core works.
- Add UI and packaging after the command model is proven.

## Phase 1: Local Retrieval CLI

Status: `done`

Outcome: Build the local index/search/ask/run/log core.

Required capabilities:

- Index Markdown notes from `~/notes`.
- Store searchable chunks in SQLite + FTS5.
- Search notes with source paths and snippets.
- Answer questions from retrieved local notes.
- Execute registered Python tools through `uv`.
- Log all commands locally.
- Work without configured remote LLM support.

MVP command path:

```bash
assistant index
assistant search "sound healing"
assistant ask "What do my notes say about sound healing?"
assistant run example-tool
```

## Phase 2: Better Local Knowledge Quality

Status: `done`

Outcome: Improve retrieval quality and source usefulness.

Scope:

- Add richer chunk metadata: title, heading path, tags, modified time.
- Improve ranking with FTS/BM25, exact-title matches, heading matches, and optional recency boost.
- Add search filters: `--tag`, `--path`, `--since`, and `--limit`.
- Reindex only changed files.
- Show source citations consistently in `assistant ask`.
- Add result inspection commands such as `assistant show <result-id>`.
- Add tests for metadata extraction, filtering, ranking, and incremental indexing.

Acceptance criteria:

- Re-running `assistant index` skips unchanged files.
- Changed and deleted notes are reflected correctly.
- Search supports `--limit`, `--tag`, `--path`, and `--since`.
- Search results include title, heading path, modified date, tags when available, and source path.
- `assistant ask` citations are consistent and readable.
- At least one result inspection command exists.
- Tests cover metadata extraction, incremental indexing, filtering, ranking behavior, and citation formatting.
- No remote service is required.

## Phase 4: Tooling Layer

Status: `done`

Outcome: Make local tool execution controlled and practical.

Implemented behavior:

- Tool manifests support `risk`, `permissions`, typed `args`, `timeout_seconds`, and `working_dir`.
- Registry loading remains backwards compatible with old command-only tool entries.
- Registry validation rejects unknown risks, unknown permissions, invalid args, invalid tool names, and duplicate list entries.
- `assistant run <tool> --arg name=value` parses and validates typed arguments.
- Args render into the command list in manifest order without shell interpolation.
- `assistant run <tool> --dry-run` prints the resolved command, risk, permissions, and approval requirement without executing.
- `assistant run <tool> --approve` explicitly authorizes medium/high risk tools and tools with `requires_approval: true`.
- The runner captures stdout, stderr, return code, duration, timeout status, structured JSON output, and artifacts.
- Tool run logs include the resolved command, args, approval result, return code, duration, summary, and artifacts.

Default built-in tools:

- `note-create`
- `note-append-daily`
- `file-search`
- `project-inspect`

## Phase 5: Local LLM Support

Status: `done`

Outcome: Add optional local generation after deterministic behavior works.

Implemented behavior:

- A stable local provider interface exists behind `assistant.providers.local`.
- `assistant ask` can use a configured local model provider for note-grounded synthesis.
- Supported local providers are `llama-cpp-python` and `llama.cpp-server`.
- Local provider settings are configured with `ASSISTANT_LOCAL_PROVIDER`, `ASSISTANT_LOCAL_MODEL`, context size, max tokens, temperature, base URL, and timeout.
- Older `ASSISTANT_LLAMA_*` settings remain compatibility aliases for the in-process `llama-cpp-python` provider.
- `assistant ask --no-model` always forces extractive local-note answers.
- `assistant ask --model-provider <provider>` can select a local provider for one request.
- `assistant ask --model-required` fails fast when a valid local provider is not configured.
- `assistant search` remains purely SQLite/FTS and never requires a model.
- Local model usage, model name, fallback behavior, prompt chunk count, and prompt character count are logged.

Safety constraints:

- Remote models are not used by `assistant ask`.
- If no local model is configured, `assistant ask` still returns an extractive answer from retrieved notes.
- If retrieved notes are insufficient, the assistant says so directly instead of inventing details.
- Local model prompts are grounded in retrieved note chunks and include source-aware context.

## Phase 3: Assistant Memory And Task State

Status: `proposed`

Outcome: Add lightweight local state across sessions.

Possible work:

- Add local task/session history.
- Add `assistant history`.
- Add `assistant remember "fact"`.
- Add `assistant forget <id>`.
- Store local user preferences.
- Add saved searches or pinned notes.
- Keep assistant-generated memory separate from indexed user notes.

## Phase 6: Note Workflows

Status: `proposed`

Outcome: Add practical note operations.

Possible work:

- Add `assistant daily`.
- Add `assistant capture "thought"`.
- Add `assistant summarize path/to/note.md`.
- Add backlinks and related-note discovery.
- Detect duplicate or near-duplicate notes.
- Support Markdown frontmatter.

## Phase 7: Project-Aware Mode

Status: `proposed`

Outcome: Extend indexing/search to local project folders.

Possible work:

- Add separate indexes for notes and projects.
- Add `assistant project index`.
- Add `assistant project search`.
- Add configurable include/exclude globs.
- Add code-aware chunking.
- Extract README, spec, package, and dependency metadata.
- Add `assistant ask --project "question"`.

## Phase 8: TUI Or Minimal UI

Status: `proposed`

Outcome: Improve ergonomics after commands stabilize.

Possible work:

- Add interactive search results.
- Add source previews.
- Browse recent runs and logs.
- Run approved tools from the UI.
- Select retrieved sources for `assistant ask`.

## Phase 9: Reliability And Packaging

Status: `proposed`

Outcome: Harden the assistant for regular local use.

Possible work:

- Add installable CLI packaging.
- Add config discovery and validation.
- Add database migrations.
- Add backup/export commands.
- Handle index corruption and rebuilds.
- Add logging retention.
- Expand tests around indexing, search, tools, and logs.
- Improve documentation.
