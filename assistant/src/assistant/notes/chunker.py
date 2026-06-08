from __future__ import annotations

import re
from dataclasses import dataclass


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Chunk:
    heading: str | None
    content: str


def chunk_markdown(markdown: str, max_chars: int = 1200) -> list[Chunk]:
    sections = _sections(markdown)
    chunks: list[Chunk] = []
    for heading, body in sections:
        for part in _split_text(body, max_chars):
            if part:
                chunks.append(Chunk(heading=heading, content=part))
    return chunks


def _sections(markdown: str) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    current_heading: str | None = None

    for line in markdown.splitlines():
        match = HEADING_RE.match(line)
        if match:
            current_heading = match.group(2).strip()
            sections.append((current_heading, [line]))
        else:
            sections[-1][1].append(line)

    return [(heading, "\n".join(lines).strip()) for heading, lines in sections if "\n".join(lines).strip()]


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    blocks = re.split(r"\n\s*\n", text)

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) > max_chars:
            if current:
                parts.append("\n\n".join(current))
                current = []
                current_len = 0
            parts.extend(_split_long_block(block, max_chars))
            continue
        next_len = current_len + len(block) + (2 if current else 0)
        if current and next_len > max_chars:
            parts.append("\n\n".join(current))
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len = next_len

    if current:
        parts.append("\n\n".join(current))
    return parts


def _split_long_block(block: str, max_chars: int) -> list[str]:
    return [block[i : i + max_chars].strip() for i in range(0, len(block), max_chars)]
