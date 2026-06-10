from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    root = Path.cwd()
    files = [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]
    interesting = [str(path.relative_to(root)) for path in files if path.name in {"pyproject.toml", "README.md", "spec.md"}]
    summary = f"files={len(files)} interesting={len(interesting)}"
    print(
        json.dumps(
            {
                "status": "succeeded",
                "summary": summary,
                "artifacts": interesting,
            }
        )
    )


if __name__ == "__main__":
    main()
