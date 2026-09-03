# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""The release pipeline has to be able to fire at all.

WHAT THIS PREVENTS. Between 2026-07-12 and 2026-09-02 this repo shipped nothing.
Every publish workflow triggered on `release: types: [published]`, no workflow ever
created a release, and no test anywhere asserted that a release could be cut. Main
reached 0.42.5 while the newest tag and release sat at v0.36.4. Each assertion below
maps to one way that pipeline can go silently dead again — silently being the
operative word, since every one of these failures leaves CI green and the repo
looking healthy while installed users receive nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CONFIG = ROOT / "release-please-config.json"
MANIFEST = ROOT / ".release-please-manifest.json"


def _wf(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    # PyYAML resolves an unquoted `on:` key to the boolean True (the YAML 1.1
    # "Norway problem"). Accept both so this test does not depend on quoting.
    return wf.get("on") or wf.get(True) or {}


def _jsonpath_get(data, jsonpath: str):
    """Resolve the small `$.a.b[0].c` subset used in release-please-config.json."""
    assert jsonpath.startswith("$."), jsonpath
    node = data
    for part in jsonpath[2:].split("."):
        while "[" in part:
            name, _, rest = part.partition("[")
            idx, _, part = rest.partition("]")
            if name:
                node = node[name]
            node = node[int(idx)]
        if part:
            node = node[part]
    return node


# ---------------------------------------------------------------- configuration

def test_manifest_version_matches_package_json() -> None:
    """release-please's idea of "last released" must equal what main actually ships.

    Desync it and release-please computes the next version from the wrong base:
    too low and npm rejects the publish as an existing version, too high and a
    version number is skipped with no record of what was in it.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert manifest["."] == package["version"], (
        f".release-please-manifest.json says {manifest['.']} but package.json ships "
        f"{package['version']} — bump both, or let release-please do it"
    )


def test_config_targets_this_package() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    root = cfg["packages"]["."]
    assert cfg["release-type"] == "node"
    assert root["package-name"] == "@tyroneross/build-loop"


def test_every_version_bearing_manifest_is_wired_into_release_please() -> None:
    """The five mirrors bump_version.py syncs must also be bumped by release-please.

    bump_version.py exists because these five fields were hand-edited and a bump
    would touch one and forget three, leaving the drift red across four unreleased
    versions. release-please replaces that hand edit; if a mirror is missing from
    `extra-files` it silently keeps the OLD version, and the drift is back with an
    automated system putting it there.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bump_version", ROOT / "scripts" / "bump_version.py"
    )
    bump = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bump)

    known = {p.relative_to(ROOT).as_posix() for _, p, _ in bump.MIRRORS}
    known.add(bump.CANONICAL[1].relative_to(ROOT).as_posix())
    # The `node` release type updates these natively, so they are correctly absent
    # from extra-files: package.json via its own updater, and the lock files via
    # PackageLockJson (release-please src/strategies/node.ts, `lockFiles`).
    for native in ("package.json", "package-lock.json", "npm-shrinkwrap.json"):
        known.discard(native)

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    wired = {e["path"] for e in cfg["packages"]["."]["extra-files"]}

    missing = known - wired
    assert not missing, (
        "bump_version.py syncs these version fields but release-please would leave "
        f"them stale: {sorted(missing)} — add them to release-please-config.json "
        "extra-files"
    )


def test_every_configured_jsonpath_resolves_to_a_real_version_string() -> None:
    """A typo'd jsonpath does not fail — release-please logs a warning and skips.

    So a mis-pathed entry looks exactly like a correct one until a release ships
    with a stale manifest. Resolve each path against the real file instead.
    """
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    for entry in cfg["packages"]["."]["extra-files"]:
        if entry.get("type") != "json":
            continue
        target = ROOT / entry["path"]
        assert target.exists(), f"{entry['path']} does not exist"
        value = _jsonpath_get(json.loads(target.read_text(encoding="utf-8")), entry["jsonpath"])
        assert isinstance(value, str), (
            f"{entry['path']} {entry['jsonpath']} is {type(value).__name__}, not a "
            "string — release-please skips non-strings with only a warning"
        )
        assert value.count(".") >= 2, (
            f"{entry['path']} {entry['jsonpath']} = {value!r} is not version-shaped"
        )


def test_readme_version_pins_carry_release_please_annotations() -> None:
    """An un-annotated pin is a guaranteed red build on the next release.

    test_readme_surface_claims.py asserts every README pin equals package.json.
    release-please bumps package.json, so any pin it cannot see reddens main the
    moment the release PR merges.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    annotated, in_block = [], False
    for line in readme:
        if "x-release-please-start-version" in line:
            in_block = True
        elif "x-release-please-end" in line:
            in_block = False
        elif in_block or "x-release-please-version" in line:
            annotated.append(line)

    import re

    pin = re.compile(r"@tyroneross/build-loop@(\d+\.\d+\.\d+)|--version v(\d+\.\d+\.\d+)")
    for line in readme:
        if pin.search(line):
            assert line in annotated, (
                f"README pins a version on an un-annotated line, so release-please "
                f"will not bump it and the next release reddens CI:\n  {line.strip()}"
            )


# ------------------------------------------------------------------- workflows

def test_a_workflow_actually_creates_releases() -> None:
    """The original defect, stated directly: nothing created a release."""
    wf = _wf("release-please.yml")
    assert "push" in _triggers(wf), "release-please must run on push to main"
    assert _triggers(wf)["push"]["branches"] == ["main"]
    uses = [
        s["uses"]
        for job in wf["jobs"].values()
        for s in job["steps"]
        if "uses" in s
    ]
    assert any("release-please-action" in u for u in uses), (
        "release-please.yml no longer runs release-please-action — nothing in this "
        "repo would create a release"
    )


def test_the_publish_bridge_survives() -> None:
    """The single most likely way this whole setup silently does nothing.

    GitHub docs, "Triggering a workflow": events triggered by GITHUB_TOKEN do not
    create a new workflow run, and `release` is not one of the exceptions. So a
    release-please release will NOT start the `on: release: [published]` publish
    workflows. The explicit workflow_dispatch bridge is what closes that, and
    deleting it leaves releases appearing on GitHub while npm gets nothing.
    """
    wf = _wf("release-please.yml")
    job = wf["jobs"]["release-please"]
    assert job.get("permissions", wf.get("permissions", {})).get("actions") == "write" or \
        wf.get("permissions", {}).get("actions") == "write", \
        "the bridge needs `actions: write` to dispatch the publish workflows"
    body = "\n".join(s.get("run", "") for s in job["steps"])
    for target in ("publish-npm.yml", "publish-npmjs.yml"):
        assert target in body, (
            f"release-please.yml no longer dispatches {target}; a GITHUB_TOKEN "
            "release will not trigger it on its own"
        )


def test_dispatched_publish_targets_exist_and_accept_dispatch() -> None:
    """A renamed publish workflow makes the bridge dispatch a 404 into the void."""
    for name in ("publish-npm.yml", "publish-npmjs.yml"):
        path = WORKFLOWS / name
        assert path.exists(), f"release-please.yml dispatches {name}, which is missing"
        assert "workflow_dispatch" in _triggers(_wf(name)), (
            f"{name} must accept workflow_dispatch — it is the only documented "
            "GITHUB_TOKEN exception the bridge can use"
        )


def test_the_weekly_cut_is_scheduled_and_manually_runnable() -> None:
    """Cadence plus an escape hatch: a security fix cannot wait for Sunday."""
    triggers = _triggers(_wf("release-weekly-merge.yml"))
    assert "schedule" in triggers and triggers["schedule"], "the weekly cut lost its cron"
    assert "workflow_dispatch" in triggers, (
        "release-weekly-merge.yml must stay manually dispatchable — without it an "
        "urgent fix waits up to a week"
    )


def test_the_weekly_cut_matches_the_release_pr_by_label() -> None:
    """PR titles change format between action majors; the label has not."""
    body = (WORKFLOWS / "release-weekly-merge.yml").read_text(encoding="utf-8")
    assert "autorelease: pending" in body
    assert "--label" in body, "match the release PR on its label, never on its title"


def test_the_weekly_cut_refuses_a_red_main() -> None:
    body = (WORKFLOWS / "release-weekly-merge.yml").read_text(encoding="utf-8")
    assert "conclusion" in body and "pytest.yml" in body, (
        "the weekly cut must check main's test conclusion before merging"
    )


def test_cadence_failure_is_monitored() -> None:
    """Automation that stops working must be louder than a manual step forgotten."""
    triggers = _triggers(_wf("release-staleness.yml"))
    assert "schedule" in triggers and triggers["schedule"]
    body = (WORKFLOWS / "release-staleness.yml").read_text(encoding="utf-8")
    assert "release_staleness.py" in body


# ---------------------------------------------------------------------------
# Cross-workflow wiring. A workflow_run trigger names its upstream by DISPLAY
# NAME, not by filename, so renaming a workflow silently severs the link: no
# error, no warning, the downstream job simply never fires again.
# ---------------------------------------------------------------------------

def _declared_workflow_names() -> dict:
    return {
        (_wf(p.name).get("name") or p.stem): p.name
        for p in sorted(WORKFLOWS.glob("*.yml"))
    }


def test_every_watched_workflow_name_still_exists() -> None:
    """claude-on-ci-failure.yml watches upstreams by display name. A rename makes
    that entry dead weight that looks alive."""
    declared = _declared_workflow_names()
    watched = _triggers(_wf("claude-on-ci-failure.yml"))["workflow_run"]["workflows"]
    unknown = [n for n in watched if n not in declared]
    assert not unknown, (
        f"claude-on-ci-failure.yml watches workflows that do not exist: {unknown}. "
        f"Declared names: {sorted(declared)}"
    )


def test_the_ci_triage_never_watches_itself_or_a_publish_workflow() -> None:
    """Watching itself is an infinite loop; watching a publish is spend on a release."""
    watched = set(_triggers(_wf("claude-on-ci-failure.yml"))["workflow_run"]["workflows"])
    forbidden = {
        _wf("claude-on-ci-failure.yml")["name"],
        _wf("publish-npm.yml")["name"],
        _wf("publish-npmjs.yml")["name"],
    }
    assert not (watched & forbidden), f"CI triage must not watch {watched & forbidden}"


def test_the_claude_workflows_skip_cleanly_without_their_api_key() -> None:
    """ANTHROPIC_API_KEY has never existed on this repo. Before the guard,
    claude-on-ci-failure went red on every genuine main failure — a permanent red
    run parked beside the real one, which is how a CI board stops being read."""
    for name in ("claude.yml", "claude-on-ci-failure.yml"):
        wf = _wf(name)
        job = next(iter(wf["jobs"].values()))
        assert job.get("env", {}).get("HAVE_ANTHROPIC_KEY"), (
            f"{name}: the job must lift ANTHROPIC_API_KEY into env "
            "(`secrets` is rejected in both job-level and step-level `if:`)"
        )
        step = next(
            s for s in job["steps"] if "claude-code-action" in str(s.get("uses", ""))
        )
        assert "HAVE_ANTHROPIC_KEY" in str(step.get("if", "")), (
            f"{name}: the claude-code-action step must be guarded on the key"
        )


# ── Trusted publishing (OIDC) ───────────────────────────────────────────────
# npmjs publishing runs on a short-lived, workflow-scoped GitHub OIDC token
# rather than a stored NPM_TOKEN. Each assertion below pins one precondition
# that fails CONFUSINGLY at publish time if it drifts — the point is that the
# failure names itself here instead of surfacing as an auth error weeks later,
# which is exactly how npmjs sat at 0.36.1 for seven weeks.
# https://docs.npmjs.com/trusted-publishers/

NPMJS_WORKFLOW = "publish-npmjs.yml"


def test_npmjs_publish_carries_no_long_lived_token() -> None:
    """A token in the environment takes precedence over OIDC.

    This is the subtle one: `id-token: write` can be set and correct while a
    lingering NODE_AUTH_TOKEN quietly wins, so the workflow looks like trusted
    publishing and never once exercises it.
    """
    text = (WORKFLOWS / NPMJS_WORKFLOW).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "NODE_AUTH_TOKEN" in line and not line.lstrip().startswith("#")
    ]
    assert offenders == [], (
        f"{NPMJS_WORKFLOW} sets NODE_AUTH_TOKEN, which takes precedence over OIDC "
        f"and silently disables trusted publishing: {offenders}"
    )


def test_npmjs_publish_requests_an_oidc_token() -> None:
    """Without id-token: write there is no OIDC token, and npm reports a 401."""
    wf = _wf(NPMJS_WORKFLOW)
    perms = wf.get("permissions") or {}
    assert perms.get("id-token") == "write", (
        f"{NPMJS_WORKFLOW} must declare 'permissions: id-token: write' — without it "
        "npm cannot exchange an OIDC token and fails with a confusing auth error"
    )


def test_npmjs_publish_meets_the_documented_version_floors() -> None:
    """npm >= 11.5.1 and Node >= 22.14.0.

    Node 22 ships npm 10.x, so relying on the bundled npm silently lands under
    the floor. The workflow must install npm explicitly.
    """
    text = (WORKFLOWS / NPMJS_WORKFLOW).read_text(encoding="utf-8")
    assert "npm@^11.5.1" in text, (
        f"{NPMJS_WORKFLOW} must install npm >= 11.5.1 explicitly; the version "
        "bundled with Node is not guaranteed to clear the trusted-publishing floor"
    )


def test_npmjs_publish_runs_on_a_github_hosted_runner() -> None:
    """npm only honours trusted publishing from GitHub-hosted runners."""
    wf = _wf(NPMJS_WORKFLOW)
    for job_name, job in (wf.get("jobs") or {}).items():
        runner = job.get("runs-on", "")
        assert isinstance(runner, str) and runner.startswith("ubuntu-"), (
            f"job {job_name!r} runs on {runner!r}; npm trusted publishing is only "
            "supported on GitHub-hosted runners"
        )
