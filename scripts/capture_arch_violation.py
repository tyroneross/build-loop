#!/usr/bin/env python3
"""Capture architecture violations into the known_violations registry.

Stdin contract:
    JSON envelope ``{"violations": [{"rule_id", "severity", "components",
    "message", ...}, ...]}`` produced by
    ``python -m build_loop.architecture rules --json``.

Behavior:
    For each input violation:
        - Compute stable ID: ``sha256(rule_id + sorted(components) + message)[:12]``.
        - If ID already in registry, skip (dedup); update ``last_seen`` and
          increment ``last_seen_count``.
        - If new, add to registry and invoke ``scripts/write_decision.py``
          to log a decision (unless ``--dry-run``).
    Atomic registry writes (temp + os.replace).

Stdout:
    JSON object ``{"new_count", "dedup_count", "decision_files",
    "schema_version"}``.

CLI flags:
    --registry PATH     default ``.episodic/architecture/known_violations.json``
    --dry-run           don't write decisions, don't mutate registry on disk

Stdlib only. No new pyproject deps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
DEFAULT_REGISTRY = ".episodic/architecture/known_violations.json"


# ---------- helpers ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_id(rule_id: str, components: list[str], message: str) -> str:
    """Deterministic 12-hex-char id; reorder-immune over components."""
    sorted_components = sorted(str(c) for c in (components or []))
    payload = "\x1f".join([str(rule_id), "\x1e".join(sorted_components), str(message)])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:12]


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via tempfile + os.replace inside the target dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of orphan temp file.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_registry(path: Path) -> dict:
    """Load registry or return a default-shaped one if missing/empty."""
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now_iso(),
            "violations": {},
        }
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[capture_arch_violation] WARN: registry unreadable ({exc}); "
            "treating as empty.",
            file=sys.stderr,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now_iso(),
            "violations": {},
        }
    # Backfill required keys defensively.
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("created_at", _now_iso())
    data.setdefault("violations", {})
    return data


def _resolve_write_decision_script() -> Path | None:
    """Return path to write_decision.py if it exists alongside this script."""
    candidate = Path(__file__).resolve().parent / "write_decision.py"
    return candidate if candidate.exists() else None


def _invoke_write_decision(
    *,
    violation_id: str,
    rule_id: str,
    severity: str,
    components: list[str],
    message: str,
    first_seen: str,
    workdir: Path,
) -> str | None:
    """Invoke scripts/write_decision.py for a new violation.

    Returns the decision_id on success, ``None`` on graceful failure
    (missing script, non-zero exit). Never raises.
    """
    script = _resolve_write_decision_script()
    if script is None:
        print(
            "[capture_arch_violation] WARN: write_decision.py missing; "
            "skipping decision write.",
            file=sys.stderr,
        )
        return None

    sorted_components = sorted(str(c) for c in (components or []))
    title = f"Architecture violation: {rule_id}"
    decision_body = (
        f"Track and remediate. {severity} per build-loop architecture rules."
    )
    context = message or "(no message)"
    consequences = (
        "Violation tracked in known_violations.json; remediation required to "
        "remove from registry."
    )
    metadata_blob = {
        "violation_id": violation_id,
        "rule_id": rule_id,
        "severity": severity,
        "components": sorted_components,
        "first_seen": first_seen,
    }
    notes = (
        "Architecture violation captured by scripts/capture_arch_violation.py.\n"
        "metadata: " + json.dumps(metadata_blob, sort_keys=True)
    )

    # Map severity → confidence (using write_decision.py's VALID_CONFIDENCES:
    # 'assumed', 'confirmed', 'explicit', 'inferred'). Architecture rule hits
    # are deterministic grep-checkable findings → 'confirmed' for hard hits,
    # 'inferred' for warn/info severities where the rule fires on heuristics.
    sev_lc = (severity or "").lower()
    if sev_lc in {"error", "critical", "blocker"}:
        confidence = "confirmed"
    elif sev_lc in {"warn", "warning", "major"}:
        confidence = "inferred"
    else:
        confidence = "inferred"

    # Tag vocabulary in write_decision.py is closed (architecture, data,
    # infra, performance, process, security, testing, tooling, ui).
    # Custom labels must be prefixed `proposed:`. Use those for the rule
    # specifics so the decision stays attributable without breaking the
    # taxonomy gate.
    tag_parts = ["architecture", "proposed:violation"]
    if rule_id:
        tag_parts.append(f"proposed:{rule_id}")
    tags = ",".join(tag_parts)

    entity = f"architecture/{rule_id}/{violation_id}"

    cmd = [
        sys.executable,
        str(script),
        "--workdir",
        str(workdir),
        "--title",
        title,
        "--decision",
        decision_body,
        "--context",
        context,
        "--consequences",
        consequences,
        "--notes",
        notes,
        "--tags",
        tags,
        "--primary-tag",
        "architecture",
        "--entity",
        entity,
        "--confidence",
        confidence,
        # Map confidence → source per write_decision.py taxonomy
        # ('auto-confirmed', 'auto-inferred', etc.). Capture script is
        # always automated, so prefix is always `auto-`.
        "--source",
        f"auto-{confidence}",
        # 'review' is not in write_decision.py's task-category taxonomy.
        # Architecture violation capture is an investigative finding →
        # 'research' fits best per the allowed list (bugfix, config,
        # docs, experiment, feature, migration, refactor, research,
        # unknown).
        "--task-category",
        "research",
        "--no-db",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(
            f"[capture_arch_violation] WARN: write_decision invocation failed "
            f"({exc!r}); skipping.",
            file=sys.stderr,
        )
        return None

    if result.returncode != 0:
        # Non-fatal: surface stderr for debugging but continue.
        snippet = (result.stderr or "").strip().splitlines()[-3:]
        print(
            "[capture_arch_violation] WARN: write_decision exited "
            f"{result.returncode}: {' | '.join(snippet)}",
            file=sys.stderr,
        )
        return None

    decision_id = (result.stdout or "").strip()
    return decision_id or None


# ---------- main ----------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Capture architecture violations into known_violations.json"
    )
    p.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"Path to registry JSON (default: {DEFAULT_REGISTRY})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write decisions, don't persist registry mutations.",
    )
    p.add_argument(
        "--workdir",
        default=".",
        help="Project root passed to write_decision.py (default: cwd)",
    )
    p.add_argument(
        "--input",
        default=None,
        help="Optional path to JSON input (default: read stdin).",
    )
    return p.parse_args(argv)


def _read_input(args: argparse.Namespace) -> dict:
    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            return json.load(fh)
    raw = sys.stdin.read()
    if not raw.strip():
        return {"violations": []}
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        envelope = _read_input(args)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[capture_arch_violation] ERROR: invalid input ({exc})", file=sys.stderr)
        return 1

    violations_in = envelope.get("violations", []) or []
    if not isinstance(violations_in, list):
        print(
            "[capture_arch_violation] ERROR: 'violations' must be a list",
            file=sys.stderr,
        )
        return 1

    registry_path = Path(args.registry)
    workdir = Path(args.workdir).resolve()
    registry = _load_registry(registry_path)
    violations_out = registry.setdefault("violations", {})

    new_count = 0
    dedup_count = 0
    decision_files: list[str] = []
    now = _now_iso()

    for raw in violations_in:
        if not isinstance(raw, dict):
            print(
                "[capture_arch_violation] WARN: skipping non-object violation",
                file=sys.stderr,
            )
            continue
        # Accept both shapes:
        #   - {"rule_id", "components": [...], ...}  (long-form, capture-native)
        #   - {"rule",    "component_id": "...",     (short-form, native engine
        #                "component_ids": [...]}      build_loop.architecture
        #                                             rules emits this shape)
        rule_id = str(raw.get("rule_id") or raw.get("rule") or "")
        severity = str(raw.get("severity", "") or "")
        components = raw.get("components")
        if components is None:
            # Fall back to component_ids[] or single component_id.
            cids = raw.get("component_ids")
            if cids is None and raw.get("component_id"):
                cids = [raw.get("component_id")]
            components = cids or []
        if not isinstance(components, list):
            components = [str(components)]
        message = str(raw.get("message", "") or "")

        if not rule_id:
            print(
                "[capture_arch_violation] WARN: skipping violation with empty rule_id",
                file=sys.stderr,
            )
            continue

        vid = _stable_id(rule_id, components, message)

        if vid in violations_out:
            existing = violations_out[vid]
            existing["last_seen"] = now
            existing["last_seen_count"] = int(existing.get("last_seen_count", 1)) + 1
            dedup_count += 1
            continue

        # New violation.
        new_count += 1
        sorted_components = sorted(str(c) for c in components)
        entry = {
            "rule_id": rule_id,
            "severity": severity,
            "components": sorted_components,
            "message": message,
            "first_seen": now,
            "last_seen": now,
            "last_seen_count": 1,
            "decision_id": None,
        }

        if not args.dry_run:
            decision_id = _invoke_write_decision(
                violation_id=vid,
                rule_id=rule_id,
                severity=severity,
                components=sorted_components,
                message=message,
                first_seen=now,
                workdir=workdir,
            )
            if decision_id:
                entry["decision_id"] = decision_id
                # Path convention from write_decision.py: numbered MADR file.
                decision_files.append(f".episodic/decisions/{decision_id}-*.md")

        violations_out[vid] = entry

    # Refresh schema_version in case registry pre-dates current schema.
    registry["schema_version"] = SCHEMA_VERSION

    if not args.dry_run:
        try:
            _atomic_write_json(registry_path, registry)
        except OSError as exc:
            print(
                f"[capture_arch_violation] ERROR: registry write failed ({exc})",
                file=sys.stderr,
            )
            return 2

    out = {
        "new_count": new_count,
        "dedup_count": dedup_count,
        "decision_files": decision_files,
        "schema_version": SCHEMA_VERSION,
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
