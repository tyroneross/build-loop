#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Single source of truth for build-loop's version across all four manifests.

`.claude-plugin/plugin.json` is canonical. Four other fields must match it:

    .codex-plugin/plugin.json          .version
    .claude-plugin/marketplace.json    .metadata.version
    .claude-plugin/marketplace.json    .plugins[0].version
    .agents/plugins/marketplace.json   .version

WHY THIS EXISTS. Those five fields were hand-edited, so a bump touched one file
and forgot three. `scripts/test_plugin_manifest.py` catches the mismatch, but
it only runs inside `verify_release_surface.py` — a RELEASE gate. So the drift
sat red across four unreleased versions (0.36.5 through 0.36.8) with nothing
surfacing it at commit time. Syncing the numbers by hand fixes one instance and
leaves the generator running; this script removes the hand-edit entirely.

Modes:
    --check          exit 1 on any drift (cheap; wire into a hook or CI)
    --sync           write the canonical version into the other four fields
    --set X.Y.Z      set all five, canonical included
    --patch          canonical Z+1  (the default release: one push, one patch)
    --minor          canonical Y+1, Z=0  (declared functionality change)
    --major          canonical X+1, Y=Z=0
    --tag            create annotated tag vX.Y.Z at HEAD; writes no manifests
    --json           machine-readable result on stdout

RELEASE MODEL. A push is the unit of release. Local commits carry only their
SHA and touch no version field; the number moves once per push. --patch is
therefore the normal path no matter how many commits are being pushed.

Tags matter here: the manifest alone cannot say WHICH commit a version shipped
from. Tagging is deliberately a separate operation so the version-bump commit
exists before its tag is created.

Pure stdlib. Python 3.11+. Never raises on a missing manifest — reports it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CANONICAL = ("Claude plugin manifest", REPO_ROOT / ".claude-plugin" / "plugin.json", ("version",))

# (label, path, key-path) — key-path walks dicts; ints index lists.
MIRRORS: list[tuple[str, Path, tuple[object, ...]]] = [
    ("Codex plugin manifest", REPO_ROOT / ".codex-plugin" / "plugin.json", ("version",)),
    ("Claude marketplace metadata", REPO_ROOT / ".claude-plugin" / "marketplace.json", ("metadata", "version")),
    ("Claude marketplace entry", REPO_ROOT / ".claude-plugin" / "marketplace.json", ("plugins", 0, "version")),
    ("open-agents marketplace mirror", REPO_ROOT / ".agents" / "plugins" / "marketplace.json", ("version",)),
    # `verify_release_surface.py` checks the npm package version too, so leaving
    # it out reproduced exactly the drift this script exists to prevent: the
    # 0.37.1 bump synced 5 fields and left package.json on 0.37.0, which the
    # release gate then failed. A surface the release gate checks is a surface
    # this script must own.
    ("npm package manifest", REPO_ROOT / "package.json", ("version",)),
    # npm writes the version twice in the lockfile and the release gate checks
    # both. `npm install` would sync them, but the bump must not depend on
    # anyone remembering to run it.
    ("npm lockfile root", REPO_ROOT / "package-lock.json", ("version",)),
    ("npm lockfile self-entry", REPO_ROOT / "package-lock.json", ("packages", "", "version")),
]

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def next_version(current: str, kind: str) -> str:
    """Compute the next version. Pure arithmetic — no commit content consulted."""
    major, minor, patch = (int(x) for x in current.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def create_tag(version: str) -> dict:
    """Annotated tag vX.Y.Z at HEAD. Never pushes — that stays the caller's call.

    Annotated (not lightweight) because a release marker should be a real git
    object carrying who/when, and because `git describe --tags` — which
    version_advisor.py relies on to find the last bump — reads these.
    """
    import subprocess
    tag = f"v{version}"
    exists = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify",
                             "--quiet", f"refs/tags/{tag}"],
                            capture_output=True, text=True)
    if exists.returncode == 0:
        return {"tag": tag, "created": False, "reason": "already exists"}
    r = subprocess.run(["git", "-C", str(REPO_ROOT), "tag", "-a", tag,
                        "-m", f"Release {version}"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"tag": tag, "created": False, "reason": (r.stderr or "").strip()}
    return {"tag": tag, "created": True}


def _load(path: Path):
    return json.loads(path.read_text())


def _dump(path: Path, data) -> None:
    """Write with the repo's 2-space indent + trailing newline, so diffs stay minimal."""
    path.write_text(json.dumps(data, indent=2) + "\n")


def _get(data, keys: tuple[object, ...]):
    cur = data
    for k in keys:
        cur = cur[k]
    return cur


def _set(data, keys: tuple[object, ...], value) -> None:
    cur = data
    for k in keys[:-1]:
        cur = cur[k]
    cur[keys[-1]] = value


def _keypath(keys: tuple[object, ...]) -> str:
    return "".join(f"[{k}]" if isinstance(k, int) else f".{k}" for k in keys)


def read_state() -> dict:
    """Canonical version plus every mirror's current value. Never raises."""
    label, path, keys = CANONICAL
    if not path.exists():
        return {"ok": False, "error": f"canonical manifest missing: {path}", "canonical": None, "mirrors": []}
    try:
        canonical = _get(_load(path), keys)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        return {"ok": False, "error": f"canonical unreadable: {exc}", "canonical": None, "mirrors": []}

    mirrors = []
    for m_label, m_path, m_keys in MIRRORS:
        entry = {
            "label": m_label,
            "path": str(m_path.relative_to(REPO_ROOT)),
            "field": _keypath(m_keys),
            "value": None,
            "matches": False,
            "error": None,
        }
        if not m_path.exists():
            entry["error"] = "file missing"
        else:
            try:
                entry["value"] = _get(_load(m_path), m_keys)
                entry["matches"] = entry["value"] == canonical
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                entry["error"] = str(exc)
        mirrors.append(entry)

    return {"ok": True, "error": None, "canonical": canonical, "mirrors": mirrors}


def apply_version(version: str, set_canonical: bool) -> list[str]:
    """Write `version` into the mirrors (and the canonical when asked). Returns changes."""
    changed: list[str] = []

    if set_canonical:
        _, path, keys = CANONICAL
        data = _load(path)
        if _get(data, keys) != version:
            _set(data, keys, version)
            _dump(path, data)
            changed.append(f"{path.relative_to(REPO_ROOT)}{_keypath(keys)}")

    # Group by file so a manifest holding two fields is written once.
    by_file: dict[Path, list[tuple[object, ...]]] = {}
    for _, m_path, m_keys in MIRRORS:
        by_file.setdefault(m_path, []).append(m_keys)

    for m_path, keysets in by_file.items():
        if not m_path.exists():
            continue
        data = _load(m_path)
        dirty = False
        for m_keys in keysets:
            try:
                if _get(data, m_keys) != version:
                    _set(data, m_keys, version)
                    dirty = True
                    changed.append(f"{m_path.relative_to(REPO_ROOT)}{_keypath(m_keys)}")
            except (KeyError, IndexError, TypeError):
                continue
        if dirty:
            _dump(m_path, data)

    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="exit 1 on drift; write nothing")
    mode.add_argument("--sync", action="store_true", help="propagate the canonical version to every mirror")
    mode.add_argument("--set", metavar="X.Y.Z", help="set all five fields to this version")
    mode.add_argument("--patch", action="store_true", help="bump Z+1 (normal per-push release)")
    mode.add_argument("--minor", action="store_true", help="bump Y+1, Z=0")
    mode.add_argument("--major", action="store_true", help="bump X+1, Y=Z=0")
    mode.add_argument("--tag", action="store_true",
                      help="tag HEAD with the current canonical version; write nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.set and not SEMVER.match(args.set):
        print(f"bump_version: {args.set!r} is not X.Y.Z", file=sys.stderr)
        return 2

    state = read_state()
    if not state["ok"]:
        print(json.dumps(state) if args.json else f"bump_version: {state['error']}", file=sys.stderr)
        return 2

    if args.check:
        drifted = [m for m in state["mirrors"] if not m["matches"]]
        result = {"mode": "check", "canonical": state["canonical"],
                  "drifted": drifted, "drift_count": len(drifted)}
        if args.json:
            print(json.dumps(result, indent=2))
        elif drifted:
            print(f"bump_version: {len(drifted)} manifest field(s) drifted from {state['canonical']}")
            for m in drifted:
                shown = m["error"] or repr(m["value"])
                print(f"  {m['path']}{m['field']} = {shown}  ({m['label']})")
            print("  fix: python3 scripts/bump_version.py --sync")
        else:
            print(f"bump_version: all {len(MIRRORS) + 1} version fields agree at {state['canonical']}")
        return 1 if drifted else 0

    if args.tag:
        result = {"mode": "tag", "version": state["canonical"],
                  "tag": create_tag(state["canonical"])}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            tag = result["tag"]
            verb = "created" if tag["created"] else tag.get("reason", "not created")
            print(f"bump_version: {tag['tag']} {verb}")
        return 0 if result["tag"]["created"] or result["tag"].get("reason") == "already exists" else 1

    kind = next((k for k in ("patch", "minor", "major") if getattr(args, k)), None)
    if kind:
        version = next_version(state["canonical"], kind)
        mode = kind
    else:
        version = args.set or state["canonical"]
        mode = "set" if args.set else "sync"

    changed = apply_version(version, set_canonical=bool(args.set or kind))
    result = {"mode": mode, "version": version, "changed": changed,
              "previous": state["canonical"]}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if changed:
            print(f"bump_version: set {len(changed)} field(s) to {version}")
            for c in changed:
                print(f"  {c}")
        else:
            print(f"bump_version: already consistent at {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
