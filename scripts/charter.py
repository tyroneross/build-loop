#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Project charter — canonical-in-memory, repo-mirror, hash-drift sync (WP-F/F3).

The charter is the persistent layer of tiered intent: stable North Star + posture
+ invariants + key architecture decisions, accreting across runs. It pairs with
the per-run ephemeral `.build-loop/intent.md` (the run intent is a snapshot; the
charter is the durable North Star).

Storage contract (user-decided 2026-06-09):
  - Canonical: build-loop-memory/projects/<slug>/charter.md  (memory reads are
    proven-reliable — context_bootstrap reads memory every run).
  - Mirror:    <repo>/.build-loop/charter.md, written each run FROM canonical,
    carrying a `canonical:` pointer line + a content hash for drift detection.
  - One writer: the run, from canonical — NOT two masters (avoids the multi-copy
    drift class). Reconciliation: canonical→mirror is normal; a user hand-edit of
    the repo mirror is detected next run via hash mismatch and PROMOTED to
    canonical (`authored_by: user`), then re-synced. The user layer is
    authoritative over inferred content.

Depth dial: charter depth scales by `stakes` (low → no charter; medium → thin;
high → full). This script does not force a charter — `sync` is a no-op when no
canonical charter exists and the caller did not pass --create.

Subcommands:
  sync     Reconcile canonical ⇄ mirror. Detects user hand-edits to the mirror and
           promotes them to canonical. Writes the mirror from canonical. Reports
           the action taken as JSON.
  status   Report charter presence + drift state without writing.
  create   Seed a canonical charter from the template if missing (used by the
           bl-constitution-create-if-missing fold-in when stakes>=medium).

Absence-tolerant and fail-soft: a missing canonical/mirror is a normal state, not
an error. Exit 0 always except on an explicit IO failure during a requested write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHARTER_TEMPLATE = HERE.parent / "templates" / "memory" / "charter.md.template"

# A stable delimiter line carrying the content hash + canonical pointer. Kept on
# its own HTML-comment line so it never renders and never collides with content.
POINTER_PREFIX = "<!-- charter-sync"


def _content_hash(text: str) -> str:
    """Hash of the charter BODY (excludes the sync pointer line itself)."""
    body = "\n".join(
        ln for ln in text.splitlines() if not ln.startswith(POINTER_PREFIX)
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _strip_pointer(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if not ln.startswith(POINTER_PREFIX)
    ).rstrip("\n")


def _pointer_line(canonical: Path, body_hash: str) -> str:
    return f"{POINTER_PREFIX} canonical={canonical} hash={body_hash} -->"


def _recorded_hash(mirror_text: str) -> str | None:
    for ln in mirror_text.splitlines():
        if ln.startswith(POINTER_PREFIX) and "hash=" in ln:
            return ln.split("hash=", 1)[1].split()[0].rstrip("-> ")
    return None


def _resolve_paths(workdir: Path, slug: str | None) -> tuple[Path, Path]:
    """Return (canonical_path, mirror_path). Imports the memory resolver lazily so
    this script stays usable in environments where context_bootstrap's heavier
    deps are absent (it only needs two pure-path helpers)."""
    sys.path.insert(0, str(HERE))
    try:
        import context_bootstrap as cb  # noqa: PLC0415
        resolved_slug = slug or cb.resolve_project(workdir)
        mem_root = cb.memory_store_root()
    finally:
        sys.path.pop(0)
    canonical = mem_root / "projects" / resolved_slug / "charter.md"
    mirror = workdir / ".build-loop" / "charter.md"
    return canonical, mirror


def status(workdir: Path, slug: str | None = None) -> dict:
    canonical, mirror = _resolve_paths(workdir, slug)
    canon_exists = canonical.is_file()
    mirror_exists = mirror.is_file()
    drift = None
    if mirror_exists:
        mtext = mirror.read_text(encoding="utf-8")
        recorded = _recorded_hash(mtext)
        actual = _content_hash(mtext)
        drift = (recorded is not None and recorded != actual)
    return {
        "canonical": str(canonical),
        "mirror": str(mirror),
        "canonical_exists": canon_exists,
        "mirror_exists": mirror_exists,
        "mirror_user_edited": bool(drift),
    }


def create(workdir: Path, slug: str | None = None) -> dict:
    canonical, _ = _resolve_paths(workdir, slug)
    if canonical.is_file():
        return {"action": "exists", "canonical": str(canonical)}
    if not CHARTER_TEMPLATE.is_file():
        return {"action": "error", "reason": f"template missing: {CHARTER_TEMPLATE}"}
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(CHARTER_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return {"action": "created", "canonical": str(canonical)}


def sync(workdir: Path, slug: str | None = None) -> dict:
    """Reconcile canonical ⇄ mirror. Returns the action taken.

    Cases:
      - No canonical, no mirror → no-op (charter not in use; depth dial = low).
      - Canonical only → write mirror from canonical (+ pointer/hash).
      - Mirror user-edited (hash mismatch) → promote mirror body to canonical
        (authored_by: user), then re-sync mirror.
      - Both, mirror unchanged → ensure mirror matches canonical (idempotent).
    """
    canonical, mirror = _resolve_paths(workdir, slug)
    canon_exists = canonical.is_file()
    mirror_exists = mirror.is_file()

    if not canon_exists and not mirror_exists:
        return {"action": "noop_no_charter", "canonical": str(canonical), "mirror": str(mirror)}

    # User hand-edit detection: mirror present, its recorded hash != actual body.
    if mirror_exists:
        mtext = mirror.read_text(encoding="utf-8")
        recorded = _recorded_hash(mtext)
        actual = _content_hash(mtext)
        if recorded is not None and recorded != actual:
            # Promote the user's mirror edit to canonical.
            body = _strip_pointer(mtext)
            promoted = body
            if "authored_by:" not in promoted:
                promoted = promoted + "\n\n<!-- authored_by: user (promoted from repo mirror) -->"
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_text(promoted + "\n", encoding="utf-8")
            _write_mirror(canonical, mirror)
            return {"action": "promoted_user_edit", "canonical": str(canonical), "mirror": str(mirror)}

    if canon_exists:
        _write_mirror(canonical, mirror)
        return {"action": "synced_from_canonical", "canonical": str(canonical), "mirror": str(mirror)}

    # Mirror exists but no canonical and no user edit: adopt the mirror as canonical.
    body = _strip_pointer(mirror.read_text(encoding="utf-8"))
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(body + "\n", encoding="utf-8")
    _write_mirror(canonical, mirror)
    return {"action": "adopted_mirror_as_canonical", "canonical": str(canonical), "mirror": str(mirror)}


def _write_mirror(canonical: Path, mirror: Path) -> None:
    body = _strip_pointer(canonical.read_text(encoding="utf-8"))
    h = _content_hash(body)
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(body + "\n" + _pointer_line(canonical, h) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("sync", "status", "create"))
    ap.add_argument("--workdir", type=Path, default=Path.cwd())
    ap.add_argument("--slug", default=None, help="override the project slug")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    workdir = args.workdir.resolve()
    try:
        if args.command == "status":
            result = status(workdir, args.slug)
        elif args.command == "create":
            result = create(workdir, args.slug)
        else:
            result = sync(workdir, args.slug)
    except OSError as exc:
        result = {"action": "error", "reason": str(exc)}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result.get("action") or json.dumps(result))
    return 0 if result.get("action") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
