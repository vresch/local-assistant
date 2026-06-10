from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--pattern", required=True)
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    matches = [
        str(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and fnmatch.fnmatch(path.name, args.pattern)
    ]
    print(
        json.dumps(
            {
                "status": "succeeded",
                "summary": f"found {len(matches)} files",
                "artifacts": matches[:50],
            }
        )
    )


if __name__ == "__main__":
    main()
