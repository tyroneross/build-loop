#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
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
    --registry PATH     default ``build-loop-memory/projects/<project>/architecture/known_violations.json``
    --dry-run           don't write decisions, don't mutate registry on disk

Stdlib only. No new pyproject deps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"

# Low-signal auto-capture filter (decision 0092, 2026-05-08):
# orphan and hotspot rules at confidence:inferred + source:auto-inferred
# produce zero-judgment per-file decisions that swamp the decision tree.
# Filtered violations skip the per-violation MD and instead append a line
# to ``projects/<project>/architecture/auto-violations.jsonl`` plus aggregate
# into a single rollup decision MD per scan. Other rules (circular-dependency,
# layer-violation, database-isolation, frontend-direct-db) and any
# confirmed-confidence violation continue to write full per-violation MDs
# because they encode deliberate architectural intent.
LOW_SIGNAL_RULES = frozenset({"orphan", "hotspot"})
DEFAULT_REGISTRY_DESC = (
    "build-loop-memory/projects/<project>/architecture/known_violations.json"
)


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


def _confidence_for_severity(severity: str) -> str:
    """Map severity → confidence per write_decision.py's VALID_CONFIDENCES.

    Architecture rule hits are deterministic grep-checkable findings, so:
      error|critical|blocker → ``confirmed`` (per-violation decision MD)
      warn|warning|major|*   → ``inferred`` (filterable when rule is low-signal)
    Filter uses this so the per-violation flow and the filter agree.
    """
    sev_lc = (severity or "").lower()
    if sev_lc in {"error", "critical", "blocker"}:
        return "confirmed"
    return "inferred"


def _resolve_write_decision_script() -> Path | None:
    """Return path to write_decision.py if it exists alongside this script."""
    candidate = Path(__file__).resolve().parent / "write_decision.py"
    return candidate if candidate.exists() else None


def _invoke_write_decision(
    *,
    project: str,
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
    # 'assumed', 'confirmed', 'explicit', 'inferred'). Shared with the
    # low-signal filter so both flows agree on the same confidence label.
    confidence = _confidence_for_severity(severity)

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
        "--project",
        project,
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


# ---------- low-signal rollup helpers (decision 0092) ----------

def _resolve_run_id() -> str:
    """Run id for naming the per-scan rollup MD.

    Priority: ``$BUILD_LOOP_RUN_ID`` env var (orchestrator-set) → current
    UTC date in ``YYYY-MM-DD`` form. Predictable for tests; survives
    orchestrator restart.
    """
    rid = os.environ.get("BUILD_LOOP_RUN_ID", "").strip()
    if rid:
        return rid
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _resolve_project_for_rollup(workdir: Path) -> str:
    """Project tag used in the rollup MD path.

    Uses `$BUILD_LOOP_PROJECT_TAG` when set so tests and orchestrators can
    pin the destination. Otherwise mirrors `write_decision.py` via
    `project_resolver.resolve_project(workdir)`.
    """
    tag = os.environ.get("BUILD_LOOP_PROJECT_TAG", "").strip()
    if tag:
        return tag
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from project_resolver import resolve_project  # type: ignore
        finally:
            try:
                sys.path.remove(str(Path(__file__).resolve().parent))
            except ValueError:
                pass
        return resolve_project(workdir)
    except Exception:
        return "_unscoped"


def _resolve_architecture_dir(project: str) -> Path | None:
    """Return the canonical project architecture dir, or None on error."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from _paths import project_architecture_dir  # type: ignore
        finally:
            try:
                sys.path.remove(str(Path(__file__).resolve().parent))
            except ValueError:
                pass
        return project_architecture_dir(project)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[capture_arch_violation] WARN: architecture_dir unavailable "
            f"({exc!r}); skipping architecture artifact write.",
            file=sys.stderr,
        )
        return None


def _resolve_decisions_dir(project: str) -> Path | None:
    """Return the global decisions dir for ``project``, or ``None`` on error.

    Imports `_paths.decisions_dir_for_project` lazily so tests can stub
    `$AGENT_MEMORY_ROOT` per-invocation. Returns ``None`` (caller logs
    a warning) when the helper is unimportable or rejects the project
    tag — the rollup is best-effort, never fatal.
    """
    try:
        # _paths.py lives next to this script; use a sys.path-clean import.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from _paths import decisions_dir_for_project  # type: ignore
        finally:
            try:
                sys.path.remove(str(Path(__file__).resolve().parent))
            except ValueError:
                pass
        return decisions_dir_for_project(project)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[capture_arch_violation] WARN: rollup decisions_dir unavailable "
            f"({exc!r}); skipping rollup write.",
            file=sys.stderr,
        )
        return None


def _next_decision_prefix(decisions_dir: Path) -> str:
    """Pick the next 4-digit numeric prefix for a new decision in ``decisions_dir``.

    Scans existing ``NNNN-*.md`` filenames, returns ``max + 1`` zero-padded
    to 4 digits. Empty/missing dir → ``"0001"``. Same convention as
    ``write_decision.py``; accept the rare race for an audit-trail file
    (no atomic counter needed).
    """
    if not decisions_dir.exists():
        return "0001"
    max_n = 0
    pattern = re.compile(r"^(\d{4})-")
    for entry in decisions_dir.iterdir():
        if not entry.is_file():
            continue
        m = pattern.match(entry.name)
        if m:
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if n > max_n:
                max_n = n
    return f"{max_n + 1:04d}"


def _existing_rollup_for_run(decisions_dir: Path, run_id: str) -> Path | None:
    """Return path of an existing rollup file for this run_id, if any.

    Looks for ``NNNN-<run_id>-architecture-violation-rollup.md`` in
    ``decisions_dir``. Used so a re-run of the same scan overwrites the
    same file rather than allocating a new prefix (idempotent).
    """
    if not decisions_dir.exists():
        return None
    suffix = f"-{run_id}-architecture-violation-rollup.md"
    for entry in decisions_dir.iterdir():
        if entry.is_file() and entry.name.endswith(suffix):
            return entry
    return None


def _append_jsonl_line(jsonl_path: Path, payload: dict) -> None:
    """Append one JSON object as a line to ``jsonl_path``.

    Creates parent dirs on demand. Append-only; never rewrites the file.
    Best-effort — surfaces a warning on OSError but does not raise so
    the capture script's stdout contract is preserved.
    """
    try:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True))
            fh.write("\n")
    except OSError as exc:
        print(
            f"[capture_arch_violation] WARN: jsonl append failed ({exc}); "
            "filtered violation not recorded.",
            file=sys.stderr,
        )


def _format_rollup_md(*, project: str, run_id: str, entries: list[dict]) -> str:
    """Render the rollup MD body. Minimal: H1, count line, table.

    No frontmatter — rollups are audit-trail summaries, not full
    decisions. Full decisions go through write_decision.py.
    """
    n = len(entries)
    lines: list[str] = []
    lines.append(f"# Architecture violation rollup — {project} ({run_id})")
    lines.append("")
    lines.append(f"Total filtered (low-signal auto-inferred) violations: **{n}**")
    lines.append("")
    lines.append("| Rule | Entity | Severity |")
    lines.append("|---|---|---|")
    for e in entries:
        rule = str(e.get("rule", "")).replace("|", r"\|")
        ent = str(e.get("entity", "")).replace("|", r"\|")
        sev = str(e.get("severity", "")).replace("|", r"\|")
        lines.append(f"| {rule} | {ent} | {sev} |")
    lines.append("")
    lines.append(
        "_Generated by `scripts/capture_arch_violation.py`. "
        "See decision 0092 for the rollup contract; the canonical "
        "live status of every architecture violation is "
        "`known_violations.json`._"
    )
    lines.append("")
    return "\n".join(lines)


def _atomic_write_text(path: Path, body: str) -> None:
    """Write text atomically via tempfile + os.replace inside the target dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _violation_is_low_signal(*, rule_id: str, confidence: str) -> bool:
    """Filter predicate per decision 0092.

    Filters when ALL three hold:
      - confidence == 'inferred'  (computed by caller from severity)
      - rule_id in LOW_SIGNAL_RULES
    The third gate from decision 0092 (``source == 'auto-inferred'``)
    is implied: this script always emits ``source = f"auto-{confidence}"``,
    so confidence==inferred ⇒ source==auto-inferred. Caller only checks
    confidence + rule.
    """
    return (confidence == "inferred") and (rule_id in LOW_SIGNAL_RULES)


# ---------- main ----------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Capture architecture violations into known_violations.json"
    )
    p.add_argument(
        "--registry",
        default=None,
        help=f"Path to registry JSON (default: {DEFAULT_REGISTRY_DESC})",
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

    workdir = Path(args.workdir).resolve()
    project_tag = _resolve_project_for_rollup(workdir)
    architecture_dir = _resolve_architecture_dir(project_tag)
    if args.registry:
        registry_path = Path(args.registry)
    elif architecture_dir is not None:
        registry_path = architecture_dir / "known_violations.json"
    else:
        print(
            "[capture_arch_violation] ERROR: no registry path available",
            file=sys.stderr,
        )
        return 2
    registry = _load_registry(registry_path)
    violations_out = registry.setdefault("violations", {})

    new_count = 0
    dedup_count = 0
    decision_files: list[str] = []
    rollup_entries: list[dict] = []  # populated only when filter fires this scan
    now = _now_iso()
    run_id = _resolve_run_id()
    jsonl_path = (
        architecture_dir / "auto-violations.jsonl"
        if architecture_dir is not None
        else registry_path.parent / "auto-violations.jsonl"
    )

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

        # Decision 0092 filter: orphan/hotspot rules with inferred confidence
        # are auto-inferred audit-trail entries; suppress per-violation MDs and
        # accumulate into a single rollup. Registry stays canonical for these
        # too — the JSONL + rollup are observability surfaces, not state.
        violation_confidence = _confidence_for_severity(severity)
        is_low_signal = _violation_is_low_signal(
            rule_id=rule_id, confidence=violation_confidence
        )

        if is_low_signal:
            entity = sorted_components[0] if sorted_components else "unknown"
            rollup_payload = {
                "ts": now,
                "project": project_tag,
                "rule": rule_id,
                "entity": entity,
                "confidence": violation_confidence,
                "source": f"auto-{violation_confidence}",
                "severity": severity,
            }
            if not args.dry_run:
                _append_jsonl_line(jsonl_path, rollup_payload)
                rollup_entries.append(rollup_payload)
            # No write_decision call for filtered violations; entry still
            # lands in the registry below for canonical state.
        elif not args.dry_run:
            decision_id = _invoke_write_decision(
                project=project_tag,
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
                decisions_dir = _resolve_decisions_dir(project_tag)
                if decisions_dir is not None:
                    decision_files.append(str(decisions_dir / f"{decision_id}-*.md"))
                else:
                    decision_files.append(f"{decision_id}-*.md")

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

    # Per-scan rollup (decision 0092). Only fires when the filter matched
    # this scan AND we're not in dry-run. Re-runs of the same run_id
    # overwrite the existing rollup file (idempotent).
    rollup_info: dict | None = None
    if rollup_entries and not args.dry_run:
        decisions_dir = _resolve_decisions_dir(project_tag)
        if decisions_dir is not None:
            existing = _existing_rollup_for_run(decisions_dir, run_id)
            if existing is not None:
                rollup_path = existing
            else:
                prefix = _next_decision_prefix(decisions_dir)
                rollup_name = (
                    f"{prefix}-{run_id}-architecture-violation-rollup.md"
                )
                rollup_path = decisions_dir / rollup_name
            try:
                body = _format_rollup_md(
                    project=project_tag,
                    run_id=run_id,
                    entries=rollup_entries,
                )
                _atomic_write_text(rollup_path, body)
                rollup_info = {
                    "path": str(rollup_path),
                    "entries": len(rollup_entries),
                }
            except OSError as exc:
                print(
                    f"[capture_arch_violation] WARN: rollup write failed "
                    f"({exc}); jsonl entries still recorded.",
                    file=sys.stderr,
                )

    out: dict[str, Any] = {
        "new_count": new_count,
        "dedup_count": dedup_count,
        "decision_files": decision_files,
        "schema_version": SCHEMA_VERSION,
    }
    if rollup_info is not None:
        out["rollup"] = rollup_info
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
