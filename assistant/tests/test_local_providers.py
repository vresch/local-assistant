from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assistant.providers.local import (
    LlamaCppPythonProvider,
    LlamaCppServerProvider,
    _load_llama_class,
)


def test_llama_cpp_python_provider_extracts_generated_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model.gguf"
    model_path.write_text("fake model", encoding="utf-8")

    class FakeLlama:
        init_kwargs: dict[str, Any] = {}
        prompt = ""
        call_kwargs: dict[str, Any] = {}

        def __init__(self, **kwargs: Any) -> None:
            FakeLlama.init_kwargs = kwargs

        def __call__(self, prompt: str, **kwargs: Any) -> dict[str, object]:
            FakeLlama.prompt = prompt
            FakeLlama.call_kwargs = kwargs
            return {"choices": [{"text": "SQLite FTS5 should be used.\nQuestion: extra prompt text"}]}

    monkeypatch.setattr("assistant.providers.local._load_llama_class", lambda: FakeLlama)

    provider = LlamaCppPythonProvider(
        model_path=model_path,
        context_size=1024,
        max_tokens=64,
        temperature=0.0,
    )
    response = provider.complete("Local notes:\nSQLite FTS5 powers local note search.")

    assert response.text == "SQLite FTS5 should be used."
    assert response.provider == "llama-cpp-python"
    assert response.model == str(model_path)
    assert FakeLlama.init_kwargs == {
        "model_path": str(model_path),
        "n_ctx": 1024,
        "verbose": False,
    }
    assert FakeLlama.call_kwargs["max_tokens"] == 64
    assert FakeLlama.call_kwargs["temperature"] == 0.0
    assert "\nQuestion:" in FakeLlama.call_kwargs["stop"]
    assert "Local notes:" in FakeLlama.prompt


def test_llama_cpp_python_provider_rejects_missing_model(tmp_path: Path) -> None:
    provider = LlamaCppPythonProvider(
        model_path=tmp_path / "missing.gguf",
        context_size=1024,
        max_tokens=64,
        temperature=0.0,
    )

    with pytest.raises(FileNotFoundError, match="llama model not found"):
        provider.complete("prompt")


def test_missing_llama_dependency_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_import_module(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr("assistant.providers.local.import_module", fake_import_module)

    with pytest.raises(RuntimeError, match="llama-cpp-python is required"):
        _load_llama_class()


def test_llama_cpp_server_provider_request_and_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "Use local notes."}}]}).encode("utf-8")

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("assistant.providers.local.urllib.request.urlopen", fake_urlopen)

    provider = LlamaCppServerProvider(
        model_name="local",
        base_url="http://127.0.0.1:8080",
        timeout=4.0,
        max_tokens=12,
        temperature=0.1,
    )
    response = provider.complete("Question?")

    assert response.text == "Use local notes."
    assert response.provider == "llama.cpp-server"
    assert response.model == "local"
    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert captured["timeout"] == 4.0
    assert captured["body"] == {
        "model": "local",
        "messages": [{"role": "user", "content": "Question?"}],
        "max_tokens": 12,
        "temperature": 0.1,
    }
