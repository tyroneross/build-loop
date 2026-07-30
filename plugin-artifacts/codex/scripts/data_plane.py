#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Run-scoped data-plane manifests for Build Loop worktree isolation.

Git worktrees isolate source files only. This module adds a deliberately
adapter-neutral contract for mutable state such as SQLite copies, generated
indexes, service projects, and externally namespaced resources. It creates no
database or cloud resource; adapters declare their surfaces here and the
lifecycle validates isolation, concurrent ownership, and closeout status.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
MANIFESTS_REL = Path(".build-loop") / "data-manifests"
DATA_REL = Path(".build-loop") / "data"
ISOLATION_MODES = {
    "per_worktree",
    "shared_readonly",
    "shared_serialized",
    "external_namespaced",
}
AUTHORITIES = {"canonical", "derived", "external"}
TERMINAL_STATUSES = {"closed", "retained", "not_owned"}
SURFACE_STATUSES = {"active", "closed", "retained", "not_owned", "error", "deferred"}


class DataPlaneError(RuntimeError):
    """Raised when a manifest cannot be initialized or terminally updated."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _resolve(value: str | Path, root: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and root is not None:
        path = root / path
    return path.resolve()


def manifest_path(workdir: Path | str, run_id: str) -> Path:
    return Path(workdir).resolve() / MANIFESTS_REL / f"{run_id}.json"


def initialize_manifest(
    workdir: Path | str,
    *,
    run_id: str,
    worktree_path: Path | str,
    branch: str,
) -> dict[str, Any]:
    """Create or return one baseline manifest and its per-run data root.

    Existing manifests are preserved only when they belong to the exact same
    run/worktree/branch. That makes resume idempotent without permitting an
    identity collision to silently reuse another run's data directory.
    """
    root = Path(workdir).resolve()
    worktree = Path(worktree_path).resolve()
    data_root = (root / DATA_REL / run_id).resolve()
    path = manifest_path(root, run_id)

    if path.exists():
        existing = load_manifest(path)
        mismatches = []
        for field, expected in (
            ("run_id", run_id),
            ("repository_path", str(root)),
            ("worktree_path", str(worktree)),
            ("branch", branch),
            ("data_root", str(data_root)),
        ):
            if existing.get(field) != expected:
                mismatches.append(field)
        if mismatches:
            raise DataPlaneError(
                "existing data manifest identity mismatch: " + ", ".join(mismatches)
            )
        data_root.mkdir(parents=True, exist_ok=True)
        return existing

    data_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "repository_path": str(root),
        "worktree_path": str(worktree),
        "branch": branch,
        "data_root": str(data_root),
        "created_at": _now(),
        "surfaces": [],
    }
    _atomic_write(path, manifest)
    return manifest


def load_manifest(path: Path | str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataPlaneError(f"manifest unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise DataPlaneError("manifest root is not an object")
    return value


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _isolation_errors(surface: dict[str, Any], prefix: str, data_root: Path | None) -> list[str]:
    isolation = surface.get("isolation")
    if isolation not in ISOLATION_MODES:
        return [f"{prefix}.isolation must be one of {sorted(ISOLATION_MODES)}"]
    if isolation == "shared_readonly":
        return [f"{prefix} shared_readonly surface cannot be writable"] if surface.get("writable") else []
    if isolation == "shared_serialized":
        return [f"{prefix} shared_serialized surface requires writer"] if not _is_nonempty_text(surface.get("writer")) else []
    if isolation == "external_namespaced":
        return [f"{prefix} external_namespaced surface requires namespace"] if not _is_nonempty_text(surface.get("namespace")) else []
    raw_path = surface.get("path")
    if not _is_nonempty_text(raw_path):
        return [f"{prefix} per_worktree surface requires path"]
    if data_root is None:
        return ["manifest.data_root is required for per_worktree surfaces"]
    resolved_path = _resolve(raw_path, data_root)
    if resolved_path != data_root and data_root not in resolved_path.parents:
        return [f"{prefix} path escapes manifest.data_root"]
    return []


def _validate_surface(surface: dict[str, Any], prefix: str, data_root: Path | None) -> list[str]:
    errors: list[str] = []
    for field in ("id", "kind", "resource_key"):
        if not _is_nonempty_text(surface.get(field)):
            errors.append(f"{prefix}.{field} is required")
    if surface.get("authority") not in AUTHORITIES:
        errors.append(f"{prefix}.authority must be one of {sorted(AUTHORITIES)}")
    if not isinstance(surface.get("writable"), bool):
        errors.append(f"{prefix}.writable must be boolean")
    if surface.get("status", "active") not in SURFACE_STATUSES:
        errors.append(f"{prefix}.status is invalid: {surface.get('status')!r}")
    errors.extend(_isolation_errors(surface, prefix, data_root))
    return errors


def _surface_errors(manifest: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    surfaces = manifest.get("surfaces")
    if not isinstance(surfaces, list):
        return ["surfaces is not a list"], []
    data_root_raw = manifest.get("data_root")
    data_root = _resolve(data_root_raw) if _is_nonempty_text(data_root_raw) else None
    errors: list[str] = []
    normalized = [surface for surface in surfaces if isinstance(surface, dict)]
    errors.extend(
        f"surface[{index}] is not an object"
        for index, surface in enumerate(surfaces)
        if not isinstance(surface, dict)
    )
    for index, surface in enumerate(normalized):
        errors.extend(_validate_surface(surface, f"surface[{index}]", data_root))
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for surface in normalized:
        surface_id = surface.get("id")
        if not _is_nonempty_text(surface_id):
            continue
        if surface_id in seen_ids:
            duplicate_ids.add(surface_id)
        seen_ids.add(surface_id)
    errors.extend(f"duplicate surface id: {surface_id}" for surface_id in sorted(duplicate_ids))
    return errors, normalized


def _manifest_identity_errors(manifest: dict[str, Any], expected_run_id: str | None) -> tuple[list[str], str | None]:
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    run_id = manifest.get("run_id")
    if not _is_nonempty_text(run_id):
        errors.append("run_id is required")
        return errors, None
    if expected_run_id is not None and run_id != expected_run_id:
        errors.append(f"manifest run_id {run_id!r} does not match expected {expected_run_id!r}")
    for field in ("repository_path", "worktree_path", "branch", "data_root"):
        if not _is_nonempty_text(manifest.get(field)):
            errors.append(f"{field} is required")
    if _is_nonempty_text(manifest.get("repository_path")) and _is_nonempty_text(manifest.get("data_root")):
        expected_root = (_resolve(manifest["repository_path"]) / DATA_REL / run_id).resolve()
        if _resolve(manifest["data_root"]) != expected_root:
            errors.append("data_root does not match the allocated repository run path")
    return errors, run_id


def _active_writable(surfaces: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        surface for surface in surfaces
        if surface.get("writable") is True and surface.get("status", "active") == "active"
    ]


def _active_resource_errors(surfaces: Iterable[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    by_key: dict[str, dict[str, Any]] = {}
    for surface in surfaces:
        key = str(surface.get("resource_key") or "")
        previous = by_key.get(key)
        if previous is not None and not _serialized_pair(previous, surface):
            errors.append(f"duplicate active writable resource_key in manifest: {key}")
        by_key[key] = surface
    return errors


def _peer_collision_errors(
    own_surfaces: Iterable[dict[str, Any]], peer_manifests: Iterable[dict[str, Any]], run_id: str | None
) -> list[str]:
    errors: list[str] = []
    own_by_key = {str(surface.get("resource_key") or ""): surface for surface in own_surfaces}
    for peer in peer_manifests:
        if peer.get("run_id") == run_id:
            continue
        peer_errors, peer_surfaces = _surface_errors(peer)
        if peer_errors:
            errors.append(f"peer manifest {peer.get('run_id', '<unknown>')} is invalid")
            continue
        for surface in _active_writable(peer_surfaces):
            key = str(surface.get("resource_key") or "")
            own = own_by_key.get(key)
            if own is not None and not _serialized_pair(own, surface):
                errors.append(f"active writable resource collision: {key} (run {peer.get('run_id', '<unknown>')})")
    return errors


def validate_manifest(
    manifest: dict[str, Any],
    *,
    expected_run_id: str | None = None,
    peer_manifests: Iterable[dict[str, Any]] = (),
    check_collisions: bool = True,
) -> dict[str, Any]:
    """Validate schema and active writable-resource ownership."""
    errors, run_id = _manifest_identity_errors(manifest, expected_run_id)
    surface_errors, surfaces = _surface_errors(manifest)
    errors.extend(surface_errors)
    own_active = _active_writable(surfaces)
    errors.extend(_active_resource_errors(own_active))
    if check_collisions:
        errors.extend(_peer_collision_errors(own_active, peer_manifests, run_id))
    return {"ok": not errors, "run_id": run_id, "errors": errors}


def _serialized_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("isolation") == "shared_serialized"
        and right.get("isolation") == "shared_serialized"
        and isinstance(left.get("writer"), str)
        and bool(left.get("writer"))
        and left.get("writer") == right.get("writer")
    )


def _peer_manifests(workdir: Path, excluding_run_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    peers: list[dict[str, Any]] = []
    errors: list[str] = []
    directory = workdir / MANIFESTS_REL
    if not directory.exists():
        return peers, errors
    for path in sorted(directory.glob("*.json")):
        if path.name == f"{excluding_run_id}.json":
            continue
        try:
            peers.append(load_manifest(path))
        except DataPlaneError as exc:
            errors.append(f"peer manifest {path.name} unavailable: {exc}")
    return peers, errors


def validate_manifest_file(
    workdir: Path | str,
    path: Path | str,
    *,
    expected_run_id: str | None = None,
    check_collisions: bool = True,
) -> dict[str, Any]:
    root = Path(workdir).resolve()
    try:
        manifest = load_manifest(path)
    except DataPlaneError as exc:
        return {"ok": False, "errors": [str(exc)]}
    run_id = expected_run_id or manifest.get("run_id")
    peers, peer_errors = _peer_manifests(root, str(run_id or "")) if check_collisions else ([], [])
    result = validate_manifest(
        manifest,
        expected_run_id=expected_run_id,
        peer_manifests=peers,
        check_collisions=check_collisions,
    )
    result["manifest_path"] = str(Path(path).resolve())
    result["errors"] = peer_errors + result["errors"]
    result["ok"] = not result["errors"]
    return result


def add_surface(
    workdir: Path | str,
    path: Path | str,
    surface: dict[str, Any],
) -> dict[str, Any]:
    """Validate and atomically add one declared data surface to a run manifest."""
    root = Path(workdir).resolve()
    manifest_path_value = Path(path).resolve()
    manifest = load_manifest(manifest_path_value)
    current = manifest.get("surfaces")
    if not isinstance(current, list):
        raise DataPlaneError("surfaces is not a list")
    if not isinstance(surface, dict):
        raise DataPlaneError("surface is not an object")
    candidate = dict(manifest)
    candidate["surfaces"] = [*current, surface]
    run_id = candidate.get("run_id")
    peers, peer_errors = _peer_manifests(root, str(run_id or ""))
    verdict = validate_manifest(candidate, peer_manifests=peers)
    errors = peer_errors + verdict["errors"]
    if errors:
        raise DataPlaneError("; ".join(errors))
    _atomic_write(manifest_path_value, candidate)
    return candidate


def check_terminal_manifest(
    workdir: Path | str,
    path: Path | str,
    *,
    expected_run_id: str,
) -> dict[str, Any]:
    """Validate the exact manifest and require terminal owned data surfaces."""
    result = validate_manifest_file(
        workdir, path, expected_run_id=expected_run_id, check_collisions=False
    )
    if not result["ok"]:
        return result
    manifest = load_manifest(path)
    active = [
        str(surface.get("id") or "<unknown>")
        for surface in manifest.get("surfaces", [])
        if isinstance(surface, dict)
        and surface.get("writable") is True
        and surface.get("status", "active") not in TERMINAL_STATUSES
    ]
    if active:
        result["errors"].append(
            "owned writable surfaces are not terminal: " + ", ".join(active)
        )
        result["ok"] = False
    return result


def set_surface_status(
    path: Path | str,
    *,
    surface_id: str,
    status: str,
) -> dict[str, Any]:
    """Record an explicit terminal disposition; adapters do their own cleanup."""
    if status not in TERMINAL_STATUSES:
        raise DataPlaneError(f"status must be terminal: {sorted(TERMINAL_STATUSES)}")
    manifest_path_value = Path(path).resolve()
    manifest = load_manifest(manifest_path_value)
    for surface in manifest.get("surfaces", []):
        if isinstance(surface, dict) and surface.get("id") == surface_id:
            surface["status"] = status
            surface["closed_at"] = _now()
            _atomic_write(manifest_path_value, manifest)
            return manifest
    raise DataPlaneError(f"surface not found: {surface_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--workdir", default=".")
    init.add_argument("--run-id", required=True)
    init.add_argument("--worktree", required=True)
    init.add_argument("--branch", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--workdir", default=".")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--run-id")
    terminal = subparsers.add_parser("terminal")
    terminal.add_argument("--workdir", default=".")
    terminal.add_argument("--manifest", required=True)
    terminal.add_argument("--run-id", required=True)
    add = subparsers.add_parser("add")
    add.add_argument("--workdir", default=".")
    add.add_argument("--manifest", required=True)
    add.add_argument("--surface-json", required=True)
    close = subparsers.add_parser("close")
    close.add_argument("--manifest", required=True)
    close.add_argument("--surface-id", required=True)
    close.add_argument("--status", required=True, choices=sorted(TERMINAL_STATUSES))
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_manifest(
                args.workdir, run_id=args.run_id, worktree_path=args.worktree, branch=args.branch
            )
            output = {"ok": True, "manifest_path": str(manifest_path(args.workdir, args.run_id)), "manifest": result}
        elif args.command == "validate":
            output = validate_manifest_file(args.workdir, args.manifest, expected_run_id=args.run_id)
        elif args.command == "terminal":
            output = check_terminal_manifest(args.workdir, args.manifest, expected_run_id=args.run_id)
        elif args.command == "add":
            try:
                surface = json.loads(args.surface_json)
            except json.JSONDecodeError as exc:
                raise DataPlaneError(f"surface-json is invalid: {exc}") from exc
            output = {"ok": True, "manifest": add_surface(args.workdir, args.manifest, surface)}
        else:
            output = {"ok": True, "manifest": set_surface_status(args.manifest, surface_id=args.surface_id, status=args.status)}
    except DataPlaneError as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
