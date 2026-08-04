# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""waivers.py — the durable waiver register behind constitution rule C-FINDINGS.

C-FINDINGS says every finding a run surfaces reaches exactly one terminal state
before "done": **fixed**, **waived against a durable record**, or **escalated as
a new record**. This script owns the middle one. Without it, "pre-existing" is
the word an agent uses to close a finding nobody ever decided about — the exact
failure this register exists to make impossible.

A waiver is plain Markdown + YAML frontmatter under
``<repo>/.build-loop/waivers/<ID>.md`` — same filesystem-first, grep-able
posture as ``backlog.py``. Frontmatter fields:

    id            WV-<rule-slug>-<random>       (generated)
    schema_version 1
    rule          check / rule identifier       e.g. no-useless-escape
    path          repo-relative file path
    anchor        symbol or "line:N"            (optional; empty = whole file)
    rationale     why this finding may stand
    date          YYYY-MM-DD the decision was made
    authority     who decided                   e.g. user, agent:<name>, decision:<path>
    expires       until-file-changes | YYYY-MM-DD | any free-text condition
    file_sha256   content hash at waiver time   (drives until-file-changes)
    status        active | retired

``expires`` is mandatory in spirit (C-FINDINGS/waiver_names_its_expiry) and
DEFAULTS to ``until-file-changes`` when omitted, so a waiver can never become
permanent absolution by accident. Two expiry forms are machine-evaluable —
``until-file-changes`` (file content hash moved) and an ISO date (today past it).
Anything else is reported with ``manual_expiry: true`` so a human still owns it.

Two subcommands, deliberately: a ``check`` that answers waived-or-not for one
finding identity, and a ``new`` that writes a record. No UI, no sync daemon.

CLI::

    waivers.py new   --repo <path> --rule <id> --path <file> --rationale "..."
                     --authority "..." [--anchor <sym|line:N>] [--expires ...]
                     [--today YYYY-MM-DD] [--json]

    waivers.py check --repo <path> --rule <id> --path <file>
                     [--anchor <sym|line:N>] [--today YYYY-MM-DD] [--json]

``check`` exit codes: **0 = waived**, **1 = not waived**, **2 = usage error**.
The non-zero "not waived" is the useful shell branch, not an error.

MODULARITY EXCEPTION: the frontmatter reader/writer here is a ~30-line
flat-scalar parser rather than a reuse of ``backlog.py``'s. Waiver frontmatter is
flat scalars only, and ``backlog.py`` must be loaded through an importlib
path-shim to dodge the sibling ``scripts/backlog/`` package — the coupling would
cost more than the duplication saves.

Pure Python stdlib. No third-party imports (asserted by test_waivers.py).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

WAIVER_DIRNAME = "waivers"
DEFAULT_EXPIRES = "until-file-changes"
STATUS_ACTIVE = "active"

FIELD_ORDER = (
    "id", "schema_version", "rule", "path", "anchor", "rationale",
    "date", "authority", "expires", "file_sha256", "status",
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Frontmatter (flat scalars only)
# ---------------------------------------------------------------------------

def _quote(value: str) -> str:
    """Quote a scalar when YAML would otherwise mis-read it."""
    text = str(value)
    if text == "":
        return '""'
    if text != text.strip() or _ISO_DATE_RE.match(text):
        return json.dumps(text)
    if text[0] in "#&*!|>%@`'\"[]{}," or ": " in text or text.endswith(":"):
        return json.dumps(text)
    return text


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError:
                pass
        return text[1:-1]
    return text


def render_waiver(fields: dict[str, Any], body: str = "") -> str:
    """Render a waiver record to markdown with deterministic key order."""
    lines = ["---"]
    for key in FIELD_ORDER:
        lines.append(f"{key}: {_quote(fields.get(key, ''))}")
    lines.append("---")
    lines.append("")
    if body.strip():
        lines.append(body.strip())
        lines.append("")
    return "\n".join(lines)


def parse_waiver(text: str) -> dict[str, Any]:
    """Parse a waiver record. Tolerant: missing keys default, unknown keys kept."""
    fields: dict[str, Any] = {key: "" for key in FIELD_ORDER}
    fields["schema_version"] = SCHEMA_VERSION
    fields["status"] = STATUS_ACTIVE
    fields["expires"] = DEFAULT_EXPIRES
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fields
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        if not key:
            continue
        value = _unquote(raw)
        fields[key] = value if value != "" else fields.get(key, "")
    try:
        fields["schema_version"] = int(fields.get("schema_version") or SCHEMA_VERSION)
    except (TypeError, ValueError):
        fields["schema_version"] = SCHEMA_VERSION
    return fields


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------

def waiver_dir(repo: Path) -> Path:
    return Path(repo) / ".build-loop" / WAIVER_DIRNAME


def file_sha256(repo: Path, rel_path: str) -> str:
    """SHA-256 of a repo-relative file, or "" when it does not exist."""
    target = Path(repo) / rel_path
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return ""


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", str(text).lower()).strip("-")
    return slug[:40] or "finding"


def make_id(rule: str) -> str:
    return f"WV-{slugify(rule)}-{secrets.token_hex(3)}"


def load_waivers(repo: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Load every waiver record. Unreadable files are skipped, never fatal."""
    directory = waiver_dir(repo)
    if not directory.is_dir():
        return []
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.md")):
        try:
            records.append((path, parse_waiver(path.read_text(encoding="utf-8"))))
        except OSError:
            continue
    return records


# ---------------------------------------------------------------------------
# Matching + expiry
# ---------------------------------------------------------------------------

def identity_matches(record: dict[str, Any], rule: str, rel_path: str,
                     anchor: str = "") -> bool:
    """Does this record cover the queried finding identity?

    Rule and path must match exactly (rule case-insensitively). A record with an
    empty anchor covers the whole file for that rule; a record WITH an anchor
    covers only that anchor.
    """
    if str(record.get("rule", "")).strip().lower() != str(rule).strip().lower():
        return False
    if str(record.get("path", "")).strip() != str(rel_path).strip():
        return False
    record_anchor = str(record.get("anchor", "")).strip()
    if not record_anchor:
        return True
    return record_anchor == str(anchor).strip()


def evaluate_expiry(repo: Path, record: dict[str, Any],
                    today: str) -> dict[str, Any]:
    """Classify a record's expiry. Returns {expired, manual_expiry, reason}."""
    expires = str(record.get("expires", "") or DEFAULT_EXPIRES).strip()
    if expires == DEFAULT_EXPIRES:
        recorded = str(record.get("file_sha256", "") or "").strip()
        current = file_sha256(repo, str(record.get("path", "")))
        if not recorded:
            return {"expired": False, "manual_expiry": True,
                    "reason": "until-file-changes with no recorded file_sha256 — cannot verify"}
        if current and current != recorded:
            return {"expired": True, "manual_expiry": False,
                    "reason": "covered file changed since the waiver was written"}
        if not current:
            return {"expired": True, "manual_expiry": False,
                    "reason": "covered file no longer readable at the recorded path"}
        return {"expired": False, "manual_expiry": False, "reason": "covered file unchanged"}
    if _ISO_DATE_RE.match(expires):
        return ({"expired": True, "manual_expiry": False,
                 "reason": f"expiry date {expires} passed (today {today})"}
                if today > expires else
                {"expired": False, "manual_expiry": False,
                 "reason": f"expires {expires}"})
    return {"expired": False, "manual_expiry": True,
            "reason": f"expiry condition is not machine-evaluable: {expires}"}


def check(repo: Path, rule: str, rel_path: str, anchor: str = "",
          today: str | None = None) -> dict[str, Any]:
    """Answer waived-or-not for one finding identity."""
    today = today or _dt.date.today().isoformat()
    matched, expired = [], []
    for path, record in load_waivers(repo):
        if str(record.get("status", STATUS_ACTIVE)).strip() != STATUS_ACTIVE:
            continue
        if not identity_matches(record, rule, rel_path, anchor):
            continue
        verdict = evaluate_expiry(repo, record, today)
        entry = {
            "record": str(path),
            "id": record.get("id", ""),
            "rationale": record.get("rationale", ""),
            "authority": record.get("authority", ""),
            "expires": record.get("expires", DEFAULT_EXPIRES),
            "reason": verdict["reason"],
            "manual_expiry": verdict["manual_expiry"],
        }
        (expired if verdict["expired"] else matched).append(entry)
    waived = bool(matched)
    if waived:
        reason = matched[0]["reason"]
    elif expired:
        reason = "waiver found but expired — re-surface this finding"
    else:
        reason = "no waiver record covers this finding identity"
    return {
        "waived": waived,
        "rule": rule,
        "path": rel_path,
        "anchor": anchor,
        "today": today,
        "matches": matched,
        "expired": expired,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def new(repo: Path, rule: str, rel_path: str, rationale: str, authority: str,
        anchor: str = "", expires: str = "", body: str = "",
        today: str | None = None) -> dict[str, Any]:
    """Write a waiver record and return its summary envelope."""
    repo = Path(repo)
    today = today or _dt.date.today().isoformat()
    warnings: list[str] = []
    if not str(expires).strip():
        expires = DEFAULT_EXPIRES
        warnings.append(
            "no --expires given; defaulted to until-file-changes "
            "(C-FINDINGS/waiver_names_its_expiry)"
        )
    digest = file_sha256(repo, rel_path)
    if not digest:
        warnings.append(f"covered file not readable at {rel_path}; file_sha256 left empty")
    fields = {
        "id": make_id(rule),
        "schema_version": SCHEMA_VERSION,
        "rule": rule,
        "path": rel_path,
        "anchor": anchor,
        "rationale": rationale,
        "date": today,
        "authority": authority,
        "expires": expires,
        "file_sha256": digest,
        "status": STATUS_ACTIVE,
    }
    directory = waiver_dir(repo)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{fields['id']}.md"
    target.write_text(render_waiver(fields, body), encoding="utf-8")
    return {"written": str(target), "id": fields["id"], "fields": fields,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waivers.py",
        description="Durable waiver register for build-loop findings (C-FINDINGS).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    new_cmd = sub.add_parser("new", help="write a waiver record")
    new_cmd.add_argument("--repo", required=True)
    new_cmd.add_argument("--rule", required=True, help="check / rule identifier")
    new_cmd.add_argument("--path", required=True, dest="rel_path",
                         help="repo-relative path of the covered file")
    new_cmd.add_argument("--anchor", default="", help="symbol or line:N; empty = whole file")
    new_cmd.add_argument("--rationale", required=True)
    new_cmd.add_argument("--authority", required=True,
                         help="user | agent:<name> | decision:<path>")
    new_cmd.add_argument("--expires", default="",
                         help="until-file-changes | YYYY-MM-DD | free-text condition")
    new_cmd.add_argument("--body", default="")
    new_cmd.add_argument("--today", default=None)
    new_cmd.add_argument("--json", action="store_true")

    check_cmd = sub.add_parser("check", help="is this finding already waived?")
    check_cmd.add_argument("--repo", required=True)
    check_cmd.add_argument("--rule", required=True)
    check_cmd.add_argument("--path", required=True, dest="rel_path")
    check_cmd.add_argument("--anchor", default="")
    check_cmd.add_argument("--today", default=None)
    check_cmd.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo)

    if args.command == "new":
        result = new(repo, args.rule, args.rel_path, args.rationale,
                     args.authority, anchor=args.anchor, expires=args.expires,
                     body=args.body, today=args.today)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"wrote {result['written']}")
            for warning in result["warnings"]:
                print(f"[warn] {warning}")
        return 0

    result = check(repo, args.rule, args.rel_path, anchor=args.anchor,
                   today=args.today)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        state = "waived" if result["waived"] else "NOT waived"
        print(f"{state}: {result['reason']}")
    return 0 if result["waived"] else 1


if __name__ == "__main__":
    sys.exit(main())
