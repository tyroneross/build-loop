#!/usr/bin/env python3
# ───────────────────────────────────────────────────────────────────────
# Groundwork — color-relationships PREVIEW: render generated palettes as an
# HTML swatch sheet so the "infinite combinations from one relationship vector"
# idea is visible, not just numeric.
#
#   python3 -m designer.color.preview --sweep-hue 12 > sweep.html
#   python3 -m designer.color.preview --dark --sweep-hue 12 > sweep-dark.html
#
# Pure stdlib. Emits a self-contained static HTML file (no JS, no innerHTML).
# ───────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import html
import json
import sys
from typing import Any, Optional

try:  # in-repo: full package path
    from designer.color import relationships as R
except ImportError:  # vendored install: module sits beside this file
    import relationships as R  # type: ignore[no-redef]


def _swatch(hex_str: str, label: str, text_hex: Optional[str] = None) -> str:
    tc = text_hex or ("#111" if int(hex_str[1:3], 16) * 0.6 + int(hex_str[3:5], 16) > 200 else "#fff")
    return (f'<div class="sw" style="background:{html.escape(hex_str)};color:{tc}">'
            f'<span>{html.escape(label)}</span><code>{html.escape(hex_str)}</code></div>')


def _ramp(ramp: list[str]) -> str:
    return '<div class="ramp">' + "".join(
        f'<i style="background:{html.escape(c)}" title="{html.escape(c)}"></i>' for c in ramp) + "</div>"


def _card(p: dict[str, Any]) -> str:
    r = p["roles"]
    c = p["contrast"]["achieved"]
    hue = round(p["params"]["anchor_hue"], 0)
    ok = "✓" if p["all_contrast_targets_met"] else "✗"
    # on_surface text shown ON the surface to prove legibility at a glance
    demo = (f'<div class="demo" style="background:{r["surface"]};color:{r["on_surface"]}">'
            f'<b>Aa</b> body text '
            f'<span style="color:{r["muted"]}">muted</span> '
            f'<span class="chip" style="background:{r["accent"]};color:{r["on_accent"]}">Accent</span></div>')
    roles = "".join([
        _swatch(r["surface"], "surface"),
        _swatch(r["on_surface"], "on", r["surface"]),
        _swatch(r["muted"], "muted"),
        _swatch(r["accent"], "accent", r["on_accent"]),
    ])
    stats = (f'on/surf {c["on_surface_vs_surface"]} · muted {c["muted_vs_surface"]} · '
             f'acc/surf {c["accent_vs_surface"]} · on-acc {c["on_accent_vs_accent"]} · {ok}')
    return (f'<article class="card"><header>hue {hue:.0f}° · {p["mode"]}</header>'
            f'{demo}<div class="roles">{roles}</div>'
            f'{_ramp(p["ramps"]["base"])}{_ramp(p["ramps"]["neutral"])}'
            f'<footer>{html.escape(stats)}</footer></article>')


def swatch_html(palettes: list[dict[str, Any]], title: str) -> str:
    cards = "".join(_card(p) for p in palettes)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>
  body{{margin:0;background:#0e0f11;color:#e8eaed;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:28px}}
  h1{{font-size:16px;font-weight:600;margin:0 0 4px}} p.sub{{color:#8a9099;margin:0 0 24px;max-width:60ch}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px}}
  .card{{border:1px solid #23262b;border-radius:12px;overflow:hidden;background:#15171a}}
  .card header{{padding:8px 12px;font-size:12px;color:#8a9099;border-bottom:1px solid #23262b;text-transform:uppercase;letter-spacing:.05em}}
  .demo{{padding:16px 14px;font-size:15px}} .demo b{{font-size:20px;margin-right:6px}}
  .chip{{padding:2px 10px;border-radius:14px;font-size:12px;font-weight:600;margin-left:6px}}
  .roles{{display:grid;grid-template-columns:repeat(4,1fr)}}
  .sw{{height:58px;display:flex;flex-direction:column;justify-content:center;align-items:center;font-size:10px;gap:2px}}
  .sw code{{font-size:10px;opacity:.85}}
  .ramp{{display:flex;height:16px}} .ramp i{{flex:1}}
  .card footer{{padding:8px 12px;font-size:11px;color:#727880;border-top:1px solid #23262b}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="sub">One relationship vector (contrast targets, neutral/base/accent chroma structure, harmony delta) rotated across the hue wheel. The relationships are identical in every card — the colors are all different. The design is the relationships.</p>
<div class="grid">{cards}</div></body></html>"""


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="designer.color.preview")
    ap.add_argument("--sweep-hue", type=int, default=12)
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--title", default=None)
    ap.add_argument("--params", default=None,
                    help="JSON relationship vector: render THIS palette as the lead card, "
                         "then its hue-rotation family (preserves the elicited relationships/mode)")
    ap.add_argument("--params-file", default=None,
                    help="path to a JSON file with a top-level params vector, or an emit "
                         "result whose .params is used")
    args = ap.parse_args(argv)

    override: Optional[dict[str, Any]] = None
    if args.params_file:
        with open(args.params_file, encoding="utf-8") as f:
            doc = json.load(f)
        override = doc.get("params", doc) if isinstance(doc, dict) else None
    elif args.params:
        override = json.loads(args.params)

    if override is not None:
        # Lead with the ACTUAL elicited palette; follow with its hue family so the
        # "infinite combinations from one relationship vector" idea stays visible.
        lead = R.generate(override)
        lead_hue = lead["params"]["anchor_hue"]
        n = max(1, args.sweep_hue)
        hues = [round(360 * i / n, 1) for i in range(n)]
        fam = [p for p in R.sweep("anchor_hue", hues, override)
               if abs(p["params"]["anchor_hue"] - lead_hue) > 0.5]
        pals = [lead] + fam
        title = args.title or "Your palette + its hue-rotation family"
    else:
        base = {"surface_L": 0.16} if args.dark else {}
        hues = [round(360 * i / args.sweep_hue, 1) for i in range(args.sweep_hue)]
        pals = R.sweep("anchor_hue", hues, base)
        title = args.title or f"Groundwork color relationships — {args.sweep_hue} hues, {'dark' if args.dark else 'light'} mode"
    sys.stdout.write(swatch_html(pals, title))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
