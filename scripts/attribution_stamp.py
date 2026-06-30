#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Idempotent attribution stamper for Apache-2.0 repos.

Applies the four-layer attribution model documented in the
``attribution-standard`` skill:

1. NOTICE file (Apache 2.0 §4(d) preservation)
2. Per-file SPDX headers (Apache 2.0 §4(c) preservation; REUSE 3.3)
3. REUSE.toml for files that cannot carry an inline comment
4. Canary markers in two stable, central files

Usage::

    python scripts/attribution_stamp.py \
        --repo . \
        --name "Tyrone Ross, Jr" \
        --email "46267523+tyroneross@users.noreply.github.com" \
        --years 2025-2026 \
        --canary-files path/to/central1.py path/to/central2.md \
        [--paths src scripts skills agents commands references] \
        [--restamp] \
        [--repo-name build-loop]

By default the script is **non-destructive**: it adds missing layers and
skips files that already carry a header. ``--restamp`` REPLACES existing
SPDX header lines so that a canonical-string change (e.g. adding ``, Jr``
or an email tail) can be rolled across the tree.

Exit codes
----------
0 — success
1 — usage error / unreadable repo
2 — partial write failure (some files updated, some failed)
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import sys
from pathlib import Path

# --- Canonical default strings -----------------------------------------------
# These mirror the values documented in skills/attribution-standard/SKILL.md.
DEFAULT_NAME = "Tyrone Ross, Jr"
DEFAULT_EMAIL = "46267523+tyroneross@users.noreply.github.com"
DEFAULT_YEARS = "2025-2026"
DEFAULT_PATHS = ["src", "scripts", "hooks", "skills", "agents", "commands", "references"]
DEFAULT_EXCLUDES = {
    "node_modules",
    "dist",
    "build",
    ".git",
    ".venv",
    "venv",
    "archive",
    "tests/fixtures",
    "docs/test-fixtures",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

# --- Language → comment-style table ------------------------------------------
# Order matters for matching shebangs / frontmatter.
COMMENT_STYLES = {
    "hash": {".py", ".sh", ".bash", ".zsh", ".rb", ".toml", ".yml", ".yaml"},
    "slash": {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".go", ".rs", ".swift", ".java", ".kt"},
    "html_comment": {".md", ".mdx", ".html"},
}

CANARY_OPEN = "build-loop@tyroneross:canary"  # generic across repos; we ship a stable token
CANARY_CLOSE = "canary-end"


@dataclasses.dataclass
class StampParams:
    """Materialised attribution parameters."""

    name: str
    email: str
    years: str
    repo_root: Path
    paths: list[str]
    excludes: set[str]
    restamp: bool
    canary_files: list[Path]
    repo_name: str

    @property
    def copyright_text_with_email(self) -> str:
        return f"{self.years} {self.name} <{self.email}>"

    @property
    def copyright_text_no_email(self) -> str:
        # Used in NOTICE / LICENSE appendix where the email tail is omitted by convention
        return f"{self.years} {self.name}"


# ---------------------------------------------------------------------------
# SPDX header rendering
# ---------------------------------------------------------------------------
def render_spdx_lines(style: str, params: StampParams) -> list[str]:
    """Return the two SPDX lines pre-formatted for the given comment style."""
    copyright_line = f"SPDX-FileCopyrightText: {params.copyright_text_with_email}"
    license_line = "SPDX-License-Identifier: Apache-2.0"
    if style == "hash":
        return [f"# {copyright_line}", f"# {license_line}"]
    if style == "slash":
        return [f"// {copyright_line}", f"// {license_line}"]
    if style == "html_comment":
        # Single-line HTML comment to match the existing repo convention
        return [f"<!-- {copyright_line} | {license_line} -->"]
    raise ValueError(f"unknown comment style: {style}")


SPDX_LINE_PATTERNS = {
    "hash": re.compile(r"^#\s*SPDX-(FileCopyrightText|License-Identifier):.*$"),
    "slash": re.compile(r"^//\s*SPDX-(FileCopyrightText|License-Identifier):.*$"),
    "html_comment": re.compile(r"^<!--\s*SPDX-FileCopyrightText:.*SPDX-License-Identifier:.*-->\s*$"),
}


def classify_style(path: Path) -> str | None:
    suffix = path.suffix.lower()
    for style, suffixes in COMMENT_STYLES.items():
        if suffix in suffixes:
            return style
    return None


# ---------------------------------------------------------------------------
# Per-file stamping
# ---------------------------------------------------------------------------
def stamp_file(path: Path, params: StampParams) -> str:
    """Return ``"added" | "restamped" | "kept" | "skipped"`` for the file.

    Behaviour:

    * If no SPDX header is present → insert one (returns ``"added"``).
    * If a header is present and ``--restamp`` is set → replace it
      (returns ``"restamped"``).
    * If a header is present and ``--restamp`` is not set → leave alone
      (returns ``"kept"``).
    """
    style = classify_style(path)
    if style is None:
        return "skipped"

    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return "skipped"
    lines = content.splitlines(keepends=False)
    eol = "\n"

    spdx_lines = render_spdx_lines(style, params)
    pattern = SPDX_LINE_PATTERNS[style]

    # Scan a generous window so YAML frontmatter (which can be 20+ lines for
    # agent definitions) does not push the existing SPDX line past the lookahead.
    detection_window = _detection_window(lines, style)
    has_header = any(pattern.match(ln) for ln in lines[:detection_window])

    if has_header and not params.restamp:
        return "kept"

    if has_header and params.restamp:
        # Replace EVERY matching SPDX line within the detection window.
        # First match becomes the new block (joined); duplicate matches drop.
        new_lines: list[str] = []
        replaced = False
        for idx, ln in enumerate(lines):
            if idx < detection_window and pattern.match(ln):
                if not replaced:
                    new_lines.extend(spdx_lines)
                    replaced = True
                # else: drop the duplicate SPDX line
            else:
                new_lines.append(ln)
        new_content = eol.join(new_lines)
        if content.endswith("\n") and not new_content.endswith("\n"):
            new_content += "\n"
        path.write_text(new_content, encoding="utf-8")
        return "restamped"

    # Insert a fresh header at the appropriate spot.
    insert_idx = _find_insert_index(lines, style)
    new_lines = list(lines[:insert_idx]) + list(spdx_lines) + list(lines[insert_idx:])
    new_content = eol.join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"
    elif not content.endswith("\n") and new_content == eol.join(new_lines):
        new_content += "\n"
    path.write_text(new_content, encoding="utf-8")
    return "added"


def _detection_window(lines: list[str], style: str) -> int:
    """How many lines to scan for an existing SPDX header.

    YAML frontmatter on agent / skill markdown can easily run 20+ lines, so a
    fixed 20-line window misses the existing SPDX line that lives right after
    the closing ``---``. Use a window that always covers (insert-index + 10).
    """
    base = _find_insert_index(lines, style)
    return min(len(lines), base + 10)


def _find_insert_index(lines: list[str], style: str) -> int:
    """Find where to insert the SPDX header.

    * After ``#!`` shebang for hash-style scripts.
    * After ``---`` YAML frontmatter for markdown.
    * After leading ``/* @license ... */`` for slash-style files (rare, leave as-is).
    * Otherwise at the very top.
    """
    if not lines:
        return 0
    if style == "hash" and lines[0].startswith("#!"):
        # Place SPDX immediately after the shebang
        return 1
    if style == "html_comment" and lines[0].strip() == "---":
        # YAML frontmatter: find closing ---
        for idx in range(1, min(len(lines), 200)):
            if lines[idx].strip() == "---":
                return idx + 1
        return 0
    return 0


# ---------------------------------------------------------------------------
# NOTICE, LICENSE, CONTRIBUTING, REUSE.toml, README
# ---------------------------------------------------------------------------
NOTICE_TEMPLATE = """{repo_name}
Copyright {copyright_text_no_email}

This product was authored by {name} (https://github.com/tyroneross/{repo_name}).
Portions of this software were developed with the assistance of Anthropic's Claude
(via Claude Code) and OpenAI's Codex (via Codex CLI); AI-pair-programming
contributions are attributed via Co-Authored-By trailers in the git history.
"""


def write_notice(params: StampParams) -> str:
    """Write NOTICE file. Returns ``"written"`` (always overwrites)."""
    notice_path = params.repo_root / "NOTICE"
    contents = NOTICE_TEMPLATE.format(
        repo_name=params.repo_name,
        copyright_text_no_email=params.copyright_text_no_email,
        name=params.name,
    )
    notice_path.write_text(contents, encoding="utf-8")
    return "written"


# Match any `   Copyright YEAR[-YEAR] <name>` line in the LICENSE appendix
# region. Matches both pre-existing Tyrone Ross variants AND placeholder
# names like "Old Name" so re-stamping cleans up legacy LICENSEs cleanly.
LICENSE_APPENDIX_LINE_RE = re.compile(
    r"^   Copyright \d{4}(?:-\d{4})?\s+[^\n]+$",
    re.MULTILINE,
)


def ensure_license_appendix(params: StampParams) -> str:
    """Ensure LICENSE contains a ``Copyright YEARS NAME`` appendix line.

    Returns ``"updated" | "kept" | "missing"``.
    """
    license_path = params.repo_root / "LICENSE"
    if not license_path.exists():
        return "missing"
    contents = license_path.read_text(encoding="utf-8")
    expected_line = f"   Copyright {params.copyright_text_no_email}"
    if expected_line in contents:
        return "kept"
    # Try to replace any existing Copyright YYYY-YYYY Tyrone Ross line
    if LICENSE_APPENDIX_LINE_RE.search(contents):
        new_contents = LICENSE_APPENDIX_LINE_RE.sub(expected_line, contents, count=1)
        license_path.write_text(new_contents, encoding="utf-8")
        return "updated"
    # Else append at end
    if not contents.endswith("\n"):
        contents += "\n"
    contents += f"\n{expected_line}\n"
    license_path.write_text(contents, encoding="utf-8")
    return "updated"


REUSE_TEMPLATE = """# SPDX-FileCopyrightText: {copyright_text_with_email}
# SPDX-License-Identifier: Apache-2.0
#
# REUSE 3.3 annotations for files that cannot carry an in-file SPDX header
# (JSON has no comment syntax; binary/generated assets must be covered by
# REUSE.toml or by sidecar .license files).
#
# Spec: https://reuse.software/spec-3.3/
# Validate: `uvx reuse lint`

version = 1

# Shipped JSON sources and top-level docs/metadata.
[[annotations]]
path = [
    "**/*.json",
    "README.md",
    "CONTRIBUTING.md",
    "NOTICE",
]
precedence = "aggregate"
SPDX-FileCopyrightText = "{copyright_text_with_email}"
SPDX-License-Identifier = "Apache-2.0"
"""


def write_reuse_toml(params: StampParams) -> str:
    """Write or update REUSE.toml. Idempotent. Returns ``"written" | "kept"``."""
    reuse_path = params.repo_root / "REUSE.toml"
    contents = REUSE_TEMPLATE.format(copyright_text_with_email=params.copyright_text_with_email)
    if reuse_path.exists():
        existing = reuse_path.read_text(encoding="utf-8")
        if params.copyright_text_with_email in existing and "version = 1" in existing:
            if not params.restamp:
                return "kept"
    reuse_path.write_text(contents, encoding="utf-8")
    return "written"


CONTRIBUTING_TEMPLATE = """<!-- SPDX-FileCopyrightText: {copyright_text_with_email} | SPDX-License-Identifier: Apache-2.0 -->

# Contributing to {repo_name}

Thanks for your interest. A few load-bearing conventions before you open a PR.

## License & Attribution

{repo_name} is licensed under the **Apache License, Version 2.0**. By contributing, you agree your contribution is licensed under the same terms.

Downstream redistribution rules (from the license itself):

- Apache 2.0 **§4(c)** — you must retain, in the source form of any derivative work, all copyright, patent, trademark, and attribution notices from the source form of the work. Translation: don't strip the per-file `SPDX-FileCopyrightText` and `SPDX-License-Identifier` headers when you fork or vendor source files.
- Apache 2.0 **§4(d)** — if the work includes a `NOTICE` file, derivative works you distribute must include a readable copy of the attribution notices it contains. Translation: when you redistribute {repo_name}, the `NOTICE` file at the repo root must travel with it (in a `NOTICE` file, in your docs, or rendered by the product). The contents of `NOTICE` are informational — they cannot add license terms — but the obligation to preserve them is binding.

Per-file headers in this repo follow REUSE 3.3 (https://reuse.software/spec-3.3/). Files that cannot carry an inline comment (`.json`, binary assets) are annotated via `REUSE.toml` at the repo root. Validate locally with `uvx reuse lint`.

## AI co-author attribution

A significant portion of this codebase was written collaboratively with AI coding assistants — Anthropic's Claude (via Claude Code) and OpenAI's Codex (via Codex CLI). The convention this repo follows: **every commit produced with meaningful AI assistance ends with a Git `Co-Authored-By:` trailer naming the model**.

For Claude Code sessions, the trailer is:

```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Substitute the actual model + tier you used (e.g., `Claude Sonnet 5`, `Claude Haiku 4.5`).

For Codex CLI sessions, the trailer is:

```
Co-Authored-By: OpenAI Codex <noreply@openai.com>
```

GitHub renders the avatar of any recognized email on the commit page, so the AI contribution is visible at the commit level. This is a community convention, not a legal requirement of Apache 2.0. If you're authoring without AI assistance, omit the trailer; don't pad commits with it.

## Signed commits

Signed commits (`git commit -S` for GPG, or SSH-signed via `git config gpg.format ssh`) are **recommended** and surfaced as `Verified` badges by GitHub. They strengthen the evidentiary chain in case of an authorship dispute. They are not enforced.

## Commit message style

Conventional Commits (https://www.conventionalcommits.org/) — `type(scope): subject`. Common types in this repo: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`.
"""


def write_contributing(params: StampParams) -> str:
    """Write CONTRIBUTING.md. Idempotent unless --restamp. Returns ``"written" | "kept"``."""
    path = params.repo_root / "CONTRIBUTING.md"
    contents = CONTRIBUTING_TEMPLATE.format(
        copyright_text_with_email=params.copyright_text_with_email,
        repo_name=params.repo_name,
    )
    if path.exists() and not params.restamp:
        existing = path.read_text(encoding="utf-8")
        # Treat as up-to-date when canonical strings AND Codex mention are present
        if (
            params.copyright_text_with_email in existing
            and "OpenAI Codex" in existing
        ):
            return "kept"
    path.write_text(contents, encoding="utf-8")
    return "written"


README_LICENSE_SECTION = """## License & Attribution

This project is licensed under the [Apache License 2.0](LICENSE).

- [`LICENSE`](LICENSE) — full license text.
- [`NOTICE`](NOTICE) — attribution notices that, per Apache 2.0 §4(d), must travel with any redistribution of this work.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution conventions: per-file SPDX headers (REUSE 3.3), AI co-author trailer, signed commits, conventional commits.

Per-file `SPDX-FileCopyrightText` and `SPDX-License-Identifier` headers are required on shipped source files. Files that cannot carry inline comments (JSON, generated assets) are annotated in [`REUSE.toml`](REUSE.toml). Validate compliance locally with `uvx reuse lint`.
"""


def ensure_readme_license_section(params: StampParams) -> str:
    """Add a 'License & Attribution' section to README.md if missing.

    Returns ``"added" | "kept" | "missing"``.
    """
    readme_path = params.repo_root / "README.md"
    if not readme_path.exists():
        return "missing"
    contents = readme_path.read_text(encoding="utf-8")
    if "## License & Attribution" in contents:
        return "kept"
    if not contents.endswith("\n"):
        contents += "\n"
    contents += "\n" + README_LICENSE_SECTION
    readme_path.write_text(contents, encoding="utf-8")
    return "added"


# ---------------------------------------------------------------------------
# Canary markers
# ---------------------------------------------------------------------------
def embed_canary(path: Path, params: StampParams) -> str:
    """Insert a canary marker comment block into a file if not already present.

    Returns ``"added" | "kept" | "skipped"``.
    """
    if not path.exists():
        return "skipped"
    style = classify_style(path)
    if style is None:
        return "skipped"
    try:
        contents = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return "skipped"
    if CANARY_OPEN in contents:
        return "kept"

    canary_lines = _render_canary(style, params)
    # Insert canary after the SPDX header block when present, else after any
    # shebang / YAML frontmatter (via _find_insert_index).
    lines = contents.splitlines(keepends=False)
    eol = "\n"
    pattern = SPDX_LINE_PATTERNS[style]
    detection_window = _detection_window(lines, style)
    insert_idx = 0
    for idx, ln in enumerate(lines[:detection_window]):
        if pattern.match(ln):
            insert_idx = idx + 1
    # Skip past shebang or YAML frontmatter when no SPDX present
    if insert_idx == 0:
        insert_idx = _find_insert_index(lines, style)
    new_lines = list(lines[:insert_idx]) + canary_lines + list(lines[insert_idx:])
    new_contents = eol.join(new_lines)
    if contents.endswith("\n") and not new_contents.endswith("\n"):
        new_contents += "\n"
    path.write_text(new_contents, encoding="utf-8")
    return "added"


def _render_canary(style: str, params: StampParams) -> list[str]:
    open_line = f"{CANARY_OPEN}:{params.repo_name}"
    close_line = CANARY_CLOSE
    if style == "hash":
        return [f"# {open_line}", f"# {close_line}"]
    if style == "slash":
        return [f"// {open_line}", f"// {close_line}"]
    if style == "html_comment":
        return [f"<!-- {open_line} -->", f"<!-- {close_line} -->"]
    raise ValueError(style)


# ---------------------------------------------------------------------------
# Walk + driver
# ---------------------------------------------------------------------------
def iter_shipped_files(params: StampParams):
    """Yield every shipped source file under params.paths, respecting excludes."""
    for top in params.paths:
        root = params.repo_root / top
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # In-place prune excluded directories
            dirnames[:] = [d for d in dirnames if d not in params.excludes]
            for fn in filenames:
                p = Path(dirpath) / fn
                rel = p.relative_to(params.repo_root)
                if any(part in params.excludes for part in rel.parts):
                    continue
                if classify_style(p) is None:
                    continue
                yield p


def run(params: StampParams) -> dict:
    """Drive the full stamping flow. Returns a counts dict for the caller/CLI."""
    counts = {
        "notice": "",
        "license": "",
        "reuse": "",
        "contributing": "",
        "readme": "",
        "files_added": 0,
        "files_restamped": 0,
        "files_kept": 0,
        "files_skipped": 0,
        "canary_added": 0,
        "canary_kept": 0,
        "canary_skipped": 0,
    }
    counts["notice"] = write_notice(params)
    counts["license"] = ensure_license_appendix(params)
    counts["reuse"] = write_reuse_toml(params)
    counts["contributing"] = write_contributing(params)
    counts["readme"] = ensure_readme_license_section(params)

    for p in iter_shipped_files(params):
        outcome = stamp_file(p, params)
        counts[f"files_{outcome}"] += 1

    for cf in params.canary_files:
        path = cf if cf.is_absolute() else params.repo_root / cf
        outcome = embed_canary(path, params)
        counts[f"canary_{outcome}"] += 1
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Idempotent attribution stamper for Apache-2.0 repos (four-layer model).",
    )
    parser.add_argument("--repo", required=True, help="Path to repo root.")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"Copyright holder (default: {DEFAULT_NAME!r}).")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help=f"Email tail for SPDX (default: {DEFAULT_EMAIL!r}).")
    parser.add_argument("--years", default=DEFAULT_YEARS, help=f"Year range (default: {DEFAULT_YEARS!r}).")
    parser.add_argument("--paths", nargs="*", default=None, help="Shipped paths to walk. Defaults to standard set.")
    parser.add_argument(
        "--excludes",
        nargs="*",
        default=None,
        help="Additional path components to exclude on top of defaults.",
    )
    parser.add_argument("--restamp", action="store_true", help="Replace existing SPDX header lines.")
    parser.add_argument("--canary-files", nargs="*", default=[], help="Files to receive canary markers.")
    parser.add_argument("--repo-name", default=None, help="Repo name used in NOTICE / canary (default: basename of --repo).")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        print(f"error: --repo {repo_root} is not a directory", file=sys.stderr)
        return 1

    excludes = set(DEFAULT_EXCLUDES)
    if args.excludes:
        excludes.update(args.excludes)

    params = StampParams(
        name=args.name,
        email=args.email,
        years=args.years,
        repo_root=repo_root,
        paths=args.paths if args.paths else list(DEFAULT_PATHS),
        excludes=excludes,
        restamp=args.restamp,
        canary_files=[Path(p) for p in args.canary_files],
        repo_name=args.repo_name or repo_root.name,
    )
    counts = run(params)

    print("attribution_stamp summary:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
