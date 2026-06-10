from __future__ import annotations

import re
from dataclasses import dataclass


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Chunk:
    heading: str | None
    content: str
    heading_path: str | None
    token_count: int
    start_line: int
    end_line: int


def chunk_markdown(markdown: str, max_chars: int = 1200) -> list[Chunk]:
    sections = _sections(markdown)
    chunks: list[Chunk] = []
    for heading, heading_path, body, section_start in sections:
        for part, start_line, end_line in _split_text(body, max_chars, section_start):
            if part:
                chunks.append(
                    Chunk(
                        heading=heading,
                        content=part,
                        heading_path=heading_path,
                        token_count=_token_count(part),
                        start_line=start_line,
                        end_line=end_line,
                    )
                )
    return chunks


def _sections(markdown: str) -> list[tuple[str | None, str | None, str, int]]:
    sections: list[tuple[str | None, str | None, int, list[str]]] = [(None, None, 1, [])]
    current_heading: str | None = None
    heading_stack: list[tuple[int, str]] = []

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        match = HEADING_RE.match(line)
        if match:
            level = len(match.group(1))
            current_heading = match.group(2).strip()
            heading_stack = [(stack_level, heading) for stack_level, heading in heading_stack if stack_level < level]
            heading_stack.append((level, current_heading))
            heading_path = " > ".join(heading for _, heading in heading_stack)
            sections.append((current_heading, heading_path, line_number, [line]))
        else:
            sections[-1][3].append(line)

    return [
        (heading, heading_path, "\n".join(lines).strip(), start_line)
        for heading, heading_path, start_line, lines in sections
        if "\n".join(lines).strip()
    ]


def _split_text(text: str, max_chars: int, start_line: int) -> list[tuple[str, int, int]]:
    if len(text) <= max_chars:
        return [(text, start_line, _end_line(start_line, text))]

    parts: list[tuple[str, int, int]] = []
    current: list[tuple[str, int, int]] = []
    current_len = 0
    cursor_line = start_line
    blocks = re.split(r"(\n\s*\n)", text)

    for raw_block in blocks:
        if re.fullmatch(r"\n\s*\n", raw_block):
            cursor_line += raw_block.count("\n")
            continue
        raw_start = cursor_line
        leading_newlines = len(raw_block) - len(raw_block.lstrip("\n"))
        block_start = raw_start + leading_newlines
        block = raw_block.strip()
        cursor_line = raw_start + raw_block.count("\n")
        if not block:
            continue
        block_end = _end_line(block_start, block)
        if len(block) > max_chars:
            if current:
                parts.append(_join_current(current))
                current = []
                current_len = 0
            parts.extend(_split_long_block(block, max_chars, block_start, block_end))
            continue
        next_len = current_len + len(block) + (2 if current else 0)
        if current and next_len > max_chars:
            parts.append(_join_current(current))
            current = [(block, block_start, block_end)]
            current_len = len(block)
        else:
            current.append((block, block_start, block_end))
            current_len = next_len

    if current:
        parts.append(_join_current(current))
    return parts


def _split_long_block(block: str, max_chars: int, start_line: int, end_line: int) -> list[tuple[str, int, int]]:
    return [(block[i : i + max_chars].strip(), start_line, end_line) for i in range(0, len(block), max_chars)]


def _join_current(blocks: list[tuple[str, int, int]]) -> tuple[str, int, int]:
    return "\n\n".join(block for block, _, _ in blocks), blocks[0][1], blocks[-1][2]


def _end_line(start_line: int, text: str) -> int:
    return start_line + text.count("\n")


def _token_count(text: str) -> int:
    return len(re.findall(r"\S+", text))
