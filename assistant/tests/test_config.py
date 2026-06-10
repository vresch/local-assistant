from pathlib import Path

import pytest

from assistant.config import Settings, get_settings, validate_local_model_settings
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
        "ASSISTANT_LOCAL_PROVIDER",
        "ASSISTANT_LOCAL_MODEL",
        "ASSISTANT_LOCAL_CONTEXT_SIZE",
        "ASSISTANT_LOCAL_MAX_TOKENS",
        "ASSISTANT_LOCAL_TEMPERATURE",
        "ASSISTANT_LOCAL_BASE_URL",
        "ASSISTANT_LOCAL_TIMEOUT",
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
ASSISTANT_LOCAL_PROVIDER=llama.cpp-server
ASSISTANT_LOCAL_MODEL=local
ASSISTANT_LOCAL_CONTEXT_SIZE=8192
ASSISTANT_LOCAL_MAX_TOKENS=128
ASSISTANT_LOCAL_TEMPERATURE=0.1
ASSISTANT_LOCAL_BASE_URL=http://127.0.0.1:8080
ASSISTANT_LOCAL_TIMEOUT=3.5
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
    assert settings.local_provider == "llama.cpp-server"
    assert settings.local_model == "local"
    assert settings.local_context_size == 8192
    assert settings.local_max_tokens == 128
    assert settings.local_temperature == 0.1
    assert settings.local_base_url == "http://127.0.0.1:8080"
    assert settings.local_timeout == 3.5
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


def test_validate_local_model_settings_reports_invalid_model_path(tmp_path: Path) -> None:
    settings = Settings(
        notes_dir=tmp_path / "notes",
        db_path=tmp_path / "assistant.db",
        registry_path=tmp_path / "registry.yaml",
        debug_log_path=tmp_path / "debug.log",
        llm_summary_path=tmp_path / "last-llm-request.md",
        research_dir=tmp_path / "research",
        local_provider="llama-cpp-python",
        local_model=tmp_path / "missing.gguf",
        local_context_size=4096,
        local_max_tokens=256,
        local_temperature=0.2,
        local_base_url=None,
        local_timeout=30.0,
        remote_provider=None,
        remote_model=None,
        remote_api_key=None,
        remote_base_url="https://api.openai.com/v1",
        remote_timeout=30.0,
    )

    assert validate_local_model_settings(settings) == [
        f"ASSISTANT_LOCAL_MODEL does not exist: {tmp_path / 'missing.gguf'}"
    ]


def test_validate_local_model_settings_ignores_model_without_provider(tmp_path: Path) -> None:
    settings = Settings(
        notes_dir=tmp_path / "notes",
        db_path=tmp_path / "assistant.db",
        registry_path=tmp_path / "registry.yaml",
        debug_log_path=tmp_path / "debug.log",
        llm_summary_path=tmp_path / "last-llm-request.md",
        research_dir=tmp_path / "research",
        local_provider=None,
        local_model=tmp_path / "missing.gguf",
        local_context_size=4096,
        local_max_tokens=256,
        local_temperature=0.2,
        local_base_url=None,
        local_timeout=30.0,
        remote_provider=None,
        remote_model=None,
        remote_api_key=None,
        remote_base_url="https://api.openai.com/v1",
        remote_timeout=30.0,
    )

    assert validate_local_model_settings(settings) == []


def test_old_llama_env_vars_are_local_provider_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    model_path = tmp_path / "models" / "local.gguf"
    for name in (
        "ASSISTANT_LOCAL_PROVIDER",
        "ASSISTANT_LOCAL_MODEL",
        "ASSISTANT_LOCAL_CONTEXT_SIZE",
        "ASSISTANT_LOCAL_MAX_TOKENS",
        "ASSISTANT_LOCAL_TEMPERATURE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ASSISTANT_LLAMA_MODEL_PATH", str(model_path))
    monkeypatch.setenv("ASSISTANT_LLAMA_CONTEXT_SIZE", "2048")
    monkeypatch.setenv("ASSISTANT_LLAMA_MAX_TOKENS", "99")
    monkeypatch.setenv("ASSISTANT_LLAMA_TEMPERATURE", "0.0")

    settings = get_settings()

    assert settings.local_provider == "llama-cpp-python"
    assert settings.local_model == model_path
    assert settings.local_context_size == 2048
    assert settings.local_max_tokens == 99
    assert settings.local_temperature == 0.0
    assert settings.llama_model_path == model_path


def test_validate_local_model_settings_rejects_invalid_provider(tmp_path: Path) -> None:
    settings = Settings(
        notes_dir=tmp_path / "notes",
        db_path=tmp_path / "assistant.db",
        registry_path=tmp_path / "registry.yaml",
        debug_log_path=tmp_path / "debug.log",
        llm_summary_path=tmp_path / "last-llm-request.md",
        research_dir=tmp_path / "research",
        local_provider="bogus",
        local_model=None,
        local_context_size=4096,
        local_max_tokens=256,
        local_temperature=0.2,
        local_base_url=None,
        local_timeout=30.0,
        remote_provider=None,
        remote_model=None,
        remote_api_key=None,
        remote_base_url="https://api.openai.com/v1",
        remote_timeout=30.0,
    )

    assert validate_local_model_settings(settings) == [
        "ASSISTANT_LOCAL_PROVIDER unsupported: bogus; supported: llama-cpp-python, llama.cpp-server"
    ]


def test_index_refuses_home_directory(tmp_path: Path) -> None:
    with connect(tmp_path / "assistant.db") as conn:
        with pytest.raises(ValueError, match="Refusing to index the home directory"):
            index_notes(conn, Path.home())
