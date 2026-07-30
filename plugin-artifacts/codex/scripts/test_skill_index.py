#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/skill_index.py — the generated, host-neutral skill index."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable when run directly via pytest <file>
sys.path.insert(0, str(Path(__file__).parent))

from skill_index import (  # noqa: E402
    DEFAULT_OUTPUT,
    DESCRIPTION_MAX,
    GENERATED_BANNER,
    SkillIndexError,
    apply_index,
    check_index,
    discover,
    generate,
    main,
    parse_frontmatter,
    plugin_name,
    truncate,
)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SCRIPT = HERE / "skill_index.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def write_skill(
    root: Path,
    rel_dir: str,
    *,
    name: str | None = "sample",
    description: str | None = "Use when the sample case applies.",
    user_invocable: str | None = "false",
    public_justification: str | None = None,
    raw: str | None = None,
) -> Path:
    """Create a SKILL.md under *root*/*rel_dir*; `raw` bypasses frontmatter build."""
    path = root / rel_dir / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    if description is not None:
        lines.append(f'description: "{description}"')
    if user_invocable is not None:
        lines.append(f"user-invocable: {user_invocable}")
    if public_justification is not None:
        lines.append(f"public-justification: {public_justification}")
    lines += ["---", "", "# Body", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    """A minimal plugin root with a manifest and two hidden skills."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo-plugin"}), encoding="utf-8"
    )
    write_skill(tmp_path, "skills/alpha", name="alpha", description="Use when alpha.")
    write_skill(tmp_path, "skills/beta", name="beta", description="Use when beta.")
    return tmp_path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def test_parse_frontmatter_reads_quoted_and_plain_scalars() -> None:
    fields = parse_frontmatter('---\nname: a\ndescription: "b: c"\nx: 1\n---\nbody\n')
    assert fields == {"name": "a", "description": "b: c", "x": "1"}


def test_parse_frontmatter_reads_block_scalars() -> None:
    text = "---\nname: a\ndescription: >\n  one line\n  two line\nother: z\n---\n"
    fields = parse_frontmatter(text)
    assert fields is not None
    assert fields["description"] == "one line two line"
    assert fields["other"] == "z"


def test_parse_frontmatter_returns_none_without_a_block() -> None:
    assert parse_frontmatter("# No frontmatter here\n") is None


def test_truncate_is_deterministic_and_bounded() -> None:
    long = "word " * 100
    short = truncate(long)
    assert len(short) <= DESCRIPTION_MAX
    assert short.endswith("…")
    assert truncate(long) == short
    assert truncate("short text") == "short text"


# ---------------------------------------------------------------------------
# Generation shape
# ---------------------------------------------------------------------------

def test_generation_shape(plugin_dir: Path) -> None:
    content = generate(plugin_dir, DEFAULT_OUTPUT)
    assert GENERATED_BANNER in content
    assert "# Skill Index — demo-plugin" in content
    assert "| Skill | When to use | Invocation | Exposure |" in content
    assert "**2 skills** · 0 public · 0 public-undeclared · 2 hidden" in content
    # Rows carry the routing answer, not just a name.
    assert "Use when alpha." in content
    assert "Use when beta." in content
    # Links resolve from the index's own directory.
    assert "](../skills/alpha/SKILL.md)" in content


def test_rows_are_sorted_by_skill_id(plugin_dir: Path) -> None:
    write_skill(plugin_dir, "skills/gamma", name="gamma")
    ids = [row.skill_id for row in discover(plugin_dir)]
    assert ids == sorted(ids)
    assert ids == ["demo-plugin:alpha", "demo-plugin:beta", "demo-plugin:gamma"]


def test_namespaced_name_is_not_double_prefixed(plugin_dir: Path) -> None:
    write_skill(plugin_dir, "skills/nested/thing", name="demo-plugin:nested-thing")
    ids = [row.skill_id for row in discover(plugin_dir)]
    assert "demo-plugin:nested-thing" in ids
    assert "demo-plugin:demo-plugin:nested-thing" not in ids


def test_pipe_in_description_is_escaped(plugin_dir: Path) -> None:
    write_skill(plugin_dir, "skills/piped", name="piped", description="a | b")
    content = generate(plugin_dir, DEFAULT_OUTPUT)
    assert "a \\| b" in content


def test_index_contains_no_timestamp(plugin_dir: Path) -> None:
    """A timestamp would make --check fail on every run."""
    first = generate(plugin_dir, DEFAULT_OUTPUT)
    second = generate(plugin_dir, DEFAULT_OUTPUT)
    assert first == second


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------

def test_public_requires_both_flag_and_justification(plugin_dir: Path) -> None:
    write_skill(
        plugin_dir,
        "skills/open",
        name="open",
        user_invocable="true",
        public_justification="single human entrypoint",
    )
    write_skill(plugin_dir, "skills/halfopen", name="halfopen", user_invocable="true")
    exposure = {row.name: row.exposure for row in discover(plugin_dir)}
    assert exposure["open"] == "public"
    assert exposure["halfopen"] == "public-undeclared"
    assert exposure["alpha"] == "hidden"
    invocation = {row.name: row.invocation for row in discover(plugin_dir)}
    assert invocation["open"] == "load `demo-plugin:open`"
    assert "internal" in invocation["alpha"]


def test_missing_user_invocable_field_is_public_not_hidden(plugin_dir: Path) -> None:
    """The harness resolves `userInvocable ?? true` — unfielded skills are PUBLIC."""
    write_skill(plugin_dir, "skills/unfielded", name="unfielded", user_invocable=None)
    exposure = {row.name: row.exposure for row in discover(plugin_dir)}
    assert exposure["unfielded"] == "public-undeclared"
    content = generate(plugin_dir, DEFAULT_OUTPUT)
    assert "**3 skills** · 0 public · 1 public-undeclared · 2 hidden" in content


# ---------------------------------------------------------------------------
# Drift detection — both directions
# ---------------------------------------------------------------------------

def test_check_passes_immediately_after_apply(plugin_dir: Path) -> None:
    apply_index(plugin_dir, DEFAULT_OUTPUT)
    check_index(plugin_dir, DEFAULT_OUTPUT)  # must not raise


def test_check_fails_when_a_description_changes(plugin_dir: Path) -> None:
    apply_index(plugin_dir, DEFAULT_OUTPUT)
    write_skill(plugin_dir, "skills/alpha", name="alpha", description="Totally new.")
    with pytest.raises(SkillIndexError, match="stale"):
        check_index(plugin_dir, DEFAULT_OUTPUT)


def test_check_fails_when_a_new_skill_is_added(plugin_dir: Path) -> None:
    apply_index(plugin_dir, DEFAULT_OUTPUT)
    write_skill(plugin_dir, "skills/delta", name="delta")
    with pytest.raises(SkillIndexError, match="stale"):
        check_index(plugin_dir, DEFAULT_OUTPUT)


def test_check_fails_when_a_skill_is_removed(plugin_dir: Path) -> None:
    apply_index(plugin_dir, DEFAULT_OUTPUT)
    (plugin_dir / "skills" / "beta" / "SKILL.md").unlink()
    with pytest.raises(SkillIndexError, match="stale"):
        check_index(plugin_dir, DEFAULT_OUTPUT)


def test_check_fails_when_the_index_is_hand_edited(plugin_dir: Path) -> None:
    apply_index(plugin_dir, DEFAULT_OUTPUT)
    target = plugin_dir / DEFAULT_OUTPUT
    target.write_text(target.read_text(encoding="utf-8") + "hand edit\n", encoding="utf-8")
    with pytest.raises(SkillIndexError, match="stale"):
        check_index(plugin_dir, DEFAULT_OUTPUT)


def test_check_fails_when_the_index_is_missing(plugin_dir: Path) -> None:
    with pytest.raises(SkillIndexError, match="missing"):
        check_index(plugin_dir, DEFAULT_OUTPUT)


def test_apply_is_idempotent(plugin_dir: Path) -> None:
    _, first = apply_index(plugin_dir, DEFAULT_OUTPUT)
    _, second = apply_index(plugin_dir, DEFAULT_OUTPUT)
    assert first is True
    assert second is False


# ---------------------------------------------------------------------------
# Worktree exclusion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "worktree_rel",
    [
        "skills/.build-loop/worktrees/run-1/skills/alpha",
        "skills/.claude/worktrees/run-2/skills/alpha",
        "skills/node_modules/pkg/skills/alpha",
        "skills/plugin-artifacts/codex/skills/alpha",
    ],
)
def test_worktree_copies_are_excluded(plugin_dir: Path, worktree_rel: str) -> None:
    write_skill(plugin_dir, worktree_rel, name="alpha")
    rows = discover(plugin_dir)
    assert len(rows) == 2, [row.path for row in rows]


def test_similarly_named_skill_is_not_excluded(plugin_dir: Path) -> None:
    """`data-plane-worktrees` must survive the worktree filter."""
    write_skill(plugin_dir, "skills/data-plane-worktrees", name="data-plane-worktrees")
    names = {row.name for row in discover(plugin_dir)}
    assert "data-plane-worktrees" in names


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_missing_skills_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(SkillIndexError, match="no skills directory"):
        discover(tmp_path)


def test_missing_skills_dir_exits_one(tmp_path: Path) -> None:
    result = run_cli("--workdir", str(tmp_path), "--check")
    assert result.returncode == 1
    assert "no skills directory" in result.stderr


def test_malformed_frontmatter_is_listed_with_a_warning(plugin_dir: Path) -> None:
    write_skill(plugin_dir, "skills/broken", raw="# no frontmatter at all\n")
    write_skill(plugin_dir, "skills/nameless", name=None, description="Has no name.")
    write_skill(plugin_dir, "skills/mute", name="mute", description=None)

    rows = {row.path: row for row in discover(plugin_dir)}
    broken = rows["skills/broken/SKILL.md"]
    assert broken.name == "broken"  # falls back to the directory name
    assert broken.warning == "no YAML frontmatter block"

    nameless = rows["skills/nameless/SKILL.md"]
    assert nameless.name == "nameless"
    assert "missing `name:`" in (nameless.warning or "")

    mute = rows["skills/mute/SKILL.md"]
    assert "no description" in mute.description
    assert "missing `description:`" in (mute.warning or "")

    content = generate(plugin_dir, DEFAULT_OUTPUT)
    assert "## Frontmatter warnings" in content
    assert "skills/broken/SKILL.md" in content


def test_plugin_name_falls_back_to_directory(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert plugin_name(tmp_path) == tmp_path.name


def test_bad_manifest_json_falls_back(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8")
    assert plugin_name(tmp_path) == tmp_path.name


# ---------------------------------------------------------------------------
# CLI — json vs plain, exit codes
# ---------------------------------------------------------------------------

def test_cli_json_reports_counts(plugin_dir: Path) -> None:
    result = run_cli("--workdir", str(plugin_dir), "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["plugin"] == "demo-plugin"
    assert payload["count"] == 2
    assert payload["hidden"] == 2
    assert payload["public"] == 0
    assert payload["public_undeclared"] == 0
    assert payload["index_path"] == DEFAULT_OUTPUT.as_posix()
    assert {row["name"] for row in payload["skills"]} == {"alpha", "beta"}


def test_cli_plain_is_not_json(plugin_dir: Path) -> None:
    result = run_cli("--workdir", str(plugin_dir), "--plain")
    assert result.returncode == 0
    assert "demo-plugin: 2 skills" in result.stdout
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


def test_cli_apply_then_check_exit_codes(plugin_dir: Path) -> None:
    applied = run_cli("--workdir", str(plugin_dir), "--apply")
    assert applied.returncode == 0
    assert (plugin_dir / DEFAULT_OUTPUT).is_file()

    ok = run_cli("--workdir", str(plugin_dir), "--check")
    assert ok.returncode == 0

    write_skill(plugin_dir, "skills/alpha", name="alpha", description="Changed.")
    drifted = run_cli("--workdir", str(plugin_dir), "--check")
    assert drifted.returncode == 1
    assert "stale" in drifted.stderr


def test_cli_check_json_reports_error_and_exit_one(plugin_dir: Path) -> None:
    result = run_cli("--workdir", str(plugin_dir), "--check", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["in_sync"] is False
    assert "missing" in payload["error"]


def test_cli_custom_output_path(plugin_dir: Path) -> None:
    result = run_cli("--workdir", str(plugin_dir), "--output", "SKILLS.md", "--apply")
    assert result.returncode == 0
    written = (plugin_dir / "SKILLS.md").read_text(encoding="utf-8")
    # Root-level index links without the `../` hop.
    assert "](skills/alpha/SKILL.md)" in written


def test_cli_rejects_output_outside_workdir(plugin_dir: Path) -> None:
    outside = plugin_dir.parent / "elsewhere" / "INDEX.md"
    result = run_cli("--workdir", str(plugin_dir), "--output", str(outside), "--apply")
    assert result.returncode == 1
    assert "inside --workdir" in result.stderr


def test_main_returns_zero_in_process(plugin_dir: Path) -> None:
    assert main(["--workdir", str(plugin_dir), "--apply"]) == 0
    assert main(["--workdir", str(plugin_dir), "--check"]) == 0


# ---------------------------------------------------------------------------
# This repository — the live drift guard
# ---------------------------------------------------------------------------

def test_checked_in_index_is_current() -> None:
    """The committed index must match a fresh render of skills/**/SKILL.md."""
    check_index(REPO_ROOT, DEFAULT_OUTPUT)


def test_repo_index_covers_every_skill_file() -> None:
    on_disk = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "skills").rglob("SKILL.md")
    }
    indexed = {row.path for row in discover(REPO_ROOT)}
    assert indexed == on_disk


def test_repo_index_is_host_neutral() -> None:
    """No host-specific invocation syntax may leak into the shared index."""
    content = (REPO_ROOT / DEFAULT_OUTPUT).read_text(encoding="utf-8")
    for token in ("Skill(", "@anthropic", "claude-code://", "cursor://"):
        assert token not in content, token
