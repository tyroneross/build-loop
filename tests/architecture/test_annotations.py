"""Semantic code-annotation parsing, indexing, search, and scan integration."""

from __future__ import annotations

import json
from pathlib import Path

from build_loop.architecture.annotations import (
    build_annotation_index,
    find_annotations,
    parse_annotation_line,
    scan_annotations,
)
from build_loop.architecture.cli import main


def test_parser_supports_human_comments_and_open_kinds() -> None:
    annotation = parse_annotation_line(
        "// BL:provider-boundary | Converts events into stable messages | provider,events",
        file="src/messages.ts",
        line_number=7,
    )

    assert annotation is not None
    assert annotation.kind == "provider-boundary"
    assert annotation.summary == "Converts events into stable messages"
    assert annotation.keywords == ("provider", "events")
    assert annotation.file == "src/messages.ts"
    assert annotation.line == 7


def test_parser_ignores_inline_code_and_malformed_markers() -> None:
    assert parse_annotation_line(
        'value = "BL:purpose | string data | ignored"',
        file="a.py",
        line_number=1,
    ) is None
    assert parse_annotation_line(
        "# BL:missing-summary",
        file="a.py",
        line_number=2,
    ) is None


def test_scan_uses_source_files_and_returns_deterministic_index(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "# BL:invariant | Preserve state before clearing it | recovery,state\n",
        encoding="utf-8",
    )
    (tmp_path / "b.ts").write_text(
        "// BL:flow | Routes review evidence to the host LLM | review,llm\n",
        encoding="utf-8",
    )
    (tmp_path / "guide.md").write_text(
        "# BL:example | Documentation examples are not code annotations | docs\n",
        encoding="utf-8",
    )

    annotations = scan_annotations(tmp_path, ["guide.md", "b.ts", "a.py"])
    index = build_annotation_index(annotations)

    assert index["annotation_count"] == 2
    assert [item["file"] for item in index["annotations"]] == ["a.py", "b.ts"]
    assert index == build_annotation_index(list(reversed(annotations)))


def test_find_matches_kind_summary_keyword_and_path() -> None:
    first = parse_annotation_line(
        "# BL:invariant | Preserve state before clearing it | recovery,history",
        file="scripts/resume.py",
        line_number=3,
    )
    second = parse_annotation_line(
        "// BL:boundary | Translate provider events | provider,messages",
        file="src/events.ts",
        line_number=4,
    )
    assert first is not None and second is not None

    assert [item["file"] for item in find_annotations([first, second], "recovery state")] == [
        "scripts/resume.py",
    ]
    assert [item["file"] for item in find_annotations([first, second], "provider")] == [
        "src/events.ts",
    ]


def test_native_scan_writes_and_queries_annotation_index(tmp_path: Path, capsys) -> None:
    (tmp_path / "main.py").write_text(
        "# BL:purpose | Keep recovery decisions auditable | recovery,audit\n",
        encoding="utf-8",
    )

    assert main(["--repo", str(tmp_path), "scan", "--json"]) == 0
    scan_output = json.loads(capsys.readouterr().out)
    assert scan_output["annotations"] == 1
    index_path = tmp_path / ".build-loop" / "architecture" / "annotations.json"
    assert json.loads(index_path.read_text())["annotation_count"] == 1

    assert main(["--repo", str(tmp_path), "annotations", "audit", "--json"]) == 0
    query_output = json.loads(capsys.readouterr().out)
    assert query_output["match_count"] == 1
    assert query_output["matches"][0]["file"] == "main.py"
