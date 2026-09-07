#!/usr/bin/env python3
"""Detect the failure mode "a release was cut and the registry never got it".

`release_staleness.py` answers a different question: did main advance without a
release being cut. Both failures end with users on an old version, but only one
of them is visible in git. This one is not:

    tag v0.9.1 exists · GitHub Release v0.9.1 exists · npm serves 0.9.0

Cadence looks perfect. The publish workflow ran and failed, nobody read the log,
and the registry the README tells users to install from silently served a stale
version. @tyroneross/build-loop sat at 0.36.1 for seven weeks that way, and the
error was past log retention by the time anyone looked.

THE INVARIANT, from the project release model: freshness runs
`local main -> GitHub -> npm`. The registry MAY lag GitHub, and must NEVER lead
it. So there are two distinct failures, not one:

    npm < github   the publish did not land            (exit 1, "behind")
    npm > github   the registry has something main
                   does not — a hand-publish or a
                   publish from an unmerged branch     (exit 1, "ahead")

`ahead` is the louder of the two: `behind` means users are stale, `ahead` means
the registry is serving code that is not on the default branch.

Deliberately separate from `release_staleness.py` rather than folded into it:
that module is pure git and runs offline, which is what makes it unit-testable
without a network. This one has to talk to a registry. Same exit contract
(0 fresh / 1 stale / 2 cannot-run) so a workflow consumes them identically.

Reads the registry over plain HTTP rather than shelling to `npm`, so it needs no
Node toolchain and cannot be shadowed by a local `.npmrc`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_REGISTRY = "https://registry.npmjs.org"
DEFAULT_TIMEOUT = 15


class CheckError(RuntimeError):
    """The check could not be run at all — distinct from a stale verdict."""


def _git(workdir: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", workdir, *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise CheckError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def package_json_at(workdir: str, ref: str | None) -> dict:
    """Read package.json from `ref`, or from the working tree when ref is None.

    The ref matters. "The version GitHub has" is `origin/main:package.json`, never
    the working tree — a dirty tree or a feature branch is not what consumers get.
    """
    if ref is None:
        with open(f"{workdir}/package.json", encoding="utf-8") as handle:
            return json.load(handle)
    raw = _git(workdir, "show", f"{ref}:package.json")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckError(f"{ref}:package.json is not valid JSON: {exc}") from exc


def registry_version(
    name: str,
    registry: str = DEFAULT_REGISTRY,
    timeout: int = DEFAULT_TIMEOUT,
) -> str | None:
    """Latest published version, or None when the package has never been published.

    None is not an error: a package whose first version has not shipped yet is a
    real, expected state, and trusted publishing cannot create a package anyway.
    """
    url = f"{registry.rstrip('/')}/{urllib.parse.quote(name, safe='@')}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise CheckError(f"registry returned HTTP {exc.code} for {name}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CheckError(f"cannot reach {registry}: {exc}") from exc
    latest = (payload.get("dist-tags") or {}).get("latest")
    if not latest:
        raise CheckError(f"{name} has no dist-tags.latest")
    return latest


def _parts(version: str) -> tuple:
    """Compare semver numerically, with prerelease sorting below its release.

    `10.0.0` must outrank `9.0.0`, which a string compare gets wrong, and
    `1.2.0-rc.1` must sort below `1.2.0`.
    """
    core, _, pre = version.partition("-")
    nums = []
    for chunk in core.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    # A release outranks any prerelease of the same core version.
    return (tuple(nums[:3]), 1 if not pre else 0, pre)


#: Distinguishes "caller did not inject a registry version" from "the package is
#: genuinely unpublished". `None` cannot carry both meanings — a test that injects
#: published_version=None to exercise the unpublished path would otherwise fall
#: through to a live registry call and grade the wrong thing.
_UNSET = object()


def evaluate(
    workdir: str = ".",
    ref: str | None = "origin/main",
    registry: str = DEFAULT_REGISTRY,
    package_version: str | None = None,
    published_version: str | None = _UNSET,
) -> dict:
    """Compare what GitHub has against what the registry serves.

    `package_version` / `published_version` are injection points for tests, so the
    verdict logic is exercised without git or a network.
    """
    if package_version is None or published_version is _UNSET:
        manifest = package_json_at(workdir, ref)
        name = manifest.get("name")
        if not name:
            raise CheckError("package.json has no name")
        if manifest.get("private"):
            return {
                "stale": False,
                "package": name,
                "verdict": "private",
                "reason": f"{name} is marked private — it is not published anywhere",
            }
        package_version = package_version or manifest.get("version")
        if not package_version:
            raise CheckError(f"{name} has no version")
        published_version = (
            registry_version(name, registry)
            if published_version is _UNSET
            else published_version
        )
    else:
        name = "(injected)"

    if published_version is None:
        return {
            "stale": False,
            "package": name,
            "github": package_version,
            "registry": None,
            "verdict": "unpublished",
            "reason": (
                f"{name} has never been published; trusted publishing cannot create "
                "a package, so the first version needs a one-time manual publish"
            ),
        }

    gh, npm = _parts(package_version), _parts(published_version)
    if npm == gh:
        verdict, stale = "level", False
        reason = f"{name}: registry and GitHub agree at {package_version}"
    elif npm < gh:
        verdict, stale = "behind", True
        reason = (
            f"{name}: GitHub has {package_version}, the registry serves "
            f"{published_version} — a publish did not land"
        )
    else:
        verdict, stale = "ahead", True
        reason = (
            f"{name}: the registry serves {published_version} but GitHub only has "
            f"{package_version} — the registry is ahead of the default branch, "
            "which the release model forbids"
        )

    return {
        "stale": stale,
        "package": name,
        "github": package_version,
        "registry": published_version,
        "verdict": verdict,
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", default=".")
    ap.add_argument(
        "--ref",
        default="origin/main",
        help="git ref to read package.json from; 'worktree' reads the working tree",
    )
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ref = None if args.ref == "worktree" else args.ref
    try:
        result = evaluate(args.workdir, ref, args.registry)
    except CheckError as exc:
        print(f"registry_lag: cannot run — {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['verdict'].upper()} — {result['reason']}")
    return 1 if result["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
