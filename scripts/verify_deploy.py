#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Post-deploy verification gate for the build-loop plugin (Vercel web deploys).

Sibling of scripts/runtime_smoke.py — same envelope contract (`status`, `findings`,
`evidence`) plus deploy-specific fields (`deployment_url`, `state`). Where
runtime_smoke.py validates a *local dev server* render, this validates that a
*production deployment* on Vercel actually went Ready and that the prod root +
each changed route respond healthily.

Resolution:
  1. Detect a Vercel link (.vercel/project.json or vercel.json) in --workdir.
     Absent  -> {"status": "skipped", "reason": "no vercel link"}.
  2. Resolve the latest production deployment URL (`vercel ls --environment
     production --format json`, fall back to `vercel ls`/`vercel inspect`).
  3. Poll `vercel inspect <url> --format json` until a terminal state
     (Ready | Error | Canceled) or the timeout cap.
  4. curl the prod root + each --changed-route.
  5. Classify:
       Ready + root 200 + every changed route in {200,401,403,3xx}  -> pass
       Error/Canceled OR build failure OR any changed route == 500   -> fail
       vercel CLI missing / not authed / transient infra             -> skipped

KEY HEURISTIC (encoded + commented below): an auth-gated 401/403 on a protected
route is HEALTHY — it proves the serverless function deployed and is running, it
just (correctly) refused an unauthenticated probe. Only a 5xx or a build/deploy
error means the deployment is actually broken. Treating 401/403 as failure would
make every authenticated app fail its own deploy gate. This mirrors
runtime_smoke.py, which also treats protected-route 401/403 as a healthy render.

Infra problems (CLI missing, not logged in, network) NEVER hard-fail the build —
they return `skipped` with a reason, exactly like runtime_smoke.py's
`no_adapter_matched`. The deploy gate must not block a build on tooling state.

CLI:
    python3 scripts/verify_deploy.py \\
        [--workdir <path>] \\
        [--changed-route /api/foo --changed-route /dashboard ...] \\
        [--poll-interval 20] [--timeout 600] \\
        [--json]

Exit codes:
    0 — pass | skipped
    1 — fail (deployment errored / canceled / a changed route 5xx'd)
    2 — runner error (malformed input)

Stdlib only (argparse, json, pathlib, subprocess, time, sys, urllib).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Terminal Vercel deployment states (readyState / state field, upper-cased).
_TERMINAL_OK = {"READY"}
_TERMINAL_BAD = {"ERROR", "CANCELED"}
_TERMINAL = _TERMINAL_OK | _TERMINAL_BAD

# A route response is HEALTHY when its HTTP status is in this set.
# 401/403 are deliberately healthy: an auth-gated function that refuses an
# unauthenticated probe is *deployed and running* — that is exactly what this
# gate is verifying. 3xx (redirect to login / canonical host) is also healthy.
# Only 5xx (function crash) or a missing/error deploy means broken.
_HEALTHY_ROUTE_STATUSES = {200, 301, 302, 303, 307, 308, 401, 403}


def _has_vercel_link(workdir: Path) -> bool:
    """True if the workdir is Vercel-linked (project.json) or carries vercel.json."""
    return (workdir / ".vercel" / "project.json").exists() or (
        workdir / "vercel.json"
    ).exists()


def _run_vercel(args: list[str], workdir: Path, timeout: int = 60):
    """Run `vercel <args>` in workdir. Returns (returncode, stdout, stderr) or
    raises FileNotFoundError when the CLI is absent."""
    proc = subprocess.run(
        ["vercel", *args],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _resolve_production_url(workdir: Path):
    """Resolve the latest production deployment URL.

    Returns (url, None) on success or (None, reason) when it can't be resolved.
    """
    rc, out, err = _run_vercel(
        ["ls", "--environment", "production", "--format", "json", "--yes"],
        workdir,
    )
    if rc != 0:
        # Auth / link problems surface here. Caller maps this to `skipped`.
        return None, f"vercel ls failed (rc={rc}): {(err or out).strip()[:200]}"

    url = _first_url_from_ls(out)
    if url:
        return url, None
    return None, "no production deployment found in `vercel ls` output"


def _first_url_from_ls(stdout: str):
    """Extract the newest deployment URL from `vercel ls --format json` output.

    The JSON format has shifted across CLI majors, so accept several shapes:
      - {"deployments": [{"url": "..."} | {"name": "..."}]}
      - [{"url": "..."}]
      - newline-delimited JSON objects
    Always return the first (newest) URL, normalized to https://.
    """
    stdout = stdout.strip()
    if not stdout:
        return None

    candidates: list = []
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, dict) and isinstance(parsed.get("deployments"), list):
            candidates = parsed["deployments"]
        elif isinstance(parsed, list):
            candidates = parsed
        elif isinstance(parsed, dict):
            candidates = [parsed]
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("url") or entry.get("name")
        if raw:
            return raw if raw.startswith("http") else f"https://{raw}"
    return None


def _inspect_state(url: str, workdir: Path):
    """Run `vercel inspect <url> --format json`. Returns (state, raw_dict, reason).

    state is the upper-cased readyState/state, or None if it couldn't be read.
    """
    rc, out, err = _run_vercel(
        ["inspect", url, "--format", "json"], workdir
    )
    if rc != 0:
        return None, {}, f"vercel inspect failed (rc={rc}): {(err or out).strip()[:200]}"
    try:
        data = json.loads(out.strip())
    except json.JSONDecodeError:
        return None, {}, "vercel inspect did not return JSON"
    state = (
        data.get("readyState")
        or data.get("state")
        or data.get("status")
        or ""
    )
    return str(state).upper() or None, data, None


def _poll_until_terminal(url: str, workdir: Path, poll_interval: int, timeout: int):
    """Poll `vercel inspect` until a terminal state or the timeout cap.

    Returns (state, raw_dict, reason). state is one of _TERMINAL on success;
    None with a reason on infra failure or timeout.
    """
    deadline = time.monotonic() + timeout
    last_state = None
    last_raw: dict = {}
    while True:
        state, raw, reason = _inspect_state(url, workdir)
        if reason is not None:
            return None, {}, reason
        last_state, last_raw = state, raw
        if state in _TERMINAL:
            return state, raw, None
        if time.monotonic() >= deadline:
            return None, last_raw, (
                f"timed out after {timeout}s polling deployment state "
                f"(last state: {last_state or 'unknown'})"
            )
        time.sleep(poll_interval)


def _probe(url: str, timeout: int = 20):
    """HTTP GET a URL, following no redirects. Returns (status_int, note).

    A redirect is reported by its 3xx code (healthy). Connection errors return
    (0, reason) and are treated as a finding by the caller.
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: D401, ANN002, ANN003
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "build-loop-verify-deploy"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        # 401/403/5xx all arrive here; the status is the signal.
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"request failed: {exc}"


def verify(
    workdir: Path,
    changed_routes: list[str],
    poll_interval: int,
    timeout: int,
) -> dict:
    """Core verification. Returns the JSON envelope (status/findings/evidence/...)."""
    # --- Gate 0: Vercel link present? ------------------------------------
    if not _has_vercel_link(workdir):
        return {
            "status": "skipped",
            "reason": "no vercel link",
            "deployment_url": None,
            "state": None,
            "findings": [],
            "evidence": [],
        }

    # --- Gate 1: CLI available? (infra -> skipped, never fail) -----------
    if shutil.which("vercel") is None:
        return {
            "status": "skipped",
            "reason": "vercel CLI not found on PATH",
            "deployment_url": None,
            "state": None,
            "findings": [],
            "evidence": [],
        }

    # --- Resolve production deployment URL -------------------------------
    try:
        url, reason = _resolve_production_url(workdir)
    except FileNotFoundError:
        return {
            "status": "skipped",
            "reason": "vercel CLI not found on PATH",
            "deployment_url": None,
            "state": None,
            "findings": [],
            "evidence": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "skipped",
            "reason": "vercel ls timed out (transient infra)",
            "deployment_url": None,
            "state": None,
            "findings": [],
            "evidence": [],
        }
    if url is None:
        # Auth failure / no deploy yet — infra, not a build defect.
        return {
            "status": "skipped",
            "reason": reason or "could not resolve production deployment",
            "deployment_url": None,
            "state": None,
            "findings": [],
            "evidence": [],
        }

    # --- Poll deployment to a terminal state -----------------------------
    try:
        state, raw, reason = _poll_until_terminal(url, workdir, poll_interval, timeout)
    except FileNotFoundError:
        return {
            "status": "skipped",
            "reason": "vercel CLI not found on PATH",
            "deployment_url": url,
            "state": None,
            "findings": [],
            "evidence": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "skipped",
            "reason": "vercel inspect timed out (transient infra)",
            "deployment_url": url,
            "state": None,
            "findings": [],
            "evidence": [],
        }

    evidence = [f"deployment {url}"]
    if state is None:
        # Could not read a terminal state (infra / poll timeout) -> skipped.
        return {
            "status": "skipped",
            "reason": reason or "could not determine deployment state",
            "deployment_url": url,
            "state": None,
            "findings": [],
            "evidence": evidence,
        }

    evidence.append(f"state={state}")

    # --- Terminal-bad state -> fail (build error / canceled) -------------
    if state in _TERMINAL_BAD:
        return {
            "status": "fail",
            "reason": f"deployment terminal state {state}",
            "deployment_url": url,
            "state": state,
            "findings": [
                {
                    "route": "(deployment)",
                    "render_status": state,
                    "finding": f"Vercel deployment ended in {state} — build or "
                    f"function error. Inspect build logs: "
                    f"`vercel inspect {url} --logs`.",
                }
            ],
            "evidence": evidence,
        }

    # --- state is READY: probe prod root + each changed route ------------
    findings: list = []

    root_status, root_note = _probe(url)
    evidence.append(f"GET / -> {root_status or 'ERR'}")
    if root_status != 200:
        findings.append(
            {
                "route": "/",
                "render_status": root_status,
                "finding": (
                    f"Production root did not return 200 (got "
                    f"{root_status or 'connection error'}{(': ' + root_note) if root_note else ''}). "
                    "A Ready deployment whose root is not 200 indicates a "
                    "runtime/render failure."
                ),
            }
        )

    for route in changed_routes:
        route_path = route if route.startswith("/") else f"/{route}"
        route_url = url.rstrip("/") + route_path
        status, note = _probe(route_url)
        evidence.append(f"GET {route_path} -> {status or 'ERR'}")
        if status in _HEALTHY_ROUTE_STATUSES:
            # 401/403 here is the encoded heuristic: auth gate active ==
            # function deployed & running == healthy. No finding.
            continue
        if status == 0:
            findings.append(
                {
                    "route": route_path,
                    "render_status": status,
                    "finding": f"Changed route unreachable ({note}).",
                }
            )
        elif 500 <= status <= 599:
            findings.append(
                {
                    "route": route_path,
                    "render_status": status,
                    "finding": (
                        f"Changed route returned {status} — server/function "
                        "error on a route this build touched. This is a real "
                        "deploy failure (contrast: 401/403 would be a healthy "
                        "auth gate)."
                    ),
                }
            )
        else:
            # 404 / 4xx that isn't an auth gate: report but it's a softer
            # signal than 5xx. Still a finding so Iterate can judge.
            findings.append(
                {
                    "route": route_path,
                    "render_status": status,
                    "finding": (
                        f"Changed route returned {status} (not in the healthy "
                        "set {200,301-308,401,403}). Verify routing/export."
                    ),
                }
            )

    # Classification: any 5xx / unreachable changed route OR non-200 root
    # whose cause is a hard error -> fail. Else pass.
    hard_fail = any(
        f["render_status"] == 0 or (isinstance(f["render_status"], int) and 500 <= f["render_status"] <= 599)
        for f in findings
    )
    root_bad = root_status != 200
    if hard_fail or root_bad:
        return {
            "status": "fail",
            "reason": "Ready deployment but root or a changed route is broken",
            "deployment_url": url,
            "state": state,
            "findings": findings,
            "evidence": evidence,
        }

    return {
        "status": "pass",
        "reason": "deployment Ready; root 200; changed routes healthy "
        "(200/3xx/401/403)",
        "deployment_url": url,
        "state": state,
        "findings": findings,  # may carry soft 4xx notes; status is still pass
        "evidence": evidence,
    }


def _emit(envelope: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope, indent=2))
        return
    status = envelope.get("status", "unknown")
    reason = envelope.get("reason", "")
    url = envelope.get("deployment_url") or "n/a"
    state = envelope.get("state") or "n/a"
    print(f"verify-deploy: status={status} state={state} url={url}" + (f" reason={reason}" if reason else ""))
    for f in envelope.get("findings", []):
        print(f"  {str(f.get('render_status')):>4}  {f.get('route')}  {f.get('finding','')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post-deploy verification gate — verify a Vercel production "
        "deployment is Ready and serving the changed routes.",
    )
    parser.add_argument("--workdir", default=None, help="Project root (defaults to cwd).")
    parser.add_argument(
        "--changed-route",
        action="append",
        dest="changed_routes",
        default=[],
        metavar="ROUTE",
        help="A route path the build changed (e.g. /api/foo). Repeatable.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=20,
        help="Seconds between `vercel inspect` polls (default 20).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Max seconds to wait for a terminal deployment state (default 600).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit JSON envelope to stdout.",
    )

    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve() if args.workdir else Path.cwd()

    try:
        envelope = verify(
            workdir,
            args.changed_routes,
            poll_interval=max(1, args.poll_interval),
            timeout=max(1, args.timeout),
        )
    except Exception as exc:  # noqa: BLE001
        # Per the gate contract: infra/runner trouble is non-fatal. Surface a
        # skipped envelope rather than blocking the build on tooling state.
        envelope = {
            "status": "skipped",
            "reason": f"verify_deploy internal error (treated as infra): {exc}",
            "deployment_url": None,
            "state": None,
            "findings": [],
            "evidence": [],
        }

    _emit(envelope, args.as_json)

    status = envelope.get("status")
    if status == "fail":
        return 1
    # pass | skipped -> 0 (never hard-fail the build on infra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
