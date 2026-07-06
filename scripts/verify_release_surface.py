#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Verify the complete release surface for a build-loop / plugin version.

A release is not complete just because manifests were edited and the test
passed locally. The release surface includes the checks below (per
references/coordination-rules.md §"Verification of release surface" and
memory feedback_verification_checks_release_surface). This script runs
all enabled checks and emits a structured JSON envelope; exit 0 if all pass, 1
if any fail.

Checks (in order):
    1. manifest_versions   — every enforced manifest/package/artifact shows
                              the target version
    2. readme_versions     — versioned README install/release examples show
                              the target version
    3. manifest_test       — scripts/test_plugin_manifest.py exits 0
    4. codex_artifact_current — checked-in Codex artifact matches source
    5. local_commit_log    — git log shows a commit on the branch whose
                              message references the target version
    6. local_tag           — git tag --list <tag> returns the tag
    7. branch_head_sha     — git rev-parse <branch> equals the commit SHA
                              referenced by the tag
    8. remote_refs         — git ls-remote <remote> <branch> <tag> shows
                              BOTH refs at the same SHA (load-bearing —
                              without this, a passing local verification
                              can ship nothing)
    9. fresh_session_load  — OPTIONAL — when --check-cache is passed,
                              diff installed cache vs canonical for the
                              plugin's agents/SKILL.md files

CLI:
    python3 scripts/verify_release_surface.py \\
        --version v0.12.8 \\
        --branch feat/peer-merged-status-2026-05-19 \\
        --remote origin \\
        --json

    Optional:
        --workdir <path>            (defaults to current dir)
        --plugin-slug <slug>        (defaults to plugin.json name)
        --check-cache               (enable fresh_session_load)
        --cache-root <path>         (defaults to ~/.claude/plugins/cache)
        --skip-check <name>         (suppress a check; may repeat)

Exit codes:
    0  all enabled checks passed
    1  at least one enabled check failed
    2  fatal error (cannot resolve repo, target version, etc.)

The verifier (Codex, CI, second Claude session) calls this instead of
running the release commands manually. Coord-file template references it
as the canonical release-verification entry point.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SEMVER_RE = re.compile(r"^v?\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$")
SEMVER_FRAGMENT = r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?"
README_VERSION_PATTERNS = (
    ("npm_build_loop_install", re.compile(rf"@tyroneross/build-loop@(?P<version>{SEMVER_FRAGMENT})")),
    ("release_surface_version_arg", re.compile(rf"--version\s+v?(?P<version>{SEMVER_FRAGMENT})")),
)
README_VERSION_FILES = (
    "README.md",
    "plugin-artifacts/codex/README.md",
)
RELEASE_SURFACE_CHECKS = (
    "manifest_versions",
    "readme_versions",
    "manifest_test",
    "codex_artifact_current",
    "local_commit_log",
    "local_tag",
    "branch_head_sha",
    "remote_refs",
    "fresh_session_load",
)
OPTIONAL_CHECKS = ("fresh_session_load",)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 15) -> tuple[int, str, str]:
    """Run cmd and return (returncode, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)


def _strip_v(version: str) -> str:
    """Return the bare X.Y.Z form (strip a leading 'v' if present)."""
    return version[1:] if version.startswith("v") else version


def _with_v(version: str) -> str:
    """Return the v-prefixed form (add leading 'v' if missing)."""
    return version if version.startswith("v") else f"v{version}"


def check_manifest_versions(workdir: Path, target: str) -> dict[str, Any]:
    """Every enforced manifest file shows the target version."""
    bare = _strip_v(target)
    manifests = {
        "package.json": "version",
        ".claude-plugin/plugin.json": "version",
        ".codex-plugin/plugin.json": "version",
        ".agents/plugins/marketplace.json": "version",
        "plugin-artifacts/codex/.codex-plugin/plugin.json": "version",
    }
    findings: list[dict[str, Any]] = []
    overall_pass = True

    for rel, field in manifests.items():
        path = workdir / rel
        if not path.is_file():
            findings.append({"file": rel, "status": "skipped", "reason": "not present"})
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            findings.append({"file": rel, "status": "fail", "reason": f"unreadable: {e}"})
            overall_pass = False
            continue
        actual = data.get(field)
        if actual == bare:
            findings.append({"file": rel, "field": field, "status": "pass", "value": actual})
        else:
            findings.append({
                "file": rel, "field": field, "status": "fail",
                "expected": bare, "actual": actual,
            })
            overall_pass = False

    lock_path = workdir / "package-lock.json"
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            findings.append({"file": "package-lock.json", "status": "fail",
                             "reason": f"unreadable: {e}"})
            overall_pass = False
        else:
            lock_fields = {
                "version": lock.get("version"),
                'packages[""].version': lock.get("packages", {}).get("", {}).get("version"),
            }
            for field, actual in lock_fields.items():
                if actual == bare:
                    findings.append({"file": "package-lock.json", "field": field,
                                     "status": "pass", "value": actual})
                else:
                    findings.append({"file": "package-lock.json", "field": field,
                                     "status": "fail", "expected": bare,
                                     "actual": actual})
                    overall_pass = False

    # marketplace.json — both metadata.version AND plugins[name=<plugin>].version
    market_path = workdir / ".claude-plugin" / "marketplace.json"
    if market_path.is_file():
        try:
            market = json.loads(market_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            findings.append({"file": ".claude-plugin/marketplace.json", "status": "fail",
                             "reason": f"unreadable: {e}"})
            overall_pass = False
        else:
            meta_v = market.get("metadata", {}).get("version")
            if meta_v is not None:
                if meta_v == bare:
                    findings.append({"file": ".claude-plugin/marketplace.json",
                                     "field": "metadata.version", "status": "pass",
                                     "value": meta_v})
                else:
                    findings.append({"file": ".claude-plugin/marketplace.json",
                                     "field": "metadata.version", "status": "fail",
                                     "expected": bare, "actual": meta_v})
                    overall_pass = False

            # plugin entry version
            plugin_json = workdir / ".claude-plugin" / "plugin.json"
            plugin_name = None
            if plugin_json.is_file():
                try:
                    plugin_name = json.loads(plugin_json.read_text(encoding="utf-8")).get("name")
                except (OSError, json.JSONDecodeError):
                    plugin_name = None
            for entry in market.get("plugins", []):
                if plugin_name and entry.get("name") == plugin_name:
                    ev = entry.get("version")
                    if ev == bare:
                        findings.append({"file": ".claude-plugin/marketplace.json",
                                         "field": f"plugins[name={plugin_name}].version",
                                         "status": "pass", "value": ev})
                    else:
                        findings.append({"file": ".claude-plugin/marketplace.json",
                                         "field": f"plugins[name={plugin_name}].version",
                                         "status": "fail", "expected": bare, "actual": ev})
                        overall_pass = False

    agents_market_path = workdir / ".agents" / "plugins" / "marketplace.json"
    if agents_market_path.is_file():
        try:
            agents_market = json.loads(agents_market_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            findings.append({"file": ".agents/plugins/marketplace.json",
                             "status": "fail", "reason": f"unreadable: {e}"})
            overall_pass = False
        else:
            meta_v = agents_market.get("metadata", {}).get("version") \
                if isinstance(agents_market.get("metadata"), dict) else None
            if meta_v is not None:
                if meta_v == bare:
                    findings.append({"file": ".agents/plugins/marketplace.json",
                                     "field": "metadata.version", "status": "pass",
                                     "value": meta_v})
                else:
                    findings.append({"file": ".agents/plugins/marketplace.json",
                                     "field": "metadata.version", "status": "fail",
                                     "expected": bare, "actual": meta_v})
                    overall_pass = False
            plugin_json = workdir / ".claude-plugin" / "plugin.json"
            plugin_name = None
            if plugin_json.is_file():
                try:
                    plugin_name = json.loads(plugin_json.read_text(encoding="utf-8")).get("name")
                except (OSError, json.JSONDecodeError):
                    plugin_name = None
            for entry in agents_market.get("plugins", []):
                if plugin_name and entry.get("name") == plugin_name and entry.get("version") is not None:
                    ev = entry.get("version")
                    if ev == bare:
                        findings.append({"file": ".agents/plugins/marketplace.json",
                                         "field": f"plugins[name={plugin_name}].version",
                                         "status": "pass", "value": ev})
                    else:
                        findings.append({"file": ".agents/plugins/marketplace.json",
                                         "field": f"plugins[name={plugin_name}].version",
                                         "status": "fail", "expected": bare, "actual": ev})
                        overall_pass = False

    return {
        "name": "manifest_versions",
        "pass": overall_pass,
        "findings": findings,
    }


def check_readme_versions(workdir: Path, target: str) -> dict[str, Any]:
    """Versioned README install/release examples show the target version."""
    bare = _strip_v(target)
    findings: list[dict[str, Any]] = []
    overall_pass = True

    for rel in README_VERSION_FILES:
        path = workdir / rel
        if not path.is_file():
            findings.append({"file": rel, "status": "skipped", "reason": "not present"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            findings.append({"file": rel, "status": "fail", "reason": f"unreadable: {e}"})
            overall_pass = False
            continue

        matches = 0
        for pattern_name, pattern in README_VERSION_PATTERNS:
            for match in pattern.finditer(text):
                matches += 1
                actual = match.group("version")
                line = text.count("\n", 0, match.start()) + 1
                if actual == bare:
                    findings.append({
                        "file": rel,
                        "line": line,
                        "pattern": pattern_name,
                        "status": "pass",
                        "value": actual,
                    })
                else:
                    findings.append({
                        "file": rel,
                        "line": line,
                        "pattern": pattern_name,
                        "status": "fail",
                        "expected": bare,
                        "actual": actual,
                    })
                    overall_pass = False
        if matches == 0:
            findings.append({
                "file": rel,
                "status": "skipped",
                "reason": "no versioned README examples found",
            })

    return {
        "name": "readme_versions",
        "pass": overall_pass,
        "findings": findings,
    }


def check_manifest_test(workdir: Path) -> dict[str, Any]:
    """scripts/test_plugin_manifest.py exits 0."""
    test = workdir / "scripts" / "test_plugin_manifest.py"
    if not test.is_file():
        return {"name": "manifest_test", "pass": False,
                "findings": [{"status": "fail", "reason": f"{test} not present"}]}
    rc, stdout, stderr = _run([sys.executable, str(test)], cwd=workdir, timeout=60)
    return {
        "name": "manifest_test",
        "pass": rc == 0,
        "findings": [{
            "command": f"{sys.executable} {test.relative_to(workdir)}",
            "exit_code": rc,
            "summary": (stderr or stdout).strip().splitlines()[-3:] if (stderr or stdout) else [],
        }],
    }


def check_codex_artifact_current(workdir: Path) -> dict[str, Any]:
    """Checked-in Codex artifact matches the canonical source tree."""
    script = workdir / "scripts" / "build_codex_plugin_artifact.py"
    artifact = workdir / "plugin-artifacts" / "codex"
    if not script.is_file() and not artifact.exists():
        return {"name": "codex_artifact_current", "pass": True,
                "findings": [{"status": "skipped",
                              "reason": "no Codex artifact builder or artifact present"}]}
    if not script.is_file():
        return {"name": "codex_artifact_current", "pass": False,
                "findings": [{"status": "fail",
                              "reason": f"{script.relative_to(workdir)} not present"}]}
    if not artifact.exists():
        return {"name": "codex_artifact_current", "pass": False,
                "findings": [{"status": "fail",
                              "reason": f"{artifact.relative_to(workdir)} not present"}]}
    rc, stdout, stderr = _run(
        [
            sys.executable,
            str(script),
            "--source",
            str(workdir),
            "--target",
            str(artifact),
            "--check",
        ],
        cwd=workdir,
        timeout=60,
    )
    return {
        "name": "codex_artifact_current",
        "pass": rc == 0,
        "findings": [{
            "command": (
                f"{sys.executable} {script.relative_to(workdir)} --source . "
                f"--target {artifact.relative_to(workdir)} --check"
            ),
            "exit_code": rc,
            "summary": (stderr or stdout).strip().splitlines()[-3:] if (stderr or stdout) else [],
        }],
    }


def check_local_commit_log(workdir: Path, branch: str, target: str) -> dict[str, Any]:
    """git log on branch shows a commit whose message references the target version."""
    bare = _strip_v(target)
    rc, stdout, stderr = _run(
        ["git", "log", "--oneline", "-30", branch], cwd=workdir, timeout=10,
    )
    if rc != 0:
        return {"name": "local_commit_log", "pass": False,
                "findings": [{"status": "fail", "reason": f"git log failed: {stderr.strip()}"}]}
    matches = [line for line in stdout.splitlines() if bare in line or _with_v(target) in line]
    return {
        "name": "local_commit_log",
        "pass": bool(matches),
        "findings": [{
            "branch": branch,
            "target_pattern": bare,
            "matches_count": len(matches),
            "matches_sample": matches[:3],
        }],
    }


def check_local_tag(workdir: Path, target: str) -> dict[str, Any]:
    """git tag --list <tag> returns the tag."""
    tag = _with_v(target)
    rc, stdout, _stderr = _run(["git", "tag", "--list", tag], cwd=workdir, timeout=5)
    present = rc == 0 and stdout.strip() == tag
    findings = {"tag": tag, "present": present}
    if present:
        rc2, sha, _ = _run(["git", "rev-list", "-n", "1", tag], cwd=workdir, timeout=5)
        if rc2 == 0:
            findings["tag_sha"] = sha.strip()
    return {"name": "local_tag", "pass": present, "findings": [findings]}


def check_branch_head_sha(workdir: Path, branch: str, target: str) -> dict[str, Any]:
    """git rev-parse <branch> equals the commit SHA referenced by the tag."""
    tag = _with_v(target)
    rc_b, branch_sha, errb = _run(["git", "rev-parse", branch], cwd=workdir, timeout=5)
    rc_t, tag_sha, errt = _run(["git", "rev-list", "-n", "1", tag], cwd=workdir, timeout=5)
    if rc_b != 0:
        return {"name": "branch_head_sha", "pass": False,
                "findings": [{"status": "fail", "reason": f"branch rev-parse failed: {errb.strip()}"}]}
    if rc_t != 0:
        # When local_tag failed, this check naturally degrades — report informative finding.
        return {"name": "branch_head_sha", "pass": False,
                "findings": [{"status": "fail", "reason": f"tag rev-list failed: {errt.strip()}",
                              "branch": branch, "branch_sha": branch_sha.strip()}]}
    bsha = branch_sha.strip()
    tsha = tag_sha.strip()
    return {
        "name": "branch_head_sha",
        "pass": bsha == tsha,
        "findings": [{
            "branch": branch, "branch_sha": bsha,
            "tag": tag, "tag_sha": tsha,
            "equal": bsha == tsha,
        }],
    }


def check_remote_refs(workdir: Path, remote: str, branch: str, target: str) -> dict[str, Any]:
    """git ls-remote <remote> <branch> <tag> shows BOTH refs at the same SHA."""
    tag = _with_v(target)
    rc, stdout, stderr = _run(
        ["git", "ls-remote", remote, branch, tag], cwd=workdir, timeout=15,
    )
    if rc != 0:
        return {"name": "remote_refs", "pass": False,
                "findings": [{"status": "fail", "reason": f"git ls-remote failed: {stderr.strip()}",
                              "remote": remote}]}
    refs: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sha, ref = parts[0], parts[1]
            refs[ref] = sha
    branch_ref = f"refs/heads/{branch}"
    tag_ref = f"refs/tags/{tag}"
    branch_sha = refs.get(branch_ref)
    tag_sha = refs.get(tag_ref)
    both_present = branch_sha is not None and tag_sha is not None
    both_same = both_present and branch_sha == tag_sha
    return {
        "name": "remote_refs",
        "pass": both_same,
        "findings": [{
            "remote": remote,
            "branch_ref": branch_ref, "branch_sha": branch_sha,
            "tag_ref": tag_ref, "tag_sha": tag_sha,
            "both_present": both_present,
            "both_same_sha": both_same,
        }],
    }


def check_fresh_session_load(
    workdir: Path, target: str, cache_root: Path, plugin_slug: str | None,
) -> dict[str, Any]:
    """Cache files for the plugin at <target> version byte-equal canonical."""
    bare = _strip_v(target)
    # Try a few common cache layouts: rosslabs-ai-toolkit/<plugin>/<version>/
    # and <plugin>/<version>/ at the top of cache_root.
    if not cache_root.is_dir():
        return {"name": "fresh_session_load", "pass": False,
                "findings": [{"status": "fail", "reason": f"cache root not found: {cache_root}"}]}
    name = plugin_slug or workdir.name
    candidates = [
        cache_root / "rosslabs-ai-toolkit" / name / bare,
        cache_root / name / bare,
    ]
    chosen = next((c for c in candidates if c.is_dir()), None)
    if chosen is None:
        return {"name": "fresh_session_load", "pass": False,
                "findings": [{"status": "fail", "reason": "cache dir for target version not found",
                              "candidates_tried": [str(c) for c in candidates]}]}
    diffs: list[str] = []
    # Compare a small set of high-signal files: every agent + every SKILL.md
    for canon in list(workdir.glob("agents/*.md")) + list(workdir.glob("skills/*/SKILL.md")):
        rel = canon.relative_to(workdir)
        cached = chosen / rel
        if not cached.is_file():
            diffs.append(f"MISSING in cache: {rel}")
            continue
        try:
            if canon.read_bytes() != cached.read_bytes():
                diffs.append(f"DRIFT: {rel}")
        except OSError as e:
            diffs.append(f"UNREADABLE {rel}: {e}")
    return {
        "name": "fresh_session_load",
        "pass": not diffs,
        "findings": [{
            "cache_dir": str(chosen),
            "drift_count": len(diffs),
            "drift_sample": diffs[:5],
        }],
    }


def verify_release_surface(
    *,
    workdir: Path,
    version: str,
    branch: str,
    remote: str,
    check_cache: bool,
    cache_root: Path,
    plugin_slug: str | None,
    skip_checks: set[str],
) -> dict[str, Any]:
    """Run every enabled release-surface check; return aggregate envelope."""
    results: list[dict[str, Any]] = []

    if "manifest_versions" not in skip_checks:
        results.append(check_manifest_versions(workdir, version))
    if "readme_versions" not in skip_checks:
        results.append(check_readme_versions(workdir, version))
    if "manifest_test" not in skip_checks:
        results.append(check_manifest_test(workdir))
    if "codex_artifact_current" not in skip_checks:
        results.append(check_codex_artifact_current(workdir))
    if "local_commit_log" not in skip_checks:
        results.append(check_local_commit_log(workdir, branch, version))
    if "local_tag" not in skip_checks:
        results.append(check_local_tag(workdir, version))
    if "branch_head_sha" not in skip_checks:
        results.append(check_branch_head_sha(workdir, branch, version))
    if "remote_refs" not in skip_checks:
        results.append(check_remote_refs(workdir, remote, branch, version))
    if check_cache and "fresh_session_load" not in skip_checks:
        results.append(check_fresh_session_load(workdir, version, cache_root, plugin_slug))

    overall_pass = all(r["pass"] for r in results)
    return {
        "schema_version": "1.0",
        "verified_at": _isoformat_now(),
        "workdir": str(workdir),
        "version": _with_v(version),
        "branch": branch,
        "remote": remote,
        "overall_pass": overall_pass,
        "checks_run": [r["name"] for r in results],
        "checks_skipped": sorted(skip_checks),
        "results": results,
    }


def _isoformat_now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--version", required=True, help="Target version, e.g. v0.12.8 or 0.12.8")
    p.add_argument("--branch", required=True, help="Branch that should carry the version commit + tag")
    p.add_argument("--remote", default="origin", help="Git remote name (default: origin)")
    p.add_argument("--workdir", default=".", help="Repo root (default: cwd)")
    p.add_argument("--plugin-slug", default=None, help="Plugin slug for cache lookup; defaults to repo basename")
    p.add_argument("--check-cache", action="store_true", help="Enable the optional fresh-session-load check")
    p.add_argument("--cache-root", default="~/.claude/plugins/cache", help="Claude plugin cache root")
    p.add_argument("--skip-check", action="append", default=[],
                   help=f"Suppress a check by name; may repeat. Valid: {','.join(RELEASE_SURFACE_CHECKS)}")
    p.add_argument("--json", action="store_true", help="Accepted for explicitness; output is always JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not SEMVER_RE.match(args.version):
        msg = {"schema_version": "1.0", "fatal_error": f"invalid --version {args.version!r}; expected vX.Y.Z[-pre]"}
        print(json.dumps(msg, indent=2, sort_keys=True))
        return 2
    workdir = Path(args.workdir).expanduser().resolve()
    cache_root = Path(args.cache_root).expanduser()
    skip = set(args.skip_check or [])
    invalid = skip - set(RELEASE_SURFACE_CHECKS)
    if invalid:
        msg = {"schema_version": "1.0",
               "fatal_error": f"unknown --skip-check name(s): {sorted(invalid)}",
               "valid": list(RELEASE_SURFACE_CHECKS)}
        print(json.dumps(msg, indent=2, sort_keys=True))
        return 2
    envelope = verify_release_surface(
        workdir=workdir,
        version=args.version,
        branch=args.branch,
        remote=args.remote,
        check_cache=args.check_cache,
        cache_root=cache_root,
        plugin_slug=args.plugin_slug,
        skip_checks=skip,
    )
    print(json.dumps(envelope, indent=2, sort_keys=True))
    return 0 if envelope["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
