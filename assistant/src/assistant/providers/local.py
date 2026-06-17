from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from assistant.config import Settings


@dataclass(frozen=True)
class LocalModelResponse:
    text: str
    provider: str
    model: str


class LocalModelProvider(Protocol):
    @property
    def provider_name(self) -> str:
        """Provider identifier."""

    @property
    def model_name(self) -> str:
        """Configured model identifier."""

    def complete(self, prompt: str) -> LocalModelResponse:
        """Return model output for a single local prompt."""


@dataclass(frozen=True)
class LlamaCppPythonProvider:
    model_path: Path
    context_size: int
    max_tokens: int
    temperature: float
    provider_name: str = "llama-cpp-python"

    @property
    def model_name(self) -> str:
        return str(self.model_path)

    def complete(self, prompt: str) -> LocalModelResponse:
        if not self.model_path.is_file():
            raise FileNotFoundError(f"llama model not found: {self.model_path}")

        llama_cls = _load_llama_class()
        model = llama_cls(model_path=str(self.model_path), n_ctx=self.context_size, verbose=False)
        response = model(
            prompt,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stop=["\nQuestion:", "\nSupporting notes:", "\nSources:", "\nNext action:"],
        )
        return LocalModelResponse(
            text=_clean_generated_answer(_extract_llama_text(response)),
            provider=self.provider_name,
            model=str(self.model_path),
        )


@dataclass(frozen=True)
class LlamaCppServerProvider:
    model_name: str
    base_url: str = "http://127.0.0.1:8080"
    timeout: float = 30.0
    max_tokens: int = 256
    temperature: float = 0.2
    provider_name: str = "llama.cpp-server"

    def complete(self, prompt: str) -> LocalModelResponse:
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        body = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"local provider request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"local provider request failed: {exc.reason}") from exc

        return LocalModelResponse(
            text=_clean_generated_answer(_extract_chat_text(payload)),
            provider=self.provider_name,
            model=self.model_name,
        )


def build_local_provider(settings: Settings) -> LocalModelProvider | None:
    if not settings.local_provider:
        return None

    if settings.local_provider == "llama-cpp-python":
        if settings.local_model is None:
            return None
        return LlamaCppPythonProvider(
            model_path=Path(settings.local_model).expanduser(),
            context_size=settings.local_context_size,
            max_tokens=settings.local_max_tokens,
            temperature=settings.local_temperature,
        )

    if settings.local_provider == "llama.cpp-server":
        return LlamaCppServerProvider(
            model_name=str(settings.local_model or "local"),
            base_url=settings.local_base_url or "http://127.0.0.1:8080",
            timeout=settings.local_timeout,
            max_tokens=settings.local_max_tokens,
            temperature=settings.local_temperature,
        )

    raise ValueError(
        f"unsupported local provider {settings.local_provider!r}; "
        "supported: llama-cpp-python, llama.cpp-server"
    )


def _load_llama_class() -> Any:
    try:
        return import_module("llama_cpp").Llama
    except ImportError as exc:
        raise RuntimeError(
            "llama-cpp-python is required for local model answers. "
            "Install dependencies with `uv sync` or run `assistant ask --no-model`."
        ) from exc


def _extract_llama_text(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    return text.strip()
    return str(response).strip()


def _extract_chat_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return str(payload).strip()
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
            if isinstance(first.get("text"), str):
                return first["text"].strip()
    return json.dumps(payload, indent=2, sort_keys=True)


def _clean_generated_answer(text: str) -> str:
    cleaned = text.strip()
    for marker in ("\nQuestion:", "\nSupporting notes:", "\nSources:", "\nNext action:"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()
    return cleaned
