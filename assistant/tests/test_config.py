from pathlib import Path

import pytest

from assistant.config import Settings, get_settings, validate_llama_settings
from assistant.db import connect
from assistant.notes.indexer import index_notes


def test_settings_load_env_file_relative_to_env_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "ASSISTANT_HOME",
        "ASSISTANT_NOTES_DIR",
        "ASSISTANT_DB_PATH",
        "ASSISTANT_REGISTRY_PATH",
        "ASSISTANT_DEBUG_LOG_PATH",
        "ASSISTANT_LLM_SUMMARY_PATH",
        "ASSISTANT_RESEARCH_DIR",
        "ASSISTANT_LLAMA_MODEL_PATH",
        "ASSISTANT_LLAMA_CONTEXT_SIZE",
        "ASSISTANT_LLAMA_MAX_TOKENS",
        "ASSISTANT_LLAMA_TEMPERATURE",
        "ASSISTANT_REMOTE_PROVIDER",
        "ASSISTANT_REMOTE_MODEL",
        "ASSISTANT_REMOTE_API_KEY",
        "ASSISTANT_REMOTE_BASE_URL",
        "ASSISTANT_REMOTE_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)

    (tmp_path / ".env").write_text(
        """
ASSISTANT_HOME=.local/assistant
ASSISTANT_NOTES_DIR=notes/pages
ASSISTANT_DB_PATH=.local/assistant/assistant.db
ASSISTANT_REGISTRY_PATH=tools/registry.yaml
ASSISTANT_DEBUG_LOG_PATH=.local/assistant/debug.log
ASSISTANT_RESEARCH_DIR=notes/research
ASSISTANT_LLAMA_MODEL_PATH=models/local.gguf
ASSISTANT_LLAMA_CONTEXT_SIZE=8192
ASSISTANT_LLAMA_MAX_TOKENS=128
ASSISTANT_LLAMA_TEMPERATURE=0.1
ASSISTANT_REMOTE_PROVIDER=openai-compatible
ASSISTANT_REMOTE_MODEL=research-model
ASSISTANT_REMOTE_API_KEY=test-key
ASSISTANT_REMOTE_BASE_URL=https://example.test/v1
ASSISTANT_REMOTE_TIMEOUT=12.5
""".strip(),
        encoding="utf-8",
    )

    settings = get_settings()

    assert settings.notes_dir == tmp_path / "notes" / "pages"
    assert settings.db_path == tmp_path / ".local" / "assistant" / "assistant.db"
    assert settings.registry_path == tmp_path / "tools" / "registry.yaml"
    assert settings.debug_log_path == tmp_path / ".local" / "assistant" / "debug.log"
    assert settings.llm_summary_path == tmp_path / ".local" / "assistant" / "last-llm-request.md"
    assert settings.research_dir == tmp_path / "notes" / "research"
    assert settings.llama_model_path == tmp_path / "models" / "local.gguf"
    assert settings.llama_context_size == 8192
    assert settings.llama_max_tokens == 128
    assert settings.llama_temperature == 0.1
    assert settings.remote_provider == "openai-compatible"
    assert settings.remote_model == "research-model"
    assert settings.remote_api_key == "test-key"
    assert settings.remote_base_url == "https://example.test/v1"
    assert settings.remote_timeout == 12.5


def test_environment_variables_override_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASSISTANT_NOTES_DIR", str(tmp_path / "explicit-notes"))
    (tmp_path / ".env").write_text("ASSISTANT_NOTES_DIR=env-file-notes\n", encoding="utf-8")

    settings = get_settings()

    assert settings.notes_dir == tmp_path / "explicit-notes"


def test_validate_llama_settings_reports_invalid_model_path(tmp_path: Path) -> None:
    settings = Settings(
        notes_dir=tmp_path / "notes",
        db_path=tmp_path / "assistant.db",
        registry_path=tmp_path / "registry.yaml",
        debug_log_path=tmp_path / "debug.log",
        llm_summary_path=tmp_path / "last-llm-request.md",
        research_dir=tmp_path / "research",
        llama_model_path=tmp_path / "missing.gguf",
        llama_context_size=4096,
        llama_max_tokens=256,
        llama_temperature=0.2,
        remote_provider=None,
        remote_model=None,
        remote_api_key=None,
        remote_base_url="https://api.openai.com/v1",
        remote_timeout=30.0,
    )

    assert validate_llama_settings(settings) == [
        f"ASSISTANT_LLAMA_MODEL_PATH does not exist: {tmp_path / 'missing.gguf'}"
    ]


def test_index_refuses_home_directory(tmp_path: Path) -> None:
    with connect(tmp_path / "assistant.db") as conn:
        with pytest.raises(ValueError, match="Refusing to index the home directory"):
            index_notes(conn, Path.home())
