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


def test_extract_metadata_reads_structured_frontmatter_fields() -> None:
    metadata = extract_metadata(
        """
---
title: Frontmatter Title
aliases:
  - Alpha Alias
type: project
status: active
created: 2026-01-02
updated: 2026-01-03
tags:
  - inbox/to-read
  - Business
---
# Body Title
Discuss #meeting/notes.
""".strip(),
        Path("project.md"),
    )

    assert metadata.title == "Frontmatter Title"
    assert metadata.aliases == ("Alpha Alias",)
    assert metadata.note_type == "project"
    assert metadata.status == "active"
    assert metadata.created == "2026-01-02"
    assert metadata.updated == "2026-01-03"
    assert metadata.tags == ("business", "inbox/to-read", "meeting/notes")


def test_extract_metadata_ignores_malformed_frontmatter() -> None:
    metadata = extract_metadata("---\ntags: [broken\n---\n# Fallback\n#nested/tag", Path("fallback.md"))

    assert metadata.title == "Fallback"
    assert metadata.tags == ("nested/tag",)
