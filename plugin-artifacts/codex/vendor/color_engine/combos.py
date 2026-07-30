#!/usr/bin/env python3
# ───────────────────────────────────────────────────────────────────────
# Groundwork — color COMBOS registry + the process for adding new ones.
#
# A "combo" is a NAMED relationship vector (see relationships.PARAMS): a point of
# departure in the infinite space. This module is the durable, machine-readable
# registry (`combos.jsonl`) plus the GATE every new combo must pass:
#
#   1. DEFINE   a relationship vector — by hand, or reverse-engineered from an
#               admired palette with relationships.describe_palette().
#   2. VALIDATE relationships.generate(params) MUST meet all contrast targets and
#               stay in gamut. add() refuses an invalid combo unless --force.
#   3. PREVIEW  render with designer/color/preview.py and eyeball it.
#   4. RECORD   append to combos.jsonl with name + intent + provenance.
#   5. PROMOTE  (optional) a repeatedly-winning combo becomes a named seed/prior
#               in Groundwork's Bayesian engine.
#
# This makes "add a new combo" a repeatable, gated action — not a vibe.
#
# Pure stdlib.  CLI:
#   python3 -m designer.color.combos seed            # write the starter set
#   python3 -m designer.color.combos add --name x --params '{"anchor_hue":30}' --intent "..."
#   python3 -m designer.color.combos list
#   python3 -m designer.color.combos validate --params '{"surface_L":0.16}'
# ───────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

try:  # in-repo: full package path
    from designer.color import relationships as R
except ImportError:  # vendored install: module sits beside this file
    import relationships as R  # type: ignore[no-redef]

STORE = os.path.join(os.path.dirname(__file__), "combos.jsonl")


def validate(params: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """A combo is valid iff generate() meets ALL its contrast targets (which also
    implies every color rendered in gamut). Returns (ok, full generation)."""
    gen = R.generate(params)
    return bool(gen["all_contrast_targets_met"]), gen


def add(name: str, params: dict[str, Any], intent: str = "", source: str = "",
        store: str = STORE, force: bool = False) -> dict[str, Any]:
    ok, gen = validate(params)
    if not ok and not force:
        raise ValueError(f"combo '{name}' fails contrast targets: "
                         f"{ {k: v for k, v in gen['contrast']['pass'].items() if not v} }. "
                         f"Adjust the vector or pass force=True to record it as known-invalid.")
    row = {
        "name": name, "intent": intent, "source": source, "mode": gen["mode"],
        "valid": ok, "params": params,
        "achieved": gen["contrast"]["achieved"], "roles": gen["roles"],
    }
    with open(store, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def load(store: str = STORE) -> list[dict[str, Any]]:
    try:
        with open(store, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


# The starter set — model defaults + priors distilled from the sources log and
# from the user's own observed taste (near-neutral surface, high-contrast text,
# saturated accent). Each is a POINT of departure; sweep any param for more.
SEED: list[dict[str, Any]] = [
    {"name": "balanced-light", "intent": "neutral default, AAA text, AA accent",
     "source": "model default", "params": {}},
    {"name": "balanced-dark", "intent": "neutral default, dark mode",
     "source": "model default", "params": {"surface_L": 0.16}},
    {"name": "punchy-dark", "intent": "high-contrast dark, saturated accent — matches the "
     "aurora/clarity/ember house style", "source": "reverse-engineered from planner-suite",
     "params": {"surface_L": 0.10, "on_surface_contrast": 16.0, "muted_contrast": 5.0,
                "accent_chroma": 0.18, "accent_contrast": 6.0}},
    {"name": "elegant-light", "intent": "restraint: near-white surface, lower-chroma accent",
     "source": "Webflow 'elegant' prior", "params": {"surface_L": 0.99, "on_surface_contrast": 9.0,
                "accent_chroma": 0.08, "accent_contrast": 4.5}},
    {"name": "editorial-warm", "intent": "warm paper surface, earthy accent",
     "source": "color-category: earth tones", "params": {"anchor_hue": 60, "surface_L": 0.965,
                "neutral_hue_delta": 40, "neutral_chroma": 0.02, "accent_hue_delta": 20,
                "accent_chroma": 0.11, "on_surface_contrast": 11.0}},
]


def seed(store: str = STORE) -> int:
    existing = {r["name"] for r in load(store)}
    n = 0
    for s in SEED:
        if s["name"] in existing:
            continue
        add(s["name"], s["params"], s["intent"], s["source"], store)
        n += 1
    return n


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="designer.color.combos", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("seed")
    pa = sub.add_parser("add")
    pa.add_argument("--name", required=True)
    pa.add_argument("--params", required=True, help="JSON relationship vector")
    pa.add_argument("--intent", default="")
    pa.add_argument("--source", default="")
    pa.add_argument("--force", action="store_true")
    sub.add_parser("list")
    pv = sub.add_parser("validate")
    pv.add_argument("--params", required=True)
    pi = sub.add_parser("ingest", help="record YOUR palette + get improvement suggestions")
    pi.add_argument("--name", required=True)
    pi.add_argument("--surface", required=True)
    pi.add_argument("--text", required=True)
    pi.add_argument("--accent", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "seed":
        print(f"seeded {seed()} new combo(s); registry: {STORE}")
        return 0
    if args.cmd == "add":
        try:
            row = add(args.name, json.loads(args.params), args.intent, args.source, force=args.force)
        except ValueError as e:
            print(f"REJECTED: {e}"); return 1
        print(f"added '{row['name']}' (valid={row['valid']}): {json.dumps(row['achieved'])}")
        return 0
    if args.cmd == "validate":
        ok, gen = validate(json.loads(args.params))
        print(json.dumps({"valid": ok, "achieved": gen["contrast"]["achieved"],
                          "pass": gen["contrast"]["pass"], "roles": gen["roles"]}, indent=2))
        return 0 if ok else 1
    if args.cmd == "list":
        for r in load():
            v = "✓" if r["valid"] else "✗"
            src = f"  ({r['source']})" if r.get("source") else ""
            print(f"  {v} {r['name']:16} [{r['mode']}]  {r['intent']}{src}")
        return 0
    if args.cmd == "ingest":
        rep = R.suggest_improvements(args.surface, args.text, args.accent)
        m = rep["measured"]
        row = {"name": args.name, "intent": "ingested user palette", "source": "mine",
               "mode": m["mode"], "valid": rep["clean"],
               "palette": {"surface": args.surface, "text": args.text, "accent": args.accent},
               "measured": m["relationships"], "suggestions": rep["suggestions"]}
        with open(STORE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"recorded '{args.name}' [{m['mode']}] — "
              + ("clean, matches the model" if rep["clean"] else f"{len(rep['suggestions'])} suggestion(s):"))
        for s in rep["suggestions"]:
            tip = f" -> {s['suggest']}" if s["suggest"] else ""
            print(f"  • {s['issue']}\n      {s['fix']}{tip}")
        return 0
    ap.print_help(); return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
