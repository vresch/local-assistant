from pathlib import Path

from assistant.notes.metadata import extract_metadata


def test_extract_metadata_uses_first_h1_title() -> None:
    metadata = extract_metadata("# Project Alpha\nBody", Path("fallback.md"))

    assert metadata.title == "Project Alpha"


def test_extract_metadata_falls_back_to_filename_stem() -> None:
    metadata = extract_metadata("No heading here.", Path("daily-note.md"))

    assert metadata.title == "daily-note"


def test_extract_metadata_reads_frontmatter_tags() -> None:
    metadata = extract_metadata(
        """
---
tags: [Business, alpha]
---
# Project
""".strip(),
        Path("project.md"),
    )

    assert metadata.tags == ("alpha", "business")


def test_extract_metadata_reads_inline_tags_and_deduplicates() -> None:
    metadata = extract_metadata("# Project\nDiscuss #Business and #business with #alpha.", Path("project.md"))

    assert metadata.tags == ("alpha", "business")
