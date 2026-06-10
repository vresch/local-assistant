from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--text", required=True)
    args = parser.parse_args()

    day = _parse_day(args.date)
    notes_dir = Path(os.environ.get("ASSISTANT_NOTES_DIR", Path.home() / "notes")).expanduser().resolve()
    target = notes_dir / "daily" / f"{day.isoformat()}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(f"# {day.isoformat()}\n", encoding="utf-8")
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"\n- {args.text}\n")
    print(
        json.dumps(
            {
                "status": "succeeded",
                "summary": f"appended daily note {target}",
                "artifacts": [str(target)],
            }
        )
    )


def _parse_day(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("--date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise SystemExit("--date must use YYYY-MM-DD")
    return parsed


if __name__ == "__main__":
    main()
