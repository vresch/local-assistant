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
    assert "Intro before heading" in chunks[0].content
    assert "# Project Alpha" in chunks[1].content
    assert "Use SQLite FTS5" in chunks[2].content


def test_chunk_markdown_splits_large_sections() -> None:
    chunks = chunk_markdown("# Long\n" + ("word " * 300), max_chars=120)

    assert len(chunks) > 1
    assert all(chunk.heading == "Long" for chunk in chunks)
    assert all(len(chunk.content) <= 120 for chunk in chunks)
