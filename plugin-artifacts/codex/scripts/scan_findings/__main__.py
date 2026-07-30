#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI + Stop-hook entry for the deterministic findings sweep.

Reads the session transcript, detects clearly-identified findings surfaced by
ANY agent/audit/critic, and persists them:

  * recognized severity   -> a backlog item via ``scripts/backlog.py new``
                             (the host-agnostic CLI that already mirrors to
                             build-loop-memory on ``sync``)
  * no recognized severity -> a review candidate in ``.build-loop/proposals/``
                             for human confirmation (high precision over recall)

Idempotent: a finding already captured (this sweep, an existing open backlog
item, or an existing review proposal) is never re-created — dedup is keyed on a
stable ``finding-hash`` carried in each item's ``provenance.ref`` and on the
normalized title.

Hook contract — never fails the session:
  - any error logs and exits 0 (unless --strict, for CI)
  - ``.build-loop/.no-capture`` (per-session opt-out) -> clean exit 0
  - single-flight ``fcntl.flock`` (default /tmp/build-loop-findings-scan.lock);
    a held lock -> clean exit 0
  - wall-clock budget (``SCAN_FINDINGS_BUDGET_S``, default 15s) governs the write
    loop; partial completion is safe (each write is atomic / its own process)

Usage:
  python3 -m scan_findings --workdir <repo> --transcript $CLAUDE_TRANSCRIPT_PATH
  python3 -m scan_findings --workdir <repo> --text-blocks-file <path>   # testing
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scan_findings.detect import (  # noqa: E402
    FindingCandidate,
    _normalize_core,
    detect_findings,
)

DEFAULT_BUDGET_S = 15
BACKLOG_SCRIPT = _SCRIPTS_DIR / "backlog.py"
PROPOSALS_DIRNAME = "proposals"
PROVENANCE_REF_PREFIX = "finding-hash:"
PROVENANCE_SOURCE_BASE = "auto-finding-sweep"


# ---------------------------------------------------------------------------
# logging (stderr + optional log file) — the hook redirects stderr to /dev/null
# ---------------------------------------------------------------------------

_LOG_FILE: Path | None = None


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} [scan_findings] {msg}\n"
    sys.stderr.write(line)
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# single-flight lock (mirrors scan_transcript_for_decisions.acquire_lock)
# ---------------------------------------------------------------------------

def acquire_lock(lock_path: Path) -> int | None:
    """Non-blocking exclusive flock. Returns fd on success (kept open for the
    process lifetime), None if another sweep holds it, -1 if locking is
    unavailable (proceed without)."""
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as e:
        log(f"lock unavailable ({e}); proceeding without lock")
        return -1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError as e:
        log(f"lock acquire failed ({e}); proceeding without lock")
        os.close(fd)
        return -1
    return fd


# ---------------------------------------------------------------------------
# dedup — existing finding-hashes + normalized titles from backlog + proposals
# ---------------------------------------------------------------------------

def _load_backlog_module():
    """Load ``scripts/backlog.py`` explicitly by file path.

    A bare ``import backlog`` resolves to the unrelated ``scripts/backlog/``
    PACKAGE (assess/triage), which has no ``load_items``. We need the backlog
    SYSTEM in ``backlog.py``, so load it from its concrete path. Cached on the
    function object so repeated calls don't re-exec the module.
    """
    cached = getattr(_load_backlog_module, "_mod", None)
    if cached is not None:
        return cached
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location("_bl_backlog_system", BACKLOG_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load backlog system from {BACKLOG_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _load_backlog_module._mod = mod  # type: ignore[attr-defined]
    return mod


def _existing_keys(workdir: Path) -> tuple[set[str], set[str]]:
    """Return (existing_finding_hashes, existing_normalized_titles).

    Sources: every backlog item (active + archived) and every review proposal.
    Best-effort — any read error is skipped, never fatal.
    """
    hashes: set[str] = set()
    titles: set[str] = set()

    # Backlog items (backlog.py's tolerant reader — read-only use).
    try:
        backlog = _load_backlog_module()
        for it in backlog.load_items(workdir, include_archive=True):
            prov = it.get("provenance")
            if isinstance(prov, dict):
                ref = prov.get("ref")
                if isinstance(ref, str) and ref.startswith(PROVENANCE_REF_PREFIX):
                    hashes.add(ref[len(PROVENANCE_REF_PREFIX):].strip())
            fh = it.get("finding_hash")
            if isinstance(fh, str) and fh.strip():
                hashes.add(fh.strip())
            t = it.get("title")
            if isinstance(t, str) and t.strip() and str(it.get("status")) not in ("done", "dropped"):
                titles.add(_normalize_core(t))
    except Exception as e:  # noqa: BLE001
        log(f"backlog read for dedup skipped ({e})")

    # Review proposals already written by a prior sweep.
    proposals_dir = workdir / ".build-loop" / PROPOSALS_DIRNAME
    if proposals_dir.is_dir():
        for p in proposals_dir.glob("auto-finding-*.md"):
            try:
                fm = _read_frontmatter(p.read_text(encoding="utf-8"))
            except OSError:
                continue
            fh = fm.get("finding_hash")
            if isinstance(fh, str) and fh.strip():
                hashes.add(fh.strip())
            t = fm.get("title")
            if isinstance(t, str) and t.strip():
                titles.add(_normalize_core(t))
    return hashes, titles


def _read_frontmatter(text: str) -> dict[str, Any]:
    """Tiny YAML-frontmatter scalar reader (proposals carry flat scalars only)."""
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}
    out: dict[str, Any] = {}
    for ln in lines[1:end]:
        if ":" not in ln or ln.lstrip().startswith("#"):
            continue
        k, v = ln.split(":", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------

def _provenance_source(agent: str) -> str:
    if agent and agent != "session":
        return f"{PROVENANCE_SOURCE_BASE}:{agent}"
    return PROVENANCE_SOURCE_BASE


def write_backlog_item(workdir: Path, cand: FindingCandidate, today: str | None,
                       timeout_s: float = 60.0) -> tuple[bool, str]:
    """Create a backlog item through ``backlog.py new`` (the single writer).

    ``timeout_s`` is capped by the caller to the remaining sweep budget so one
    hung write cannot overrun the budget (the whole sweep is backgrounded by the
    hook, so it can never block the session regardless)."""
    context = (
        f"Auto-captured by the findings sweep from {cand.agent} "
        f"({cand.source_kind}); severity {cand.severity}. "
        "An agent/audit/critic stated this as a clearly-identified issue in the "
        "session, so it is persisted automatically — no manual selection."
    )
    args = [
        sys.executable, str(BACKLOG_SCRIPT), "new",
        "--repo", str(workdir),
        "--area", "audit",
        "--type", "fix",
        "--title", cand.title[:160] or "(untitled finding)",
        "--priority", cand.priority,
        "--provenance-source", _provenance_source(cand.agent),
        "--provenance-ref", f"{PROVENANCE_REF_PREFIX}{cand.finding_hash}",
        "--context", context,
        "--notes", (cand.evidence or cand.title)[:1000],
    ]
    if today:
        args += ["--today", today]
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=max(1.0, timeout_s))
    except (OSError, subprocess.SubprocessError) as e:
        log(f"backlog.py new failed to launch ({e})")
        return False, str(e)
    if cp.returncode != 0:
        log(f"backlog.py new rejected {cand.title[:60]!r}: {cp.stderr.strip()[:300]}")
        return False, cp.stderr.strip()
    try:
        payload = json.loads(cp.stdout)
        return True, str(payload.get("id", ""))
    except json.JSONDecodeError:
        return True, ""


def _emit_proposal(cand: FindingCandidate) -> str:
    captured_at = datetime.now(timezone.utc).isoformat()
    lines = [
        "---",
        f"id: {cand.finding_hash}",
        "kind: finding",
        "route: review",
        "severity: unknown",
        f"finding_hash: {cand.finding_hash}",
        f"source: {_provenance_source(cand.agent)}",
        f"agent: {cand.agent}",
        f"source_kind: {cand.source_kind}",
        "tier: 1-deterministic",
        f"title: {json.dumps(cand.title)}",
        f"captured_at: {captured_at}",
        "---",
        "",
        "## Finding (no severity asserted — needs human triage)",
        "",
        "> " + (cand.evidence or cand.title).replace("\n", "\n> "),
        "",
        "## Why this is in the review queue",
        "",
        "An agent surfaced a finding-shaped statement but did NOT tag it with a "
        "severity, so the auto-sweep routed it here instead of straight to the "
        "backlog (high precision over recall).",
        "",
        "## Next action",
        "",
        "Confirm + promote to the backlog with a severity:",
        "",
        "```bash",
        "python3 scripts/backlog.py new --repo . --area audit --type fix \\",
        f"  --title {json.dumps(cand.title)} --priority P2 \\",
        f"  --provenance-source {_provenance_source(cand.agent)} "
        f"--provenance-ref {PROVENANCE_REF_PREFIX}{cand.finding_hash}",
        "```",
        "",
        "...or delete this file if it is not a real finding.",
    ]
    return "\n".join(lines) + "\n"


def write_review_proposal(workdir: Path, cand: FindingCandidate) -> tuple[bool, str]:
    proposals_dir = workdir / ".build-loop" / PROPOSALS_DIRNAME
    proposals_dir.mkdir(parents=True, exist_ok=True)
    # Dedup on disk too (defensive — _existing_keys already filtered).
    for existing in proposals_dir.glob(f"auto-finding-*-{cand.finding_hash}.md"):
        return False, str(existing)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = proposals_dir / f"auto-finding-{ts}-{cand.finding_hash}.md"
    tmp = target.with_suffix(".md.tmp")
    try:
        tmp.write_text(_emit_proposal(cand), encoding="utf-8")
        os.replace(tmp, target)
    except OSError as e:
        log(f"proposal write failed ({e})")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False, str(e)
    return True, str(target)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _load_text_blocks(path: Path) -> list[Any]:
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("["):
        data = json.loads(raw)
        # JSON arrays of [text, kind, agent] arrive as lists -> coerce to tuples.
        return [tuple(x) if isinstance(x, list) else x for x in data]
    return [ln for ln in raw.splitlines() if ln.strip()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="scan_findings", description=__doc__)
    ap.add_argument("--workdir", default=os.environ.get("CLAUDE_PROJECT_DIR", "."))
    ap.add_argument("--transcript", default=os.environ.get("CLAUDE_TRANSCRIPT_PATH", ""))
    ap.add_argument("--text-blocks-file", default=None,
                    help="Testing surface: a JSON array of strings or [text,kind,agent], "
                         "or one block per line.")
    ap.add_argument("--budget-s", type=int, default=None)
    ap.add_argument("--lock-file", default=None,
                    help="Single-flight lock path. Default: a per-workdir lock in the "
                         "temp dir, so concurrent sweeps in different repos don't starve "
                         "each other.")
    ap.add_argument("--log-file", default=None)
    ap.add_argument("--today", default=None, help="Forwarded to backlog.py (deterministic tests).")
    ap.add_argument("--print-json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero on error (CI only).")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _LOG_FILE
    start = time.monotonic()
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    _LOG_FILE = Path(args.log_file).expanduser() if args.log_file else None
    workdir = Path(args.workdir).resolve()

    budget_s = args.budget_s
    if budget_s is None:
        try:
            budget_s = int(os.environ.get("SCAN_FINDINGS_BUDGET_S", str(DEFAULT_BUDGET_S)))
        except ValueError:
            budget_s = DEFAULT_BUDGET_S

    # Per-session opt-out.
    if (workdir / ".build-loop" / ".no-capture").exists():
        log("skipping per .no-capture flag")
        return 0

    # Single-flight lock.
    if args.lock_file:
        lock_path = Path(args.lock_file).expanduser()
    else:
        wd_hash = hashlib.sha1(str(workdir).encode("utf-8")).hexdigest()[:12]
        lock_path = Path(tempfile.gettempdir()) / f"build-loop-findings-scan-{wd_hash}.lock"
    lock_fd = acquire_lock(lock_path)
    if lock_fd is None:
        log(f"another sweep holds {lock_path}; skipping")
        return 0
    try:
        return _run_sweep(args, workdir, budget_s, start)
    finally:
        # Release the flock when the sweep finishes (covers the critical
        # section). Closing the fd releases the lock; in the real hook each
        # invocation is its own process so this is a no-op, but it keeps main()
        # re-entrant (e.g. two sequential calls in one test process).
        if isinstance(lock_fd, int) and lock_fd >= 0:
            try:
                os.close(lock_fd)
            except OSError:
                pass


def _run_sweep(args: argparse.Namespace, workdir: Path, budget_s: int, start: float) -> int:
    # Detect.
    try:
        if args.text_blocks_file:
            blocks = _load_text_blocks(Path(args.text_blocks_file))
            candidates = detect_findings(text_blocks=blocks)
        elif args.transcript:
            tp = Path(args.transcript)
            if not tp.exists():
                log(f"transcript not found at {tp}; nothing to do")
                return 0
            candidates = detect_findings(transcript_path=tp)
        else:
            log("no --transcript and no --text-blocks-file; nothing to do")
            return 0
    except Exception as e:  # noqa: BLE001
        log(f"detection error (swallowed): {e}")
        return 1 if args.strict else 0

    if not candidates:
        log("no findings detected")
        if args.print_json:
            print(json.dumps({"candidates": [], "backlog": [], "review": [], "skipped_dup": 0}, indent=2))
        return 0

    existing_hashes, existing_titles = _existing_keys(workdir)
    seen_hashes = set(existing_hashes)
    seen_titles = set(existing_titles)

    backlog_written: list[str] = []
    review_written: list[str] = []
    skipped_dup = 0

    for cand in candidates:
        remaining = budget_s - (time.monotonic() - start)
        if remaining <= 0:
            log(f"budget exceeded ({budget_s}s); bailing partial "
                f"(backlog={len(backlog_written)} review={len(review_written)})")
            break
        norm_title = _normalize_core(cand.title)
        if cand.finding_hash in seen_hashes or (norm_title and norm_title in seen_titles):
            skipped_dup += 1
            continue
        if cand.route == "backlog":
            # Cap each write to the remaining budget so one hung write can't
            # overrun it (whole sweep is backgrounded anyway).
            ok, item_id = write_backlog_item(workdir, cand, args.today, timeout_s=min(60.0, remaining))
            if ok:
                backlog_written.append(item_id or cand.finding_hash)
                seen_hashes.add(cand.finding_hash)
                if norm_title:
                    seen_titles.add(norm_title)
            else:
                # Trusted write failed (e.g. taxonomy) — fall back to review so
                # the signal is never dropped.
                ok2, _ = write_review_proposal(workdir, cand)
                if ok2:
                    review_written.append(cand.finding_hash)
                    seen_hashes.add(cand.finding_hash)
        else:
            ok, _ = write_review_proposal(workdir, cand)
            if ok:
                review_written.append(cand.finding_hash)
                seen_hashes.add(cand.finding_hash)
                if norm_title:
                    seen_titles.add(norm_title)

    log(f"done — detected={len(candidates)} backlog={len(backlog_written)} "
        f"review={len(review_written)} skipped_dup={skipped_dup}")

    if args.print_json:
        print(json.dumps({
            "candidates": [c.to_dict() for c in candidates],
            "backlog": backlog_written,
            "review": review_written,
            "skipped_dup": skipped_dup,
        }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"unexpected error (swallowed for hook safety): {e}")
        sys.exit(0)
