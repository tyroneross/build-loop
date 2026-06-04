# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for bridge_lesson_to_harness."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "bridge_lesson_to_harness.py"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_lesson(path: Path, *, name: str, description: str, type_: str = "lesson") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"type: {type_}\n"
        "scope: global\n"
        "---\n\n"
        f"# Lesson: {name}\n\n"
        "Body text.\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def test_bridge_single_lesson_writes_target_and_index(tmp_path: Path) -> None:
    src = _write_lesson(tmp_path / "src" / "verify-real-launch.md", name="verify-real-launch-path",
                        description="Build-green never equals runtime-correct.")
    target_dir = tmp_path / "harness"

    r = _run(["--source", str(src), "--target-dir", str(target_dir), "--json"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr

    payload = json.loads(r.stdout)
    assert payload["totals"]["written"] == 1
    assert payload["totals"]["errors"] == 0

    expected = target_dir / "lesson_verify-real-launch-path.md"
    assert expected.exists()
    body = expected.read_text(encoding="utf-8")
    assert "bridged_from:" in body
    assert "bridged_at:" in body
    assert "source_store: build-loop-memory" in body
    assert "# Lesson: verify-real-launch-path" in body

    index = target_dir / "MEMORY.md"
    assert index.exists()
    idx_text = index.read_text(encoding="utf-8")
    assert "## Bridged from build-loop-memory" in idx_text
    assert "[verify-real-launch-path](lesson_verify-real-launch-path.md)" in idx_text
    assert "Build-green never equals runtime-correct." in idx_text


def test_re_bridge_is_idempotent(tmp_path: Path) -> None:
    src = _write_lesson(tmp_path / "src" / "ux.md", name="ux-rule", description="X is Y")
    target_dir = tmp_path / "harness"

    r1 = _run(["--source", str(src), "--target-dir", str(target_dir), "--json"], cwd=tmp_path)
    r2 = _run(["--source", str(src), "--target-dir", str(target_dir), "--json"], cwd=tmp_path)

    p1 = json.loads(r1.stdout)
    p2 = json.loads(r2.stdout)
    assert p1["totals"]["written"] == 1
    # Second run should be a no-op (skipped_identical).
    assert p2["totals"]["written"] == 0
    assert p2["totals"]["skipped_identical"] == 1


def test_index_entry_dedup(tmp_path: Path) -> None:
    src1 = _write_lesson(tmp_path / "src" / "a.md", name="rule-a", description="A")
    src2 = _write_lesson(tmp_path / "src" / "b.md", name="rule-b", description="B")
    target_dir = tmp_path / "harness"

    _run(["--source", str(src1), "--target-dir", str(target_dir)], cwd=tmp_path)
    _run(["--source", str(src2), "--target-dir", str(target_dir)], cwd=tmp_path)

    idx = (target_dir / "MEMORY.md").read_text(encoding="utf-8")
    # Both entries present.
    assert "[rule-a]" in idx
    assert "[rule-b]" in idx
    # Each only once.
    assert idx.count("(lesson_rule-a.md)") == 1
    assert idx.count("(lesson_rule-b.md)") == 1


def test_non_bridgeable_type_is_skipped(tmp_path: Path) -> None:
    src = _write_lesson(tmp_path / "src" / "x.md", name="x", description="X", type_="decision")
    target_dir = tmp_path / "harness"
    r = _run(["--source", str(src), "--target-dir", str(target_dir), "--json"], cwd=tmp_path)
    payload = json.loads(r.stdout)
    assert payload["totals"]["skipped_not_bridgeable"] == 1
    assert payload["totals"]["written"] == 0
    assert not (target_dir / "MEMORY.md").exists()


def test_source_dir_recursive_bridge(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    _write_lesson(src_dir / "a" / "a1.md", name="aaa", description="A")
    _write_lesson(src_dir / "b" / "b1.md", name="bbb", description="B")
    _write_lesson(src_dir / "skip-decision.md", name="dec", description="D", type_="decision")
    # README must be skipped.
    (src_dir / "README.md").write_text("readme\n", encoding="utf-8")

    target_dir = tmp_path / "harness"
    r = _run(["--source-dir", str(src_dir), "--target-dir", str(target_dir), "--json"], cwd=tmp_path)
    payload = json.loads(r.stdout)
    assert payload["totals"]["written"] == 2
    assert payload["totals"]["skipped_not_bridgeable"] == 1
    # README isn't even surfaced (filtered before bridge_one).
    assert all("README.md" not in r["source"] for r in payload["items"])


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = _write_lesson(tmp_path / "src" / "dry.md", name="dry-rule", description="dry")
    target_dir = tmp_path / "harness"

    r = _run(["--source", str(src), "--target-dir", str(target_dir), "--dry-run", "--json"], cwd=tmp_path)
    payload = json.loads(r.stdout)
    assert payload["totals"]["written"] == 1  # accounted but not written
    # Verify nothing on disk.
    assert not target_dir.exists()


def test_force_rewrites_even_when_identical(tmp_path: Path) -> None:
    src = _write_lesson(tmp_path / "src" / "f.md", name="force-rule", description="F")
    target_dir = tmp_path / "harness"
    _run(["--source", str(src), "--target-dir", str(target_dir)], cwd=tmp_path)
    r2 = _run(["--source", str(src), "--target-dir", str(target_dir), "--force", "--json"], cwd=tmp_path)
    payload = json.loads(r2.stdout)
    assert payload["totals"]["written"] == 1
    assert payload["totals"]["skipped_identical"] == 0


def test_metadata_nested_type_recognized(tmp_path: Path) -> None:
    """harness-style lesson where the type lives under metadata."""
    p = tmp_path / "src" / "nested.md"
    p.parent.mkdir(parents=True)
    body = (
        "---\n"
        "name: nested-type-lesson\n"
        "description: type under metadata block.\n"
        "metadata:\n"
        "  type: lesson\n"
        "  scope: global\n"
        "---\n\n"
        "Body.\n"
    )
    p.write_text(body, encoding="utf-8")
    target_dir = tmp_path / "harness"
    r = _run(["--source", str(p), "--target-dir", str(target_dir), "--json"], cwd=tmp_path)
    payload = json.loads(r.stdout)
    assert payload["totals"]["written"] == 1


def test_no_inputs_exits_zero_without_strict(tmp_path: Path) -> None:
    r = _run([], cwd=tmp_path)
    assert r.returncode == 0
    assert "no sources" in r.stderr.lower()


def test_no_inputs_strict_exits_nonzero(tmp_path: Path) -> None:
    r = _run(["--strict"], cwd=tmp_path)
    assert r.returncode != 0


def test_f4_default_memory_dir_derived_from_home(tmp_path: Path) -> None:
    """f4 — DEFAULT_HARNESS_MEMORY_DIR must derive from Path.home(), not hardcode a username."""
    import importlib
    import unittest.mock as mock
    from pathlib import Path as _Path

    fake_home = _Path("/home/alice")
    with mock.patch("pathlib.Path.home", return_value=fake_home):
        # Re-evaluate the module-level constant via the module's own logic.
        # Import fresh so the patched home is visible at module level.
        import sys
        mod_name = "bridge_lesson_to_harness"
        # Remove cached module so we get a fresh import with the patched home.
        sys.modules.pop(mod_name, None)
        import bridge_lesson_to_harness as blh_fresh  # noqa: PLC0415
        expected = fake_home / ".claude" / "projects" / "-home-alice" / "memory"
        assert blh_fresh.DEFAULT_HARNESS_MEMORY_DIR == expected, (
            f"Expected {expected}, got {blh_fresh.DEFAULT_HARNESS_MEMORY_DIR}"
        )
        sys.modules.pop(mod_name, None)


def test_f5_malicious_name_sanitized_in_index(tmp_path: Path) -> None:
    """f5 — frontmatter name with link-injection chars must not produce broken MEMORY.md syntax."""
    p = tmp_path / "src" / "evil.md"
    p.parent.mkdir(parents=True)
    # name contains ]( which would break the Markdown link if interpolated raw.
    body = (
        "---\n"
        "name: \"]( evil\"\n"
        "description: injection test.\n"
        "type: lesson\n"
        "scope: global\n"
        "---\n\n"
        "Body.\n"
    )
    p.write_text(body, encoding="utf-8")
    target_dir = tmp_path / "harness"
    r = _run(["--source", str(p), "--target-dir", str(target_dir), "--json"], cwd=tmp_path)
    assert r.returncode == 0

    index = target_dir / "MEMORY.md"
    assert index.exists()
    idx_text = index.read_text(encoding="utf-8")
    # The line must not contain unbalanced ]( that would inject a second link.
    # A safe entry has the pattern "- [<text>](<basename>) —" where <text> has no ][().
    import re
    for line in idx_text.splitlines():
        if line.startswith("- ["):
            # Extract link text between "- [" and "]("
            m = re.match(r"^- \[([^\]]*)\]\(", line)
            assert m, f"Malformed link line: {line!r}"
            link_text = m.group(1)
            assert "]" not in link_text, f"Injected ] in link text: {link_text!r}"
            assert "(" not in link_text, f"Injected ( in link text: {link_text!r}"


def test_f6_concurrent_write_lock_file_created(tmp_path: Path) -> None:
    """f6 — _update_index creates a .lock sidecar for advisory locking (POSIX)."""
    import sys
    sys.path.insert(0, str(HERE))
    import bridge_lesson_to_harness as blh  # noqa: PLC0415
    target_dir = tmp_path / "harness"
    target_dir.mkdir()

    src_fm = {"name": "lock-test", "description": "lock test", "type": "lesson"}
    blh._update_index(target_dir, src_fm, "lesson_lock-test.md", tmp_path / "src.md")

    lock_file = target_dir / "MEMORY.md.lock"
    # On POSIX (macOS/Linux) the lock sidecar must exist after a write.
    import platform
    if platform.system() != "Windows":
        assert lock_file.exists(), "MEMORY.md.lock sidecar was not created on POSIX"


def test_index_append_preserves_existing_other_sections(tmp_path: Path) -> None:
    """MEMORY.md may already have content; bridge must append at end of Bridged section only."""
    target_dir = tmp_path / "harness"
    target_dir.mkdir(parents=True)
    existing = (
        "# Project Memory\n\n"
        "## User Preferences\n\n"
        "- pref one\n"
        "- pref two\n\n"
        "## Reference\n\n"
        "- ref one\n"
    )
    (target_dir / "MEMORY.md").write_text(existing, encoding="utf-8")

    src = _write_lesson(tmp_path / "src" / "z.md", name="z", description="Z")
    _run(["--source", str(src), "--target-dir", str(target_dir)], cwd=tmp_path)

    idx = (target_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "## User Preferences" in idx
    assert "## Reference" in idx
    assert "## Bridged from build-loop-memory" in idx
    assert "[z](lesson_z.md)" in idx
