from assistant.notes.chunker import chunk_markdown


def test_chunk_markdown_tracks_headings() -> None:
    chunks = chunk_markdown(
        """
Intro before heading.

# Project Alpha
Alpha details.

## Decisions
Use SQLite FTS5.
""".strip()
    )

    assert [chunk.heading for chunk in chunks] == [None, "Project Alpha", "Decisions"]
    assert [chunk.heading_path for chunk in chunks] == [None, "Project Alpha", "Project Alpha > Decisions"]
    assert "Intro before heading" in chunks[0].content
    assert "# Project Alpha" in chunks[1].content
    assert "Use SQLite FTS5" in chunks[2].content


def test_chunk_markdown_tracks_line_ranges_and_tokens() -> None:
    chunks = chunk_markdown("# Alpha\nFirst line.\nSecond line.\n\n## Decision\nUse SQLite FTS5.")

    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3
    assert chunks[0].token_count == 6
    assert chunks[1].start_line == 5
    assert chunks[1].end_line == 6
    assert chunks[1].heading_path == "Alpha > Decision"


def test_chunk_markdown_splits_large_sections() -> None:
    chunks = chunk_markdown("# Long\n" + ("word " * 300), max_chars=120)

    assert len(chunks) > 1
    assert all(chunk.heading == "Long" for chunk in chunks)
    assert all(chunk.heading_path == "Long" for chunk in chunks)
    assert all(chunk.start_line == 1 for chunk in chunks)
    assert all(len(chunk.content) <= 120 for chunk in chunks)


def test_chunk_markdown_split_line_ranges_account_for_blank_lines() -> None:
    chunks = chunk_markdown("# Alpha\nLine one.\n\nLine two.\n\nLine three.", max_chars=18)

    assert [(chunk.content, chunk.start_line, chunk.end_line) for chunk in chunks] == [
        ("# Alpha\nLine one.", 1, 2),
        ("Line two.", 4, 4),
        ("Line three.", 6, 6),
    ]
