"""Gate for skills/decision-queue/assets/template.html.

The template is a companion asset the skill tells authors to COPY and adapt, so
a defect here ships into every decision-queue page built from it. Three defect
classes have already occurred or been found in it, and each has a check below.

1. SELF-PUBLISH CONSTANT DRIFT. The <head> CSS and the save-bar markup exist
   twice — as real markup, and as `HEAD_HTML` / `SAVE_BAR_HTML` template
   literals that `buildDocument()` splices into every republished copy.
   SKILL.md asked authors to sync them by hand. Found 2026-08-29:
   `SAVE_BAR_HTML` was an empty string against 382 characters of markup, so
   every republished page shipped with no Save button, no status line and no
   counter — savable exactly once, then broken.

2. LIVE-DOM CAPTURE. Replacing those constants with `document.head.innerHTML`
   sweeps up the artifact viewer's injected bootstrap script; the next load
   injects a second copy and the page's <style> stops applying. Real shipped
   bug, 2026-08-26.

3. UNESCAPED INTERPOLATION. Card fields are concatenated into an `innerHTML`
   string. Field values come from Operations Center task records — text
   written by other agents — so an unescaped field is an injection path.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "skills" / "decision-queue" / "assets" / "template.html"
REGEN = REPO / "skills" / "decision-queue" / "scripts" / "regen_template_constants.py"


def _load_regen():
    spec = importlib.util.spec_from_file_location("regen", REGEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_template_and_regen_script_exist():
    assert TEMPLATE.is_file(), f"missing template: {TEMPLATE}"
    assert REGEN.is_file(), f"missing regen script: {REGEN}"


def test_self_publish_constants_match_authored_markup(html):
    """HEAD_HTML / SAVE_BAR_HTML must reproduce the markup byte for byte."""
    bad = _load_regen().drift(html)
    assert not bad, (
        "self-publish constants have drifted from the authored markup: "
        + "; ".join(bad)
        + " — run skills/decision-queue/scripts/regen_template_constants.py"
    )


def test_save_bar_constant_is_not_empty(html):
    """An empty SAVE_BAR_HTML silently strips the save bar on republish.

    Kept separate from the drift check so the failure names the user-visible
    consequence rather than a character count.
    """
    m = re.search(r"var SAVE_BAR_HTML = `(.*?)`;", html, re.S)
    assert m, "SAVE_BAR_HTML constant not found"
    assert "save-bar-shell" in m.group(1), (
        "SAVE_BAR_HTML does not contain the save bar; every republished page "
        "would lose its Save button, status line and counter"
    )


def _strip_comments(text: str) -> str:
    """Blank out /* */, // and <!-- --> comments, preserving line numbering.

    A line-prefix test is not enough: the template documents this very hazard
    inside a block comment, and the continuation line `...capturing
    document.head.innerHTML at runtime bakes...` starts with prose, not `*`.
    Matching on that line reports the warning ABOUT the bug as the bug.
    """
    out = list(text)
    i, n = 0, len(text)
    state = None  # None | "block" | "line" | "html"
    while i < n:
        if state is None:
            if text.startswith("/*", i):
                state, span = "block", 2
            elif text.startswith("//", i):
                state, span = "line", 2
            elif text.startswith("<!--", i):
                state, span = "html", 4
            else:
                i += 1
                continue
            for k in range(i, i + span):
                out[k] = " "
            i += span
        else:
            ends = {"block": "*/", "line": "\n", "html": "-->"}[state]
            if text.startswith(ends, i):
                for k in range(i, i + len(ends)):
                    if text[k] != "\n":
                        out[k] = " "
                i += len(ends)
                state = None
            else:
                if text[i] != "\n":
                    out[i] = " "
                i += 1
    return "".join(out)


def test_no_live_dom_capture_for_self_publish(html):
    """Only the app-script's own tag may be read from the DOM."""
    code = _strip_comments(html)
    offenders = []
    for lineno, line in enumerate(code.splitlines(), 1):
        if "document.head.innerHTML" in line:
            offenders.append(f"{lineno}: document.head.innerHTML")
        if ".outerHTML" in line and 'getElementById("app-script")' not in line:
            offenders.append(f"{lineno}: {line.strip()[:70]}")
    assert not offenders, (
        "live-DOM capture in self-publish path (bakes the viewer's injected "
        "bootstrap into the save): " + "; ".join(offenders)
    )


def _app_script(html: str) -> str:
    return html[re.search(r'^<script id="app-script">', html, re.M).start():]


def test_html_builder_escapes_by_construction(html):
    """The h`` tag must exist and escape every interpolation it receives."""
    app = _app_script(html)
    assert re.search(r"function h\(strings\)", app), (
        "the h`` tagged-template builder is missing; card markup would fall back "
        "to manual escaping, which historically covered 5 of ~23 sites"
    )
    body = re.search(r"function h\(strings\).*?\n  \}", app, re.S).group(0)
    assert "escapeHtml(value)" in body, (
        "h`` no longer routes interpolated values through escapeHtml()"
    )


def test_no_untagged_literal_interpolates_a_field(html):
    """Every `${item.x}` must sit inside an h`` literal, not a bare one.

    This is the failure mode of the CURRENT design. Escaping is by construction,
    so the way it regresses is not a forgotten escapeHtml() call — it is someone
    adding markup with a plain backtick literal and dropping the `h` prefix.
    """
    app = _strip_comments(_app_script(html))
    raw = []
    for m in re.finditer(r"(\w*)`([^`]*)`", app, re.S):
        tag, inner = m.group(1), m.group(2)
        if tag == "h":
            continue
        for f in re.findall(r"\$\{\s*((?:item|meta|opt|c)\.[A-Za-z_]\w*)", inner):
            raw.append(f"line~{app[:m.start()].count(chr(10)) + 1}: ${{{f}}}")
    assert not raw, (
        "field interpolated in an untagged template literal (not escaped): "
        + "; ".join(raw)
    )
