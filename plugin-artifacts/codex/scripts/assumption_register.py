#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""assumption_register — the file-based half of the silent-assumptions skill.

One register per review: `register.json` is the record the agent writes and the
user edits. Everything else in the folder is generated from it and may be
deleted without loss.

    assumption_register.py new    --slug S --title T --repo R -o register.json
    assumption_register.py check  register.json [--json]
    assumption_register.py build  register.json --outdir DIR [--check]
    assumption_register.py read   register.json [--json]

Why the register is the source and the dashboard is a projection
----------------------------------------------------------------
`dashboard_build.py` (interface-built-right) renders a READ-ONLY page: its own
footer says "State lives in the record, not in this page." So the page cannot
be the place a decision is recorded. The user edits `rows[].decision.pick` and
`rows[].decision.note` in `register.json`; `build` re-renders the page from it.
That round trip needs no browser, no artifact host, and no model tokens, which
is what makes it work identically under Claude and under Codex.

Stdlib only. No network.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

SCHEMA = "build-loop.assumption-register/v1"

LEVERAGE = ("high", "med", "low")

# The eleven detector classes from
# skills/silent-assumptions/references/elicitation-detectors.md. Recorded on
# each row so a later pass can audit which detectors ever fire — a class that
# never fires across many registers is either dead or being skipped.
TRIGGER_CLASSES = (
    "ambiguous-term",        # a word in the request with >1 defensible referent
    "scope-narrowed",        # did N of M and did not say so
    "rule-applied-or-waived",
    "tool-output-as-truth",
    "number-wrong-basis",
    "invented-context",
    "assumed-workflow",      # incl. execution ORDER, not just optimisation target
    "static-for-dynamic",
    "root-cause-not-swept",
    # Added 2026-09-01 after an adversarial audit of the first nine against a
    # real multi-agent transcript found two classes with large blast radius that
    # none of them located. Both are documented in
    # skills/silent-assumptions/references/elicitation-detectors.md.
    "source-authority",      # took an instruction as authoritative without checking who sent it
    "irreversible-act",      # did something un-undoable while still deciding
    "other",
)

# Where dashboard_build.py lives. It ships in interface-built-right, a separate
# repo, so there is no in-tree resolver to cite. IBR_DASHBOARD_BUILD wins; the
# fallback guesses a sibling checkout beside this repo rather than naming a
# maintainer-only address, which would send an installed user's agent chasing a
# file that cannot exist. A wrong guess is not fatal: `build` reports
# "dashboard_build.py not found at <path>" and exits 3.
_SIBLING_DASHBOARD_BUILD = (
    Path(__file__).resolve().parent.parent.parent
    / "interface-built-right"
    / "scripts"
    / "dashboard_build.py"
)
DEFAULT_DASHBOARD_BUILD = os.environ.get(
    "IBR_DASHBOARD_BUILD",
    str(_SIBLING_DASHBOARD_BUILD),
)


# Offer weights. A `low` row is by definition reversible with no downstream, so
# it scores zero: weighting it above zero would let volume alone trigger an
# offer, which is how a useful prompt becomes ignorable noise. 6 = three highs,
# or two highs plus two mediums. The audit session that motivated this skill
# scored 24, so a real case clears the bar rather than scraping it.
OFFER_WEIGHTS = {"high": 2, "med": 1, "low": 0}
OFFER_THRESHOLD = 6


class RegisterError(Exception):
    """The register cannot produce a correct review. Fail closed."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegisterError(f"cannot read register: {exc}") from exc


# ---------------------------------------------------------------------------
# new


def new_register(slug: str, title: str, repo: str, session: str = "") -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "slug": slug,
        "title": title,
        "reviewed": {"repo": repo, "session": session, "date": _now()[:10]},
        "created_at": _now(),
        "updated_at": _now(),
        "rows": [
            {
                "id": "example-id",
                "area": "What I chose to look at",
                "leverage": "high",
                "trigger_class": "ambiguous-term",
                "title": "One sentence naming the call, in the first person",
                "what_i_did": "The action, stated plainly.",
                "why_and_cost": "The reasoning, and what it gives up.",
                # The cut test lives here. A row that cannot fill this is not a
                # decision the user can act on — delete it rather than ship it.
                "consequence": "What breaks, for whom, and when, if this is wrong.",
                "evidence": "A real path, selector, line number, or command output.",
                "options": [
                    {"label": "What I did", "detail": "Restate it as a choice.",
                     "is_default": True},
                    {"label": "The real alternative", "detail": "What changes if you pick it."},
                ],
                "decision": {"pick": None, "note": "", "reviewed_at": None},
            }
        ],
    }


# ---------------------------------------------------------------------------
# check


def check(reg: dict[str, Any]) -> list[dict[str, str]]:
    """Return findings. Any severity 'error' means the register must not ship."""
    out: list[dict[str, str]] = []

    def err(where: str, msg: str) -> None:
        out.append({"severity": "error", "where": where, "message": msg})

    def warn(where: str, msg: str) -> None:
        out.append({"severity": "warn", "where": where, "message": msg})

    if reg.get("schema") != SCHEMA:
        err("schema", f"schema must be {SCHEMA!r}")
    if not str(reg.get("slug") or "").strip():
        err("slug", "slug is required — it names the folder")
    if not str(reg.get("title") or "").strip():
        err("title", "title is required")

    rows = reg.get("rows") or []
    if not rows:
        err("rows", "a register with no rows records nothing")

    seen: set[str] = set()
    for i, r in enumerate(rows):
        w = f"rows[{i}]:{r.get('id') or '?'}"
        rid = str(r.get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rid):
            err(w, "id must be lowercase alphanumeric with dashes")
        if rid in seen:
            err(w, f"duplicate id {rid!r}")
        seen.add(rid)

        if r.get("leverage") not in LEVERAGE:
            err(w, f"leverage must be one of {', '.join(LEVERAGE)}")
        if r.get("trigger_class") not in TRIGGER_CLASSES:
            warn(w, f"trigger_class {r.get('trigger_class')!r} is not a known detector")

        # The four fields a cold reader needs. Missing any one makes the row
        # unactionable, which is the whole failure this register exists to avoid.
        for f in ("title", "what_i_did", "why_and_cost", "consequence", "evidence"):
            if not str(r.get(f) or "").strip():
                err(w, f"{f} is empty — a row a cold reader cannot act on is not finished")

        cons = str(r.get("consequence") or "")
        if cons.strip() and len(cons.split()) < 6:
            warn(w, "consequence is too short to name what breaks, for whom, and when")

        ev = str(r.get("evidence") or "")
        # Evidence must point at something checkable, not gesture at it.
        if ev.strip() and not re.search(r"[/.]|:\d|\bPID\b|\b\d{2,}\b|`", ev):
            warn(w, "evidence names no path, selector, line, count, or command output")

        opts = r.get("options") or []
        if len(opts) < 2:
            err(w, "a row needs at least 2 real options — a single option is not a choice")
        if len(opts) > 4:
            warn(w, "more than 4 options; the user has to hold them all at once")
        defaults = [o for o in opts if o.get("is_default")]
        if len(defaults) != 1:
            err(w, f"exactly one option must carry is_default; found {len(defaults)}")
        for j, o in enumerate(opts):
            if not str(o.get("label") or "").strip():
                err(f"{w}.options[{j}]", "option label is empty")

        dec = r.get("decision") or {}
        pick = dec.get("pick")
        if pick is not None and not (isinstance(pick, int) and 0 <= pick < len(opts)):
            err(w, f"decision.pick {pick!r} is not an index into options")

    return out


# ---------------------------------------------------------------------------
# read — the round trip's second half


def read_back(reg: dict[str, Any]) -> dict[str, Any]:
    rows = reg.get("rows") or []
    reviewed, overrides, confirmations, notes, open_high = [], [], [], [], []

    for r in rows:
        dec = r.get("decision") or {}
        pick = dec.get("pick")
        opts = r.get("options") or []
        default_i = next((i for i, o in enumerate(opts) if o.get("is_default")), None)
        note = str(dec.get("note") or "").strip()

        if pick is None:
            if r.get("leverage") == "high":
                open_high.append({"id": r["id"], "title": r.get("title", ""),
                                  "area": r.get("area", "")})
            continue

        reviewed.append(r["id"])
        entry = {
            "id": r["id"],
            "area": r.get("area", ""),
            "leverage": r.get("leverage"),
            "title": r.get("title", ""),
            "chose": opts[pick].get("label") if pick < len(opts) else str(pick),
            "instead_of": opts[default_i].get("label") if default_i is not None else None,
            "note": note,
        }
        (overrides if pick != default_i else confirmations).append(entry)
        if note:
            notes.append({"id": r["id"], "note": note})

    return {
        "slug": reg.get("slug"),
        "title": reg.get("title"),
        "counts": {
            "rows": len(rows),
            "reviewed": len(reviewed),
            "overrides": len(overrides),
            "confirmations": len(confirmations),
            "notes": len(notes),
            "high_leverage_unreviewed": len(open_high),
        },
        # Ordered by what changes the agent's behaviour most.
        "overrides": overrides,
        "notes": notes,
        "confirmations": confirmations,
        "high_leverage_unreviewed": open_high,
    }


# ---------------------------------------------------------------------------
# offer — the ONE proactive behaviour, and it is never a prompt


def score(reg: dict[str, Any]) -> dict[str, Any]:
    """Score only UNRULED rows. A ruled row has already had its say."""
    unruled = [r for r in (reg.get("rows") or [])
               if (r.get("decision") or {}).get("pick") is None]
    total = sum(OFFER_WEIGHTS.get(str(r.get("leverage")), 0) for r in unruled)
    # Consequence beats count: a row whose consequence is already shipped or
    # cannot be undone offers immediately, at any score.
    escalated = [r["id"] for r in unruled if r.get("escalate")]
    return {
        "score": total,
        "threshold": OFFER_THRESHOLD,
        "unruled": len(unruled),
        "by_leverage": {k: sum(1 for r in unruled if r.get("leverage") == k)
                        for k in LEVERAGE},
        "escalated": escalated,
        "offer": bool(escalated) or total >= OFFER_THRESHOLD,
        "already_offered": bool(reg.get("offered_at")),
    }


# ---------------------------------------------------------------------------
# promote — mirror into the EXISTING central decision store, no second store


def promote_args(reg: dict[str, Any], row: dict[str, Any], reg_path: str) -> list[str]:
    """Map one register row onto write_decision's existing schema.

    A silent assumption is a decision with a subtype, not a new record type.
    Confidence and status track the ruling: an unruled row is the agent's own
    assumption (assumed/proposed); a confirmed default is the user's word
    (explicit/accepted); an override rejects what the agent did.
    """
    opts = row.get("options") or []
    dec = row.get("decision") or {}
    pick = dec.get("pick")
    default_i = next((i for i, o in enumerate(opts) if o.get("is_default")), None)

    if pick is None:
        confidence, status = "assumed", "proposed"
    elif pick == default_i:
        confidence, status = "explicit", "accepted"
    else:
        confidence, status = "explicit", "rejected"

    alts = " | ".join(
        f"{o.get('label', '')}" + (" (agent default)" if o.get("is_default") else "")
        + (f": {o['detail']}" if o.get("detail") else "")
        for o in opts
    )
    notes = [f"register: {reg_path}", f"row: {row.get('id')}",
             f"leverage: {row.get('leverage')}",
             f"detector: {row.get('trigger_class')}"]
    if pick is not None and pick < len(opts):
        notes.append(f"user ruled: {opts[pick].get('label')}")
    if str(dec.get("note") or "").strip():
        # The note is standing instruction more often than row commentary, so it
        # must survive into the central store verbatim.
        notes.append(f"user note: {dec['note'].strip()}")

    return [
        "--title", str(row.get("title") or row.get("id")),
        "--decision", str(row.get("what_i_did") or ""),
        "--context", str(row.get("why_and_cost") or ""),
        "--alternatives", alts,
        "--consequences", str(row.get("consequence") or ""),
        "--notes", " · ".join(notes),
        # `process` is a real taxonomy tag. The subtype tags are NOT in the
        # taxonomy, and the writer's own extension mechanism for that is the
        # `proposed:` prefix — use it rather than widening the taxonomy, so a
        # silent assumption stays a decision with a subtype and the shared
        # vocabulary keeps one owner.
        "--tags", ",".join([
            "process",
            "proposed:silent-assumption",
            f"proposed:leverage-{row.get('leverage')}",
            f"proposed:{row.get('trigger_class')}",
        ]),
        "--primary-tag", "process",
        # One row = one decision. Keying dedup on the AREA collapsed 20 distinct
        # calls into ~5 topics and the writer correctly refused the duplicates.
        "--entity", f"silent-assumption:{row.get('id')}",
        "--confidence", confidence,
        "--status", status,
        "--source", "orchestrator",
    ]


def promote(reg: dict[str, Any], reg_path: str, workdir: str,
            writer: str, dry_run: bool = False) -> dict[str, Any]:
    import subprocess
    results = []
    for row in reg.get("rows") or []:
        argv = [sys.executable, writer, "--workdir", workdir] + promote_args(
            reg, row, reg_path)
        if dry_run:
            results.append({"id": row["id"], "argv": argv, "rc": None})
            continue
        p = subprocess.run(argv, capture_output=True, text=True)
        results.append({"id": row["id"], "rc": p.returncode,
                        "stdout": p.stdout.strip()[-400:],
                        "stderr": p.stderr.strip()[-400:]})
    return {"promoted": results,
            "failed": [r["id"] for r in results if r.get("rc") not in (0, None)]}


# ---------------------------------------------------------------------------
# build — project the register into the read-only dashboard


def _label(r: dict[str, Any]) -> str:
    # dashboard_build renders rows.label and a status chip only; leverage has no
    # slot of its own, so it rides in the label to stay visible without opening
    # a row. See "Known gaps" in the skill.
    return f"{str(r.get('leverage', '')).upper()} · {r.get('title', '')}"


# A ruled row has had its say; leaving it interleaved with open ones buries the
# calls that still need the user. dashboard_build renders groups in
# FIRST-ENCOUNTER order (see its `order.push(k)`), so sorting the entities is
# what puts the open sections above the ruled one.
RULED_SECTION = "Ruled · already answered"
_LEV_ORDER = {"high": 0, "med": 1, "medium": 1, "low": 2}


def _section_sort_key(e: dict[str, Any]) -> tuple[int, int, str]:
    return (
        1 if e.get("reviewed") else 0,
        _LEV_ORDER.get(str(e.get("leverage") or "").lower(), 3),
        str(e.get("entity_id") or ""),
    )


def to_dashboard_data(reg: dict[str, Any]) -> dict[str, Any]:
    ents = []
    for r in reg.get("rows") or []:
        opts = r.get("options") or []
        dec = r.get("decision") or {}
        pick = dec.get("pick")
        default_i = next((i for i, o in enumerate(opts) if o.get("is_default")), None)
        reviewed = pick is not None

        opt_txt = " | ".join(
            f"{chr(65 + i)}. {o.get('label', '')}"
            + (" (my default)" if o.get("is_default") else "")
            + (f" — {o['detail']}" if o.get("detail") else "")
            for i, o in enumerate(opts)
        )
        if reviewed and pick < len(opts):
            your = f"{chr(65 + pick)}. {opts[pick].get('label', '')}"
            if pick != default_i:
                your += "  [overrides my default]"
        else:
            your = "not reviewed"

        area = r.get("area", "(ungrouped)")
        ents.append({
            "entity_id": r["id"],
            "label": _label(r),
            "area": area,
            # Open rows keep their area so related calls read together; ruled
            # rows collapse into one trailing section.
            "section": RULED_SECTION if reviewed else f"Open · {area}",
            "reviewed": reviewed,
            # Priority = high leverage AND still unreviewed. This is the number
            # the summary strip must lead with.
            "needs_you": (r.get("leverage") == "high") and not reviewed,
            "leverage": r.get("leverage"),
            "trigger_class": r.get("trigger_class"),
            "what_i_did": r.get("what_i_did", ""),
            "why_and_cost": r.get("why_and_cost", ""),
            "consequence": r.get("consequence", ""),
            "evidence": r.get("evidence", ""),
            "options": opt_txt,
            "my_call": (opts[default_i].get("label") if default_i is not None else ""),
            "your_call": your,
            "note": str(dec.get("note") or ""),
        })

    ents.sort(key=_section_sort_key)

    body = json.dumps(ents, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {
        "as_of": reg.get("updated_at") or _now(),
        "record_hash": "sha256:" + hashlib.sha256(body).hexdigest(),
        "entities": ents,
    }


def to_dashboard_spec(reg: dict[str, Any]) -> dict[str, Any]:
    n_high_open = sum(
        1 for r in reg.get("rows") or []
        if r.get("leverage") == "high" and (r.get("decision") or {}).get("pick") is None
    )
    return {
        "schema": "ibr.dashboard.spec/v1",
        "title": reg.get("title") or "Silent assumptions",
        # queue: "What is waiting on me?" — the register is a queue of calls
        # awaiting the user's ruling, ordered by consequence then age.
        "archetype": "queue",
        "asks": "Which calls did I make for you without asking, and which do you want changed?",
        "scope": "single",
        "binding": "replay",
        "data": {"js": "./data.js", "json": "./data.json", "var": "DASHBOARD_DATA"},
        "rows": {"path": "entities", "id": "entity_id", "label": "label"},
        "priority": {
            "label": "High leverage, not yet ruled on",
            "when": {"field": "needs_you", "equals": True},
            "empty": "Every high-leverage call has your ruling.",
        },
        "columns": [{"key": "label", "label": "Call"}],
        "status": {"field": "reviewed", "true": "Ruled", "false": "Not ruled"},
        "detail": ["leverage", "what_i_did", "why_and_cost", "consequence",
                   "evidence", "options", "my_call", "your_call", "note"],
        # Split open from ruled. Ruled rows collapse into one trailing section
        # so the calls still awaiting a ruling are not buried among answered
        # ones — on a mature register most rows are ruled.
        "group": {"by": "section", "label": "Section"},
        # DB402: the runtime "as of" lands in the provenance strip via JS, which a
        # static lint cannot see. Repeating the date here is what makes a stale
        # snapshot legible without opening the file's mtime.
        "footer": (
            f"Snapshot as of {(reg.get('updated_at') or _now())[:10]}. "
            f"{n_high_open} high-leverage call(s) still unruled. "
            "To rule: edit rows[].decision.pick (0-based index into options) and "
            "rows[].decision.note in register.json, then rebuild. "
            "The agent reads your rulings with: assumption_register.py read register.json"
        ),
    }


def _load_dashboard_build(path: str):
    """Import the renderer from another repo WITHOUT writing anything into it.

    dashboard_build.py lives in interface-built-right, which this script reads
    and invokes but must never modify. A plain import writes a .pyc into that
    repo's scripts/__pycache__/ as a side effect — observed 2026-09-01. Suppress
    bytecode for the duration of the import and restore the flag afterwards, so
    a read-only dependency stays read-only.
    """
    p = Path(path)
    if not p.is_file():
        return None
    spec = importlib.util.spec_from_file_location("dashboard_build", p)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev
    return mod


def build(reg: dict[str, Any], outdir: Path, dash_path: str) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    data = to_dashboard_data(reg)
    spec = to_dashboard_spec(reg)

    (outdir / "data.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    # data.js is not a duplicate for convenience: dashboard_build's binding
    # ladder falls back to a script tag when the page is opened from file://,
    # where fetch() throws. Without it a double-clicked page shows no rows.
    (outdir / "data.js").write_text(
        "window.DASHBOARD_DATA = "
        + json.dumps(data, indent=1, ensure_ascii=False) + ";\n", encoding="utf-8")
    (outdir / "spec.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    written = ["data.json", "data.js", "spec.json"]
    mod = _load_dashboard_build(dash_path)
    if mod is None:
        # Graceful degradation: the record and its data still land. Only the
        # rendered page is missing, and the caller is told exactly why.
        return {"written": written, "dashboard": None,
                "note": f"dashboard_build.py not found at {dash_path}; "
                        "set IBR_DASHBOARD_BUILD or run it yourself against spec.json"}

    html = mod.build(spec)
    (outdir / "dashboard.html").write_text(html, encoding="utf-8")
    written.append("dashboard.html")
    return {"written": written, "dashboard": str(outdir / "dashboard.html"), "note": None}


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="assumption_register",
        description="Write, validate, render, and read back a silent-assumptions register.")
    p.add_argument("--version", action="version", version=f"assumption_register {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="scaffold a register")
    n.add_argument("--slug", required=True)
    n.add_argument("--title", required=True)
    n.add_argument("--repo", required=True)
    n.add_argument("--session", default="")
    n.add_argument("-o", "--output", required=True)

    c = sub.add_parser("check", help="validate a register; exit 1 on any error")
    c.add_argument("register")
    c.add_argument("--json", action="store_true")

    b = sub.add_parser("build", help="render spec/data/dashboard next to the register")
    b.add_argument("register")
    b.add_argument("--outdir", default=None, help="defaults to the register's folder")
    b.add_argument("--dashboard-build", default=DEFAULT_DASHBOARD_BUILD)
    b.add_argument("--check", action="store_true", help="run dashboard_lint on the result")

    r = sub.add_parser("read", help="read the user's rulings back out")
    r.add_argument("register")
    r.add_argument("--json", action="store_true")

    o = sub.add_parser("offer", help="exit 0 if the register is worth offering, 1 if not")
    o.add_argument("register")
    o.add_argument("--json", action="store_true")
    o.add_argument("--force", action="store_true",
                   help="score even if this register was already offered once")

    pr = sub.add_parser("promote", help="mirror rows into build-loop-memory decisions")
    pr.add_argument("register")
    pr.add_argument("--workdir", default=".")
    pr.add_argument("--writer", default=str(
        Path(__file__).resolve().parent / "write_decision" / "__main__.py"))
    pr.add_argument("--dry-run", action="store_true")
    pr.add_argument("--json", action="store_true")

    args = p.parse_args(argv)

    try:
        if args.cmd == "new":
            reg = new_register(args.slug, args.title, args.repo, args.session)
            Path(args.output).write_text(
                json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {args.output} — replace the example row, then run check")
            return 0

        reg = _read(args.register)

        if args.cmd == "check":
            findings = check(reg)
            errs = [f for f in findings if f["severity"] == "error"]
            if args.json:
                print(json.dumps({"ok": not errs, "findings": findings}, indent=2))
            else:
                for f in findings:
                    print(f"  {f['severity']:5} {f['where']}: {f['message']}")
                print(f"{len(reg.get('rows') or [])} rows, "
                      f"{len(errs)} error(s), {len(findings) - len(errs)} warning(s)")
            return 1 if errs else 0

        if args.cmd == "read":
            out = read_back(reg)
            if args.json:
                print(json.dumps(out, indent=2, ensure_ascii=False))
                return 0
            c_ = out["counts"]
            print(f"{out['title']}")
            print(f"  {c_['reviewed']}/{c_['rows']} ruled · {c_['overrides']} override(s) · "
                  f"{c_['notes']} note(s) · {c_['high_leverage_unreviewed']} high-leverage unruled")
            for o in out["overrides"]:
                print(f"\n  OVERRIDE [{o['leverage']}] {o['id']}: {o['title']}")
                print(f"    you chose : {o['chose']}")
                print(f"    instead of: {o['instead_of']}")
                if o["note"]:
                    print(f"    note      : {o['note']}")
            for h in out["high_leverage_unreviewed"]:
                print(f"\n  UNRULED (high) {h['id']}: {h['title']}")
            return 0

        if args.cmd == "offer":
            s = score(reg)
            if s["already_offered"] and not args.force:
                s["offer"] = False
                s["reason"] = "already offered once; do not ask again"
            if args.json:
                print(json.dumps(s, indent=2))
            elif s["offer"]:
                n = s["by_leverage"]
                print(f"I made {s['unruled']} calls on this without asking "
                      f"({n['high']} high-leverage). Want to see them? "
                      f"Nothing is waiting on your answer.")
            return 0 if s["offer"] else 1

        if args.cmd == "promote":
            res = promote(reg, str(Path(args.register).resolve()),
                          args.workdir, args.writer, args.dry_run)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"promoted {len(res['promoted'])} row(s); "
                      f"{len(res['failed'])} failed")
                for f in res["failed"]:
                    print(f"  FAILED {f}", file=sys.stderr)
            return 1 if res["failed"] else 0

        if args.cmd == "build":
            errs = [f for f in check(reg) if f["severity"] == "error"]
            if errs:
                for f in errs:
                    print(f"  error {f['where']}: {f['message']}", file=sys.stderr)
                raise RegisterError("register has errors; fix them before building")
            outdir = Path(args.outdir) if args.outdir else Path(args.register).resolve().parent
            res = build(reg, outdir, args.dashboard_build)
            print(f"wrote {', '.join(res['written'])} in {outdir}")
            if res["note"]:
                print(res["note"], file=sys.stderr)
                return 3
            if args.check:
                lint = Path(args.dashboard_build).with_name("dashboard_lint.py")
                if lint.is_file():
                    import subprocess
                    return subprocess.run(
                        [sys.executable, str(lint), "check", res["dashboard"]]).returncode
                print("dashboard_lint.py not found — skipped --check", file=sys.stderr)
            return 0

        return 2
    except RegisterError as exc:
        print(f"assumption_register: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
