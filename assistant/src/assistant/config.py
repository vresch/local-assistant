from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    notes_dir: Path
    db_path: Path
    registry_path: Path
    debug_log_path: Path
    llm_summary_path: Path
    research_dir: Path
    local_provider: str | None
    local_model: str | Path | None
    local_context_size: int
    local_max_tokens: int
    local_temperature: float
    local_base_url: str | None
    local_timeout: float
    remote_provider: str | None
    remote_model: str | None
    remote_api_key: str | None
    remote_base_url: str
    remote_timeout: float

    @property
    def llama_model_path(self) -> Path | None:
        if self.local_provider != "llama-cpp-python" or self.local_model is None:
            return None
        return Path(self.local_model).expanduser()

    @property
    def llama_context_size(self) -> int:
        return self.local_context_size

    @property
    def llama_max_tokens(self) -> int:
        return self.local_max_tokens

    @property
    def llama_temperature(self) -> float:
        return self.local_temperature


def get_settings() -> Settings:
    env_file = find_env_file(Path.cwd())
    file_env = load_env_file(env_file) if env_file else {}
    app_dir = _path_setting(
        "ASSISTANT_HOME",
        Path.home() / ".local" / "share" / "local-assistant",
        file_env,
        env_file,
    )
    notes_dir = _path_setting("ASSISTANT_NOTES_DIR", Path.home() / "notes", file_env, env_file)
    local_provider = _local_provider_setting(file_env)
    return Settings(
        notes_dir=notes_dir,
        db_path=_path_setting("ASSISTANT_DB_PATH", app_dir / "assistant.db", file_env, env_file),
        registry_path=_path_setting(
            "ASSISTANT_REGISTRY_PATH",
            Path.cwd() / "tools" / "registry.yaml",
            file_env,
            env_file,
        ),
        debug_log_path=_path_setting("ASSISTANT_DEBUG_LOG_PATH", app_dir / "debug.log", file_env, env_file),
        llm_summary_path=_path_setting(
            "ASSISTANT_LLM_SUMMARY_PATH",
            app_dir / "last-llm-request.md",
            file_env,
            env_file,
        ),
        research_dir=_path_setting("ASSISTANT_RESEARCH_DIR", notes_dir / "research", file_env, env_file),
        local_provider=local_provider,
        local_model=_local_model_setting(local_provider, file_env, env_file),
        local_context_size=_int_alias_setting("ASSISTANT_LOCAL_CONTEXT_SIZE", "ASSISTANT_LLAMA_CONTEXT_SIZE", 4096, file_env),
        local_max_tokens=_int_alias_setting("ASSISTANT_LOCAL_MAX_TOKENS", "ASSISTANT_LLAMA_MAX_TOKENS", 256, file_env),
        local_temperature=_float_alias_setting("ASSISTANT_LOCAL_TEMPERATURE", "ASSISTANT_LLAMA_TEMPERATURE", 0.2, file_env),
        local_base_url=_optional_str_setting("ASSISTANT_LOCAL_BASE_URL", file_env),
        local_timeout=_float_setting("ASSISTANT_LOCAL_TIMEOUT", 30.0, file_env),
        remote_provider=_optional_str_setting("ASSISTANT_REMOTE_PROVIDER", file_env),
        remote_model=_optional_str_setting("ASSISTANT_REMOTE_MODEL", file_env),
        remote_api_key=_optional_str_setting("ASSISTANT_REMOTE_API_KEY", file_env),
        remote_base_url=_str_setting("ASSISTANT_REMOTE_BASE_URL", "https://api.openai.com/v1", file_env),
        remote_timeout=_float_setting("ASSISTANT_REMOTE_TIMEOUT", 30.0, file_env),
    )


def validate_local_model_settings(settings: Settings) -> list[str]:
    """Return local model configuration problems that should block model use."""
    issues: list[str] = []
    if settings.local_provider is None:
        return issues

    if settings.local_provider not in {"llama-cpp-python", "llama.cpp-server"}:
        issues.append(
            f"ASSISTANT_LOCAL_PROVIDER unsupported: {settings.local_provider}; "
            "supported: llama-cpp-python, llama.cpp-server"
        )

    if settings.local_provider == "llama-cpp-python":
        if settings.local_model is None:
            issues.append("ASSISTANT_LOCAL_MODEL is required for llama-cpp-python")
        else:
            model_path = Path(settings.local_model).expanduser()
            if not model_path.exists():
                issues.append(f"ASSISTANT_LOCAL_MODEL does not exist: {model_path}")
            elif not model_path.is_file():
                issues.append(f"ASSISTANT_LOCAL_MODEL is not a file: {model_path}")

    if settings.local_context_size <= 0:
        issues.append("ASSISTANT_LOCAL_CONTEXT_SIZE must be greater than zero")
    if settings.local_max_tokens <= 0:
        issues.append("ASSISTANT_LOCAL_MAX_TOKENS must be greater than zero")
    if settings.local_temperature < 0:
        issues.append("ASSISTANT_LOCAL_TEMPERATURE must be zero or greater")
    if settings.local_timeout <= 0:
        issues.append("ASSISTANT_LOCAL_TIMEOUT must be greater than zero")
    return issues


def validate_llama_settings(settings: Settings) -> list[str]:
    """Compatibility wrapper for older callers."""
    issues = validate_local_model_settings(settings)
    return [issue.replace("ASSISTANT_LOCAL_MODEL", "ASSISTANT_LLAMA_MODEL_PATH") for issue in issues]


def find_env_file(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _path_setting(name: str, default: Path, file_env: dict[str, str], env_file: Path | None) -> Path:
    if name in os.environ:
        return Path(os.environ[name]).expanduser()
    if name in file_env:
        return _resolve_file_env_path(file_env[name], env_file)
    return default.expanduser()


def _optional_path_setting(name: str, file_env: dict[str, str], env_file: Path | None) -> Path | None:
    if name in os.environ:
        value = os.environ[name].strip()
        return Path(value).expanduser() if value else None
    if name in file_env:
        value = file_env[name].strip()
        return _resolve_file_env_path(value, env_file) if value else None
    return None


def _local_provider_setting(file_env: dict[str, str]) -> str | None:
    provider = _optional_str_setting("ASSISTANT_LOCAL_PROVIDER", file_env)
    if provider:
        return provider
    old_model = os.environ.get("ASSISTANT_LLAMA_MODEL_PATH", file_env.get("ASSISTANT_LLAMA_MODEL_PATH"))
    if old_model is not None and old_model.strip():
        return "llama-cpp-python"
    return None


def _optional_path_alias_setting(
    name: str,
    alias: str,
    file_env: dict[str, str],
    env_file: Path | None,
) -> Path | None:
    value = _optional_path_setting(name, file_env, env_file)
    if value is not None:
        return value
    return _optional_path_setting(alias, file_env, env_file)


def _local_model_setting(
    local_provider: str | None,
    file_env: dict[str, str],
    env_file: Path | None,
) -> str | Path | None:
    if local_provider == "llama-cpp-python":
        return _optional_path_alias_setting("ASSISTANT_LOCAL_MODEL", "ASSISTANT_LLAMA_MODEL_PATH", file_env, env_file)
    value = _optional_str_setting("ASSISTANT_LOCAL_MODEL", file_env)
    if value is not None:
        return value
    return _optional_path_setting("ASSISTANT_LLAMA_MODEL_PATH", file_env, env_file)


def _int_setting(name: str, default: int, file_env: dict[str, str]) -> int:
    raw = os.environ.get(name, file_env.get(name))
    if raw is None or not raw.strip():
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _int_alias_setting(name: str, alias: str, default: int, file_env: dict[str, str]) -> int:
    if os.environ.get(name, file_env.get(name)) is not None:
        return _int_setting(name, default, file_env)
    return _int_setting(alias, default, file_env)


def _float_setting(name: str, default: float, file_env: dict[str, str]) -> float:
    raw = os.environ.get(name, file_env.get(name))
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _float_alias_setting(name: str, alias: str, default: float, file_env: dict[str, str]) -> float:
    if os.environ.get(name, file_env.get(name)) is not None:
        return _float_setting(name, default, file_env)
    return _float_setting(alias, default, file_env)


def _str_setting(name: str, default: str, file_env: dict[str, str]) -> str:
    raw = os.environ.get(name, file_env.get(name))
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _optional_str_setting(name: str, file_env: dict[str, str]) -> str | None:
    raw = os.environ.get(name, file_env.get(name))
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _resolve_file_env_path(value: str, env_file: Path | None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute() or env_file is None:
        return path
    return env_file.parent / path
