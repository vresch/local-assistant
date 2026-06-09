from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from assistant.config import Settings


@dataclass(frozen=True)
class RemoteResponse:
    text: str
    provider: str
    model: str


class RemoteProvider(Protocol):
    provider_name: str
    model_name: str

    def generate(self, prompt: str) -> RemoteResponse:
        """Return model output for a single research prompt."""


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    api_key: str
    model_name: str
    base_url: str = "https://api.openai.com/v1"
    timeout: float = 30.0
    provider_name: str = "openai-compatible"

    def generate(self, prompt: str) -> RemoteResponse:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        body = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"remote provider request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"remote provider request failed: {exc.reason}") from exc

        return RemoteResponse(
            text=_extract_text(payload),
            provider=self.provider_name,
            model=self.model_name,
        )


def build_remote_provider(settings: Settings) -> RemoteProvider | None:
    if not settings.remote_model or not settings.remote_api_key:
        return None

    provider = settings.remote_provider or "openai-compatible"
    if provider != "openai-compatible":
        raise ValueError(f"unsupported remote provider {provider!r}; supported: openai-compatible")

    return OpenAICompatibleProvider(
        api_key=settings.remote_api_key,
        model_name=settings.remote_model,
        base_url=settings.remote_base_url,
        timeout=settings.remote_timeout,
        provider_name=provider,
    )


def _extract_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return str(payload)
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
