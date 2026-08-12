#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Operational-state probe: what is actually ON, OFF, CORRUPT, or UNKNOWN.

Grep-bait (so a future agent hunting for this finds it): feature flag status,
ENABLE_* flags, dormant feature, silently disabled, runtime state, service
status, operational state inventory, env corruption, trailing newline in env
value, inline comment stored in env value, production env vs local env.

Why this exists (RCA 2026-07-25, atomize-ai): 59 of ~180 production env values
were corrupted for ~6 months — trailing newlines from `echo "$v" | vercel env
add`, and 13 values that captured an entire `.env` line INCLUDING its inline
`# comment` (naive `IFS='=' read -r key value` parsing). Flags are read with
strict `process.env.X === 'true'` and no trim, so every corrupted value
silently reads as OFF. No build-loop control ever asked "is what I think is
running actually running?" — architecture inventories existed, an
operational-state inventory did not. This probe is that inventory, GENERATED
(never hand-maintained), and surfaced at Phase 1 Assess via
``context_bootstrap.py``.

Source-of-truth contract (hardened 2026-07-25 after coordinator review — the
first version classified from ``.env.local``, which is LOCAL DEV CONFIG and
was clean while production was corrupted, so the probe would have reported the
real failure as fine):

- Flags are classified ON / OFF / CORRUPT ONLY from a PRODUCTION source:
  ``vercel env pull --environment=production`` into a tempfile that is parsed
  and deleted. Pulled values are never persisted.
- When no production source is reachable (CLI missing, project not linked,
  pull failed), every flag is ``UNKNOWN`` with reason ``no_production_source``.
  Local files are NEVER silently substituted for production — reporting a
  state you did not observe is the same defect class as the original bug.
- Local env files still get an advisory HYGIENE scan (corrupted values,
  duplicate keys) reported separately under ``local_hygiene`` and labeled
  non-production, because the corrupting habit writes through local files.
- ``env_source_kind`` in every artifact says what the classification is
  worth: ``production-pull`` | ``none``.
- Duplicate keys within a source are their own defect class
  (``duplicate-key``): last-write-wins ambiguity.

Contract (unchanged):
- Discovers flag READ sites in code (strict-boolean env compares + reads of
  flag-shaped names like ``ENABLE_*`` / ``*_ENABLED`` / ``FEATURE_*``).
- ``UNKNOWN`` is a first-class answer. ``CORRUPT`` carries ``reads_as`` (what
  strict `=== 'true'` yields) and ``intended`` (what the value was meant to be).
- Never emits secret values — only key names, value SHAPE, and hygiene defects.
- Fail-soft: any error yields a partial result with ``reasons``; never raises
  out of ``probe_ops_state``.

CLI:
    python3 scripts/ops_state_probe.py --workdir <repo> [--json] [--write]
      [--no-pull]
      --write persists .build-loop/ops-state.json and, when findings are
      nonzero, the generated memory-lane artifact
      <memory-root>/projects/<slug>/references/operational-state.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# Discovery: flag read sites in code
# ---------------------------------------------------------------------------

CODE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"}
SKIP_DIRS = {
    "node_modules", ".next", ".git", "dist", "build", "coverage", ".turbo",
    ".vercel", "__pycache__", ".venv", "venv", ".build-loop", "playwright-report",
    "test-results", "out", ".cache", "_archive",
}
MAX_FILES = 4000
MAX_FILE_BYTES = 1_000_000
PULL_TIMEOUT_S = 30

# strict boolean compare: process.env.X === 'true' (also ==, !==, !=)
_STRICT_TS = re.compile(
    r"process\.env\.([A-Z][A-Z0-9_]*)\s*[!=]==?\s*['\"]true['\"]"
)
# any read of a flag-shaped name
_FLAGGY_TS = re.compile(
    r"process\.env\.((?:ENABLE|FEATURE|DISABLE)_[A-Z0-9_]+|[A-Z0-9_]+_(?:ENABLED|DISABLED))\b"
)
_STRICT_PY = re.compile(
    r"os\.(?:environ\.get|getenv)\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\)?[^\n]{0,40}?[!=]=\s*['\"]true['\"]"
)
_FLAGGY_PY = re.compile(
    r"os\.(?:environ\.get|getenv|environ\[)\(?\s*['\"]((?:ENABLE|FEATURE|DISABLE)_[A-Z0-9_]+|[A-Z0-9_]+_(?:ENABLED|DISABLED))['\"]"
)

SECRETY = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASS|DSN|URL|URI|CREDENTIAL|COOKIE|SALT|PRIVATE)",
)


@dataclass
class FlagRead:
    name: str
    file: str
    line: int
    strict: bool  # read via untrimmed strict === 'true' compare


@dataclass
class FlagStatus:
    name: str
    status: str  # ON | OFF | UNKNOWN | CORRUPT
    strict_read: bool
    read_sites: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    reads_as: str | None = None   # for CORRUPT boolean flags: effective result
    intended: str | None = None   # what the value appears meant to be
    reason: str | None = None     # for UNKNOWN


def iter_code_files(workdir: Path) -> Iterable[Path]:
    count = 0
    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            p = Path(root) / fn
            if p.suffix not in CODE_SUFFIXES:
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            count += 1
            if count > MAX_FILES:
                return
            yield p


def discover_flag_reads(workdir: Path) -> list[FlagRead]:
    reads: dict[tuple[str, str, int], FlagRead] = {}
    for path in iter_code_files(workdir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(workdir))
        for i, line in enumerate(text.splitlines(), 1):
            if "process.env" not in line and "os.environ" not in line and "os.getenv" not in line:
                continue
            strict_names = set()
            for rx in (_STRICT_TS, _STRICT_PY):
                for m in rx.finditer(line):
                    strict_names.add(m.group(1))
            flaggy_names = set()
            for rx in (_FLAGGY_TS, _FLAGGY_PY):
                for m in rx.finditer(line):
                    flaggy_names.add(m.group(1))
            for name in strict_names | flaggy_names:
                key = (name, rel, i)
                reads[key] = FlagRead(
                    name=name, file=rel, line=i, strict=name in strict_names
                )
    return list(reads.values())


# ---------------------------------------------------------------------------
# Observation: env parsing (shared by production pull and local hygiene)
# ---------------------------------------------------------------------------

ENV_LINE = re.compile(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


@dataclass
class EnvObservation:
    key: str
    raw: str                 # raw right-hand side, exactly as stored
    defects: list[str]
    shape: str               # true|false|numeric|empty|other
    intended: str | None     # cleaned interpretation for boolean-ish values


def _hygiene(raw: str) -> tuple[list[str], str, str | None]:
    """Return (defects, shape, intended) for a raw env value."""
    defects: list[str] = []
    v = raw
    if v != v.rstrip():
        defects.append("trailing-whitespace")
    if "\\n" in v:
        defects.append("embedded-backslash-n")
    # strip one layer of quotes for inspection
    stripped = v.strip()
    quote = ""
    if stripped[:1] in ("'", '"'):
        quote = stripped[0]
        if stripped.endswith(quote) and len(stripped) > 1:
            inner = stripped[1:-1]
        else:
            defects.append("unterminated-quote")
            inner = stripped[1:]
    else:
        inner = stripped
    if re.search(r"\s#", inner) or inner.startswith("#"):
        defects.append("inline-comment-in-value")
    if inner != inner.strip():
        defects.append("padded-value")
    if "\n" in inner:
        defects.append("embedded-newline")
    # intended interpretation: first whitespace-delimited token, comment and
    # literal \n escapes removed (a stored trailing newline surfaces as a
    # trailing `\n` escape when the store is materialized to dotenv text)
    cleaned = re.split(r"\s+#", inner, maxsplit=1)[0].strip().strip("'\"")
    while cleaned.endswith("\\n"):
        cleaned = cleaned[:-2].rstrip()
    token = cleaned.split()[0] if cleaned.split() else ""
    low = token.lower()
    if low in ("true", "false"):
        shape = low
        intended = low
    elif re.fullmatch(r"-?\d+(\.\d+)?", token or ""):
        shape = "numeric"
        intended = token
    elif token == "":
        shape = "empty"
        intended = None
    else:
        shape = "other"
        intended = None
    return defects, shape, intended


def parse_env_text(text: str) -> dict[str, EnvObservation]:
    """Parse raw dotenv text, preserving corruption; flags duplicate keys.

    Duplicate keys use last-write-wins for the value (matching dotenv
    semantics) but carry a ``duplicate-key`` defect — the ambiguity itself is
    a defect class (which definition is live depends on parser order).
    """
    out: dict[str, EnvObservation] = {}
    pending_key: str | None = None
    for line in text.split("\n"):
        if pending_key is not None:
            # continuation of an unterminated quoted value (a stored real
            # newline). Mark the original observation; do not start a new key.
            obs = out[pending_key]
            if "embedded-newline" not in obs.defects:
                obs.defects.append("embedded-newline")
            pending_key = None if line.rstrip().endswith(('"', "'")) else pending_key
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = ENV_LINE.match(line)
        if not m:
            continue
        key, raw = m.group(2), m.group(3)
        defects, shape, intended = _hygiene(raw)
        if key in out:
            defects = defects + ["duplicate-key"]
        out[key] = EnvObservation(
            key=key, raw=raw, defects=defects, shape=shape, intended=intended
        )
        if "unterminated-quote" in defects:
            pending_key = key
    return out


def parse_env_file(path: Path) -> dict[str, EnvObservation]:
    try:
        return parse_env_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Production source (the ONLY source flags are classified from)
# ---------------------------------------------------------------------------

def fetch_production_env(workdir: Path) -> tuple[str | None, str]:
    """Pull production env values via the Vercel CLI into a throwaway file.

    Returns ``(env_text, reason)``. ``env_text`` is None when production is
    unreachable; ``reason`` says why (surfaced verbatim in the artifact).
    The tempfile is always deleted — pulled values are never persisted.
    """
    if not (workdir / ".vercel" / "project.json").is_file():
        return None, "no_vercel_project_link"
    if shutil.which("vercel") is None:
        return None, "vercel_cli_not_found"
    tmpdir = tempfile.mkdtemp(prefix="ops-state-pull-")
    tmp = Path(tmpdir) / "production.env"
    try:
        proc = subprocess.run(
            ["vercel", "env", "pull", "--environment=production", "--yes", str(tmp)],
            cwd=workdir, capture_output=True, text=True, timeout=PULL_TIMEOUT_S,
        )
        if proc.returncode != 0 or not tmp.is_file():
            detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:160]
            return None, f"pull_failed: {detail}"
        return tmp.read_text(encoding="utf-8", errors="replace"), "production-pull"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"pull_error: {exc}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Local hygiene (advisory only — NEVER used to classify flags)
# ---------------------------------------------------------------------------

LOCAL_ENV_CANDIDATES = (".env.local", ".env.production", ".env",
                        ".env.development.local")


def local_hygiene_scan(workdir: Path) -> dict[str, Any] | None:
    """Hygiene-only scan of local env files: corrupted values + duplicates.

    Local files are dev config, not production truth — they matter here only
    because the corrupting habit (`echo | vercel env add` fed from these
    files) writes through them. Names and defects only; no values.
    """
    findings: list[dict[str, Any]] = []
    for name in LOCAL_ENV_CANDIDATES:
        p = workdir / name
        if not p.is_file():
            continue
        obs = parse_env_file(p)
        corrupt = [
            {"key": k, "defects": o.defects, "shape": o.shape}
            for k, o in sorted(obs.items()) if o.defects
        ]
        if corrupt:
            findings.append({"source": f"local-file:{name}", "corrupt_keys": corrupt})
    if not findings:
        return None
    return {
        "note": "advisory — local dev config, NOT production state",
        "files": findings,
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(
    reads: list[FlagRead],
    observations: dict[str, EnvObservation] | None,
    unknown_reason: str = "no_production_source",
) -> list[FlagStatus]:
    by_name: dict[str, list[FlagRead]] = {}
    for r in reads:
        by_name.setdefault(r.name, []).append(r)
    statuses: list[FlagStatus] = []
    for name in sorted(by_name):
        sites = by_name[name]
        strict = any(s.strict for s in sites)
        site_strs = [f"{s.file}:{s.line}" for s in sites[:5]]
        if observations is None:
            statuses.append(FlagStatus(
                name=name, status="UNKNOWN", strict_read=strict,
                read_sites=site_strs, reason=unknown_reason,
            ))
            continue
        obs = observations.get(name)
        if obs is None:
            statuses.append(FlagStatus(
                name=name, status="UNKNOWN", strict_read=strict,
                read_sites=site_strs, reason="not_in_source",
            ))
            continue
        if obs.defects:
            # strict `=== 'true'` on a defective value: only exact 'true'
            # matches, so the corrupted value reads as OFF regardless of intent.
            reads_as = "ON" if obs.raw == "true" else "OFF"
            statuses.append(FlagStatus(
                name=name, status="CORRUPT", strict_read=strict,
                read_sites=site_strs, defects=obs.defects,
                reads_as=reads_as if strict else None,
                intended=obs.intended,
            ))
            continue
        clean = obs.raw.strip().strip("'\"")
        if clean == "true":
            status = "ON"
        elif clean in ("false", "0", ""):
            status = "OFF"
        else:
            # present but not boolean-shaped: for a strict reader this is OFF
            status = "OFF" if strict else "UNKNOWN"
        statuses.append(FlagStatus(
            name=name, status=status, strict_read=strict, read_sites=site_strs,
        ))
    return statuses


def unreferenced_keys(
    reads: list[FlagRead], observations: dict[str, EnvObservation] | None
) -> list[str]:
    if not observations:
        return []
    read_names = {r.name for r in reads}
    return sorted(k for k in observations if k not in read_names)


def corrupt_nonflag_keys(
    observations: dict[str, EnvObservation] | None,
) -> list[dict[str, Any]]:
    """Hygiene defects on env keys that are not flag reads — names + defects only."""
    if not observations:
        return []
    out = []
    for k in sorted(observations):
        obs = observations[k]
        if obs.defects:
            out.append({"key": k, "defects": obs.defects, "shape": obs.shape})
    return out


# ---------------------------------------------------------------------------
# Probe entrypoint
# ---------------------------------------------------------------------------

def probe_ops_state(
    workdir: Path,
    allow_pull: bool = True,
    fetch: Callable[[Path], tuple[str | None, str]] = fetch_production_env,
) -> dict[str, Any]:
    """Fail-soft probe. Returns a JSON-serializable operational-state packet.

    ``env_source_kind`` is the honesty label: ``production-pull`` means the
    ON/OFF/CORRUPT classification reflects observed production values;
    ``none`` means production was unreachable and every flag is UNKNOWN.
    """
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workdir": str(workdir),
        "env_source_kind": "none",     # production-pull | none
        "env_source": None,            # human label incl. failure reason
        "flags": [],
        "counts": {"ON": 0, "OFF": 0, "UNKNOWN": 0, "CORRUPT": 0},
        "corrupt_values_total": 0,
        "corrupt_other_keys": [],
        "unreferenced_keys": [],
        "local_hygiene": None,
        "reasons": [],
    }
    try:
        workdir = workdir.resolve()
        reads = discover_flag_reads(workdir)
        observations: dict[str, EnvObservation] | None = None
        if allow_pull:
            prod_text, prod_reason = fetch(workdir)
        else:
            prod_text, prod_reason = None, "pull_disabled"
        if prod_text is not None:
            observations = parse_env_text(prod_text)
            result["env_source_kind"] = "production-pull"
            result["env_source"] = (
                "production-pull (vercel env pull --environment=production)"
            )
        else:
            result["env_source"] = f"none ({prod_reason})"
            result["reasons"].append(f"no_production_source: {prod_reason}")
        statuses = classify(reads, observations)
        result["flags"] = [asdict(s) for s in statuses]
        for s in statuses:
            result["counts"][s.status] += 1
        result["corrupt_other_keys"] = [
            e for e in corrupt_nonflag_keys(observations)
            if e["key"] not in {s.name for s in statuses}
        ]
        result["corrupt_values_total"] = (
            result["counts"]["CORRUPT"] + len(result["corrupt_other_keys"])
        )
        result["unreferenced_keys"] = unreferenced_keys(reads, observations)
        # Advisory local-file hygiene — the corrupting habit writes through
        # local files, so defects here predict future production corruption.
        result["local_hygiene"] = local_hygiene_scan(workdir)
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract
        result["reasons"].append(f"probe_error: {exc}")
    return result


def summary_line(result: dict[str, Any]) -> str:
    c = result["counts"]
    kind = result.get("env_source_kind") or "none"
    if kind == "production-pull":
        src = "prod, vercel pull"
    else:
        src = result.get("env_source") or "no production source"
    corrupt_names = [f["name"] for f in result["flags"] if f["status"] == "CORRUPT"][:4]
    extra_corrupt = len(result.get("corrupt_other_keys") or [])
    parts = [
        f"{c['ON']} ON / {c['OFF']} OFF / {c['CORRUPT']} CORRUPT / {c['UNKNOWN']} UNKNOWN flags",
        f"[source: {src}]",
    ]
    if corrupt_names:
        parts.append("CORRUPT: " + ", ".join(corrupt_names) + ("…" if c["CORRUPT"] > 4 else ""))
    if extra_corrupt:
        parts.append(f"+{extra_corrupt} corrupted non-flag values")
    lh = result.get("local_hygiene")
    if lh:
        n = sum(len(f["corrupt_keys"]) for f in lh["files"])
        parts.append(f"local-file hygiene (advisory, not prod): {n} defective keys")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Persistence: repo-local JSON + generated memory-lane artifact
# ---------------------------------------------------------------------------

def write_repo_artifact(workdir: Path, result: dict[str, Any]) -> Path | None:
    try:
        out = workdir / ".build-loop" / "ops-state.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return out
    except OSError:
        return None


def write_memory_artifact(project: str, result: dict[str, Any]) -> Path | None:
    """Generated operational-state reference in the project memory lane.

    Written only when there is something worth finding (CORRUPT / OFF /
    UNKNOWN flags exist) — the artifact answers "what is turned off or in
    unknown status", per the 2026-07-25 RCA requirement.
    """
    c = result["counts"]
    if not (c["CORRUPT"] or c["OFF"] or c["UNKNOWN"] or result["corrupt_other_keys"]):
        return None
    try:
        from _paths import project_root  # noqa: PLC0415
        lane = project_root(project) / "references"
        if not lane.parent.is_dir():
            return None  # no memory lane for this project; repo JSON still exists
        lane.mkdir(parents=True, exist_ok=True)
        out = lane / "operational-state.md"
        lines = [
            "---",
            "name: operational-state",
            "type: reference",
            "generated: true",
            "generator: build-loop/scripts/ops_state_probe.py",
            f"generated_at: \"{result['generated_at']}\"",
            f"env_source_kind: {result.get('env_source_kind')}",
            "description: \"GENERATED feature-flag / operational-state inventory:",
            "  which features are ON, OFF, CORRUPT, or UNKNOWN in the runtime env.",
            "  Regenerate via ops_state_probe.py --write; do not hand-edit.\"",
            "---",
            "",
            "# Operational state (feature flags, services, runtime env)",
            "",
            "GENERATED — regenerate with `python3 scripts/ops_state_probe.py "
            "--workdir <repo> --write`. Hand edits will be overwritten.",
            "",
            f"Summary: {summary_line(result)}",
            f"Source of truth: {result.get('env_source')}. ON/OFF/CORRUPT are "
            "trustworthy ONLY when env_source_kind is production-pull; "
            "otherwise every flag is UNKNOWN by design (local files are never "
            "substituted for production).",
            "",
            "| Flag | Status | Strict read | Defects | Reads as | Intended | Sites |",
            "|---|---|---|---|---|---|---|",
        ]
        for f in result["flags"]:
            lines.append(
                "| {name} | {status} | {strict} | {defects} | {reads_as} | {intended} | {sites} |".format(
                    name=f["name"], status=f["status"],
                    strict="yes" if f["strict_read"] else "no",
                    defects=", ".join(f.get("defects") or []) or "-",
                    reads_as=f.get("reads_as") or "-",
                    intended=f.get("intended") or "-",
                    sites="; ".join(f.get("read_sites") or [])[:120] or "-",
                )
            )
        if result["corrupt_other_keys"]:
            lines += ["", "## Corrupted non-flag env values (production, names only)", ""]
            for e in result["corrupt_other_keys"]:
                lines.append(f"- `{e['key']}`: {', '.join(e['defects'])} (shape: {e['shape']})")
        lh = result.get("local_hygiene")
        if lh:
            lines += ["", "## Local-file hygiene (advisory, NOT production)", ""]
            for fentry in lh["files"]:
                lines.append(f"### {fentry['source']}")
                for e in fentry["corrupt_keys"]:
                    lines.append(
                        f"- `{e['key']}`: {', '.join(e['defects'])} (shape: {e['shape']})"
                    )
        if result["unreferenced_keys"]:
            lines += [
                "", "## Env keys with zero code references (dead config?)", "",
                ", ".join(f"`{k}`" for k in result["unreferenced_keys"][:60]),
            ]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out
    except Exception:  # noqa: BLE001 — fail-soft
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workdir", type=Path, default=Path.cwd())
    ap.add_argument("--json", action="store_true", help="emit full JSON")
    ap.add_argument("--no-pull", action="store_true",
                    help="skip the vercel production pull (flags become UNKNOWN)")
    ap.add_argument("--write", action="store_true",
                    help="persist .build-loop/ops-state.json + memory-lane artifact")
    args = ap.parse_args(argv)
    result = probe_ops_state(args.workdir, allow_pull=not args.no_pull)
    if args.write:
        repo_path = write_repo_artifact(args.workdir, result)
        project = None
        try:
            from project_resolver import resolve_project  # noqa: PLC0415
            project = resolve_project(args.workdir)
        except Exception:  # noqa: BLE001
            pass
        mem_path = write_memory_artifact(project, result) if project else None
        result["written"] = {
            "repo": str(repo_path) if repo_path else None,
            "memory": str(mem_path) if mem_path else None,
        }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(summary_line(result))
    # exit 0 always: this is an inventory, not a gate
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
