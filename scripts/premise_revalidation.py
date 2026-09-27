#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""premise_revalidation.py — re-validate queue-item premises at drain time.

Mirrors the design shipped in RossLabs Operations Center (commit 3fd0a23):
a nullable ``validated:`` frontmatter field, freshness falling back to
``created:``, a 7-day default window, a refusal code
(``stale_needs_revalidation``) wired into the same gate that already refuses
a card with no target repo, plus a ``validate --note`` stamp-and-receipt
command and a ``stale`` listing. Build-loop's queues are Markdown files, not
a DB, so the plumbing differs but the semantics match — do not reinvent them.

Beyond the OC design, this module ALSO mechanically re-checks the item's
cited anchors (file paths, commit SHAs) — this is what catches the "my own
disproof was itself stale" failure mode: an operator sweep reported a file
as deleted when it had actually been RELOCATED, and a repo as 0-ahead when
it was 6. A missing cited path is never concluded ``premise_broken`` on its
own — a same-basename match elsewhere in the repo routes it to
``needs_human_recheck`` (with the candidate named) instead, so a relocation
is never mistaken for a resolution.

Covers all three Phase-5 drain surfaces: ``.build-loop/issues/``,
``.build-loop/backlog/`` (top-level items AND the ``items/`` subdir), and
``.build-loop/followup/``.

Frontmatter parsing reuses ``backlog.py``'s ``parse_frontmatter`` /
``render_frontmatter`` rather than writing a second YAML-ish reader. Loaded
via ``importlib.util.spec_from_file_location`` (NOT a plain ``import
backlog``) because ``scripts/backlog/`` is ALSO a real package in this repo
(capture-time product-impact triage) — a plain ``sys.path.insert`` +
``import backlog`` resolves to that package (packages shadow same-named
modules in one FileFinder directory), silently hiding
``backlog.py``'s frontmatter functions. Loading ``backlog.py`` by explicit
file path sidesteps the name collision entirely without touching either
pre-existing file.
Deliberately NOT using ``backlog.py``'s ``read_item`` (which defaults every
field in backlog.py's OWN item schema — id/priority/type/area/gated/...):
issues/ and followup/ items have a different, thinner frontmatter shape, and
defaulting backlog-schema fields onto them would inject schema noise on the
next ``validate`` rewrite. ``parse_frontmatter``/``render_frontmatter`` are
schema-agnostic — they round-trip exactly the keys present.

Pure stdlib. Subprocess is used only for two OPTIONAL git calls
(``git ls-files`` for the basename/relocation index, ``git cat-file -e`` for
SHA reachability); both degrade gracefully (skip, never crash) when the
target directory isn't a git repo or git isn't on PATH.

Subcommands::

    premise_revalidation.py sweep    [--repo P] [--window-days N]
                                      [--queue issues|backlog|followup|all]
                                      [--json]
    premise_revalidation.py gate     --item <path> [--repo P]
                                      [--window-days N]
    premise_revalidation.py validate --item <path> --note "<evidence>"
    premise_revalidation.py stale    [--repo P] [--window-days N]
                                      [--queue issues|backlog|followup|all]
    premise_revalidation.py citations --repo P --input <lanes.json> [--json]
        Re-check subagent evidence before accepting it: each `ref` of the form
        path:line[-end] must exist, and its optional `expect` substring must sit
        within 3 lines; `kind: executed` must be a command, not a citation.

``sweep``/``stale`` always exit 0 (a sweep/listing reports; it does not
gate). ``gate`` exits 1 on ``stale_needs_revalidation``, ``premise_broken``,
or ``needs_human_recheck`` — only ``fresh`` is exit 0 — so the drain refuses
to schedule an item whose premise wasn't re-confirmed. ``validate`` exits 1
if ``--note`` is missing/empty (a bare timestamp would assert freshness
without evidence — the exact failure mode this module exists to close) and
does not touch the file in that case.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent


def _load_backlog_module():
    """Load scripts/backlog.py's parse_frontmatter/render_frontmatter by file
    path — NOT `sys.path.insert` + `import backlog`, because scripts/backlog/
    is ALSO a real package in this repo and would shadow the module (see
    module docstring)."""
    backlog_path = _SCRIPTS / "backlog.py"
    spec = importlib.util.spec_from_file_location("_premise_revalidation_backlog", backlog_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load backlog.py from {backlog_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backlog = _load_backlog_module()  # parse_frontmatter, render_frontmatter

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DEFAULT_WINDOW_DAYS = 7
_ENV_WINDOW_DAYS = "BL_PREMISE_TTL_DAYS"

# Statuses excluded from revalidation entirely — done/dropped work is history,
# not a live premise to re-check.
_EXCLUDED_STATUSES = ("done", "closed")

# Each queue maps to one or more subpaths under .build-loop/ to glob for
# `*.md` items. "backlog" covers BOTH the flat top-level dir (legacy/simple
# items, templates/backlog-item.md shape) and the items/ subdir (backlog.py's
# own schema) — the brief calls out both explicitly.
QUEUE_DIRS: dict[str, list[tuple[str, ...]]] = {
    "issues": [("issues",)],
    "backlog": [("backlog",), ("backlog", "items")],
    "followup": [("followup",)],
}
ALL_QUEUES = tuple(QUEUE_DIRS)

# Managed/derived files that live alongside real items in these dirs and must
# never be treated as an item to classify.
_SKIP_NAMES = {"INDEX.md", "README.md", "BACKLOG.md"}

VERDICTS = (
    "fresh",
    "stale_needs_revalidation",
    "premise_broken",
    "needs_human_recheck",
)

# ----------------------------------------------------------------------------
# Date helpers
# ----------------------------------------------------------------------------


def _today_iso(today: str | None = None) -> str:
    """Resolve "today" as YYYY-MM-DD. Explicit arg wins; else the system clock.

    No env/CLI override is exposed for this — the public functions accept
    ``today`` directly for deterministic unit tests, which is simpler than a
    second injection path and keeps the CLI surface to what the brief asked
    for.
    """
    if today:
        date.fromisoformat(today)  # validate; raises loudly on garbage
        return today
    return date.today().isoformat()


def resolve_window_days(arg: int | None) -> int:
    """--window-days > BL_PREMISE_TTL_DAYS env > DEFAULT_WINDOW_DAYS."""
    if arg is not None:
        return int(arg)
    env = os.environ.get(_ENV_WINDOW_DAYS)
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_WINDOW_DAYS


def _freshness_date(fm: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (source, date_str). ``validated`` wins; falls back to ``created``.

    An item filed a minute ago is fresh by construction (the ``created``
    fallback); one filed weeks ago and never re-checked is not.
    """
    validated = fm.get("validated")
    if validated:
        return "validated", str(validated)
    created = fm.get("created")
    if created:
        return "created", str(created)
    return None, None


# ----------------------------------------------------------------------------
# Anchor extraction (file paths + commit SHAs cited in the item body)
# ----------------------------------------------------------------------------

_BACKTICK_RE = re.compile(r"`([^`\s]+)`")
# One-level `{a,b,c}` brace-group placeholder, e.g.
# `.build-loop/evidence/composition-{400,1000}-before.json`. Used both to
# keep a brace-group token out of the numeric-segment rejection below (the
# LITERAL segment "composition-{400,1000}-before.json" is not itself
# numeric) and, in `_expand_braces`, to fan a cited path out into its
# alternatives so a broken/exists check can run against each one.
_BRACE_RE = re.compile(r"\{([^{}]+)\}")
# A trailing `:N`, `:N-M`, or `:N,M` line locator on an otherwise real path
# (`docs/observability.md:44`, `Sources/.../WorkStore.swift:4362-4407`) —
# strip it before the existence check, which must run against the real file,
# never a path+line composite that can never exist on disk.
_LINE_SUFFIX_RE = re.compile(r"^(.+):(\d+)(?:[-,]\d+)?$")
# A negative lookbehind (NOT `\b`) opens this so a leading dot-directory
# (`.build-loop/...`, `.github/...`, `.claude-plugin/...`) is admitted into
# the match rather than having its `.` stripped off as a "word boundary" —
# `\b` sits BETWEEN a non-word char (nothing, or another dot) and the first
# letter, so `\b((?:...)+...)"` on ".build-loop/config.json" was matching
# from "build-loop" onward, discarding the leading dot and inventing a
# garbage second candidate. `.?` in the group makes the leading dot part of
# the captured token; the lookbehind still refuses to start mid-token (e.g.
# never re-anchors after a `.` or `/` that's itself inside a longer path).
_BARE_PATH_RE = re.compile(r"(?<![\w./~-])(\.?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,6})\b")
_PATH_EXT_HINT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")

# A cited SHA must appear near a commit-ish word — bare 7-40 char hex runs are
# far too common (line numbers, IDs) to treat as commit citations on their own.
_SHA_CONTEXT_RE = re.compile(
    r"(?:commit|sha|rev(?:ision)?)\b[^\n]{0,24}?`?\b([0-9a-fA-F]{7,40})\b",
    re.IGNORECASE,
)


def _segment_is_numeric(seg: str) -> bool:
    """True if the WHOLE segment parses as a number (int or float) — used to
    reject non-path tokens like `13.6/-28.6` or `hardCeilingBytes/16` that
    are numeric ratios/params, not filesystem paths."""
    try:
        float(seg)
        return True
    except ValueError:
        return False


def _strip_line_suffix(tok: str) -> str:
    """Strip a trailing `:N`, `:N-M`, or `:N,M` line locator (see
    `_LINE_SUFFIX_RE`) so what's checked for existence is the real path."""
    m = _LINE_SUFFIX_RE.match(tok)
    return m.group(1) if m else tok


def _expand_braces(path: str) -> list[str]:
    """Expand ONE `{a,b,c}` brace group into its alternatives
    (`x-{400,1000}-y` -> `[x-400-y, x-1000-y]`). No path in the examples this
    module was built against nests brace groups, so only the first group is
    expanded; a path with none returns unchanged as a 1-element list."""
    m = _BRACE_RE.search(path)
    if not m:
        return [path]
    alts = [a.strip() for a in m.group(1).split(",")]
    prefix, suffix = path[: m.start()], path[m.end() :]
    return [f"{prefix}{alt}{suffix}" for alt in alts]


def _is_git_ref(repo: Path, tok: str) -> bool:
    """True if `tok` resolves as a reachable git ref (local/remote branch or
    tag) — covers an item citing a working-branch name (`bl/run-899386`)
    rather than a file path. `.exists()`, not `.is_dir()`: a git WORKTREE's
    `.git` is a FILE pointing at the real gitdir, not a directory — this
    module runs from inside one. False (skip, never crash) when there's no
    git metadata or git isn't on PATH."""
    if not (repo / ".git").exists():
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{tok}^{{commit}}"],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return r.returncode == 0


def _looks_like_path_token(tok: str, repo: Path | None = None) -> bool:
    """Conservative path-shaped check — a false positive is worse than a miss.

    ``repo``, when given, is used ONLY to reject an extension-less token
    whose first segment isn't an existing directory (a git ref/branch name
    like ``codex/storage-accounting-integration`` looks path-shaped but
    isn't). Callers that don't have a repo yet (bare unit tests on
    ``extract_paths``) keep the pre-existing, repo-agnostic behavior.
    """
    tok = tok.strip()
    if not tok or " " in tok or "\t" in tok:
        return False
    if "://" in tok or tok.startswith("http"):
        return False
    if tok.startswith("-"):
        return False
    if "<" in tok or ">" in tok:
        # `/private/tmp/ambient-core-tests-<uuid>` — a templated placeholder,
        # never a real filesystem path.
        return False
    if "::" in tok:
        # A pytest node ID (`scripts/test_x.py::TestA::test_b`) is a test
        # SELECTOR, not a filesystem path — the part after `::` is a class/
        # function name that will never exist on disk. Backtick extraction
        # (`_BACKTICK_RE`) has no internal boundary check, so a whole node ID
        # citation would otherwise be captured verbatim and convicted broken.
        return False
    if "/" not in tok:
        return False
    segments = [s for s in tok.split("/") if s]
    if not segments:
        # `/`, `//` or a prose slash run: nothing path-shaped remains.
        return False
    for seg in segments:
        bare_seg = _BRACE_RE.sub("", seg)  # a brace group isn't itself numeric
        if bare_seg.startswith("-") or _segment_is_numeric(bare_seg):
            # `13.6/-28.6`, `hardCeilingBytes/16` — a ratio or bare param,
            # not a path.
            return False
    if _PATH_EXT_HINT_RE.search(tok):
        return True
    if repo is not None and not (repo / segments[0]).is_dir():
        # No extension AND the first segment isn't a real directory here —
        # most likely a branch name (`bl/run-899386`) or similar non-path
        # token. `_is_git_ref` is the mechanism that still rescues a
        # genuine branch/tag citation from `premise_broken` when it DOES
        # reach the existence check (first segment happens to be a real
        # dir); this filter just keeps the common case out of the checked
        # path list entirely.
        return False
    return tok.count("/") >= 1 and not tok.endswith("/")


def extract_paths(body: str, repo: Path | None = None) -> list[str]:
    """Extract plausible repo-relative file paths cited in an item body.

    Backtick-quoted tokens (`` `scripts/gone.py` ``) and bare
    extension-bearing paths. Trailing punctuation from prose (``.``, ``,``,
    ``)``, ``:``) is stripped, then a trailing line locator (``:44``,
    ``:188,193``, ``:4362-4407``) is stripped, so a citation with a line
    reference or at the end of a sentence still matches. ``repo``, when
    given, tightens the extension-less branch-name case (see
    ``_looks_like_path_token``); omit it to keep the old repo-agnostic
    behavior.
    """
    found: set[str] = set()
    for m in _BACKTICK_RE.finditer(body):
        tok = _strip_line_suffix(m.group(1).rstrip(".,;:)"))
        if _looks_like_path_token(tok, repo):
            found.add(tok)
    for m in _BARE_PATH_RE.finditer(body):
        tok = _strip_line_suffix(m.group(1).rstrip(".,;:)"))
        if _looks_like_path_token(tok, repo):
            found.add(tok)
    return sorted(found)


_REF_SHAPE_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")


def extract_ref_candidates(body: str, repo: Path) -> list[str]:
    """Backtick tokens that look like a branch/tag name, not a path.

    ``extract_paths`` drops extension-less tokens whose first segment is not a
    repo directory so a live branch (`bl/run-899386`) never convicts an item.
    Dropping them silently also hid a DELETED branch citation (2026-09-14,
    `bl/open-items-closeout-20260903`), so they are collected here and the
    unresolvable ones route to needs_human_recheck.
    """
    found: set[str] = set()
    for m in _BACKTICK_RE.finditer(body):
        tok = m.group(1).rstrip(".,;:)")
        if not _REF_SHAPE_RE.match(tok) or _PATH_EXT_HINT_RE.search(tok):
            continue
        segments = tok.split("/")
        if any(_segment_is_numeric(s) for s in segments):
            continue
        if (repo / segments[0]).is_dir():
            continue
        found.add(tok)
    return sorted(found)


def strip_repo_abs_prefix(body: str, repo: Path) -> str:
    """Collapse the repo's own absolute path out of ``body`` before
    extraction — an absolute in-repo citation whose path contains a SPACE
    (``/Users/.../RossLabs Ambient Agent/.build-loop/...``) otherwise splits
    at the space (the bare-path regex can't span whitespace) and a bare
    regex match starts fresh mid-string, extracting a garbage suffix-only
    token (``Agent/.build-loop/...``) instead of the real repo-relative
    path. Tries both ``repo.resolve()`` and the as-given ``repo`` (longest
    first, so a longer literal match wins over a prefix of it)."""
    prefixes: list[str] = []
    try:
        prefixes.append(str(repo.resolve()))
    except OSError:
        pass
    raw = str(repo)
    if raw not in prefixes:
        prefixes.append(raw)
    for prefix in sorted(prefixes, key=len, reverse=True):
        if prefix:
            body = body.replace(prefix + "/", "")
    return body


def extract_shas(body: str) -> list[str]:
    """Extract commit SHAs cited in a commit-ish context (7-40 hex chars)."""
    found: set[str] = set()
    for m in _SHA_CONTEXT_RE.finditer(body):
        found.add(m.group(1).lower())
    return sorted(found)


# ----------------------------------------------------------------------------
# Repo introspection (git-first, filesystem-walk fallback)
# ----------------------------------------------------------------------------

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


def _repo_tracked_files(repo: Path) -> list[str]:
    """Repo-relative POSIX paths of every file git knows about (tracked +
    untracked-but-not-ignored), or a filesystem walk when git is unavailable.

    ``--others --exclude-standard`` alongside ``--cached`` so a freshly moved
    file that hasn't been ``git add``-ed yet still counts as "exists in the
    repo" for the relocation check — the exact case a careless `git status`
    only, or a stage-first workflow, would miss.
    """
    if (repo / ".git").is_dir():
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                check=True,
            )
            return [ln for ln in r.stdout.splitlines() if ln]
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            pass
    out: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            full = Path(root) / name
            try:
                out.append(full.relative_to(repo).as_posix())
            except ValueError:
                continue
    return out


def build_basename_index(repo: Path) -> dict[str, list[str]]:
    """basename -> [repo-relative paths] for the entire repo (relocation lookup)."""
    idx: dict[str, list[str]] = {}
    for rel in _repo_tracked_files(repo):
        idx.setdefault(Path(rel).name, []).append(rel)
    return idx


def _anchor_target_exists(repo: Path, rel: str) -> bool:
    """True if the cited path exists on disk. A leading ``~/`` is resolved
    against ``$HOME`` (``os.path.expanduser`` honors the env var, so tests
    can override it) rather than treated as a literal repo-relative
    subdirectory named ``~`` — ``~/.codex/hooks.json`` is a real global-config
    path, not a broken repo path, and must not be convicted as one."""
    if rel == "~" or rel.startswith("~/"):
        return Path(os.path.expanduser(rel)).exists()
    return (repo / rel).exists()


def _relocation_candidates(
    repo: Path, rel_path: str, basename_index: dict[str, list[str]] | None
) -> list[str]:
    basename = Path(rel_path).name
    if basename_index is None:
        basename_index = build_basename_index(repo)
    return [p for p in basename_index.get(basename, []) if p != rel_path]


def _reachable_sha(repo: Path, sha: str) -> bool | None:
    """True/False if determinable; None if not a git repo or git is absent
    (SKIP, never fail the item just because we couldn't check)."""
    if not (repo / ".git").is_dir():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    return r.returncode == 0


# ----------------------------------------------------------------------------
# Core classification
# ----------------------------------------------------------------------------


def classify_item(
    fm: dict[str, Any],
    body: str,
    repo: Path,
    window_days: int,
    today: str,
    basename_index: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Classify one item into one of VERDICTS. Pure function — no filesystem
    writes, no git repo mutation. ``repo`` is read-only'd for path existence
    and the two optional git lookups.
    """
    repo = Path(repo)
    status = str(fm.get("status") or "").strip().lower()
    excluded = status in _EXCLUDED_STATUSES

    if excluded:
        return {
            "verdict": "fresh",
            "reason_code": "excluded_done",
            "excluded": True,
            "freshness_source": None,
            "freshness_date": None,
            "freshness_error": None,
            "age_days": None,
            "window_days": window_days,
            "anchors": {
                "paths_checked": [],
                "shas_checked": [],
                "broken_paths": [],
                "relocated_paths": [],
                "broken_shas": [],
            },
        }

    freshness_source, freshness_value = _freshness_date(fm)
    age_days: int | None = None
    freshness_error: str | None = None
    # Fail-safe default: an item with NO freshness signal at all (neither
    # `validated` nor `created`) is the oldest, least-tracked card in the
    # queue — treat it as stale, not fresh, until a real date is on record.
    # Matches the fail-safe posture the sibling gates take
    # (hostile_input_gate.py, merge_risk.py "never guesses") instead of
    # letting an undated item fall through to `fresh` by default.
    stale = True
    if freshness_value:
        try:
            age_days = (date.fromisoformat(today) - date.fromisoformat(freshness_value)).days
            # Inclusive at the boundary: an item exactly `window_days` old is
            # still fresh (the window is a closed interval); only strictly
            # PAST the window is stale.
            stale = age_days > window_days
        except ValueError:
            # Malformed date (`2026/08/01`, `yesterday`, ...). Previously
            # swallowed silently (`except ValueError: pass`) and fell through
            # to the `stale = False` default, i.e. an unparseable date read
            # as FRESH — a fail-open bug (C-AGENT/no_silent_failure). Now
            # fails CLOSED (stale) and surfaces the reason via
            # `freshness_error` instead of disappearing.
            freshness_error = (
                f"freshness date {freshness_value!r} (source: {freshness_source}) "
                "is not a valid ISO-8601 date (YYYY-MM-DD) — treated as stale, "
                "not silently defaulted to fresh"
            )
            stale = True

    paths = extract_paths(strip_repo_abs_prefix(body, repo), repo)
    if paths and basename_index is None:
        basename_index = build_basename_index(repo)
    tracked_files: list[str] = []
    if basename_index:
        for lst in basename_index.values():
            tracked_files.extend(lst)

    broken_paths: list[dict[str, Any]] = []
    relocated_paths: list[dict[str, Any]] = []
    for rel in paths:
        if any(_anchor_target_exists(repo, alt) for alt in _expand_braces(rel)):
            continue
        # A crate-relative citation (`ambient-store/src/lib.rs` for the real
        # `engine-rs/crates/ambient-store/src/lib.rs`) is resolved outright
        # when exactly one tracked file ends with it; 2+ candidates need a
        # human to pick (needs_human_recheck), same shape as the basename
        # relocation case below.
        suffix_hits = sorted(f for f in tracked_files if f.endswith("/" + rel))
        if len(suffix_hits) == 1:
            continue
        if len(suffix_hits) > 1:
            relocated_paths.append({"path": rel, "candidates": suffix_hits})
            continue
        if _is_git_ref(repo, rel):
            continue
        candidates = _relocation_candidates(repo, rel, basename_index)
        if candidates:
            relocated_paths.append({"path": rel, "candidates": candidates})
        else:
            broken_paths.append({"path": rel})

    unresolved_refs = [
        ref for ref in extract_ref_candidates(strip_repo_abs_prefix(body, repo), repo)
        if not _is_git_ref(repo, ref)
    ]

    shas = extract_shas(body)
    broken_shas: list[str] = []
    for sha in shas:
        reachable = _reachable_sha(repo, sha)
        if reachable is False:
            broken_shas.append(sha)
        # reachable is True -> fine; None -> can't determine, skip (never
        # penalize an item just because we lack a git repo to check against).

    if broken_paths or broken_shas:
        verdict = "premise_broken"
    elif relocated_paths or unresolved_refs:
        verdict = "needs_human_recheck"
    elif stale:
        verdict = "stale_needs_revalidation"
    else:
        verdict = "fresh"

    reason_code = verdict
    if verdict == "stale_needs_revalidation" and (freshness_value is None or freshness_error):
        # Distinguish "genuinely past the window" from "we could never
        # establish a freshness date at all" — both fail closed to the same
        # verdict, but the reason an operator sees should say which.
        reason_code = "no_parseable_freshness_date"

    return {
        "verdict": verdict,
        "reason_code": reason_code,
        "excluded": False,
        "freshness_source": freshness_source,
        "freshness_date": freshness_value,
        "freshness_error": freshness_error,
        "age_days": age_days,
        "window_days": window_days,
        "anchors": {
            "paths_checked": paths,
            "shas_checked": shas,
            "broken_paths": broken_paths,
            "relocated_paths": relocated_paths,
            "unresolved_refs": unresolved_refs,
            "broken_shas": broken_shas,
        },
    }


# ----------------------------------------------------------------------------
# Queue discovery
# ----------------------------------------------------------------------------


def _normalize_queues(queues: list[str] | str | None) -> list[str]:
    if queues is None or queues == "all" or queues == ["all"]:
        return list(ALL_QUEUES)
    if isinstance(queues, str):
        queues = [queues]
    return [q for q in queues if q in QUEUE_DIRS]


def iter_queue_files(repo: Path, queues: list[str] | str | None = None) -> list[tuple[str, Path]]:
    """[(queue_name, path), ...] for every real item file across the requested
    queues, de-duplicated (a path is never double-listed even if two queue
    subpaths happened to resolve to the same directory)."""
    repo = Path(repo)
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for q in _normalize_queues(queues):
        for parts in QUEUE_DIRS[q]:
            d = repo / ".build-loop"
            for part in parts:
                d = d / part
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                if f.name in _SKIP_NAMES:
                    continue
                key = f.resolve()
                if key in seen:
                    continue
                seen.add(key)
                out.append((q, f))
    return out


# ----------------------------------------------------------------------------
# `sweep`
# ----------------------------------------------------------------------------


def sweep(
    repo: str | Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    queues: list[str] | str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Verdict per item across the requested queues + counts. Never raises on
    a well-formed-enough tree; a per-file read error is skipped, not fatal."""
    repo = Path(repo)
    today = _today_iso(today)
    basename_index = build_basename_index(repo)
    counts = {v: 0 for v in VERDICTS}
    items: list[dict[str, Any]] = []
    for queue_name, path in iter_queue_files(repo, queues):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = backlog.parse_frontmatter(text)
        result = classify_item(fm, body, repo, window_days, today, basename_index)
        try:
            rel = str(path.relative_to(repo))
        except ValueError:
            rel = str(path)
        entry = {
            "path": rel,
            "queue": queue_name,
            "id": fm.get("id") or path.stem,
            "title": fm.get("title"),
            **result,
        }
        items.append(entry)
        if not result["excluded"]:
            counts[result["verdict"]] += 1
    return {
        "command": "sweep",
        "repo": str(repo),
        "today": today,
        "window_days": window_days,
        "queues": _normalize_queues(queues),
        "counts": counts,
        "items": items,
    }


def _render_sweep_text(result: dict[str, Any]) -> str:
    lines = [
        f"Premise sweep — {result['repo']} (window {result['window_days']}d, today {result['today']})",
        "",
    ]
    for v in VERDICTS:
        lines.append(f"  {v}: {result['counts'][v]}")
    lines.append("")
    for it in result["items"]:
        if it["excluded"]:
            continue
        lines.append(f"  [{it['verdict']}] {it['queue']}/{it['path']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# `gate` — the drain-time refusal check
# ----------------------------------------------------------------------------


def _infer_repo(item_path: Path) -> Path:
    """Walk up from the item looking for a containing .build-loop/; falls
    back to cwd when the item lives outside any recognizable repo."""
    item_path = item_path.resolve()
    for parent in item_path.parents:
        if (parent / ".build-loop").is_dir():
            return parent
    return Path.cwd()


def gate(
    item_path: str | Path,
    repo: str | Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: str | None = None,
) -> dict[str, Any]:
    """The drain-time gate for ONE item. Exit 1 (refuse) on anything but
    ``fresh``. Always returns — never raises on a well-formed file."""
    item_path = Path(item_path)
    repo_path = Path(repo) if repo else _infer_repo(item_path)
    today = _today_iso(today)
    text = item_path.read_text(encoding="utf-8")
    fm, body = backlog.parse_frontmatter(text)
    result = classify_item(fm, body, repo_path, window_days, today)
    verdict = result["verdict"]
    exit_code = 0 if verdict == "fresh" else 1
    out = {
        "command": "gate",
        "item": str(item_path),
        "repo": str(repo_path),
        "exit_code": exit_code,
    }
    out.update(result)
    return out


# ----------------------------------------------------------------------------
# `validate` — stamp freshness AND write an evidence receipt
# ----------------------------------------------------------------------------

_RECEIPT_HEADING = "## Premise validated"


def validate(item_path: str | Path, note: str | None, today: str | None = None) -> dict[str, Any]:
    """Stamp ``validated: <date>`` AND append a receipt section carrying the
    date + note. ``note`` is REQUIRED and non-empty — a bare timestamp would
    assert freshness without evidence, which is the exact failure mode this
    module exists to close. Returns ``{"ok": False, ...}`` without touching
    the file when note is missing/empty."""
    note = (note or "").strip()
    if not note:
        return {
            "ok": False,
            "error": "validate requires a non-empty --note (evidence) — a bare timestamp asserts freshness without evidence",
            "item": str(item_path),
        }
    item_path = Path(item_path)
    today = _today_iso(today)
    text = item_path.read_text(encoding="utf-8")
    fm, body = backlog.parse_frontmatter(text)
    fm["validated"] = today

    body_stripped = body.rstrip("\n")
    receipt = f"{_RECEIPT_HEADING}\n\n{today} — {note}\n"
    new_body = (body_stripped + "\n\n" + receipt) if body_stripped else receipt

    doc = backlog.render_frontmatter(fm) + "\n\n" + new_body
    if not doc.endswith("\n"):
        doc += "\n"

    tmp = item_path.parent / f".{item_path.name}.{os.getpid()}.tmp"
    try:
        tmp.write_text(doc, encoding="utf-8")
        os.replace(tmp, item_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    return {"ok": True, "item": str(item_path), "validated": today, "note": note}


# ----------------------------------------------------------------------------
# `stale` — open items past the window (time-only; not the full anchor recheck)
# ----------------------------------------------------------------------------


def stale(
    repo: str | Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    queues: list[str] | str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """List OPEN items past the freshness window. done/closed items never
    appear here (excluded upstream in classify_item)."""
    result = sweep(repo, window_days=window_days, queues=queues, today=today)
    items = [
        it for it in result["items"]
        if it["verdict"] == "stale_needs_revalidation" and not it["excluded"]
    ]
    return {
        "command": "stale",
        "repo": result["repo"],
        "today": result["today"],
        "window_days": window_days,
        "items": items,
        "count": len(items),
    }


# ----------------------------------------------------------------------------
# `citations` — re-check path:line evidence refs from lane-result JSON
# ----------------------------------------------------------------------------

# `<path>:<line>` or `<path>:<start>-<end>`. Deliberately NOT the `:N,M` form
# `extract_paths` strips — Part 2's contract (per the brief) only names the
# single-line and dash-range shapes.
_CITATION_REF_RE = re.compile(r"^(?P<path>.+):(?P<start>\d+)(?:-(?P<end>\d+))?$")


def _parse_citation_ref(ref: Any) -> tuple[str, int, int] | None:
    """None if `ref` isn't a `<path>:<line>`/`<path>:<start>-<end>` citation
    (e.g. a plain shell command) — the caller reports `not_a_citation` and
    skips it rather than treating it as a failure."""
    if not isinstance(ref, str):
        return None
    m = _CITATION_REF_RE.match(ref.strip())
    if not m or not m.group("path").strip():
        return None
    start = int(m.group("start"))
    end = int(m.group("end")) if m.group("end") else start
    if start < 1 or end < start:
        return None
    return m.group("path").strip(), start, end


def _resolve_citation_path(repo: Path, path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else repo / path


def check_citation(repo: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    """Re-check one evidence item's `ref`. Returns a result dict with an
    `ok` bool the caller aggregates into the command's exit code."""
    kind = evidence.get("kind")
    ref = evidence.get("ref")
    expect = evidence.get("expect")
    parsed = _parse_citation_ref(ref)

    if parsed is None:
        return {
            "kind": kind, "ref": ref, "status": "not_a_citation",
            "executed_tag_on_citation": False, "nearest_line": None,
            "path": None, "ok": True,
        }

    path, start, end = parsed
    full = _resolve_citation_path(repo, path)
    # `executed` evidence is supposed to be a COMMAND, not a file:line
    # citation — flagged independently of whether the citation itself is
    # otherwise fine, per the brief ("also flag").
    executed_flag = kind == "executed"

    if not full.is_file():
        return {
            "kind": kind, "ref": ref, "status": "missing_file",
            "executed_tag_on_citation": executed_flag, "nearest_line": None,
            "path": str(full), "ok": False,
        }

    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {
            "kind": kind, "ref": ref, "status": "missing_file",
            "executed_tag_on_citation": executed_flag, "nearest_line": None,
            "path": str(full), "ok": False,
        }

    total = len(lines)
    if start > total or end > total:
        return {
            "kind": kind, "ref": ref, "status": "line_out_of_range",
            "executed_tag_on_citation": executed_flag, "nearest_line": None,
            "path": str(full), "ok": False,
        }

    status = "ok"
    nearest_line: int | None = None
    if expect:
        window_start = max(1, start - 3)
        window_end = min(total, end + 3)
        in_window = any(expect in lines[i - 1] for i in range(window_start, window_end + 1))
        if not in_window:
            status = "expect_not_found"
            best_line: int | None = None
            best_dist: int | None = None
            for i, line_text in enumerate(lines, start=1):
                if expect in line_text:
                    dist = abs(i - start)
                    if best_dist is None or dist < best_dist:
                        best_line, best_dist = i, dist
            nearest_line = best_line

    if status == "ok" and executed_flag:
        status = "executed_tag_on_citation"
    return {
        "kind": kind, "ref": ref, "status": status,
        "executed_tag_on_citation": executed_flag, "nearest_line": nearest_line,
        "path": str(full), "ok": status == "ok" and not executed_flag,
    }


def citations(repo: str | Path, input_path: str | Path) -> dict[str, Any]:
    """Re-check every path:line evidence ref across all lanes in the JSON
    array at ``input_path``. Never raises on well-formed input; a
    non-dict/non-list lane or evidence entry is skipped, not fatal."""
    repo = Path(repo)
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("citations --input must be a JSON array of lane results")

    items: list[dict[str, Any]] = []
    for lane_idx, lane in enumerate(data):
        if not isinstance(lane, dict):
            continue
        lane_name = lane.get("lane", lane_idx)
        evidence_list = lane.get("evidence")
        if not isinstance(evidence_list, list):
            continue
        for ev_idx, ev in enumerate(evidence_list):
            if not isinstance(ev, dict):
                continue
            result = check_citation(repo, ev)
            result["lane"] = lane_name
            result["evidence_index"] = ev_idx
            items.append(result)

    failed = sum(1 for it in items if not it["ok"])
    return {
        "command": "citations",
        "repo": str(repo),
        "input": str(input_path),
        "count": len(items),
        "failed": failed,
        "ok": failed == 0,
        "items": items,
    }


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="premise_revalidation.py")
    sub = p.add_subparsers(dest="command", required=True)

    sp_sweep = sub.add_parser("sweep", help="Verdict per item + counts")
    sp_sweep.add_argument("--repo", default=".")
    sp_sweep.add_argument("--window-days", type=int, default=None)
    sp_sweep.add_argument("--queue", default="all", choices=[*ALL_QUEUES, "all"])
    sp_sweep.add_argument("--json", action="store_true")

    sp_gate = sub.add_parser("gate", help="Drain-time gate for one item")
    sp_gate.add_argument("--item", required=True)
    sp_gate.add_argument("--repo", default=None)
    sp_gate.add_argument("--window-days", type=int, default=None)
    # `gate`/`validate`/`stale` always emit JSON. `--json` is accepted as a no-op
    # so every documented invocation in phase-5-iterate.md and
    # agents/build-orchestrator.md parses, and so the flag is uniform across the
    # sibling scripts an orchestrator calls in the same breath. A documented
    # command that argparse rejects is a shipped defect, not a cosmetic one.
    sp_gate.add_argument("--json", action="store_true", help="no-op; output is always JSON")

    sp_validate = sub.add_parser("validate", help="Stamp validated: + write receipt")
    sp_validate.add_argument("--item", required=True)
    sp_validate.add_argument("--note", default=None)
    sp_validate.add_argument("--repo", default=None, help="accepted for call-site symmetry; unused")
    sp_validate.add_argument("--json", action="store_true", help="no-op; output is always JSON")

    sp_stale = sub.add_parser("stale", help="List open items past the window")
    sp_stale.add_argument("--repo", default=".")
    sp_stale.add_argument("--window-days", type=int, default=None)
    sp_stale.add_argument("--queue", default="all", choices=[*ALL_QUEUES, "all"])
    sp_stale.add_argument("--json", action="store_true", help="no-op; output is always JSON")

    sp_citations = sub.add_parser("citations", help="Re-check path:line evidence refs from lane-result JSON")
    sp_citations.add_argument("--repo", required=True)
    sp_citations.add_argument("--input", required=True)
    sp_citations.add_argument("--json", action="store_true", help="no-op; output is always JSON")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "sweep":
        window_days = resolve_window_days(args.window_days)
        result = sweep(args.repo, window_days=window_days, queues=args.queue)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(_render_sweep_text(result))
        return 0

    if args.command == "gate":
        window_days = resolve_window_days(args.window_days)
        result = gate(args.item, repo=args.repo, window_days=window_days)
        print(json.dumps(result, indent=2))
        return result["exit_code"]

    if args.command == "validate":
        result = validate(args.item, args.note)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.command == "stale":
        window_days = resolve_window_days(args.window_days)
        result = stale(args.repo, window_days=window_days, queues=args.queue)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "citations":
        result = citations(args.repo, args.input)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    return 1  # unreachable — argparse enforces `command` is one of the above


if __name__ == "__main__":
    sys.exit(main())
