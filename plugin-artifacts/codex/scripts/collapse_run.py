#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""collapse_run.py — End-of-run branch/worktree cleanup for build-loop.

Reads the ref registries from .build-loop/state.json for the selected run.
Live mutation requires explicit owner release, a verified branch-scoped bundle,
a prepared receipt, and immediate safety/OID rechecks. Then for each ref:

  - MERGED into main          → delete branch (+ worktree folder if any)
  - UNMERGED + review_hold    → remove worktree folder only; keep branch
  - UNMERGED + no review_hold → remove worktree folder only; surface branch
                                 for operator keep/discard decision

CONTRACT NOTE: this script NEVER runs `git merge`. Merging the winning
branch onto main is the orchestrator's exclusive responsibility (it does so
before calling collapse). After a successful merge, the branch reads as
MERGED here and is deleted cleanly. This separation ensures collapse is
always safe to call as an idempotent cleanup step.

CLI:
  collapse_run.py --workdir <repo> [--run-id latest|<id>] [--branch <name>]
      [--strict] [--owner-released] [--tool <owner-host>]
      [--session-id <owner-session>]
      [--dry-run] [--json]

SessionStart and background discovery never supply owner release. They report
candidates only; this module is the sole destructive authority.

Exit codes:
  0 — success (non-strict mode remains fail-soft for compatibility)
  1 — hard failure, or strict closeout without a verified terminal receipt
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from atomic_io import LockedFile, atomic_write_bytes
except ImportError:  # package import: python3 -m scripts.worktree_reaper
    from scripts.atomic_io import LockedFile, atomic_write_bytes

try:
    from rally_point import actor_identity
    from rally_point import backend_adapter as rally_backend
except ImportError:  # package import
    from scripts.rally_point import actor_identity
    from scripts.rally_point import backend_adapter as rally_backend


STATE_REL = Path(".build-loop") / "state.json"
RECEIPT_DIR_REL = Path(".build-loop") / "branch-closeout"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _git_available(workdir: Path) -> bool:
    try:
        r = _git(workdir, "rev-parse", "--git-dir", check=False)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _current_branch(workdir: Path) -> str | None:
    """Return the branch currently checked out in the primary worktree, or None."""
    try:
        r = _git(workdir, "symbolic-ref", "--short", "HEAD", check=False)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _is_ancestor(workdir: Path, branch: str, target: str = "main") -> bool | None:
    """Return True if branch is an ancestor of target (i.e. fully merged).

    Returns None on error (unknown branch, detached HEAD, etc.).
    """
    try:
        r = _git(workdir, "merge-base", "--is-ancestor", branch, target, check=False)
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        # rc=128: branch or target unknown
        return None
    except Exception:
        return None


def _delete_branch(workdir: Path, branch: str) -> str | None:
    """Delete branch. Returns None on success, error string on failure."""
    try:
        r = _git(workdir, "branch", "-D", branch, check=False)
        if r.returncode == 0:
            return None
        return (r.stderr or r.stdout).strip() or f"git branch -D {branch} exited {r.returncode}"
    except Exception as exc:
        return str(exc)


def _branch_oid(workdir: Path, branch: str) -> str | None:
    """Return the exact local branch object ID, or None when absent."""
    try:
        r = _git(workdir, "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _delete_branch_expected(workdir: Path, branch: str, expected_oid: str) -> str | None:
    """Delete only the expected, merged, non-checked-out branch.

    ``git update-ref -d`` supports an atomic expected-OID check but bypasses
    Git's checked-out-branch protection. Recheck the OID, then use safe
    ``git branch -d`` so a last-moment worktree checkout is an operative veto.
    A concurrent unmerged ref move is likewise rejected by ``-d``.
    """
    try:
        current_oid = _branch_oid(workdir, branch)
        if current_oid != expected_oid:
            return f"expected {expected_oid}, found {current_oid}"
        records, records_error = _worktree_records(workdir)
        if records_error:
            return f"worktree registration unavailable: {records_error}"
        registered_paths = sorted(
            path
            for path, record in records.items()
            if record.get("branch") == branch
        )
        if registered_paths:
            return "branch is checked out at " + ", ".join(registered_paths)
        r = _git(workdir, "branch", "-d", branch, check=False)
        if r.returncode == 0:
            return None
        return (r.stderr or r.stdout).strip() or (
            f"git branch -d {branch} exited {r.returncode}"
        )
    except Exception as exc:
        return str(exc)


def _worktree_claim_event_ids(
    claims: object,
    wt_relpath: str,
    *,
    native_tool: str,
    protocol_session_id: str,
) -> list[str]:
    """Return exact-session claim ids scoped beneath one removed worktree."""
    # Match on a path BOUNDARY, not a bare substring: the worktree folder
    # itself (exact) or any file beneath it (prefix + "/"). A bare substring
    # would false-match a sibling worktree whose name shares this prefix
    # (e.g. ".../rally-lifecycle" vs ".../rally-lifecycle-2/foo.py") and
    # over-release another worktree's claim — violating the never-touch-a-
    # different-path's-claim guard.
    prefix = f"file:{wt_relpath}"
    ids: list[str] = []
    seen: set[str] = set()

    def _walk(node) -> None:
        if isinstance(node, dict):
            fact = node.get("fact") if isinstance(node.get("fact"), dict) else node
            if (
                fact.get("kind") == "claim"
                and fact.get("tool") == native_tool
                and fact.get("from_session_id") == protocol_session_id
            ):
                scope = fact.get("scope")
                scopes = scope if isinstance(scope, list) else ([scope] if scope else [])
                if any(
                    isinstance(s, str)
                    and (s == prefix or s.startswith(prefix + "/"))
                    for s in scopes
                ):
                    ev = fact.get("event_id")
                    if isinstance(ev, str) and ev and ev not in seen:
                        seen.add(ev)
                        ids.append(ev)
            # A wrapped fact was already examined; do not visit it twice.
            if fact is node:
                for v in node.values():
                    _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(claims)
    return ids


def _protocol_session_id(
    context: rally_backend.BackendContext,
    *,
    native_tool: str,
    session_id: str,
) -> str | None:
    """Resolve Rally's exact managed-session generation through typed whoami."""
    result = rally_backend.invoke_native(
        context,
        ["whoami", "--json", "--tool", native_tool],
        expected_schema="agent-rally.command.whoami.v1",
        tool=native_tool,
        session_id=session_id,
    )
    if not result.ok or not isinstance(result.payload, dict):
        return None
    data = result.payload.get("data")
    outer = data.get("whoami") if isinstance(data, dict) else None
    whoami = (
        outer.get("whoami")
        if isinstance(outer, dict) and isinstance(outer.get("whoami"), dict)
        else outer
    )
    identity = whoami.get("session_identity") if isinstance(whoami, dict) else None
    protocol_session_id = (
        identity.get("session_id") if isinstance(identity, dict) else None
    )
    if not isinstance(protocol_session_id, str) or not protocol_session_id.strip():
        return None
    return protocol_session_id


def _exact_release_receipt(
    result: rally_backend.NativeResult,
    *,
    native_tool: str,
    protocol_session_id: str,
    event_id: str,
) -> bool:
    """Require the release fact itself to use the typed whoami generation."""
    if not result.ok or not isinstance(result.payload, dict):
        return False
    data = result.payload.get("data")
    say = data.get("say") if isinstance(data, dict) else None
    fact = say.get("fact") if isinstance(say, dict) else None
    return bool(
        isinstance(fact, dict)
        and fact.get("kind") == "release"
        and fact.get("tool") == native_tool
        and fact.get("from_session_id") == protocol_session_id
        and (fact.get("ref") == event_id or fact.get("ref_id") == event_id)
        and type(fact.get("seq")) is int
        and fact["seq"] > 0
    )


def _release_worktree_claims(
    workdir: Path,
    path: str,
    *,
    session_id: str | None,
    tool: str | None,
) -> int:
    """Best-effort release of rally file-claims orphaned by a removed worktree.

    Root cause this addresses: when a dispatch worktree folder is deleted, its
    file-scope claims (``file:.claude/worktrees/agent-*``) were never released,
    so they orphan the instant the backing folder vanishes (84 dead-worktree
    claims observed live for an already-empty .claude/worktrees/).

    Fire-and-forget: swallows all errors and never raises, so a Rally outage
    or CLI error never breaks worktree teardown. Native reads and releases go
    through the typed backend adapter. A claim must match the exact resolved
    actor, exact typed-whoami session generation, and worktree path boundary; ambiguous or
    partial outcomes stop the release loop without retrying. Local fallback is
    unchanged (no embedded cleanup mutation). Returns the count released.
    """
    try:
        try:
            wt_relpath = str(Path(path).resolve().relative_to(workdir.resolve()))
        except ValueError:
            return 0
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or not isinstance(tool, str)
            or not tool.strip()
        ):
            return 0
        raw_session_id = session_id.strip()
        identity = actor_identity.resolve_identity(
            actor_identity.base_tool(tool),
            raw_session_id,
        )
        context = rally_backend.resolve_context(workdir)
        if not context.native:
            return 0
        protocol_session_id = _protocol_session_id(
            context,
            native_tool=identity.native_tool,
            session_id=identity.session_id,
        )
        if protocol_session_id is None:
            return 0
        snapshot = rally_backend.room_snapshot(
            context,
            actor=identity.native_tool,
        )
        if not snapshot.ok:
            return 0
        claims = rally_backend.native_room_summary(snapshot).get("active_claims")
        event_ids = _worktree_claim_event_ids(
            claims,
            wt_relpath,
            native_tool=identity.native_tool,
            protocol_session_id=protocol_session_id,
        )
        released = 0
        for ev in event_ids:
            result = rally_backend.invoke_native(
                context,
                [
                    "say",
                    "release",
                    "--json",
                    "--tool",
                    identity.native_tool,
                    "--ref",
                    ev,
                    "--subject",
                    "worktree teardown: releasing orphaned claim",
                ],
                expected_schema="agent-rally.command.say.v1",
                tool=identity.native_tool,
                session_id=identity.session_id,
                mutating=True,
            )
            if not _exact_release_receipt(
                result,
                native_tool=identity.native_tool,
                protocol_session_id=protocol_session_id,
                event_id=ev,
            ):
                break
            released += 1
        return released
    except Exception:  # noqa: BLE001 — teardown must never break on rally errors
        return 0


def _remove_worktree(
    workdir: Path,
    path: str,
    *,
    session_id: str | None = None,
    tool: str | None = None,
) -> str | None:
    """Remove a worktree folder. Returns None on success, error string on failure."""
    wt_path = Path(path)
    if not wt_path.exists():
        # Idempotent: already gone is fine. Still release any orphaned claims
        # left behind from a prior removal that predates this fix.
        try:
            _release_worktree_claims(
                workdir,
                path,
                session_id=session_id,
                tool=tool,
            )
        except Exception:  # noqa: BLE001 — teardown must never break on rally errors
            pass
        return None
    try:
        # Safety checks run immediately before this call. Do not use force:
        # double-force overrides Git's lock and can delete a live terminal cwd.
        r = _git(workdir, "worktree", "remove", str(wt_path), check=False)
        if r.returncode == 0:
            try:
                _release_worktree_claims(
                    workdir,
                    path,
                    session_id=session_id,
                    tool=tool,
                )
            except Exception:  # noqa: BLE001 — teardown must never break on rally errors
                pass
            return None
        return (r.stderr or r.stdout).strip() or f"git worktree remove exited {r.returncode}"
    except Exception as exc:
        return str(exc)


# ---------------------------------------------------------------------------
# State loading
# ---------------------------------------------------------------------------

def _load_state(workdir: Path) -> dict[str, Any]:
    state_path = workdir / STATE_REL
    if not state_path.exists():
        raise SystemExit(f"state.json not found at {state_path}")
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"state.json unparseable: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("state.json root must be a JSON object")
    return data


def _write_state(workdir: Path, state: dict[str, Any]) -> None:
    """Atomically replace state.json.

    The caller holds ``LockedFile(state_path)`` for live collapse. Keeping the
    primitive lock-free avoids a recursive flock while preserving atomic bytes.
    """
    state_path = workdir / STATE_REL
    atomic_write_bytes(state_path, (json.dumps(state, indent=2) + "\n").encode())


def _canonical_run_identity(row: dict[str, Any] | None) -> str | None:
    """Canonical identity for current and legacy run/execution rows."""
    if not isinstance(row, dict):
        return None
    for key in ("build_loop_id", "run_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _row_identities(row: dict[str, Any] | None) -> set[str]:
    if not isinstance(row, dict):
        return set()
    return {
        value
        for key in ("build_loop_id", "run_id", "id")
        if isinstance((value := row.get(key)), str) and value
    }


def _rows_share_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return bool(_row_identities(left) & _row_identities(right))


def _pick_run(state: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    runs = state.get("runs")
    if not isinstance(runs, list) or not runs:
        return None
    if run_id == "latest":
        return runs[-1]
    for run in reversed(runs):
        if isinstance(run, dict) and run_id in _row_identities(run):
            return run
    return None


def _execution_for_run(
    state: dict[str, Any],
    run: dict[str, Any],
    *,
    allow_anonymous_latest: bool = False,
) -> dict[str, Any] | None:
    """Resolve active or archived execution metadata for exactly one run."""
    raw = state.get("execution")
    if isinstance(raw, dict) and raw:
        if _rows_share_identity(run, raw):
            return raw
        if allow_anonymous_latest and not _row_identities(run) and not _row_identities(raw):
            return raw

    history = state.get("historicalExecutions")
    if isinstance(history, list):
        for entry in reversed(history):
            if isinstance(entry, dict) and _rows_share_identity(run, entry):
                return entry
    return None


def _claim_release_session_id(
    execution: dict[str, Any] | None,
    explicit: str | None,
) -> tuple[str | None, str | None]:
    """Return destructive cleanup identity only from explicit/selected state."""
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip(), "cli"
    value = execution.get("current_session_id") if isinstance(execution, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip(), "execution.current_session_id"
    return None, None


def _claim_release_tool(
    execution: dict[str, Any] | None,
    explicit: str | None,
) -> tuple[str | None, str | None]:
    """Return owner host only from explicit/selected state, never current host."""
    if isinstance(explicit, str) and explicit.strip():
        return actor_identity.base_tool(explicit), "cli"
    value = execution.get("started_by_tool") if isinstance(execution, dict) else None
    if isinstance(value, str) and value.strip():
        return actor_identity.base_tool(value), "execution.started_by_tool"
    return None, None


# ---------------------------------------------------------------------------
# Worktree safety
# ---------------------------------------------------------------------------

def _worktree_records(workdir: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Return real Git worktree records, including the porcelain lock flag."""
    try:
        r = _git(workdir, "worktree", "list", "--porcelain", check=False)
    except Exception as exc:
        return {}, str(exc)
    if r.returncode != 0:
        return {}, (r.stderr or r.stdout).strip() or "git worktree list failed"

    records: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None

    def _finish() -> None:
        nonlocal current
        if current and current.get("path"):
            records[str(Path(current["path"]).resolve())] = current
        current = None

    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            _finish()
            current = {
                "path": line[len("worktree "):].strip(),
                "branch": None,
                "locked": False,
            }
        elif current is not None and line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = (
                ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
            )
        elif current is not None and line.startswith("locked"):
            current["locked"] = True
            current["lock_reason"] = line[len("locked"):].strip() or None
        elif line == "":
            _finish()
    _finish()
    return records, None


def _live_process_cwds() -> tuple[list[dict[str, Any]], str | None]:
    """Return observable live process CWDs; unknown platforms fail closed."""
    proc_root = Path("/proc")
    if proc_root.is_dir():
        rows: list[dict[str, Any]] = []
        try:
            entries = list(proc_root.iterdir())
        except OSError as exc:
            return [], f"cannot scan /proc: {exc}"
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                cwd = (entry / "cwd").resolve(strict=True)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            rows.append({"pid": int(entry.name), "cwd": str(cwd)})
        return rows, None

    lsof = shutil.which("lsof")
    if not lsof:
        return [], "live-CWD sensor unavailable: neither /proc nor lsof is available"
    try:
        r = subprocess.run(
            [lsof, "-a", "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"live-CWD sensor failed: {exc}"
    if r.returncode not in (0, 1):
        return [], (r.stderr or r.stdout).strip() or f"lsof exited {r.returncode}"

    rows: list[dict[str, Any]] = []
    pid: int | None = None
    saw_cwd = False
    for line in r.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
            saw_cwd = False
        elif line == "fcwd":
            saw_cwd = True
        elif saw_cwd and pid is not None and line.startswith("n"):
            rows.append({"pid": pid, "cwd": line[1:]})
            saw_cwd = False
    return rows, None


def _path_contains_cwd(path: Path, cwd: Path) -> bool:
    return cwd == path or path in cwd.parents


def inspect_worktree_safety(
    workdir: Path,
    path: str | None,
    branch: str,
    *,
    live_cwds: list[dict[str, Any]] | None = None,
    live_cwd_error: str | None = None,
) -> dict[str, Any]:
    """Fail-closed safety evidence for a candidate worktree."""
    records, records_error = _worktree_records(workdir)
    if records_error:
        return {"safe": False, "reason": records_error, "path": path}
    branch_paths = sorted(
        record_path
        for record_path, record in records.items()
        if record.get("branch") == branch
    )
    if not path:
        if branch_paths:
            return {
                "safe": False,
                "reason": "branch is checked out but the run ledger has no worktree path",
                "path": None,
                "registered_paths": branch_paths,
            }
        return {"safe": True, "reason": "branch-only ref is not checked out", "path": None}

    candidate = Path(path).resolve()
    if not candidate.exists():
        if branch_paths:
            return {
                "safe": False,
                "reason": "recorded path is absent but branch is checked out elsewhere",
                "path": str(candidate),
                "registered_paths": branch_paths,
            }
        return {
            "safe": True,
            "reason": "worktree path already absent and branch is not checked out",
            "path": str(candidate),
        }

    record = records.get(str(candidate))
    if record is None:
        return {
            "safe": False,
            "reason": "existing path is not a registered Git worktree",
            "path": str(candidate),
        }
    if record.get("branch") != branch:
        return {
            "safe": False,
            "reason": f"registered branch mismatch: {record.get('branch')!r}",
            "path": str(candidate),
        }
    if record.get("locked"):
        return {
            "safe": False,
            "reason": "Git worktree is locked",
            "path": str(candidate),
            "lock_reason": record.get("lock_reason"),
        }

    status = _git(candidate, "status", "--porcelain", check=False)
    if status.returncode != 0:
        return {
            "safe": False,
            "reason": (status.stderr or status.stdout).strip() or "worktree status failed",
            "path": str(candidate),
        }
    if status.stdout.strip():
        return {
            "safe": False,
            "reason": "worktree is dirty",
            "path": str(candidate),
        }

    if live_cwds is None and live_cwd_error is None:
        live_cwds, live_cwd_error = _live_process_cwds()
    if live_cwd_error:
        return {
            "safe": False,
            "reason": live_cwd_error,
            "path": str(candidate),
        }
    owners = []
    for row in live_cwds or []:
        try:
            cwd = Path(str(row["cwd"])).resolve()
        except (KeyError, OSError, RuntimeError):
            continue
        if _path_contains_cwd(candidate, cwd):
            owners.append(row)
    if owners:
        return {
            "safe": False,
            "reason": "live process cwd is inside worktree",
            "path": str(candidate),
            "owners": owners,
        }
    return {
        "safe": True,
        "reason": "registered, unlocked, clean, and no live process cwd",
        "path": str(candidate),
    }


# ---------------------------------------------------------------------------
# Ref normalization
# ---------------------------------------------------------------------------

def _default_close_criteria(
    branch: str,
    merge_target: str,
    kind: str,
    review_hold: bool,
) -> list[str]:
    criteria = [f"{branch} is merged into {merge_target}"]
    if kind == "worktree":
        criteria.append("worktree folder is removed from .build-loop/worktrees")
    if review_hold:
        criteria.append("human review disposition is recorded before branch deletion")
    else:
        criteria.append("branch is deleted after merge")
    return criteria


def _ensure_ledger_ref(run: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    created_refs = run.setdefault("createdRefs", [])
    for entry in created_refs:
        if isinstance(entry, dict) and entry.get("branch") == ref["branch"]:
            return entry

    kind = "worktree" if ref.get("path") else "branch"
    entry = {
        "kind": kind,
        "path": ref.get("path"),
        "branch": ref["branch"],
        "merge_target": ref.get("merge_target", "main"),
        "purpose": ref.get("summary", ""),
        "close_criteria": _default_close_criteria(
            ref["branch"],
            ref.get("merge_target", "main"),
            kind,
            bool(ref.get("review_hold", False)),
        ),
        "status": "open",
        "close_reason": None,
        "review_hold": bool(ref.get("review_hold", False)),
        "created_ts": _now_iso(),
        "closed_ts": None,
        "last_status_ts": _now_iso(),
    }
    created_refs.append(entry)
    return entry


def _mark_ref_status(
    run: dict[str, Any],
    ref: dict[str, Any],
    status: str,
    reason: str,
    **fields: Any,
) -> dict[str, Any]:
    entry = _ensure_ledger_ref(run, ref)
    now = _now_iso()
    entry.setdefault("kind", "worktree" if ref.get("path") else "branch")
    if ref.get("path") and not entry.get("path"):
        entry["path"] = ref["path"]
    entry.setdefault("merge_target", ref.get("merge_target", "main"))
    entry.setdefault("purpose", ref.get("summary", ""))
    entry.setdefault(
        "close_criteria",
        _default_close_criteria(
            ref["branch"],
            entry.get("merge_target", "main"),
            entry.get("kind", "branch"),
            bool(entry.get("review_hold", False)),
        ),
    )
    entry.setdefault("review_hold", bool(ref.get("review_hold", False)))
    entry["status"] = status
    entry["close_reason"] = reason
    entry["last_status_ts"] = now
    if status == "closed":
        entry["closed_ts"] = now
    else:
        entry.setdefault("closed_ts", None)
    for key, value in fields.items():
        if value is not None:
            entry[key] = value
    return {
        "branch": ref["branch"],
        "status": status,
        "reason": reason,
    }


def _safe_receipt_name(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip("-.")
    return safe or "unknown-run"


def _receipt_path(workdir: Path, run_id: str) -> Path:
    return workdir / RECEIPT_DIR_REL / f"{_safe_receipt_name(run_id)}.json"


def _load_receipt(path: Path, run_id: str) -> dict[str, Any]:
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                if loaded.get("schema_version") != 1:
                    raise ValueError("unsupported branch-closeout receipt schema")
                if loaded.get("run_id") not in (None, run_id):
                    raise ValueError("branch-closeout receipt run_id mismatch")
                loaded.setdefault("refs", [])
                return loaded
            raise ValueError("branch-closeout receipt root must be an object")
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"branch-closeout receipt unreadable: {exc}") from exc
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "open",
        "created_ts": _now_iso(),
        "updated_ts": _now_iso(),
        "refs": [],
    }


def _receipt_ref(receipt: dict[str, Any], branch: str) -> dict[str, Any] | None:
    refs = receipt.get("refs")
    if not isinstance(refs, list):
        return None
    for entry in refs:
        if isinstance(entry, dict) and entry.get("branch") == branch:
            return entry
    return None


def _upsert_receipt_ref(
    receipt: dict[str, Any],
    ref: dict[str, Any],
    **fields: Any,
) -> dict[str, Any]:
    refs = receipt.setdefault("refs", [])
    entry = _receipt_ref(receipt, ref["branch"])
    if entry is None:
        entry = {
            "branch": ref["branch"],
            "path": ref.get("path"),
            "source": ref.get("source"),
        }
        refs.append(entry)
    if ref.get("path") and not entry.get("path"):
        entry["path"] = ref["path"]
    for key, value in fields.items():
        if value is not None:
            entry[key] = value
    receipt["updated_ts"] = _now_iso()
    return entry


def _receipt_status(receipt: dict[str, Any]) -> str:
    refs = [entry for entry in receipt.get("refs", []) if isinstance(entry, dict)]
    if not refs:
        return "open"
    statuses = {str(entry.get("status") or "open") for entry in refs}
    if statuses <= {"closed", "retained"}:
        return "complete"
    if "error" in statuses:
        return "error"
    if "prepared" in statuses:
        return "prepared"
    return "open"


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    receipt["status"] = _receipt_status(receipt)
    receipt["updated_ts"] = _now_iso()
    atomic_write_bytes(path, (json.dumps(receipt, indent=2) + "\n").encode())


def _set_branch_closeout(
    run: dict[str, Any],
    *,
    status: str,
    receipt_path: Path | None,
    reason: str | None = None,
) -> None:
    entry = run.setdefault("branch_closeout", {})
    entry["status"] = status
    entry["updated_ts"] = _now_iso()
    if receipt_path is not None:
        entry["receipt_path"] = str(receipt_path)
    if reason:
        entry["reason"] = reason


def _normalize_refs(
    run: dict[str, Any],
    execution: dict[str, Any] | None = None,
    workdir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge ref registries into one unified ref list, deduped by branch name.

    Sources (in order — later sources can upgrade review_hold and fill in
    missing paths):
      1. ``run.dispatchedWorktrees[]`` — work already integrated.
      2. ``run.riskyBranches[]`` — always review_hold=True.
      3. ``run.createdRefs[]`` — uses its own review_hold flag.
      4. ``execution.run_worktree_path`` / ``execution.run_worktree_branch``
         (when provided) — the per-run isolation worktree from
         ``build_loop_id.generate_or_resume(provision_worktree=True)``.
         Treated like a dispatchedWorktree (review_hold=False) since its
         purpose is just "isolate this run"; if the run reached closeout,
         its work was either merged or is otherwise accounted for via the
         other registries.

    Each entry has:
      branch         str
      path           str | None   (worktree folder, if known)
      review_hold    bool
      summary        str          (human note for kept_for_review output)
      source         str          (which registry it came from)
    """
    seen: dict[str, dict[str, Any]] = {}

    skipped_protected: list[str] = []
    closed_branches = {
        entry.get("branch")
        for entry in run.get("createdRefs") or []
        if isinstance(entry, dict) and entry.get("status") == "closed"
    }

    def _canonical_path(value: str | None) -> str | None:
        if not value:
            return None
        candidate = Path(str(value))
        if not candidate.is_absolute() and workdir is not None:
            candidate = workdir / candidate
        return str(candidate.resolve())

    def _add(
        branch: str,
        path: str | None,
        review_hold: bool,
        summary: str,
        source: str,
        merge_target: str = "main",
    ) -> None:
        if not branch:
            return
        if branch in closed_branches:
            return
        if branch == "main":
            skipped_protected.append(f"skipped protected branch in {source}: main")
            return
        path = _canonical_path(path)
        if branch not in seen:
            seen[branch] = {
                "branch": branch,
                "path": path,
                "review_hold": review_hold,
                "summary": summary,
                "source": source,
                "merge_target": merge_target,
            }
        else:
            # Later registries can upgrade review_hold (risky overrides dispatched)
            if review_hold:
                seen[branch]["review_hold"] = True
            existing_path = seen[branch].get("path")
            if path and not existing_path:
                seen[branch]["path"] = path
            elif path and existing_path and path != existing_path:
                seen[branch]["path_conflicts"] = sorted(
                    {
                        str(existing_path),
                        str(path),
                        *(seen[branch].get("path_conflicts") or []),
                    }
                )
            if merge_target and not seen[branch].get("merge_target"):
                seen[branch]["merge_target"] = merge_target

    # dispatchedWorktrees[] — work already integrated, so review_hold=False
    for entry in run.get("dispatchedWorktrees") or []:
        if not isinstance(entry, dict):
            continue
        _add(
            branch=entry.get("branch", ""),
            path=entry.get("path") or entry.get("worktree"),
            review_hold=False,
            summary="dispatch worktree (already integrated)",
            source="dispatchedWorktrees",
            merge_target=entry.get("merge_target", "main"),
        )

    # riskyBranches[] — always review_hold=True
    for entry in run.get("riskyBranches") or []:
        if not isinstance(entry, dict):
            continue
        _add(
            branch=entry.get("branch", ""),
            path=entry.get("path"),
            review_hold=True,
            summary=entry.get("summary", "risky branch"),
            source="riskyBranches",
            merge_target=entry.get("merge_target", "main"),
        )

    # createdRefs[] — use their own review_hold flag
    for entry in run.get("createdRefs") or []:
        if not isinstance(entry, dict):
            continue
        _add(
            branch=entry.get("branch", ""),
            path=entry.get("path") or entry.get("worktree"),
            review_hold=bool(entry.get("review_hold", False)),
            summary=entry.get("purpose") or entry.get("summary", ""),
            source="createdRefs",
            merge_target=entry.get("merge_target", "main"),
        )

    # execution.run_worktree_* — Phase 1a run-entry isolation worktree.
    # See scripts/rally_point/build_loop_id.py:_provision_run_worktree for why
    # this lives on state.execution rather than runs[N].createdRefs[].
    if execution:
        exec_branch = (
            execution.get("run_worktree_branch")
            or execution.get("branch")
            or execution.get("branch_name")
        )
        exec_path = (
            execution.get("run_worktree_path")
            or execution.get("worktree")
            or execution.get("worktree_path")
        )
        if exec_branch:
            _add(
                branch=str(exec_branch),
                path=str(exec_path) if exec_path else None,
                review_hold=False,
                summary="run-entry isolation worktree (state.execution)",
                source="executionRunWorktree",
                merge_target="main",
            )

    return [v for v in seen.values() if v["branch"]], skipped_protected


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def _create_bundle(
    workdir: Path,
    run_id: str,
    expected_oids: dict[str, str],
) -> tuple[str | None, bool, str | None]:
    """Create and verify a bundle containing each exact branch/OID."""
    if not expected_oids:
        return None, False, "no branch refs available to bundle"
    bundles_dir = workdir / ".build-loop" / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundles_dir / (
        f"collapse-{_safe_receipt_name(run_id)}-{_now_utc()}.bundle"
    )
    try:
        refs = [f"refs/heads/{branch}" for branch in sorted(expected_oids)]
        r = _git(workdir, "bundle", "create", str(bundle_path), *refs, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout).strip() or (
                f"git bundle create exited {r.returncode}"
            )
            return None, False, err
        verify = _git(workdir, "bundle", "verify", str(bundle_path), check=False)
        if verify.returncode != 0:
            err = (verify.stderr or verify.stdout).strip() or (
                f"git bundle verify exited {verify.returncode}"
            )
            return str(bundle_path), False, err
        heads = _git(workdir, "bundle", "list-heads", str(bundle_path), check=False)
        if heads.returncode != 0:
            err = (heads.stderr or heads.stdout).strip() or (
                f"git bundle list-heads exited {heads.returncode}"
            )
            return str(bundle_path), False, err
        bundled: dict[str, str] = {}
        for line in heads.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith("refs/heads/"):
                bundled[parts[1][len("refs/heads/"):]] = parts[0]
        mismatches = [
            f"{branch}: expected {expected}, bundled {bundled.get(branch)}"
            for branch, expected in expected_oids.items()
            if bundled.get(branch) != expected
        ]
        if mismatches:
            return str(bundle_path), False, "bundle head mismatch: " + "; ".join(mismatches)
        return str(bundle_path), True, None
    except Exception as exc:
        return None, False, str(exc)


def _project_ref_state(
    workdir: Path,
    run_identity: str,
    ref: dict[str, Any],
    *,
    created_ref_status: str,
    reason: str,
    branch_closeout_status: str,
    receipt_path: Path | None,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reload and atomically project receipt state onto the current run row."""
    state_path = workdir / STATE_REL
    with LockedFile(state_path):
        state = _load_state(workdir)
        run = _pick_run(state, run_identity)
        if run is None:
            raise ValueError(f"run disappeared during closeout: {run_identity}")
        update = _mark_ref_status(
            run,
            ref,
            created_ref_status,
            reason,
            **(fields or {}),
        )
        _set_branch_closeout(
            run,
            status=branch_closeout_status,
            receipt_path=receipt_path,
            reason=reason,
        )
        _write_state(workdir, state)
        return update


def _approved_run_worktree_path(workdir: Path, path: str | None) -> bool:
    if not path:
        return True
    root = (workdir / ".build-loop" / "worktrees").resolve()
    candidate = Path(path).resolve()
    return candidate == root or root in candidate.parents


def _verified_receipt_bundle(
    workdir: Path,
    entry: dict[str, Any],
) -> bool:
    bundle = entry.get("bundle_path")
    branch = entry.get("branch")
    expected = entry.get("expected_oid")
    if not all(isinstance(value, str) and value for value in (bundle, branch, expected)):
        return False
    bundle_path = Path(str(bundle))
    if not bundle_path.exists():
        return False
    verify = _git(workdir, "bundle", "verify", str(bundle_path), check=False)
    if verify.returncode != 0:
        return False
    heads = _git(workdir, "bundle", "list-heads", str(bundle_path), check=False)
    if heads.returncode != 0:
        return False
    wanted = f"refs/heads/{branch}"
    return any(
        line.split(maxsplit=1) == [expected, wanted]
        for line in heads.stdout.splitlines()
    )


# ---------------------------------------------------------------------------
# Core collapse logic
# ---------------------------------------------------------------------------

def collapse(
    workdir: Path,
    run_id: str = "latest",
    dry_run: bool = False,
    *,
    branch: str | None = None,
    strict: bool = False,
    merged_only: bool = False,
    owner_released: bool = False,
    require_run_root: bool = False,
    release_source: str = "direct",
    expected_path: str | None = None,
    session_id: str | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    """Finalize attributable refs through a verified, replayable transaction.

    owner_released is positive deletion authority. Safety sensors only veto;
    they never infer release. Background callers must leave it false.
    """
    workdir = Path(workdir).resolve()
    if not _git_available(workdir):
        raise SystemExit("git is not available or workdir is not a git repo")

    state = _load_state(workdir)
    run = _pick_run(state, run_id)
    actual_run_id = _canonical_run_identity(run) or run_id
    result: dict[str, Any] = {
        "run_id": actual_run_id,
        "branch": branch,
        "expected_path": expected_path,
        "bundle_path": None,
        "bundle_verified": False,
        "receipt_path": None,
        "receipt_status": None,
        "deleted": [],
        "already_closed": [],
        "kept_for_review": [],
        "surfaced_unmerged": [],
        "retained": [],
        "safety": [],
        "errors": [],
        "dry_run": dry_run,
        "strict": strict,
        "strict_success": False,
        "ledger_updated": [],
        "claim_release_session_source": None,
        "claim_release_tool_source": None,
    }

    if strict and run_id == "latest":
        result["errors"].append("strict closeout requires an exact --run-id")
        return result
    if strict and not branch:
        result["errors"].append("strict closeout requires an exact --branch")
        return result
    if run is None:
        result["errors"].append(
            f"no attributable runs[] row for {run_id}; execution-only cleanup is forbidden"
        )
        return result

    execution = _execution_for_run(
        state,
        run,
        allow_anonymous_latest=(run_id == "latest"),
    )
    claim_release_session_id, claim_release_session_source = (
        _claim_release_session_id(execution, session_id)
    )
    claim_release_tool, claim_release_tool_source = _claim_release_tool(
        execution,
        tool,
    )
    result["claim_release_session_source"] = claim_release_session_source
    result["claim_release_tool_source"] = claim_release_tool_source
    refs, skipped_notes = _normalize_refs(run, execution, workdir)
    result["errors"].extend(skipped_notes)
    if branch:
        refs = [ref for ref in refs if ref.get("branch") == branch]

    receipt_path = _receipt_path(workdir, actual_run_id)
    try:
        receipt = _load_receipt(receipt_path, actual_run_id)
    except ValueError as exc:
        result["errors"].append(str(exc))
        return result
    if receipt_path.exists():
        result["receipt_path"] = str(receipt_path)
        result["receipt_status"] = receipt.get("status")

    if not refs:
        if branch:
            closed = next(
                (
                    entry
                    for entry in run.get("createdRefs") or []
                    if isinstance(entry, dict)
                    and entry.get("branch") == branch
                    and entry.get("status") == "closed"
                ),
                None,
            )
            closed_path = (closed or {}).get("path") or (closed or {}).get("worktree")
            ref_absent = _branch_oid(workdir, branch) is None
            path_absent = not closed_path or not Path(str(closed_path)).exists()
            receipt_entry = _receipt_ref(receipt, branch)
            receipt_terminal = bool(
                receipt_entry
                and receipt_entry.get("status") == "closed"
                and _verified_receipt_bundle(workdir, receipt_entry)
            )
            if receipt_terminal and receipt_entry:
                result["bundle_path"] = receipt_entry.get("bundle_path")
                result["bundle_verified"] = True
                result["receipt_path"] = str(receipt_path)
                result["receipt_status"] = _receipt_status(receipt)
            if closed and ref_absent and path_absent:
                result["already_closed"].append(
                    {"branch": branch, "path": closed_path, "action": "already_closed"}
                )
                if strict and not receipt_terminal:
                    result["errors"].append(
                        f"closed ledger for {branch} has no verified terminal receipt"
                    )
                else:
                    result["strict_success"] = strict
                return result
            result["errors"].append(
                f"branch {branch} is not attributable to run {actual_run_id}"
            )
        return result

    current_branch = _current_branch(workdir)
    live_cwds: list[dict[str, Any]] = []
    live_cwd_error: str | None = None
    if any(ref.get("path") and Path(str(ref["path"])).exists() for ref in refs):
        live_cwds, live_cwd_error = _live_process_cwds()

    candidates: list[dict[str, Any]] = []
    for ref in refs:
        ref_branch = str(ref["branch"])
        path = ref.get("path")
        merge_target = str(ref.get("merge_target") or "main")

        path_conflicts = ref.get("path_conflicts") or []
        if path_conflicts:
            reason = (
                f"conflicting worktree paths for {ref_branch}: "
                + ", ".join(str(item) for item in path_conflicts)
            )
            result["errors"].append(reason)
            result["retained"].append(
                {"branch": ref_branch, "path": path, "reason": reason}
            )
            if not dry_run:
                try:
                    result["ledger_updated"].append(
                        _project_ref_state(
                            workdir,
                            actual_run_id,
                            ref,
                            created_ref_status="error",
                            reason=reason,
                            branch_closeout_status="error",
                            receipt_path=receipt_path if receipt_path.exists() else None,
                        )
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(f"state projection failed: {exc}")
            continue

        if expected_path:
            expected_abs = str(Path(expected_path).resolve())
            actual_abs = str(Path(str(path)).resolve()) if path else None
            if actual_abs != expected_abs:
                reason = (
                    f"expected worktree path mismatch for {ref_branch}: "
                    f"expected {expected_abs}, ledger has {actual_abs}"
                )
                result["errors"].append(reason)
                result["retained"].append(
                    {"branch": ref_branch, "path": path, "reason": reason}
                )
                continue

        if current_branch and ref_branch == current_branch:
            reason = f"skipped currently-checked-out branch: {ref_branch}"
            result["errors"].append(reason)
            if not dry_run:
                try:
                    result["ledger_updated"].append(
                        _project_ref_state(
                            workdir,
                            actual_run_id,
                            ref,
                            created_ref_status="error",
                            reason=reason,
                            branch_closeout_status="error",
                            receipt_path=None,
                        )
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(f"state projection failed: {exc}")
            continue

        expected_oid = _branch_oid(workdir, ref_branch)
        if expected_oid is None:
            receipt_entry = _receipt_ref(receipt, ref_branch)
            path_absent = not path or not Path(str(path)).exists()
            recoverable = bool(
                receipt_entry
                and receipt_entry.get("status") in ("prepared", "closed")
                and path_absent
                and _verified_receipt_bundle(workdir, receipt_entry)
            )
            if recoverable and not dry_run:
                reason = "reconciled from verified receipt after ref/worktree removal"
                _upsert_receipt_ref(
                    receipt,
                    ref,
                    status="closed",
                    reason=reason,
                    closed_ts=_now_iso(),
                )
                try:
                    _write_receipt(receipt_path, receipt)
                    result["ledger_updated"].append(
                        _project_ref_state(
                            workdir,
                            actual_run_id,
                            ref,
                            created_ref_status="closed",
                            reason=reason,
                            branch_closeout_status="complete",
                            receipt_path=receipt_path,
                            fields={
                                "bundle_path": receipt_entry.get("bundle_path"),
                                "bundle_verified": True,
                                "expected_oid": receipt_entry.get("expected_oid"),
                            },
                        )
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(f"receipt reconciliation failed: {exc}")
                    continue
                result["already_closed"].append({"branch": ref_branch, "path": path})
                result["bundle_path"] = receipt_entry.get("bundle_path")
                result["bundle_verified"] = True
                result["receipt_path"] = str(receipt_path)
                result["receipt_status"] = receipt.get("status")
                continue

            reason = f"could not classify {ref_branch} (branch ref is absent)"
            result["errors"].append(reason)
            if not dry_run:
                try:
                    result["ledger_updated"].append(
                        _project_ref_state(
                            workdir,
                            actual_run_id,
                            ref,
                            created_ref_status="error",
                            reason=reason,
                            branch_closeout_status="error",
                            receipt_path=receipt_path if receipt_path.exists() else None,
                        )
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(f"state projection failed: {exc}")
            continue

        is_merged = _is_ancestor(workdir, ref_branch, merge_target)
        if is_merged is None:
            reason = f"could not classify {ref_branch} against {merge_target}"
            result["errors"].append(reason)
            continue

        if dry_run:
            if is_merged:
                result["deleted"].append(
                    {"branch": ref_branch, "path": path, "action": "would_delete"}
                )
            elif ref.get("review_hold"):
                result["kept_for_review"].append(
                    {
                        "branch": ref_branch,
                        "path": path,
                        "summary": ref.get("summary", ""),
                        "action": "would_keep_branch_remove_worktree",
                    }
                )
            else:
                result["surfaced_unmerged"].append(
                    {
                        "branch": ref_branch,
                        "path": path,
                        "action": "would_remove_worktree_surface_branch",
                    }
                )
            continue

        if merged_only and not is_merged:
            reason = f"{ref_branch} is not merged into {merge_target}; retained"
            result["errors"].append(reason)
            result["retained"].append(
                {"branch": ref_branch, "path": path, "reason": reason}
            )
            try:
                result["ledger_updated"].append(
                    _project_ref_state(
                        workdir,
                        actual_run_id,
                        ref,
                        created_ref_status="open",
                        reason=reason,
                        branch_closeout_status="deferred",
                        receipt_path=receipt_path if receipt_path.exists() else None,
                    )
                )
            except (OSError, ValueError, TimeoutError) as exc:
                result["errors"].append(f"state projection failed: {exc}")
            continue

        if not owner_released:
            reason = f"owner release required before mutating {ref_branch}"
            result["errors"].append(reason)
            result["retained"].append(
                {"branch": ref_branch, "path": path, "reason": reason}
            )
            try:
                result["ledger_updated"].append(
                    _project_ref_state(
                        workdir,
                        actual_run_id,
                        ref,
                        created_ref_status="open",
                        reason=reason,
                        branch_closeout_status="deferred",
                        receipt_path=receipt_path if receipt_path.exists() else None,
                    )
                )
            except (OSError, ValueError, TimeoutError) as exc:
                result["errors"].append(f"state projection failed: {exc}")
            continue

        if require_run_root and not _approved_run_worktree_path(workdir, path):
            reason = f"worktree path is outside .build-loop/worktrees: {path}"
            result["errors"].append(reason)
            result["retained"].append(
                {"branch": ref_branch, "path": path, "reason": reason}
            )
            continue

        safety = inspect_worktree_safety(
            workdir,
            str(path) if path else None,
            ref_branch,
            live_cwds=live_cwds,
            live_cwd_error=live_cwd_error,
        )
        result["safety"].append({"branch": ref_branch, **safety})
        if not safety["safe"]:
            reason = f"unsafe worktree for {ref_branch}: {safety['reason']}"
            result["errors"].append(reason)
            result["retained"].append(
                {"branch": ref_branch, "path": path, "reason": reason}
            )
            try:
                result["ledger_updated"].append(
                    _project_ref_state(
                        workdir,
                        actual_run_id,
                        ref,
                        created_ref_status="error",
                        reason=reason,
                        branch_closeout_status="error",
                        receipt_path=receipt_path if receipt_path.exists() else None,
                    )
                )
            except (OSError, ValueError, TimeoutError) as exc:
                result["errors"].append(f"state projection failed: {exc}")
            continue

        candidates.append(
            {
                "ref": ref,
                "expected_oid": expected_oid,
                "is_merged": is_merged,
                "merge_target": merge_target,
            }
        )

    if dry_run:
        result["strict_success"] = False
        return result
    if not candidates:
        if strict and branch and not result["errors"]:
            target_receipt = _receipt_ref(receipt, branch)
            verified_terminal = bool(
                target_receipt
                and target_receipt.get("status") == "closed"
                and _verified_receipt_bundle(workdir, target_receipt)
                and _branch_oid(workdir, branch) is None
                and (
                    not target_receipt.get("path")
                    or not Path(str(target_receipt["path"])).exists()
                )
            )
            if verified_terminal and target_receipt:
                result["bundle_path"] = target_receipt.get("bundle_path")
                result["bundle_verified"] = True
                result["receipt_path"] = str(receipt_path)
            result["strict_success"] = verified_terminal
            result["receipt_status"] = _receipt_status(receipt)
        return result

    expected_oids = {
        str(candidate["ref"]["branch"]): str(candidate["expected_oid"])
        for candidate in candidates
    }
    bundle_path_str, bundle_verified, bundle_error = _create_bundle(
        workdir,
        actual_run_id,
        expected_oids,
    )
    if not bundle_verified or not bundle_path_str:
        reason = f"bundle failed: {bundle_error}"
        result["errors"].append(reason)
        for candidate in candidates:
            try:
                result["ledger_updated"].append(
                    _project_ref_state(
                        workdir,
                        actual_run_id,
                        candidate["ref"],
                        created_ref_status="error",
                        reason=reason,
                        branch_closeout_status="error",
                        receipt_path=None,
                    )
                )
            except (OSError, ValueError, TimeoutError) as exc:
                result["errors"].append(f"state projection failed: {exc}")
        return result

    result["bundle_path"] = bundle_path_str
    result["bundle_verified"] = True
    result["receipt_path"] = str(receipt_path)
    receipt["owner_release"] = {
        "confirmed": True,
        "source": release_source,
        "confirmed_ts": _now_iso(),
    }
    for candidate in candidates:
        ref = candidate["ref"]
        _upsert_receipt_ref(
            receipt,
            ref,
            status="prepared",
            expected_oid=candidate["expected_oid"],
            bundle_path=bundle_path_str,
            bundle_verified=True,
            merge_target=candidate["merge_target"],
            prepared_ts=_now_iso(),
        )

    try:
        _write_receipt(receipt_path, receipt)
    except OSError as exc:
        result["errors"].append(f"prepared receipt write failed: {exc}")
        return result

    for candidate in candidates:
        ref = candidate["ref"]
        try:
            result["ledger_updated"].append(
                _project_ref_state(
                    workdir,
                    actual_run_id,
                    ref,
                    created_ref_status="open",
                    reason="verified bundle prepared; mutation pending",
                    branch_closeout_status="prepared",
                    receipt_path=receipt_path,
                    fields={
                        "closeout_attempt": {
                            "expected_oid": candidate["expected_oid"],
                            "bundle_path": bundle_path_str,
                            "bundle_verified": True,
                            "prepared_ts": _now_iso(),
                        }
                    },
                )
            )
        except (OSError, ValueError, TimeoutError) as exc:
            result["errors"].append(f"prepared state projection failed: {exc}")
            result["receipt_status"] = receipt.get("status")
            return result

    for candidate in candidates:
        ref = candidate["ref"]
        ref_branch = str(ref["branch"])
        path = ref.get("path")
        expected_oid = str(candidate["expected_oid"])
        merge_target = str(candidate["merge_target"])

        current_oid = _branch_oid(workdir, ref_branch)
        if current_oid != expected_oid:
            reason = (
                f"branch moved after bundle for {ref_branch}: "
                f"expected {expected_oid}, found {current_oid}"
            )
            result["errors"].append(reason)
            _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
            try:
                _write_receipt(receipt_path, receipt)
                result["ledger_updated"].append(
                    _project_ref_state(
                        workdir,
                        actual_run_id,
                        ref,
                        created_ref_status="error",
                        reason=reason,
                        branch_closeout_status="error",
                        receipt_path=receipt_path,
                    )
                )
            except (OSError, ValueError, TimeoutError) as exc:
                result["errors"].append(f"error projection failed: {exc}")
            continue

        if require_run_root and not _approved_run_worktree_path(workdir, path):
            reason = f"worktree path moved outside .build-loop/worktrees: {path}"
            result["errors"].append(reason)
            _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
            _write_receipt(receipt_path, receipt)
            continue

        fresh_cwds: list[dict[str, Any]] = []
        fresh_cwd_error: str | None = None
        if path and Path(str(path)).exists():
            fresh_cwds, fresh_cwd_error = _live_process_cwds()
        safety = inspect_worktree_safety(
            workdir,
            str(path) if path else None,
            ref_branch,
            live_cwds=fresh_cwds,
            live_cwd_error=fresh_cwd_error,
        )
        result["safety"].append({"branch": ref_branch, "stage": "pre-mutation", **safety})
        if not safety["safe"]:
            reason = f"pre-mutation safety veto for {ref_branch}: {safety['reason']}"
            result["errors"].append(reason)
            _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
            try:
                _write_receipt(receipt_path, receipt)
                result["ledger_updated"].append(
                    _project_ref_state(
                        workdir,
                        actual_run_id,
                        ref,
                        created_ref_status="error",
                        reason=reason,
                        branch_closeout_status="error",
                        receipt_path=receipt_path,
                    )
                )
            except (OSError, ValueError, TimeoutError) as exc:
                result["errors"].append(f"error projection failed: {exc}")
            continue

        now_merged = _is_ancestor(workdir, ref_branch, merge_target)
        if now_merged is None or (merged_only and not now_merged):
            reason = f"merge classification changed before mutation for {ref_branch}"
            result["errors"].append(reason)
            _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
            _write_receipt(receipt_path, receipt)
            continue

        if path:
            wt_err = _remove_worktree(
                workdir,
                str(path),
                session_id=claim_release_session_id,
                tool=claim_release_tool,
            )
            if wt_err:
                reason = f"worktree remove failed for {ref_branch}: {wt_err}"
                result["errors"].append(reason)
                _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
                try:
                    _write_receipt(receipt_path, receipt)
                    result["ledger_updated"].append(
                        _project_ref_state(
                            workdir,
                            actual_run_id,
                            ref,
                            created_ref_status="error",
                            reason=reason,
                            branch_closeout_status="error",
                            receipt_path=receipt_path,
                        )
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(f"error projection failed: {exc}")
                continue

        if now_merged:
            records, records_error = _worktree_records(workdir)
            registered_paths = sorted(
                record_path
                for record_path, record in records.items()
                if record.get("branch") == ref_branch
            )
            if records_error or registered_paths:
                reason = (
                    f"branch registration recheck failed for {ref_branch}: {records_error}"
                    if records_error
                    else (
                        f"branch became checked out before ref deletion for {ref_branch}: "
                        + ", ".join(registered_paths)
                    )
                )
                result["errors"].append(reason)
                _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
                try:
                    _write_receipt(receipt_path, receipt)
                    result["ledger_updated"].append(
                        _project_ref_state(
                            workdir,
                            actual_run_id,
                            ref,
                            created_ref_status="error",
                            reason=reason,
                            branch_closeout_status="error",
                            receipt_path=receipt_path,
                        )
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(f"error projection failed: {exc}")
                continue
            post_remove_oid = _branch_oid(workdir, ref_branch)
            if post_remove_oid != expected_oid:
                reason = (
                    f"branch moved during worktree removal for {ref_branch}: "
                    f"expected {expected_oid}, found {post_remove_oid}"
                )
                result["errors"].append(reason)
                _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
                _write_receipt(receipt_path, receipt)
                continue
            branch_error = _delete_branch_expected(workdir, ref_branch, expected_oid)
            if branch_error:
                reason = f"branch delete failed for {ref_branch}: {branch_error}"
                result["errors"].append(reason)
                _upsert_receipt_ref(receipt, ref, status="error", reason=reason)
                try:
                    _write_receipt(receipt_path, receipt)
                    result["ledger_updated"].append(
                        _project_ref_state(
                            workdir,
                            actual_run_id,
                            ref,
                            created_ref_status="error",
                            reason=reason,
                            branch_closeout_status="error",
                            receipt_path=receipt_path,
                        )
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(f"error projection failed: {exc}")
                continue
            receipt_state = "closed"
            created_ref_status = "closed"
            reason = f"merged into {merge_target}; expected ref and worktree closed"
            result["deleted"].append(
                {"branch": ref_branch, "path": path, "expected_oid": expected_oid}
            )
        elif ref.get("review_hold"):
            receipt_state = "retained"
            created_ref_status = "kept_for_review"
            reason = "unmerged review_hold branch retained; worktree folder removed"
            result["kept_for_review"].append(
                {
                    "branch": ref_branch,
                    "path": path,
                    "summary": ref.get("summary", ""),
                }
            )
        else:
            receipt_state = "retained"
            created_ref_status = "surfaced_unmerged"
            reason = "unmerged branch surfaced for operator disposition; worktree removed"
            result["surfaced_unmerged"].append(
                {"branch": ref_branch, "path": path}
            )

        _upsert_receipt_ref(
            receipt,
            ref,
            status=receipt_state,
            reason=reason,
            closed_ts=_now_iso(),
        )
        try:
            _write_receipt(receipt_path, receipt)
        except OSError as exc:
            result["errors"].append(f"terminal receipt write failed: {exc}")
            continue
        try:
            result["ledger_updated"].append(
                _project_ref_state(
                    workdir,
                    actual_run_id,
                    ref,
                    created_ref_status=created_ref_status,
                    reason=reason,
                    branch_closeout_status=(
                        "complete" if _receipt_status(receipt) == "complete" else "error"
                    ),
                    receipt_path=receipt_path,
                    fields={
                        "bundle_path": bundle_path_str,
                        "bundle_verified": True,
                        "expected_oid": expected_oid,
                    },
                )
            )
        except (OSError, ValueError, TimeoutError) as exc:
            result["errors"].append(f"terminal state projection failed: {exc}")

    result["receipt_status"] = _receipt_status(receipt)
    if strict and branch:
        target_receipt = _receipt_ref(receipt, branch)
        result["strict_success"] = bool(
            not result["errors"]
            and target_receipt
            and target_receipt.get("status") == "closed"
            and _verified_receipt_bundle(workdir, target_receipt)
            and _branch_oid(workdir, branch) is None
            and (
                not target_receipt.get("path")
                or not Path(str(target_receipt["path"])).exists()
            )
        )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def enforce_terminal_memory_closeout(
    workdir: Path,
    result: dict[str, Any],
    *,
    memory_root: str | Path | None = None,
) -> dict[str, Any]:
    """Make a successful strict ref closeout prove or owe its milestone.

    Strict collapse has two production entry points: this module's CLI and the
    worktree reaper. Keeping the enforcement in one callable prevents either
    terminal path from deleting the last run worktree without emitting the
    closeout record and milestone evidence.
    """
    result["memory_closeout"] = None
    if not result.get("strict_success") or result.get("dry_run"):
        return result

    try:
        from closeout.status import run as run_memory_closeout  # noqa: PLC0415

        result["memory_closeout"] = run_memory_closeout(
            Path(workdir).resolve(),
            run_id=str(result["run_id"]),
            source="phase-6-learn",
            memory_root=str(memory_root) if memory_root is not None else None,
        )
        milestone_status = (result["memory_closeout"].get("milestone") or {}).get("status")
        if milestone_status not in ("recorded", "queued"):
            result["errors"].append(
                "strict branch closeout did not record or durably queue the run milestone "
                f"(status={milestone_status or 'missing'})"
            )
            result["strict_success"] = False
    except Exception as exc:  # noqa: BLE001 — strict lifecycle converts failure to evidence
        result["errors"].append(f"strict memory closeout failed: {type(exc).__name__}: {exc}")
        result["strict_success"] = False
    return result

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", default=".", help="Repo root (must contain .build-loop/state.json)")
    parser.add_argument("--run-id", default="latest", help="Run ID to collapse, or 'latest' (default)")
    parser.add_argument("--branch", help="Operate on exactly one attributable branch")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require exact run/branch, merged cleanup, zero errors, and a terminal receipt",
    )
    parser.add_argument(
        "--owner-released",
        action="store_true",
        help="Positive authority that the target terminal/worktree owner has released it",
    )
    parser.add_argument(
        "--merged-only",
        action="store_true",
        help="Retain unmerged branches/worktrees instead of applying legacy Phase-D disposition",
    )
    parser.add_argument(
        "--require-run-root",
        action="store_true",
        help="Require worktree paths under .build-loop/worktrees (background/reaper safety)",
    )
    parser.add_argument(
        "--release-source",
        default="direct-cli",
        help="Receipt provenance for the explicit owner release",
    )
    parser.add_argument(
        "--expected-path",
        help="Require the attributed worktree path to match this exact path",
    )
    parser.add_argument(
        "--session-id",
        help=(
            "Exact owner session for Rally claim cleanup; defaults only to the "
            "selected execution.current_session_id, never process environment"
        ),
    )
    parser.add_argument(
        "--tool",
        help=(
            "Owner host family for Rally claim cleanup; defaults only to the "
            "selected execution.started_by_tool, never the current host"
        ),
    )
    parser.add_argument(
        "--memory-root",
        help="Override the canonical memory root for strict terminal closeout/testing",
    )
    parser.add_argument("--dry-run", action="store_true", help="Classify refs but perform no git operations")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print result JSON to stdout")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()

    try:
        result = collapse(
            workdir,
            run_id=args.run_id,
            dry_run=args.dry_run,
            branch=args.branch,
            strict=args.strict,
            merged_only=(args.merged_only or args.strict),
            owner_released=args.owner_released,
            require_run_root=args.require_run_root,
            release_source=args.release_source,
            expected_path=args.expected_path,
            session_id=args.session_id,
            tool=args.tool,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.strict and result["strict_success"] and not args.dry_run:
        enforce_terminal_memory_closeout(
            workdir,
            result,
            memory_root=args.memory_root,
        )
    else:
        result["memory_closeout"] = None

    # Human summary to stderr
    dr_tag = " [DRY RUN]" if result["dry_run"] else ""
    print(
        f"collapse{dr_tag} run={result['run_id']} "
        f"deleted={len(result['deleted'])} "
        f"kept_for_review={len(result['kept_for_review'])} "
        f"surfaced_unmerged={len(result['surfaced_unmerged'])} "
        f"bundle_verified={result['bundle_verified']} "
        f"strict_success={result['strict_success']} "
        f"errors={len(result['errors'])}",
        file=sys.stderr,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))

    return 1 if args.strict and not result["strict_success"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
