#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Render mermaid diagrams from a markdown or .mmd file for visual QA.

Build-loop capability: extract every ```mermaid fenced block from a file, render
each to a PNG via mermaid-cli (`mmdc`, or `npx @mermaid-js/mermaid-cli`), and
emit a JSON envelope of output paths and errors. The orchestrator (or a human)
then VIEWS the PNGs and applies the diagram-mapper QA pass: no dangling decision
branches, no crossed/back-edge connectors, dashed edges only for exception or
additive paths, and node types distinguishable (thin input, standard process,
heavy terminal, accent decision).

Why this exists: mermaid only renders in a browser, so "looks fine in source"
is not QA. This turns a diagram edit into a render-and-look loop the build-loop
review phase can run on any doc that changed mermaid.

Usage:
    python3 mermaid_render.py <file.md|file.mmd> [--out DIR] [--json]

Exit code: 0 when every block rendered (or the file has no mermaid); 1 when any
block failed or no renderer is installed.
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

_MERMAID_RE = re.compile(r"```mermaid[^\n]*\n(.*?)```", re.S)


def extract_mermaid_blocks(text: str) -> list[str]:
    """Return the body of each ```mermaid fenced block, in document order.

    Pure (no I/O) so it is unit-testable without a renderer. A leading info
    string after the ``` fence (rare) is tolerated and dropped.
    """
    return [block.rstrip("\n") for block in _MERMAID_RE.findall(text)]


def renderer_cmd() -> list[str] | None:
    """Resolve the mermaid-cli invocation: a local `mmdc`, else `npx`."""
    if shutil.which("mmdc"):
        return ["mmdc"]
    if shutil.which("npx"):
        return ["npx", "--yes", "@mermaid-js/mermaid-cli"]
    return None


def render_file(path: str, out_dir: str, *, timeout: int = 180) -> dict:
    """Extract and render every mermaid block in `path` into `out_dir`."""
    with open(path, encoding="utf-8") as fh:
        blocks = extract_mermaid_blocks(fh.read())
    result: dict = {"file": path, "blocks": len(blocks), "rendered": [], "errors": []}
    if not blocks:
        return result
    cmd = renderer_cmd()
    if cmd is None:
        result["errors"].append(
            "no mermaid renderer found; install mmdc or npx @mermaid-js/mermaid-cli"
        )
        return result
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    for index, body in enumerate(blocks, 1):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".mmd", delete=False)
        try:
            tmp.write(body)
            tmp.close()
            png = os.path.join(out_dir, f"{stem}-diagram-{index}.png")
            proc = subprocess.run(
                cmd + ["-i", tmp.name, "-o", png, "-b", "white"],
                capture_output=True, text=True, timeout=timeout,
            )
            if proc.returncode == 0 and os.path.exists(png):
                result["rendered"].append(png)
            else:
                msg = (proc.stderr or proc.stdout or "render failed").strip()
                result["errors"].append(f"block {index}: {msg[:200]}")
        except Exception as exc:  # noqa: BLE001 - report, never crash the loop
            result["errors"].append(f"block {index}: {exc}")
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render mermaid blocks for visual QA.")
    parser.add_argument("file", help="Markdown or .mmd file containing mermaid blocks")
    parser.add_argument("--out", default=None, help="Output directory for PNGs")
    parser.add_argument("--json", action="store_true", help="Emit a JSON envelope")
    args = parser.parse_args(argv)

    out_dir = args.out or os.path.join(tempfile.gettempdir(), "buildloop-mermaid")
    result = render_file(args.file, out_dir)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"{result['file']}: {result['blocks']} block(s), "
            f"{len(result['rendered'])} rendered, {len(result['errors'])} error(s)"
        )
        for path in result["rendered"]:
            print(f"  ok  {path}")
        for err in result["errors"]:
            print(f"  ERR {err}")

    ok = result["blocks"] == 0 or (result["rendered"] and not result["errors"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
