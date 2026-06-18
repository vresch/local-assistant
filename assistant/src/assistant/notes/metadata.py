from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
INLINE_TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z][A-Za-z0-9_/-]*)\b")


@dataclass(frozen=True)
class NoteMetadata:
    title: str
    tags: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    note_type: str | None = None
    status: str | None = None
    created: str | None = None
    updated: str | None = None


def extract_metadata(markdown: str, path: Path) -> NoteMetadata:
    frontmatter, body = _split_frontmatter(markdown)
    data = _parse_frontmatter(frontmatter)
    title = _as_text(data.get("title")) or _extract_title(body) or path.stem
    tags = set(_frontmatter_tags(data))
    tags.update(_inline_tags(body))
    aliases = tuple(sorted(set(_phrase_list(data.get("aliases"))), key=str.lower))
    return NoteMetadata(
        title=title,
        tags=tuple(sorted(tags, key=str.lower)),
        aliases=aliases,
        note_type=_as_text(data.get("type")),
        status=_as_text(data.get("status")),
        created=_as_text(data.get("created")),
        updated=_as_text(data.get("updated")),
    )


def _split_frontmatter(markdown: str) -> tuple[str | None, str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, markdown
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, markdown


def _extract_title(markdown: str) -> str | None:
    match = H1_RE.search(markdown)
    if not match:
        return None
    return match.group(1).strip()


def _parse_frontmatter(frontmatter: str | None) -> dict[str, object]:
    if not frontmatter:
        return {}
    try:
        parsed = yaml.safe_load(frontmatter)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _frontmatter_tags(data: dict[str, object]) -> list[str]:
    return [_normalize_tag(tag) for tag in _text_list(data.get("tags")) if _normalize_tag(tag)]


def _text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(_text_list(item))
        return items
    if isinstance(value, str):
        if "," in value:
            return [part.strip() for part in value.split(",") if part.strip()]
        return [part for part in re.split(r"\s+", value.strip()) if part]
    return [str(value)]


def _phrase_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(_phrase_list(item))
        return items
    text = str(value).strip()
    return [text] if text else []


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _inline_tags(markdown: str) -> list[str]:
    return [_normalize_tag(match.group(1)) for match in INLINE_TAG_RE.finditer(markdown)]


def _normalize_tag(tag: str) -> str:
    return tag.strip().strip("'\"[]#").lower()
