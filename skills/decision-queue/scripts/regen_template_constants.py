#!/usr/bin/env python3
"""Regenerate the decision-queue template's self-publish constants from its markup.

WHY THIS EXISTS
---------------
`assets/template.html` carries its <head> CSS and its save-bar markup TWICE:

  1. as real markup the authored page renders, and
  2. as the `HEAD_HTML` / `SAVE_BAR_HTML` template-literal constants inside
     `<script id="app-script">`, which `buildDocument()` splices into every
     republished copy.

They must be byte-identical. They cannot be replaced with a runtime
`document.head.innerHTML` read: the claude.ai artifact viewer injects its own
bootstrap script into <head> before the page's script runs, so a DOM capture
bakes that injected script into the save. One reload later the viewer injects a
second copy on top of the stale one and the page's <style> stops applying —
a real, shipped, user-visible bug (2026-08-26).

SKILL.md told authors to keep the two copies in sync by hand and sketched this
script without shipping it. The predictable happened: `SAVE_BAR_HTML` was left
as an empty string while 285 characters of save-bar markup sat in the file, so
every republished page shipped with no Save button, no status line, and no
counter — the page could be saved exactly once, then broke.

Hand-syncing is the defect. Run this instead, and let
`tests/test_decision_queue_template.py` fail the build when the copies drift.

USAGE
    python3 regen_template_constants.py            # rewrite in place
    python3 regen_template_constants.py --check    # exit 1 if out of sync
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "assets" / "template.html"

# The authored markup runs from <title> to the close of the page's own <style>,
# and the save bar from its shell div to just before the content-zone script.
HEAD_BLOCK_RE = re.compile(r"<title>.*?</style>", re.S)
SAVE_BAR_BLOCK_RE = re.compile(
    r'<div class="save-bar-shell".*?</div>\s*\n</div>', re.S
)
HEAD_CONST_RE = re.compile(r"(  var HEAD_HTML = `)(.*?)(`;)", re.S)
SAVE_BAR_CONST_RE = re.compile(r"(  var SAVE_BAR_HTML = `)(.*?)(`;)", re.S)


class TemplateError(RuntimeError):
    pass


def _authored_region(html: str) -> str:
    """Everything before the plumbing script — the markup the page renders.

    Bounding the search matters: `HEAD_HTML`'s own value also contains
    `<title>...</style>`, so an unbounded regex would match the constant and
    compare it against itself, which passes no matter how far it has drifted.
    """
    # Anchor to line start: the authoring comment at the top of the file names
    # `<script id="app-script">` in prose, and a plain substring search finds
    # that mention first, truncating the region to ~270 chars and hiding the
    # real markup entirely.
    m = re.search(r'^<script id="app-script">', html, re.M)
    if not m:
        raise TemplateError('no <script id="app-script"> tag in template')
    return html[:m.start()]


def extract_blocks(html: str) -> tuple[str, str]:
    authored = _authored_region(html)

    head = HEAD_BLOCK_RE.search(authored)
    if not head:
        raise TemplateError("could not find the authored <title>...</style> block")

    bar = SAVE_BAR_BLOCK_RE.search(authored)
    if not bar:
        raise TemplateError("could not find the authored save-bar-shell block")

    head_txt, bar_txt = head.group(0), bar.group(0)
    for name, block in (("head", head_txt), ("save bar", bar_txt)):
        # These get spliced into JS template literals; a backtick or ${ would
        # terminate the literal or interpolate, silently corrupting the constant.
        if "`" in block:
            raise TemplateError(f"{name} block contains a backtick; template literal would break")
        if "${" in block:
            raise TemplateError(f"{name} block contains ${{; template literal would interpolate")
    return head_txt, bar_txt


def render(html: str) -> str:
    """Return `html` with both constants rewritten from the authored markup."""
    head_txt, bar_txt = extract_blocks(html)

    for const_re, value, label in (
        (HEAD_CONST_RE, head_txt, "HEAD_HTML"),
        (SAVE_BAR_CONST_RE, bar_txt, "SAVE_BAR_HTML"),
    ):
        if not const_re.search(html):
            raise TemplateError(f"could not find the {label} constant to rewrite")
        # A lambda avoids re.sub's backslash-escape handling mangling CSS.
        html = const_re.sub(lambda m, v=value: m.group(1) + v + m.group(3), html, count=1)
    return html


def drift(html: str) -> list[str]:
    """Names of constants that do not match the authored markup."""
    head_txt, bar_txt = extract_blocks(html)
    out = []
    for const_re, value, label in (
        (HEAD_CONST_RE, head_txt, "HEAD_HTML"),
        (SAVE_BAR_CONST_RE, bar_txt, "SAVE_BAR_HTML"),
    ):
        m = const_re.search(html)
        if not m:
            out.append(f"{label} (constant missing)")
        elif m.group(2) != value:
            got, want = len(m.group(2)), len(value)
            out.append(f"{label} (constant {got} chars, markup {want} chars)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1 instead of rewriting")
    ap.add_argument("--path", type=pathlib.Path, default=TEMPLATE)
    args = ap.parse_args()

    html = args.path.read_text(encoding="utf-8")
    try:
        bad = drift(html)
    except TemplateError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.check:
        if bad:
            print("template constants are OUT OF SYNC with the authored markup:")
            for b in bad:
                print(f"  - {b}")
            print("\nfix: python3 skills/decision-queue/scripts/regen_template_constants.py")
            return 1
        print("template constants match the authored markup")
        return 0

    if not bad:
        print("already in sync; nothing to do")
        return 0
    args.path.write_text(render(html), encoding="utf-8")
    print("regenerated: " + ", ".join(b.split(" ")[0] for b in bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
