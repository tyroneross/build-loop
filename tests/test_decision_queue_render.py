"""Execute the decision-queue template and assert its behaviour, not its text.

The sibling `test_decision_queue_template.py` greps the file. Grep proves the
markup is present; it cannot prove the page works. This test extracts the real
`app-data` + `app-script` blocks, runs them under Node against a small DOM shim,
and asserts on rendered output — escaping, the staleness chip, the standing
"no longer relevant" option, the answered/filter class, the fieldset grouping,
draft round-trip, and the save bar surviving `buildDocument()`.

That last one is why this exists. `SAVE_BAR_HTML` was an empty string for four
days while every static check passed, because nothing executed `buildDocument()`
and looked at what came out.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "skills" / "decision-queue" / "assets" / "template.html"
PROBE = pathlib.Path(__file__).resolve().parent / "assets" / "decision_queue_render_probe.js"


def _block(html: str, tag: str) -> str:
    """Extract one <script id="..."> body.

    Anchored to line start on purpose: the template's authoring comment names
    both script tags in prose, and an unanchored match returns that comment —
    which then fails to parse as JS for reasons that look nothing like the cause.
    """
    m = re.search(r'^<script id="%s">\n(.*?)\n</script>' % tag, html, re.S | re.M)
    assert m, f"could not extract <script id={tag}> from the template"
    return m.group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_template_renders_and_behaves(tmp_path):
    html = TEMPLATE.read_text(encoding="utf-8")
    app = tmp_path / "app.js"
    app.write_text(_block(html, "app-data") + "\n" + _block(html, "app-script"),
                   encoding="utf-8")

    syntax = subprocess.run(["node", "--check", str(app)],
                            capture_output=True, text=True)
    assert syntax.returncode == 0, f"template JS does not parse:\n{syntax.stderr}"

    run = subprocess.run(["node", str(PROBE), str(app)],
                         capture_output=True, text=True)
    assert run.returncode == 0, (
        "decision-queue template render probe failed:\n"
        + run.stdout + "\n" + run.stderr
    )
