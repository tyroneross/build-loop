"""Behavioral checks for the retrieval acceptance probe."""
import json
import sys
from pathlib import Path

from retrieval_review_probe import run_spec


def _case(case_id: str, top_id: str, *, route: str = "hybrid", writes: str = "") -> dict:
    program = (
        "import json,pathlib;"
        + (f"pathlib.Path({writes!r}).write_text('sidecar');" if writes else "")
        + f"print(json.dumps({{'actual_route': {route!r}, 'hits': [{{'id': {top_id!r}}}]}}))"
    )
    return {"id": case_id, "argv": [sys.executable, "-c", program]}


def test_distinct_constrained_queries_pass(tmp_path: Path) -> None:
    broad = _case("broad", "portfolio", route="family_anchor")
    broad["expected_route"] = "family_anchor"
    named = _case("named", "deal-pila")
    named["top_id"] = "deal-pila"
    result = run_spec({"cases": [broad, named],
                       "distinct_top_pairs": [["broad", "named"]]}, tmp_path, 5)
    assert result["pass"]


def test_same_generic_source_and_wrong_route_fail(tmp_path: Path) -> None:
    left = _case("pila", "portfolio")
    right = _case("coursestorm", "portfolio")
    right["expected_route"] = "named_deal"
    result = run_spec({"cases": [left, right],
                       "distinct_top_pairs": [["pila", "coursestorm"]]}, tmp_path, 5)
    assert not result["pass"]
    assert result["pair_failures"]
    assert any("route" in error for error in result["cases"][1]["errors"])


def test_read_only_probe_catches_sqlite_sidecar_creation(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    database.write_bytes(b"existing")
    alias = tmp_path / "alias.db"
    alias.symlink_to(database)
    case = _case("read", "graph", writes="graph.db-wal")
    case["no_write_paths"] = ["alias.db"]
    result = run_spec({"cases": [case]}, tmp_path, 5)
    assert not result["pass"]
    assert "no-write path changed: alias.db" in result["cases"][0]["errors"]


def test_missing_expected_evidence_fails(tmp_path: Path) -> None:
    case = _case("model", "project-a")
    case["required_ids"] = ["project-b"]
    case["forbidden_ids"] = ["project-a"]
    result = run_spec({"cases": [case]}, tmp_path, 5)
    assert not result["pass"]
    assert len(result["cases"][0]["errors"]) == 2
