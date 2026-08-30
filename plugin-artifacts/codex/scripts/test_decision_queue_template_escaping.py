#!/usr/bin/env python3
"""The decision-queue page must not execute the text it displays.

skills/decision-queue/assets/template.html builds its markup by string
concatenation. On 2026-08-28 it applied escapeHtml() at 5 of ~23 interpolation
sites, so item.title, item.decision, item.why, item.impact, item.recommendation,
meta.lede and meta.footer all reached innerHTML raw. Those fields carry text
written by agents -- Operations Center tasks, backlog items, peer-authored rally
records -- so a crafted title executed in the reader's page. Rendering the audited
payload through the pre-fix template produced 14 live <img onerror> elements.

A second sink sat in buildDocument(): JSON.stringify() does not escape "<", so an
item containing "</script>" closed the data element early and the remainder parsed
as markup.

This test renders a hostile item through the real template and asserts nothing
executes. It runs the actual shipped file rather than a copy, so an edit that
reintroduces a raw interpolation fails here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "skills" / "decision-queue" / "assets" / "template.html"
PAYLOAD = "<img src=x onerror=alert(1)>"

HARNESS = r"""
import fs from "node:fs";
const lines = fs.readFileSync(process.argv[2], "utf8").split("\n");
const s = lines.findIndex(l => l.trim().startsWith('<script id="app-script">'));
const e = lines.findIndex((l, i) => i > s && l.trim() === "</script>");
if (s < 0 || e < 0) { console.log(JSON.stringify({error: "app-script block not found"})); process.exit(0); }
// Function declarations hoist inside the IIFE, so exporting at the top reaches
// the pure render helpers even though the DOM plumbing below throws under stubs.
const body = lines.slice(s + 1, e).join("\n").replace(
  "(function () {", "(function () {\n globalThis.__X = { renderBody, buildDocument };", 1);
const stub = () => ({ innerHTML:"", textContent:"", outerHTML:"", className:"", style:{},
  addEventListener(){}, appendChild(){}, setAttribute(){}, querySelectorAll:()=>[],
  querySelector:()=>null, classList:{add(){},remove(){},toggle(){}} });
globalThis.document = { getElementById: stub, querySelector: () => null, querySelectorAll: () => [],
  addEventListener(){}, createElement: stub, head: { innerHTML: "" }, body: stub() };
globalThis.window = globalThis;
globalThis.__META__ = { eyebrow:"", title:"", lede:"", footer:"", summaryCells: [] };
globalThis.__ITEMS__ = [];
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
try { new Function(body)(); } catch (err) { /* DOM plumbing throws under stubs */ }
if (!globalThis.__X) { console.log(JSON.stringify({error: "helpers not exported"})); process.exit(0); }

const P = process.argv[3];
const item = { id: 'a"><script>alert(2)<\/script>', num: P, opened: P, touched: P, title: P,
  repo: P, typeLabel: P, classChip: 'x">' + P, priority: P, selected: null, respondedAt: null,
  decision: P, why: P, whyInferred: false, impact: P, recommendation: P, comment: P,
  options: [{ letter: "A", text: P }] };
const meta = { eyebrow: P, title: P, lede: P, footer: P, summaryCells: [{ num: P, label: P }] };
const out = globalThis.__X.renderBody(meta, [item]);

const clean = { ...item, id: "T-1", title: "Ship the migration", decision: "Approve or hold",
  why: "irreversible", impact: "prod", recommendation: "approve", comment: "", num: "1",
  opened: "Aug 1", touched: "Aug 2", repo: "build-loop", typeLabel: "decision",
  classChip: "", priority: "P1", options: [{ letter: "A", text: "Approve" }] };
const cleanOut = globalThis.__X.renderBody(
  { eyebrow: "Q", title: "Decisions", lede: "3 open", footer: "end",
    summaryCells: [{ num: "3", label: "Open" }] }, [clean]);

let escapedDoc = null;
try {
  const doc = globalThis.__X.buildDocument({ ...meta, lede: "</script><script>alert(3)</script>" }, [clean]);
  escapedDoc = !/<\/script><script>alert\(3\)/.test(doc);
} catch (err) { escapedDoc = null; }

console.log(JSON.stringify({
  live_handlers: (out.match(/<img\s[^>]*onerror/gi) || []).length,
  injected_scripts: (out.match(/<script/gi) || []).length,
  raw_payload_tags: (out.match(/<img/gi) || []).length,
  attrs_with_raw_quote: [...out.matchAll(/="([^"]*)"/g)].filter(m => m[1].includes('"')).length,
  escaped_text_present: out.includes("&lt;img src=x onerror=alert(1)&gt;"),
  clean_renders: cleanOut.includes("Ship the migration") && cleanOut.includes("Approve"),
  clean_structure: /<div class="card" id="card-T-1">/.test(cleanOut),
  script_tag_payload_escaped: escapedDoc,
}));
"""


@unittest.skipUnless(shutil.which("node"), "node is required to execute the template")
class DecisionQueueTemplateEscapingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(HARNESS)
            harness = fh.name
        result = subprocess.run(
            ["node", harness, str(TEMPLATE), PAYLOAD],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        Path(harness).unlink(missing_ok=True)
        if result.returncode != 0:
            raise AssertionError(f"harness failed: {result.stderr[:2000]}")
        cls.report = json.loads(result.stdout.strip().splitlines()[-1])
        if "error" in cls.report:
            raise AssertionError(
                f"template structure changed: {cls.report['error']} — update this test's "
                "extraction alongside the template"
            )

    def test_no_event_handler_executes(self) -> None:
        self.assertEqual(
            self.report["live_handlers"], 0,
            "a hostile item field rendered a live event handler; the pre-fix template "
            "produced 14 here",
        )

    def test_no_script_element_is_injected(self) -> None:
        self.assertEqual(self.report["injected_scripts"], 0)

    def test_no_raw_markup_from_any_field(self) -> None:
        self.assertEqual(self.report["raw_payload_tags"], 0)

    def test_no_attribute_can_break_out(self) -> None:
        self.assertEqual(self.report["attrs_with_raw_quote"], 0)

    def test_payload_still_shown_to_the_reader_as_text(self) -> None:
        """Escaping must neutralise the markup, not silently drop the content."""
        self.assertTrue(self.report["escaped_text_present"])

    def test_legitimate_content_and_structure_survive(self) -> None:
        self.assertTrue(self.report["clean_renders"])
        self.assertTrue(self.report["clean_structure"])

    def test_data_script_tag_cannot_be_closed_early(self) -> None:
        escaped = self.report["script_tag_payload_escaped"]
        if escaped is None:
            self.skipTest("buildDocument not reachable in this harness")
        self.assertTrue(escaped, "an item containing </script> closed the data element early")


if __name__ == "__main__":
    unittest.main()
