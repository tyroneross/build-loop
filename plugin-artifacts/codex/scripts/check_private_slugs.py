#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pre-commit guard — fails a commit that stages a private app slug.

build-loop is open source. Shipping a real private project slug in
examples, fixtures, or docs leaks the maintainer's private project data.
This scanner attacks the root cause: it runs on every commit, scans the
staged content of each tracked file against a denylist, and exits
non-zero (blocking the commit) on a hit.

Usage:
    python3 scripts/check_private_slugs.py            # scan staged files
    python3 scripts/check_private_slugs.py --all      # scan whole tree
    python3 scripts/check_private_slugs.py FILE...     # scan named files

Exit codes:
    0 — no private slug found (commit may proceed)
    1 — private slug found (commit blocked); offending lines printed
    2 — usage / git / config error (e.g. missing .private-slugs)

------------------------------------------------------------------------
DENYLIST — runtime config, NOT shipped in the tracked tree.
The list of guarded slugs lives in a gitignored ``.private-slugs`` file
(one slug per line) at the repo root. A tracked ``.private-slugs.example``
documents the format with generic placeholders. This keeps the real
private slugs out of every tracked file, including this one.
------------------------------------------------------------------------
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# Runtime denylist config (gitignored). One slug per line; ``#`` comments
# and blank lines ignored. Each line is a LITERAL slug (regex
# metacharacters are escaped), matched case-insensitively as a word-ish
# token.
DENYLIST_FILENAME = ".private-slugs"
EXAMPLE_FILENAME = ".private-slugs.example"

# This file necessarily contains denylist-adjacent logic; never scan it.
# Matched by resolved path (worktree/submodule-safe) and by basename.
SELF_BASENAME = "check_private_slugs.py"

# Files where a slug is an intentional, load-bearing historical record.
# These are exempt because genericizing them would falsify the record.
# Keep this list short and justify every entry. Currently empty: every
# tracked file is fully scrubbed and the guard enforces zero exceptions.
EXEMPT_PATHS: set[str] = set()


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if not out:
            print("check_private_slugs: cannot resolve repo root", file=sys.stderr)
            sys.exit(2)
        return Path(out)
    except (subprocess.CalledProcessError, OSError):
        print("check_private_slugs: not a git repo", file=sys.stderr)
        sys.exit(2)


def _load_denylist(root: Path) -> list[str]:
    """Read the gitignored .private-slugs file.

    Fail closed: a missing or empty config file is a usage error (exit 2),
    never a silent pass. Shipping the guard with no denylist would let
    every slug through unnoticed.
    """
    cfg = root / DENYLIST_FILENAME
    if not cfg.exists():
        print(
            f"check_private_slugs: {DENYLIST_FILENAME} not found at repo root.",
            file=sys.stderr,
        )
        print(
            f"  Copy {EXAMPLE_FILENAME} to {DENYLIST_FILENAME} and add the "
            f"private slugs to guard (one per line). The guard cannot run "
            f"without it.",
            file=sys.stderr,
        )
        sys.exit(2)
    example = root / EXAMPLE_FILENAME
    if example.exists():
        try:
            same = cfg.read_bytes() == example.read_bytes()
        except OSError:
            same = False
        if same:
            # UNARMED, not merely misconfigured. A verbatim copy of the example
            # denylist contains only placeholders, so every scan passes and the
            # guard reports success while protecting nothing. That is strictly
            # worse than a missing file, which at least exits 2 — it looks like
            # a green check. Observed 2026-08-18: this repo's local copy was
            # byte-identical to the example, so the pre-commit guard had been
            # vacuous while CI (which has the real list) failed on 101 hits.
            print(
                f"check_private_slugs: {DENYLIST_FILENAME} is byte-identical to "
                f"{EXAMPLE_FILENAME} — it holds placeholders, so this guard is "
                f"UNARMED and would pass anything.",
                file=sys.stderr,
            )
            print(
                "  Replace it with the real slugs (one per line). A guard that "
                "cannot fail is not a guard.",
                file=sys.stderr,
            )
            sys.exit(2)
    try:
        raw = cfg.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"check_private_slugs: cannot read {DENYLIST_FILENAME}: {exc}",
              file=sys.stderr)
        sys.exit(2)
    slugs = [
        ln.strip() for ln in raw.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not slugs:
        print(
            f"check_private_slugs: {DENYLIST_FILENAME} is empty — no slugs "
            f"to guard. Add at least one slug or remove the guard.",
            file=sys.stderr,
        )
        sys.exit(2)
    return slugs


def _compile_pattern(slugs: list[str]) -> re.Pattern[str]:
    # Boundary class is alphanumeric ONLY — underscore is treated as a
    # boundary that still allows the match, so an embedded slug like
    # ``_sample`` or ``sample_`` is caught. The original guard's
    # lookbehind included ``_`` while the lookahead did not; that
    # asymmetry let an underscore-prefixed slug slip past (SEC-005).
    #
    # Each denylist entry is a LITERAL slug, not a regex fragment — the
    # SEC-011 runtime ``.private-slugs`` config holds plain strings a
    # maintainer types without knowing regex. ``re.escape`` neutralises
    # every metacharacter, so a literal dot in ``rosslabs.ai`` matches
    # only a real dot, not the public ``rosslabs-ai-toolkit`` name.
    # Skipping ``re.escape`` here was the SEC-011 regression: it turned
    # a literal ``.`` into a wildcard and flagged ~30 legitimate public
    # marketplace references in CI.
    escaped = (re.escape(s) for s in slugs)
    return re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(escaped) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _staged_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _all_tracked(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _staged_content(root: Path, path: str) -> str | None:
    """Return the staged (index) blob of `path`, or None if unreadable/binary.

    Capture as bytes then decode; NUL bytes signal a binary blob — slug
    patterns are text-only so binaries are irrelevant and safe to skip.
    """
    r = subprocess.run(
        ["git", "show", f":{path}"], cwd=root, capture_output=True,
    )
    if r.returncode != 0:
        return None
    text = r.stdout.decode("utf-8", errors="replace")
    if "\x00" in text:  # binary file — no text slug can contain NUL
        return None
    return text


def _disk_content(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeError):
        return None


def _is_self(root: Path, path: str) -> bool:
    """Worktree/submodule-safe SELF check.

    A relative-path string compare breaks when the script is invoked
    from a worktree, a submodule, or with a cwd that differs from the
    repo root. Compare the resolved absolute path, with a basename
    fallback so the exemption holds even if path resolution is degraded.
    """
    if Path(path).name == SELF_BASENAME:
        try:
            resolved = (root / path).resolve()
            return resolved == Path(__file__).resolve()
        except (OSError, RuntimeError):
            # Resolution failed — fall back to the basename match, which
            # is already True at this point. The guard scanning itself
            # would always block, so basename exemption is the safe call.
            return True
    return False


# ---------------------------------------------------------------------------
# RATCHET BASELINE
# The tree carries pre-existing hits that predate this guard. Blocking all of
# them means the guard fails on every commit, and a check that always fails is
# a check everyone learns to bypass. So: record the KNOWN hits once, allow
# exactly those, and fail on anything NEW. The count only ever goes down.
#
# Safe to commit: the baseline stores the same redacted sha256:<12> digest the
# guard already prints to public Actions logs — never a slug value. It is keyed
# by file and digest rather than line number so ordinary edits do not churn it.
# ---------------------------------------------------------------------------
BASELINE_FILENAME = ".private-slugs-baseline.json"


def _load_baseline(root: Path) -> dict[str, dict[str, int]]:
    f = root / BASELINE_FILENAME
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        # A corrupt baseline must not silently disarm the guard.
        print(f"check_private_slugs: {BASELINE_FILENAME} unreadable — "
              f"treating every hit as new", file=sys.stderr)
        return {}


def _tally(hits: list[tuple[str, int, str, str]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for path, _lineno, slug, _line in hits:
        digest = "sha256:" + hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]
        out.setdefault(path, {}).setdefault(digest, 0)
        out[path][digest] += 1
    return out


def _write_baseline(root: Path, hits: list[tuple[str, int, str, str]]) -> None:
    payload = {
        "_comment": (
            "Known pre-existing private-slug hits, allowed so the guard does not "
            "block every commit. Digests only — never slug values. NEW hits still "
            "fail. Regenerate ONLY after genuinely reducing hits: "
            "python3 scripts/check_private_slugs.py --all --update-baseline"
        ),
        "total": len(hits),
        "files": _tally(hits),
    }
    (root / BASELINE_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _split_new(hits, baseline):
    """Partition hits into (new, baselined). A file/digest over its recorded
    count is new; under it is fine — the ratchet only tightens."""
    seen: dict[str, dict[str, int]] = {}
    new, old = [], []
    for hit in hits:
        path, _lineno, slug, _line = hit
        digest = "sha256:" + hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]
        seen.setdefault(path, {}).setdefault(digest, 0)
        seen[path][digest] += 1
        allowed = baseline.get(path, {}).get(digest, 0)
        (old if seen[path][digest] <= allowed else new).append(hit)
    return new, old


def main(argv: list[str]) -> int:
    root = _repo_root()
    pattern = _compile_pattern(_load_denylist(root))
    mode_all = "--all" in argv
    update_baseline = "--update-baseline" in argv
    explicit = [a for a in argv if not a.startswith("-")]

    if explicit:
        files = explicit
        reader = _disk_content
        ci_mode = True  # explicit/CI invocation — fail closed on unreadable
    elif mode_all:
        files = _all_tracked(root)
        reader = _disk_content
        ci_mode = True
    else:
        files = _staged_files(root)
        reader = _staged_content
        ci_mode = False

    hits: list[tuple[str, int, str, str]] = []
    unreadable: list[str] = []
    for path in files:
        # The denylist config and its tracked format-template both
        # necessarily contain denylist-vocabulary tokens; never scan
        # either. `.private-slugs` is gitignored, but an explicit
        # FILE... invocation could still name it; `.private-slugs.example`
        # IS tracked and would otherwise self-trip the guard on its own
        # sentinel tokens. Match by basename so the exemption holds from
        # any cwd / worktree. Neither file can hold a real private slug:
        # `.private-slugs` is gitignored and `.private-slugs.example`
        # ships sentinel placeholders reviewed in every PR.
        if Path(path).name in (DENYLIST_FILENAME, EXAMPLE_FILENAME):
            continue
        if _is_self(root, path) or path in EXEMPT_PATHS:
            continue
        content = reader(root, path)
        if content is None:
            unreadable.append(path)
            # Staged mode: a blob git can't show is not committable
            # content for this path — skip is reasonable. CI/explicit
            # mode: never silently pass an unreadable tracked file.
            if ci_mode:
                print(f"check_private_slugs: cannot read tracked file: {path}",
                      file=sys.stderr)
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            m = pattern.search(line)
            if m:
                hits.append((path, lineno, m.group(1), line.strip()[:200]))

    if update_baseline:
        if not mode_all:
            print("check_private_slugs: --update-baseline requires --all",
                  file=sys.stderr)
            return 2
        _write_baseline(root, hits)
        print(f"baseline written: {len(hits)} hit(s) recorded in "
              f"{BASELINE_FILENAME}. NEW hits will still fail.")
        return 0

    # Ratchet: allow the recorded pre-existing hits, fail on anything new.
    new_hits, baselined = _split_new(hits, _load_baseline(root))
    if baselined and not new_hits:
        print(f"check_private_slugs: {len(baselined)} known hit(s) allowed by "
              f"{BASELINE_FILENAME}; 0 new. Cleanup still owed.", file=sys.stderr)
    hits = new_hits

    if hits:
        print("BLOCKED: a NEW private app slug was introduced.", file=sys.stderr)
        print("build-loop is open source. Pre-existing hits are baselined and",
              file=sys.stderr)
        print("allowed; this one is NOT in the baseline. Replace it with a",
              file=sys.stderr)
        print("generic placeholder before committing.\n", file=sys.stderr)
        for path, lineno, slug, _line in hits:
            # Redact: emit location + a stable short hash only — never the raw
            # slug or its surrounding line. This output lands in public Actions
            # logs, so printing the matched private slug would re-leak it. The
            # hash lets a maintainer correlate locally without exposure.
            digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]
            print(f"  {path}:{lineno}: [redacted slug sha256:{digest}]", file=sys.stderr)
        print(f"\nIf a hit is an intentional historical record, add the path to",
              file=sys.stderr)
        print(f"EXEMPT_PATHS in scripts/{SELF_BASENAME}.", file=sys.stderr)
        return 1

    if ci_mode and unreadable:
        print(
            f"\ncheck_private_slugs: {len(unreadable)} tracked file(s) could "
            f"not be read and were NOT scanned — failing closed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
