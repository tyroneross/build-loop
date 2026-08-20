#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Audit and repair opaque macOS LaunchAgent identities.

macOS Background Task Management attributes a legacy LaunchAgent to its
registered executable.  A plist whose executable is ``/bin/bash`` or
``/usr/bin/env`` therefore appears in System Settings as "bash" or "env" even
when its launchd ``Label`` is descriptive.

This tool keeps the original command intact behind a purpose-named executable
trampoline.  ``apply`` is deliberately file-only: it never calls ``launchctl``
and never starts or stops a job.  Every rewrite has a byte-for-byte backup and
a restore manifest.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import tempfile
import xml.parsers.expat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


RULE_ID = "C-IDENTITY/background_item_identity"
DEFAULT_OWNED_PREFIXES = (
    "ai.rosslabs.",
    "com.rosslabs.",
    "com.tyroneross.",
    "com.ai-assistant.",
    "com.build-loop.",
)
GENERIC_BASENAMES = {
    "bash",
    "sh",
    "zsh",
    "env",
    "node",
    "nodejs",
    "ruby",
    "perl",
    "php",
    "osascript",
    "swift",
}
PYTHON_BASENAME = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")
LAUNCHER_BODY = b'''#!/bin/bash
program="$1"
argv0="$2"
shift 2
exec -a "$argv0" "$program" "$@"
'''
_XML_COMMENT = re.compile(br"<!--(.*?)-->", re.DOTALL)

_PREFIX_NAMES = (
    ("com.ai-assistant.", ("AI Assistant",)),
    ("com.build-loop.", ("Build Loop",)),
    ("ai.rosslabs.", ("RossLabs",)),
    ("com.rosslabs.", ("RossLabs",)),
    ("com.tyroneross.", ()),
)
_TOKEN_NAMES = {
    "ai": "AI",
    "api": "API",
    "buildloop": "Build Loop",
    "eod": "End of Day",
    "gh": "GitHub",
    "mcp": "MCP",
    "pmbl": "Prompt Model Benchmark Lab",
    "productpilot": "ProductPilot",
    "rosslabs": "RossLabs",
    "selfreview": "Self Review",
}


@dataclass(frozen=True)
class IdentityFinding:
    path: str
    label: str
    program: str
    display_name: str
    owned: bool
    rule_id: str = RULE_ID


def is_generic_program(program: str) -> bool:
    """Return True when *program* identifies an interpreter, not a purpose."""
    base = Path(program).name.lower()
    return base in GENERIC_BASENAMES or bool(PYTHON_BASENAME.fullmatch(base))


def registered_program(plist: dict[str, Any]) -> str:
    """Resolve launchd's executable using the installed launchd.plist contract."""
    program = plist.get("Program")
    if isinstance(program, str) and program:
        return program
    args = plist.get("ProgramArguments")
    if isinstance(args, list) and args and isinstance(args[0], str):
        return args[0]
    return ""


def _original_argv(plist: dict[str, Any], program: str) -> list[str]:
    """Return launchd's original argv vector without changing argv[0]."""
    args = plist.get("ProgramArguments")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        return [program]
    return list(args) if args else [program]


def _human_token(token: str) -> str:
    lower = token.lower()
    if lower in _TOKEN_NAMES:
        return _TOKEN_NAMES[lower]
    if token.isupper() and len(token) <= 5:
        return token
    return token[:1].upper() + token[1:]


def display_name_for_label(label: str) -> str:
    """Derive a stable product/purpose name from a reverse-DNS launchd label."""
    prefix_words: tuple[str, ...] = ()
    remainder = label
    for prefix, words in _PREFIX_NAMES:
        if label.startswith(prefix):
            prefix_words = words
            remainder = label[len(prefix) :]
            break
    else:
        parts = label.split(".")
        if len(parts) >= 3 and parts[0] in {"ai", "com", "dev", "io", "org"}:
            remainder = ".".join(parts[2:])

    tokens = [token for token in re.split(r"[._-]+", remainder) if token]
    words = [*prefix_words, *(_human_token(token) for token in tokens)]
    return " ".join(dict.fromkeys(words)) or label


def _owned(label: str, prefixes: Iterable[str]) -> bool:
    return any(label.startswith(prefix) for prefix in prefixes)


def inspect_plist_bytes(
    data: bytes,
    *,
    path: str,
    owned_prefixes: Iterable[str] = DEFAULT_OWNED_PREFIXES,
) -> IdentityFinding | None:
    """Return a finding for a launchd plist that exposes a generic identity."""
    raw = _load_plist(data)
    if not isinstance(raw, dict):
        return None
    label = raw.get("Label")
    if not isinstance(label, str) or not label:
        return None
    program = registered_program(raw)
    if not program or not is_generic_program(program):
        return None
    return IdentityFinding(
        path=path,
        label=label,
        program=program,
        display_name=display_name_for_label(label),
        owned=_owned(label, owned_prefixes),
    )


def _load_plist(data: bytes) -> dict[str, Any]:
    """Parse a plist, tolerating legacy ``--`` text inside XML comments.

    Apple's ``plutil`` accepts these installed files, while the XML standard and
    Python's Expat parser reject double hyphens inside comments.  Comments do
    not affect launchd semantics, so normalize only comment bodies and retry.
    """
    if data.lstrip().startswith(b"<?xml"):
        data = data.lstrip()
    try:
        raw = plistlib.loads(data)
    except xml.parsers.expat.ExpatError:
        normalized = _XML_COMMENT.sub(
            lambda match: b"<!--" + match.group(1).replace(b"--", b"- -") + b"-->",
            data,
        )
        if normalized == data:
            raise
        raw = plistlib.loads(normalized)
    if not isinstance(raw, dict):
        raise ValueError("launchd plist root must be a dictionary")
    return raw


def audit_directory(
    launch_agents_dir: Path,
    *,
    owned_prefixes: Iterable[str] = DEFAULT_OWNED_PREFIXES,
) -> dict[str, Any]:
    """Audit top-level LaunchAgent plists without requiring admin privileges."""
    findings: list[IdentityFinding] = []
    errors: list[dict[str, str]] = []
    paths = sorted(launch_agents_dir.glob("*.plist")) if launch_agents_dir.exists() else []
    for path in paths:
        try:
            finding = inspect_plist_bytes(
                path.read_bytes(), path=str(path), owned_prefixes=owned_prefixes
            )
        except (
            OSError,
            plistlib.InvalidFileException,
            xml.parsers.expat.ExpatError,
            ValueError,
            TypeError,
        ) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        if finding:
            findings.append(finding)
    return {
        "status": "findings" if findings else "clean",
        "launch_agents_dir": str(launch_agents_dir),
        "total_plists": len(paths),
        "generic_count": len(findings),
        "owned_generic_count": sum(item.owned for item in findings),
        "findings": [asdict(item) for item in findings],
        "errors": errors,
    }


def default_identity_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "RossLabs" / "Background Item Identity"


def _safe_launcher_name(display_name: str) -> str:
    name = re.sub(r"[/:\x00]", " ", display_name)
    name = re.sub(r"\s+", " ", name).strip().lstrip(".")
    if not name:
        raise ValueError("display name did not produce a safe launcher filename")
    return name


def launcher_path(identity_root: Path, display_name: str) -> Path:
    return identity_root / "launchers" / _safe_launcher_name(display_name)


def ensure_launcher(identity_root: Path, display_name: str) -> Path:
    """Create the purpose-named trampoline atomically and return its path."""
    path = launcher_path(identity_root, display_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == LAUNCHER_BODY:
        path.chmod(0o755)
        return path
    _atomic_write(path, LAUNCHER_BODY, 0o755)
    return path


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.chmod(mode)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _atomic_write(path, payload, 0o600)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def apply_directory(
    launch_agents_dir: Path,
    *,
    identity_root: Path | None = None,
    owned_prefixes: Iterable[str] = DEFAULT_OWNED_PREFIXES,
) -> dict[str, Any]:
    """Repair owned generic identities without loading or executing any job."""
    root = identity_root or default_identity_root()
    audit = audit_directory(launch_agents_dir, owned_prefixes=owned_prefixes)
    targets = [item for item in audit["findings"] if item["owned"]]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = root / "backups" / stamp
    manifest_path = backup_dir / "manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "launch_agents_dir": str(launch_agents_dir),
        "reload_performed": False,
        "status": "prepared",
        "entries": [],
    }

    if not targets:
        return {
            "status": "clean",
            "applied_count": 0,
            "manifest_path": None,
            "reload_performed": False,
            "audit": audit,
        }

    backup_dir.mkdir(parents=True, exist_ok=False)
    for target in targets:
        plist_path = Path(target["path"])
        original = plist_path.read_bytes()
        original_mode = stat.S_IMODE(plist_path.stat().st_mode)
        plist = _load_plist(original)
        program = registered_program(plist)
        original_argv = _original_argv(plist, program)
        display_name = target["display_name"]
        launcher = ensure_launcher(root, display_name)
        backup_path = backup_dir / plist_path.name
        shutil.copy2(plist_path, backup_path)

        entry = {
            "label": target["label"],
            "display_name": display_name,
            "plist_path": str(plist_path),
            "backup_path": str(backup_path),
            "launcher_path": str(launcher),
            "original_program": program,
            "original_argv": original_argv,
            "original_sha256": _sha256(original),
            "status": "prepared",
        }
        manifest["entries"].append(entry)
        # Persist the recovery record before replacing the source plist.
        _write_manifest(manifest_path, manifest)

        plist["Program"] = str(launcher)
        plist["ProgramArguments"] = [str(launcher), program, *original_argv]
        rewritten = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=False)
        _atomic_write(plist_path, rewritten, original_mode)
        entry["applied_sha256"] = _sha256(rewritten)
        entry["status"] = "applied"
        _write_manifest(manifest_path, manifest)

    manifest["status"] = "applied"
    _write_manifest(manifest_path, manifest)
    return {
        "status": "applied",
        "applied_count": len(targets),
        "manifest_path": str(manifest_path),
        "reload_performed": False,
        "restart_or_reregister_required": True,
        "audit_before": audit,
        "audit_after": audit_directory(launch_agents_dir, owned_prefixes=owned_prefixes),
    }


def restore_manifest(manifest_path: Path, *, force: bool = False) -> dict[str, Any]:
    """Restore every plist byte-for-byte, refusing to erase later edits."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[dict[str, str]] = []
    for entry in manifest.get("entries", []):
        plist_path = Path(entry["plist_path"])
        backup_path = Path(entry["backup_path"])
        current_sha256 = _sha256(plist_path.read_bytes()) if plist_path.exists() else None
        applied_sha256 = entry.get("applied_sha256")
        if not force and current_sha256 != applied_sha256:
            raise RuntimeError(
                f"restore conflict for {plist_path}: current content differs from "
                "the applied rewrite; rerun with --force only if overwriting that edit is intentional"
            )
        data = backup_path.read_bytes()
        mode = stat.S_IMODE(backup_path.stat().st_mode)
        _atomic_write(plist_path, data, mode)
        digest = _sha256(plist_path.read_bytes())
        if digest != entry["original_sha256"]:
            raise RuntimeError(f"restore hash mismatch for {plist_path}")
        restored.append({"label": entry["label"], "plist_path": str(plist_path)})
    return {
        "status": "restored",
        "restored_count": len(restored),
        "reload_performed": False,
        "items": restored,
    }


def verify_manifest(manifest_path: Path) -> dict[str, Any]:
    """Verify launchers and rewritten argument vectors without executing jobs."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []
    for entry in manifest.get("entries", []):
        plist_path = Path(entry["plist_path"])
        launcher = Path(entry["launcher_path"])
        reasons: list[str] = []
        try:
            plist = _load_plist(plist_path.read_bytes())
            original_argv = entry.get("original_argv")
            if original_argv is None:  # schema v1 manifests written before argv preservation
                original_argv = [entry["original_program"], *entry.get("original_tail", [])]
            expected_args = [str(launcher), entry["original_program"], *original_argv]
            if plist.get("Program") != str(launcher):
                reasons.append("Program does not match launcher")
            if plist.get("ProgramArguments") != expected_args:
                reasons.append("ProgramArguments do not preserve original command")
        except (OSError, ValueError, TypeError, xml.parsers.expat.ExpatError) as exc:
            reasons.append(f"plist unreadable: {exc}")
        if not launcher.exists():
            reasons.append("launcher missing")
        else:
            if launcher.read_bytes() != LAUNCHER_BODY:
                reasons.append("launcher content changed")
            if not os.access(launcher, os.X_OK):
                reasons.append("launcher is not executable")
        checks.append(
            {
                "label": entry["label"],
                "plist_path": str(plist_path),
                "launcher_path": str(launcher),
                "status": "pass" if not reasons else "fail",
                "reasons": reasons,
            }
        )
    failures = [item for item in checks if item["status"] == "fail"]
    return {
        "status": "pass" if not failures else "fail",
        "checked_count": len(checks),
        "failure_count": len(failures),
        "reload_performed": False,
        "checks": checks,
    }


def _print_human(result: dict[str, Any]) -> None:
    print(f"status: {result.get('status', 'unknown')}")
    audit = result.get("audit") or result.get("audit_after") or result
    if "generic_count" in audit:
        print(
            f"generic: {audit.get('generic_count', 0)} "
            f"(owned: {audit.get('owned_generic_count', 0)})"
        )
        for item in audit.get("findings", []):
            print(f"  {item['label']}: {item['program']} -> {item['display_name']}")
    if result.get("manifest_path"):
        print(f"manifest: {result['manifest_path']}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--launch-agents-dir",
        default=str(Path.home() / "Library" / "LaunchAgents"),
        help="LaunchAgents directory to inspect (default: user LaunchAgents).",
    )
    parser.add_argument(
        "--identity-root",
        default=str(default_identity_root()),
        help="Directory for launchers and reversible backups.",
    )
    parser.add_argument("--owned-prefix", action="append", default=None)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--strict", action="store_true", help="Exit 1 when any generic identity exists.")
    sub.add_parser("apply")
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--manifest", required=True)
    restore.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a plist even when it changed after apply.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    prefixes = tuple(args.owned_prefix or DEFAULT_OWNED_PREFIXES)
    if args.command == "audit":
        result = audit_directory(Path(args.launch_agents_dir), owned_prefixes=prefixes)
        exit_code = 1 if args.strict and result["generic_count"] else 0
    elif args.command == "apply":
        result = apply_directory(
            Path(args.launch_agents_dir),
            identity_root=Path(args.identity_root),
            owned_prefixes=prefixes,
        )
        exit_code = 0
    elif args.command == "verify":
        result = verify_manifest(Path(args.manifest))
        exit_code = 0 if result["status"] == "pass" else 1
    else:
        result = restore_manifest(Path(args.manifest), force=args.force)
        exit_code = 0
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_human(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
