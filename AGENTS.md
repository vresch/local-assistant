# Project: Local-First Personal AI Assistant

## Goal

Build Phase 1 of a CLI-first local AI assistant.

The assistant should:
- Index Markdown notes from ~/notes
- Store searchable chunks in SQLite + FTS5
- Provide CLI commands:
  - assistant index
  - assistant search "query"
  - assistant ask "question"
  - assistant run <tool>
- Execute registered Python tools through uv
- Log all actions locally

## Constraints

- Keep architecture simple.
- Do not use LangChain, LlamaIndex, vector DBs, background workers, or multi-agent frameworks.
- Use SQLite FTS5 for retrieval.
- Use Python.
- Use Typer for CLI.
- Prefer boring, testable code.
- Add tests for core indexing/search behavior.
- Keep the Phase 1 core usable without remote LLM support. Existing remote research support is optional and must remain disabled unless configured; do not expand remote behavior unless explicitly requested.

## Target structure

assistant/
  cli.py
  config.py
  notes/
    indexer.py
    search.py
    chunker.py
  tools/
    registry.py
    runner.py
  logs/
    logger.py
  orchestrator.py

tests/
  test_chunker.py
  test_search.py
