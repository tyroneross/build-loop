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
- ``hooks/pre-edit-rally-point.sh`` and ``hooks/tool-trace.sh`` run the binary
  only when the first lines of the two files are equal, read with shell
  builtins. Anything else runs the shell or Python hook.

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
``--dest DIR`` installs (or checks) ``DIR/bl`` instead of ``bin/native/bl``.

The build runs ``cargo build --release --locked``, ``cargo test --locked`` and
the parity harness (``tests/hook_parity``: pre-edit and tool-trace cases)
against ``target/release/bl``, then
installs. Installing into the repo's own ``bin/native`` also reruns the
launcher parity cases through the real hook script; if they fail the sidecar
is removed, so the launcher goes back to the shell hook.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIGEST_FILE = "crates/SOURCES.digest"
SIDECAR_NAME = "bl.sources"
DEFAULT_DEST = REPO / "bin" / "native"

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
    # bl hook trace: the launcher, tool_trace.py and the modules its hook
    # path imports (lock primitive, bounded state.json reader, run identity).
    "hooks/tool-trace.sh",
    "scripts/atomic_io.py",
    "scripts/rally_point/build_loop_id.py",
    "scripts/temporal_membership.py",
    "scripts/tool_trace.py",
    "scripts/transcript_adapter.py",
)
# Every path whose change changes the digest; also the artifact guard's watch list.
WATCHED = ("Cargo.toml", "Cargo.lock", "crates/") + MIRRORED

_HEADER = (
    "# Digest of the sources bin/native/bl is built from: crates/**, Cargo.toml,\n"
    "# Cargo.lock and the mirrored files listed in scripts/build_native.py.\n"
    "# Line 1 is read by hooks/pre-edit-rally-point.sh and hooks/tool-trace.sh.\n"
    "# Regenerate with\n"
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


def _index_bytes(repo: Path, paths: list[str]) -> dict[str, bytes]:
    """Staged (index) content of ``paths``; absent entries are omitted."""
    ls = subprocess.run(["git", "-C", str(repo), "ls-files", "-s", "-z", "--", *paths],
                        capture_output=True)
    if ls.returncode != 0:
        raise BuildError("git ls-files -s failed")
    shas: list[tuple[str, str]] = []
    for entry in ls.stdout.decode().split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            shas.append((path, meta.split()[1]))
    cat = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                         input="".join(f"{sha}\n" for _, sha in shas).encode(), capture_output=True)
    if cat.returncode != 0:
        raise BuildError("git cat-file failed")
    out, buf = {}, cat.stdout
    for path, _ in shas:
        header, buf = buf.split(b"\n", 1)
        size = int(header.split()[2])
        out[path], buf = buf[:size], buf[size + 1:]
    return out


def compute_digest(repo: Path = REPO, staged: bool = False) -> str:
    """Digest of the watched sources: working-tree bytes, or index blobs when
    ``staged`` (what a commit will record)."""
    paths = source_paths(repo)
    blobs = _index_bytes(repo, paths) if staged else {}
    h = hashlib.sha256(b"build-loop native sources v1\n")
    for rel in paths:
        try:
            data = blobs[rel] if staged else (repo / rel).read_bytes()
        except (OSError, KeyError):
            raise BuildError(f"cannot read {rel}") from None
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return h.hexdigest()


def read_first_line(path: Path) -> str:
    """First line of ``path`` (``""`` when absent or unreadable)."""
    try:
        return path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
    except (OSError, UnicodeDecodeError):
        return ""


def tracked_digest(repo: Path = REPO, staged: bool = False) -> str:
    if staged:
        data = _index_bytes(repo, [DIGEST_FILE]).get(DIGEST_FILE, b"")
        return data.decode("utf-8", "replace").split("\n", 1)[0].strip()
    return read_first_line(repo / DIGEST_FILE)


def write_digest(repo: Path = REPO) -> str:
    digest = compute_digest(repo)
    (repo / DIGEST_FILE).write_text(digest + "\n" + _HEADER, encoding="utf-8")
    return digest


def run_step(label: str, argv: list[str], repo: Path, env: dict[str, str] | None = None) -> None:
    """Run one build step in ``repo``; raise BuildError naming it on failure."""
    print(f"build_native: {label}", file=sys.stderr, flush=True)
    cp = subprocess.run(argv, cwd=str(repo), env={**os.environ, **(env or {})})
    if cp.returncode != 0:
        raise BuildError(f"{label} failed (exit {cp.returncode})")


def install(built: Path, dest: Path, digest: str) -> None:
    """Replace ``dest/bl`` and then write its sidecar, each atomically.

    The old sidecar goes first, so no moment pairs a new binary with a digest
    it was not built from; an interrupted install leaves the shell hook in use.
    """
    dest.mkdir(parents=True, exist_ok=True)
    sidecar = dest / SIDECAR_NAME
    sidecar.unlink(missing_ok=True)
    tmp = dest / "bl.tmp"
    shutil.copy2(built, tmp)
    tmp.chmod(0o755)
    os.replace(tmp, dest / "bl")
    tmp_sidecar = dest / (SIDECAR_NAME + ".tmp")
    tmp_sidecar.write_text(digest + "\n", encoding="utf-8")
    os.replace(tmp_sidecar, sidecar)


def dirty_sources(repo: Path = REPO) -> list[str]:
    """Watched tracked files (other than the digest) that differ from HEAD or the index."""
    cp = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z",
                         "--untracked-files=no", "--", *WATCHED], capture_output=True)
    if cp.returncode != 0:
        raise BuildError("git status failed")
    paths = [e[3:] for e in cp.stdout.decode().split("\0") if len(e) > 3]
    return sorted(p for p in paths if p != DIGEST_FILE)


def build_and_install(repo: Path = REPO, dest: Path = DEFAULT_DEST, run=run_step,
                      allow_dirty: bool = False) -> str:
    if not allow_dirty:
        dirty = dirty_sources(repo)
        if dirty:
            raise BuildError(f"uncommitted changes in {', '.join(dirty)}; commit them "
                             "or pass --allow-dirty (a revert would leave the binary in use)")
    digest = compute_digest(repo)
    if tracked_digest(repo) != digest:
        write_digest(repo)
        print(f"build_native: updated {DIGEST_FILE}; commit it with the source change",
              file=sys.stderr)
    # An explicit target dir: CARGO_TARGET_DIR or a cargo config could
    # otherwise build elsewhere and leave an older binary at this path.
    target = repo / "target"
    built = target / "release" / "bl"
    built.unlink(missing_ok=True)
    run("cargo build", ["cargo", "build", "--release", "--locked", "--target-dir", str(target)], repo)
    run("cargo test", ["cargo", "test", "--locked", "--target-dir", str(target)], repo)
    if not built.is_file():
        raise BuildError(f"cargo build left no binary at {built}")
    run("parity harness", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "tests/hook_parity"], repo, {"BL_BIN": str(built)})
    if compute_digest(repo) != digest:
        raise BuildError("sources changed during the build; run it again")
    install(built, dest, digest)
    if dest.resolve() == (repo / "bin" / "native").resolve():
        try:
            run("launcher parity", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                    "tests/hook_parity", "-k", "launcher"], repo)
        except BuildError:
            (dest / SIDECAR_NAME).unlink(missing_ok=True)
            raise
    return digest


def installed_status(repo: Path = REPO, dest: Path = DEFAULT_DEST) -> tuple[bool, str]:
    """(fresh, one-line reason) for the binary installed in ``dest``."""
    if not os.access(dest / "bl", os.X_OK):
        return False, f"no executable {dest / 'bl'}"
    built = read_first_line(dest / SIDECAR_NAME)
    if not built:
        return False, f"{dest / SIDECAR_NAME} missing or empty; the shell hook runs"
    tracked = tracked_digest(repo)
    if built != tracked:
        return False, f"built from other sources than {DIGEST_FILE}; the shell hook runs"
    if tracked != compute_digest(repo):
        return False, f"{DIGEST_FILE} is out of date with the tree"
    return True, f"fresh ({built[:12]})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check-digest", action="store_true",
                      help="exit 1 when crates/SOURCES.digest is out of date")
    mode.add_argument("--write-digest", action="store_true",
                      help="rewrite crates/SOURCES.digest from the tree")
    mode.add_argument("--check", action="store_true",
                      help="report whether the installed binary is fresh; no build")
    ap.add_argument("--staged", action="store_true",
                    help="with --check-digest: compare the index, not the working tree")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="install even when watched files have uncommitted changes")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                    help="install directory (default: bin/native in this repo)")
    args = ap.parse_args(argv)
    try:
        if args.write_digest:
            print(write_digest())
            return 0
        if args.check_digest:
            want = compute_digest(staged=args.staged)
            have = tracked_digest(staged=args.staged)
            if want == have:
                print(f"{DIGEST_FILE} current")
                return 0
            print(f"{DIGEST_FILE} is out of date; run: "
                  "python3 scripts/build_native.py --write-digest", file=sys.stderr)
            return 1
        if args.check:
            fresh, reason = installed_status(dest=args.dest)
            print(f"build_native: {reason}", file=sys.stdout if fresh else sys.stderr)
            return 0 if fresh else 1
        digest = build_and_install(dest=args.dest, allow_dirty=args.allow_dirty)
    except BuildError as exc:
        print(f"build_native: {exc}", file=sys.stderr)
        return 1
    print(f"build_native: installed {args.dest / 'bl'} ({digest[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
