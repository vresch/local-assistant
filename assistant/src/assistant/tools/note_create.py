from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    args = parser.parse_args()

    notes_dir = Path(os.environ.get("ASSISTANT_NOTES_DIR", Path.home() / "notes")).expanduser().resolve()
    target = _confined_note_path(notes_dir, args.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SystemExit(f"note already exists: {target}")

    body = args.body.strip()
    content = f"# {args.title}\n"
    if body:
        content += f"\n{body}\n"
    target.write_text(content, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "succeeded",
                "summary": f"created note {target}",
                "artifacts": [str(target)],
            }
        )
    )


def _confined_note_path(notes_dir: Path, raw_path: str) -> Path:
    relative = Path(raw_path).expanduser()
    if relative.is_absolute() or not relative.parts:
        raise SystemExit("--path must be relative to ASSISTANT_NOTES_DIR")
    target = (notes_dir / relative).with_suffix(".md").resolve()
    try:
        target.relative_to(notes_dir)
    except ValueError as exc:
        raise SystemExit("--path must stay inside ASSISTANT_NOTES_DIR") from exc
    return target


if __name__ == "__main__":
    main()
