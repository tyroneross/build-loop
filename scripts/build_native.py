#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Build, test and install build-loop's native hook binary (``bin/native/bl``).

Freshness contract
------------------
``bl`` mirrors Python and shell in this repo. One SHA-256 digest covers every
file whose change can change what ``bl`` should do: ``crates/**``,
``Cargo.toml``, ``Cargo.lock`` and the mirrored sources in ``MIRRORED``.

- ``crates/SOURCES.digest`` (tracked) holds the digest of the checked-out tree.
  The pre-commit artifact guard regenerates it whenever a watched file is
  staged, and ``tests/test_native_sources_digest.py`` fails when it is stale.
- ``bin/native/bl.sources`` (ignored) holds the digest the installed binary was
  built from. It is written last, only after every test passed.
- ``hooks/pre-edit-rally-point.sh`` runs the binary only when the first lines
  of the two files are equal, read with shell builtins. Anything else runs the
  shell hook.

The digest hashes working-tree bytes of the files ``git ls-files`` lists under
those paths (the build compiles working-tree bytes). This repo applies no
end-of-line conversion, so a clean checkout hashes the same bytes git stores
and the digest matches across machines. Paths are repo-relative and sorted; no
mtimes, modes or absolute paths enter it.

Usage
-----
``python3 scripts/build_native.py``            build, test, install (exit 0 on install)
``python3 scripts/build_native.py --check``    is the installed binary fresh?
``python3 scripts/build_native.py --check-digest``  is crates/SOURCES.digest current?
``python3 scripts/build_native.py --write-digest``  rewrite crates/SOURCES.digest
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIGEST_FILE = "crates/SOURCES.digest"
SIDECAR_NAME = "bl.sources"

# Python and shell whose behaviour crates/bl and crates/bl-contract reproduce
# (their doc comments cite these modules), plus the modules hooks.py loads on
# the mirrored pre-edit path.
MIRRORED = (
    "hooks/_bounded_hook.sh",
    "hooks/pre-edit-rally-point.sh",
    "scripts/hook_payload.py",
    "scripts/rally_point/actor_identity.py",
    "scripts/rally_point/backend_adapter.py",
    "scripts/rally_point/channel_paths.py",
    "scripts/rally_point/discovery_bridge.py",
    "scripts/rally_point/hook_budget.py",
    "scripts/rally_point/hooks.py",
)
# Every path whose change changes the digest; also the artifact guard's watch list.
WATCHED = ("Cargo.toml", "Cargo.lock", "crates/") + MIRRORED

_HEADER = (
    "# Digest of the sources bin/native/bl is built from: crates/**, Cargo.toml,\n"
    "# Cargo.lock and the mirrored files listed in scripts/build_native.py.\n"
    "# Line 1 is read by hooks/pre-edit-rally-point.sh. Regenerate with\n"
    "#   python3 scripts/build_native.py --write-digest\n"
    "# (the pre-commit artifact guard does this when a watched file is staged).\n"
    "# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>\n"
    "# SPDX-License-Identifier: Apache-2.0\n"
)


class BuildError(Exception):
    """A step failed; the message is the one-line reason."""


def source_paths(repo: Path = REPO) -> list[str]:
    """Sorted repo-relative paths the digest covers."""
    cp = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", *WATCHED],
        capture_output=True,
    )
    if cp.returncode != 0:
        raise BuildError("git ls-files failed: " + cp.stderr.decode(errors="replace").strip())
    paths = sorted(
        {p for p in cp.stdout.decode().split("\0") if p and p != DIGEST_FILE}
    )
    missing = [m for m in MIRRORED if m not in paths]
    if missing:
        raise BuildError("mirrored file not tracked: " + ", ".join(missing))
    return paths


def compute_digest(repo: Path = REPO) -> str:
    h = hashlib.sha256(b"build-loop native sources v1\n")
    for rel in source_paths(repo):
        try:
            data = (repo / rel).read_bytes()
        except OSError:
            raise BuildError(f"cannot read {rel}") from None
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return h.hexdigest()


def read_first_line(path: Path) -> str:
    """First line of ``path`` (``""`` when absent or unreadable)."""
    try:
        return path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
    except (OSError, UnicodeDecodeError):
        return ""


def tracked_digest(repo: Path = REPO) -> str:
    return read_first_line(repo / DIGEST_FILE)


def write_digest(repo: Path = REPO) -> str:
    digest = compute_digest(repo)
    (repo / DIGEST_FILE).write_text(digest + "\n" + _HEADER, encoding="utf-8")
    return digest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check-digest", action="store_true",
                      help="exit 1 when crates/SOURCES.digest is out of date")
    mode.add_argument("--write-digest", action="store_true",
                      help="rewrite crates/SOURCES.digest from the tree")
    args = ap.parse_args(argv)
    try:
        if args.write_digest:
            print(write_digest())
            return 0
        if args.check_digest:
            want, have = compute_digest(), tracked_digest()
            if want == have:
                print(f"{DIGEST_FILE} current")
                return 0
            print(f"{DIGEST_FILE} is out of date; run: "
                  "python3 scripts/build_native.py --write-digest", file=sys.stderr)
            return 1
    except BuildError as exc:
        print(f"build_native: {exc}", file=sys.stderr)
        return 1
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
