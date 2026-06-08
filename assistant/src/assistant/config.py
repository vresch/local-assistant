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


def get_settings() -> Settings:
    env_file = find_env_file(Path.cwd())
    file_env = load_env_file(env_file) if env_file else {}
    app_dir = _path_setting(
        "ASSISTANT_HOME",
        Path.home() / ".local" / "share" / "local-assistant",
        file_env,
        env_file,
    )
    return Settings(
        notes_dir=_path_setting("ASSISTANT_NOTES_DIR", Path.home() / "notes", file_env, env_file),
        db_path=_path_setting("ASSISTANT_DB_PATH", app_dir / "assistant.db", file_env, env_file),
        registry_path=_path_setting(
            "ASSISTANT_REGISTRY_PATH",
            Path.cwd() / "tools" / "registry.yaml",
            file_env,
            env_file,
        ),
        debug_log_path=_path_setting("ASSISTANT_DEBUG_LOG_PATH", app_dir / "debug.log", file_env, env_file),
    )


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


def _resolve_file_env_path(value: str, env_file: Path | None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute() or env_file is None:
        return path
    return env_file.parent / path
