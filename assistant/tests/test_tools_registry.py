from __future__ import annotations

from pathlib import Path

import pytest

from assistant.tools.registry import build_command, load_registry


def write_registry(path: Path, text: str) -> Path:
    path.write_text(text.strip(), encoding="utf-8")
    return path


def test_load_registry_accepts_backwards_compatible_format(tmp_path: Path) -> None:
    registry = load_registry(
        write_registry(
            tmp_path / "registry.yaml",
            """
tools:
  sample:
    description: Old format.
    command: python -c "print('ok')"
    requires_approval: false
""",
        )
    )

    tool = registry["sample"]
    assert tool.command == ["python", "-c", "print('ok')"]
    assert tool.description == "Old format."
    assert tool.risk == "low"
    assert tool.permissions == ()
    assert tool.args == ()
    assert tool.timeout_seconds == 60


def test_load_registry_parses_v2_manifest_and_renders_args(tmp_path: Path) -> None:
    registry = load_registry(
        write_registry(
            tmp_path / "registry.yaml",
            """
tools:
  report:
    command: ["python", "tools/report.py"]
    risk: medium
    permissions: ["read"]
    timeout_seconds: 12
    working_dir: reports
    args:
      - name: month
        type: str
        required: true
        flag: "--month"
      - name: verbose
        type: bool
        required: false
        default: false
        flag: "--verbose"
""",
        )
    )

    tool = registry["report"]
    assert tool.risk == "medium"
    assert tool.permissions == ("read",)
    assert tool.timeout_seconds == 12
    assert tool.working_dir == "reports"
    assert build_command(tool, {"month": "2026-06"}) == [
        "python",
        "tools/report.py",
        "--month",
        "2026-06",
        "--verbose",
        "false",
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("risk", "extreme", "invalid risk"),
        ("permissions", '["read", "admin"]', "invalid permissions"),
    ],
)
def test_load_registry_rejects_invalid_risk_or_permission(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    path = write_registry(
        tmp_path / "registry.yaml",
        f"""
tools:
  sample:
    command: ["python", "-V"]
    {field}: {value}
""",
    )

    with pytest.raises(ValueError, match=message):
        load_registry(path)


def test_load_registry_rejects_invalid_arg_spec(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path / "registry.yaml",
        """
tools:
  sample:
    command: ["python", "-V"]
    args:
      - name: count
        type: integer
""",
    )

    with pytest.raises(ValueError, match="invalid type"):
        load_registry(path)


def test_build_command_rejects_unknown_and_missing_args(tmp_path: Path) -> None:
    registry = load_registry(
        write_registry(
            tmp_path / "registry.yaml",
            """
tools:
  sample:
    command: ["python", "tool.py"]
    args:
      - name: month
        type: str
        required: true
        flag: "--month"
""",
        )
    )

    with pytest.raises(ValueError, match="Missing required arg"):
        build_command(registry["sample"], {})
    with pytest.raises(ValueError, match="Unknown args"):
        build_command(registry["sample"], {"month": "2026-06", "extra": "x"})


def test_load_registry_rejects_unstable_tool_names(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path / "registry.yaml",
        """
tools:
  Bad Tool:
    command: ["python", "-V"]
""",
    )

    with pytest.raises(ValueError, match="stable identifier"):
        load_registry(path)


def test_load_registry_rejects_duplicate_list_tool_names(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path / "registry.yaml",
        """
tools:
  - name: sample
    command: ["python", "-V"]
  - name: sample
    command: ["python", "-c", "print('replace')"]
""",
    )

    with pytest.raises(ValueError, match="duplicate tool name"):
        load_registry(path)
