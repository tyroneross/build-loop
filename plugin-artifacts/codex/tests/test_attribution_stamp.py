# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/attribution_stamp.py``.

Covers:

* Idempotency — re-running with the same args is a no-op.
* ``--restamp`` behaviour — replaces existing SPDX header lines.
* Language coverage — Python, TypeScript, shell, markdown (with and without
  frontmatter), shebang preservation.
* REUSE.toml correctness — written; idempotent.
* Canary marker embedding — added once, kept on re-run.
* NOTICE / CONTRIBUTING / README integration.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "attribution_stamp.py"


@pytest.fixture(scope="module")
def stamp_module():
    """Load ``attribution_stamp`` as a module by file path.

    The script uses a uv-style PEP 723 shebang so it is not importable
    via the usual ``scripts.`` namespace; load via spec instead.
    """
    spec = importlib.util.spec_from_file_location("attribution_stamp", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["attribution_stamp"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    """Build a small scratch repo that exercises every language path."""
    root = tmp_path / "scratch-repo"
    root.mkdir()

    # LICENSE — minimal stub so ensure_license_appendix has something to update
    (root / "LICENSE").write_text(
        "Apache License\nVersion 2.0\n   Copyright 2024 Old Name\n",
        encoding="utf-8",
    )
    # README — has no License section
    (root / "README.md").write_text("# scratch-repo\n\nSomething.\n", encoding="utf-8")

    # Standard shipped paths
    for d in ("src", "scripts", "skills/test", "agents", "commands", "references"):
        (root / d).mkdir(parents=True)

    # TypeScript file
    (root / "src" / "core.ts").write_text(
        "export const X = 1;\nexport function f() { return X; }\n",
        encoding="utf-8",
    )
    # Python with shebang
    (root / "scripts" / "tool.py").write_text(
        '#!/usr/bin/env python3\n"""Tool docstring."""\nprint("hi")\n',
        encoding="utf-8",
    )
    # Python without shebang
    (root / "scripts" / "lib.py").write_text(
        '"""Library module."""\n\ndef g():\n    return 1\n',
        encoding="utf-8",
    )
    # Shell with shebang
    (root / "scripts" / "do.sh").write_text(
        "#!/usr/bin/env bash\necho hi\n",
        encoding="utf-8",
    )
    # Markdown with frontmatter
    (root / "skills" / "test" / "SKILL.md").write_text(
        "---\nname: test\ndescription: t\n---\n\n# Test Skill\n",
        encoding="utf-8",
    )
    # Markdown without frontmatter
    (root / "references" / "notes.md").write_text(
        "# Notes\n\nSome notes.\n",
        encoding="utf-8",
    )
    # JSON file (REUSE.toml should cover, no per-file SPDX)
    (root / "src" / "config.json").write_text('{"key": "value"}\n', encoding="utf-8")
    # Agent file
    (root / "agents" / "thing.md").write_text("# Thing agent\n", encoding="utf-8")

    return root


def _default_params(stamp_module, repo: Path, restamp: bool = False, canary=None):
    return stamp_module.StampParams(
        name=stamp_module.DEFAULT_NAME,
        email=stamp_module.DEFAULT_EMAIL,
        years=stamp_module.DEFAULT_YEARS,
        repo_root=repo,
        paths=list(stamp_module.DEFAULT_PATHS),
        excludes=set(stamp_module.DEFAULT_EXCLUDES),
        restamp=restamp,
        canary_files=[Path(p) for p in (canary or [])],
        repo_name="scratch-repo",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_first_run_adds_headers_everywhere(stamp_module, scratch_repo):
    counts = stamp_module.run(_default_params(stamp_module, scratch_repo))
    # Every language-supported file should be stamped
    assert counts["files_added"] >= 6
    assert counts["files_kept"] == 0
    assert counts["notice"] == "written"
    assert counts["reuse"] == "written"
    assert counts["contributing"] == "written"
    assert counts["readme"] == "added"
    assert counts["license"] == "updated"

    # Spot-check actual content
    ts = (scratch_repo / "src" / "core.ts").read_text()
    assert "// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>" in ts
    assert "// SPDX-License-Identifier: Apache-2.0" in ts

    # Shebang preserved at line 0 for Python script
    tool_py = (scratch_repo / "scripts" / "tool.py").read_text()
    assert tool_py.startswith("#!/usr/bin/env python3\n")
    assert "# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>" in tool_py
    assert "# SPDX-License-Identifier: Apache-2.0" in tool_py

    # Markdown frontmatter preserved at top; SPDX inserted after closing ---
    skill_md = (scratch_repo / "skills" / "test" / "SKILL.md").read_text()
    lines = skill_md.splitlines()
    assert lines[0] == "---"
    closing_idx = lines.index("---", 1)
    spdx_after_idx = closing_idx + 1
    assert lines[spdx_after_idx].startswith("<!-- SPDX-FileCopyrightText:")
    assert "Tyrone Ross, Jr" in lines[spdx_after_idx]
    assert "46267523+tyroneross@users.noreply.github.com" in lines[spdx_after_idx]


def test_idempotency(stamp_module, scratch_repo):
    """Second run with same args should keep, not add."""
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    counts2 = stamp_module.run(_default_params(stamp_module, scratch_repo))
    assert counts2["files_added"] == 0
    assert counts2["files_kept"] >= 6
    assert counts2["files_restamped"] == 0
    assert counts2["reuse"] == "kept"
    assert counts2["contributing"] == "kept"
    assert counts2["readme"] == "kept"
    assert counts2["license"] == "kept"


def test_restamp_replaces_outdated_strings(stamp_module, scratch_repo):
    # Stamp first with old-style strings (simulate the build-loop pre-fix state)
    old_params = stamp_module.StampParams(
        name="Tyrone Ross",  # missing ", Jr"
        email="noreply@github.com",  # missing canonical numeric prefix
        years="2025-2026",
        repo_root=scratch_repo,
        paths=list(stamp_module.DEFAULT_PATHS),
        excludes=set(stamp_module.DEFAULT_EXCLUDES),
        restamp=False,
        canary_files=[],
        repo_name="scratch-repo",
    )
    stamp_module.run(old_params)
    ts = (scratch_repo / "src" / "core.ts").read_text()
    assert "Tyrone Ross <noreply@github.com>" in ts
    assert "Tyrone Ross, Jr" not in ts

    # Now re-stamp with canonical params
    new_params = _default_params(stamp_module, scratch_repo, restamp=True)
    counts = stamp_module.run(new_params)
    assert counts["files_restamped"] >= 6
    ts2 = (scratch_repo / "src" / "core.ts").read_text()
    assert "Tyrone Ross, Jr" in ts2
    assert "46267523+tyroneross@users.noreply.github.com" in ts2
    # Old form must be gone
    assert "Tyrone Ross <noreply@github.com>" not in ts2


def test_no_duplicate_headers_on_restamp(stamp_module, scratch_repo):
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    stamp_module.run(_default_params(stamp_module, scratch_repo, restamp=True))
    ts = (scratch_repo / "src" / "core.ts").read_text()
    assert ts.count("SPDX-FileCopyrightText:") == 1
    assert ts.count("SPDX-License-Identifier:") == 1


def test_reuse_toml_contents(stamp_module, scratch_repo):
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    reuse = (scratch_repo / "REUSE.toml").read_text()
    assert "version = 1" in reuse
    assert "Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>" in reuse
    assert "Apache-2.0" in reuse
    assert "**/*.json" in reuse
    assert "NOTICE" in reuse


def test_notice_mentions_both_ai_assistants(stamp_module, scratch_repo):
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    notice = (scratch_repo / "NOTICE").read_text()
    assert "Tyrone Ross, Jr" in notice
    assert "Anthropic's Claude" in notice
    assert "Codex" in notice
    assert "Claude Code" in notice


def test_contributing_mentions_both_ai_assistants(stamp_module, scratch_repo):
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    contrib = (scratch_repo / "CONTRIBUTING.md").read_text()
    assert "Claude Opus" in contrib
    assert "OpenAI Codex" in contrib
    assert "noreply@anthropic.com" in contrib
    assert "noreply@openai.com" in contrib


def test_canary_embedded_once(stamp_module, scratch_repo):
    canary = ["src/core.ts", "references/notes.md"]
    p = _default_params(stamp_module, scratch_repo, canary=canary)
    counts = stamp_module.run(p)
    assert counts["canary_added"] == 2
    # Re-run keeps canary
    counts2 = stamp_module.run(_default_params(stamp_module, scratch_repo, canary=canary))
    assert counts2["canary_kept"] == 2
    assert counts2["canary_added"] == 0
    # And the marker is present
    ts = (scratch_repo / "src" / "core.ts").read_text()
    assert "build-loop@tyroneross:canary" in ts


def test_readme_section_added_when_missing_and_kept_when_present(stamp_module, scratch_repo):
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    assert "## License & Attribution" in (scratch_repo / "README.md").read_text()
    # Second pass keeps it
    counts = stamp_module.run(_default_params(stamp_module, scratch_repo))
    assert counts["readme"] == "kept"


def test_license_appendix_updated(stamp_module, scratch_repo):
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    license_text = (scratch_repo / "LICENSE").read_text()
    assert "Copyright 2025-2026 Tyrone Ross, Jr" in license_text
    # Old "Copyright 2024 Old Name" line should be replaced
    assert "Copyright 2024 Old Name" not in license_text


def test_excludes_respected(stamp_module, scratch_repo):
    # Drop a file in node_modules which should be ignored
    (scratch_repo / "src" / "node_modules").mkdir()
    (scratch_repo / "src" / "node_modules" / "vendor.ts").write_text("export const v = 1;\n")
    stamp_module.run(_default_params(stamp_module, scratch_repo))
    vendor = (scratch_repo / "src" / "node_modules" / "vendor.ts").read_text()
    assert "SPDX-FileCopyrightText" not in vendor


def test_restamp_through_long_frontmatter(stamp_module, scratch_repo):
    """Regression: agent .md files have 20+ line YAML frontmatter; the
    existing SPDX line lives after the closing ``---`` and was missed by
    the original fixed-20-line detection window.
    """
    agent = scratch_repo / "agents" / "big.md"
    agent.write_text(
        "---\n"
        "name: big-agent\n"
        "description: |\n"
        "  line 1\n  line 2\n  line 3\n  line 4\n  line 5\n  line 6\n  line 7\n"
        "  line 8\n  line 9\n  line 10\n  line 11\n  line 12\n  line 13\n  line 14\n"
        "  line 15\n  line 16\n  line 17\n  line 18\n  line 19\n  line 20\n"
        "model: claude-opus-4-7\n"
        "color: magenta\n"
        "tools: ['Read']\n"
        "---\n"
        "\n"
        "<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->\n"
        "\n"
        "# Body\n",
        encoding="utf-8",
    )

    # Restamp with canonical params; the existing bare SPDX must be replaced,
    # not duplicated alongside a new canonical line.
    stamp_module.run(_default_params(stamp_module, scratch_repo, restamp=True))
    text = agent.read_text()
    # Canonical form present
    assert "Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>" in text
    # Old bare form absent
    assert "SPDX-FileCopyrightText: 2025-2026 Tyrone Ross |" not in text
    # Exactly ONE SPDX line in the head of the file
    assert text.count("SPDX-FileCopyrightText:") == 1


def test_custom_paths(stamp_module, scratch_repo):
    """Caller can pass non-default --paths (e.g. for Python-heavy repos)."""
    (scratch_repo / "my_package").mkdir()
    (scratch_repo / "my_package" / "__init__.py").write_text('"""init."""\n')
    params = stamp_module.StampParams(
        name=stamp_module.DEFAULT_NAME,
        email=stamp_module.DEFAULT_EMAIL,
        years=stamp_module.DEFAULT_YEARS,
        repo_root=scratch_repo,
        paths=["my_package"],
        excludes=set(stamp_module.DEFAULT_EXCLUDES),
        restamp=False,
        canary_files=[],
        repo_name="scratch-repo",
    )
    stamp_module.run(params)
    init = (scratch_repo / "my_package" / "__init__.py").read_text()
    assert "SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr" in init
    # Did NOT touch src/ because we overrode --paths
    src_ts = (scratch_repo / "src" / "core.ts").read_text()
    assert "SPDX-FileCopyrightText" not in src_ts
