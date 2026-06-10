from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
INLINE_TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z][A-Za-z0-9_-]*)\b")


@dataclass(frozen=True)
class NoteMetadata:
    title: str
    tags: tuple[str, ...]


def extract_metadata(markdown: str, path: Path) -> NoteMetadata:
    frontmatter, body = _split_frontmatter(markdown)
    title = _extract_title(body) or path.stem
    tags = set(_frontmatter_tags(frontmatter))
    tags.update(_inline_tags(body))
    return NoteMetadata(title=title, tags=tuple(sorted(tags, key=str.lower)))


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


def _frontmatter_tags(frontmatter: str | None) -> list[str]:
    if not frontmatter:
        return []
    lines = frontmatter.splitlines()
    tags: list[str] = []
    in_tags_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("tags:"):
            in_tags_list = True
            value = stripped.split(":", 1)[1].strip()
            tags.extend(_parse_tag_value(value))
            continue
        if in_tags_list and stripped.startswith("- "):
            tags.extend(_parse_tag_value(stripped[2:].strip()))
            continue
        if stripped and not line.startswith((" ", "\t")):
            in_tags_list = False
    return tags


def _parse_tag_value(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [_normalize_tag(part) for part in re.split(r"[,\s]+", value) if _normalize_tag(part)]


def _inline_tags(markdown: str) -> list[str]:
    return [_normalize_tag(match.group(1)) for match in INLINE_TAG_RE.finditer(markdown)]


def _normalize_tag(tag: str) -> str:
    return tag.strip().strip("'\"[]#").lower()
