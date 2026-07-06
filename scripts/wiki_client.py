#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Wiki client: federate Tyrone's Obsidian LLM Wiki as a 4th RRF leg.

The wiki at `~/ObsidianVault/` ships its own graph-aware retrieval
stack — vector + lexical + PageRank rerank, exposed via the read-only
CLI at `~/ObsidianVault/tools/scripts/llmwiki search <query> -k N`.

This client wraps the CLI as a subprocess, parses the human-readable
output into dicts compatible with `scripts/rrf.py`'s rrf_fuse(), and
hands them to `recall.py`'s hybrid pipeline as a 4th input list.

Why subprocess and not an in-process import:
  - The wiki tool is its own Python project with its own venv +
    embedding model (Ollama nomic-embed-text). Importing
    `vault_vector.py` directly would force build-loop's interpreter to
    pick up its deps, conflicting with build-loop's own embedding stack.
  - Subprocess overhead is ~50-150ms steady-state on the first call
    (Python startup + Ollama embed). Acceptable for a leg that runs
    once per recall() — measured against the ~5s cold-load reranker
    cost it's a rounding error.

Wire format (CLI output as of 2026-04):
    query: 'hybrid search BM25 vector RRF'
    provider: ollama / nomic-embed-text  ·  --walk-graph (α=0.85)
    top 3 pages from 1760 chunks  ·  graph: 198 nodes / 1114 edges

    1. [ppr 0.013 | cos 0.622 | lex 2.470] page-id § Section  (graph)
       wiki/path/to/page.md
       <multiline excerpt, may wrap>

    2. ...

Parser is forgiving: any line starting with `<digit>.` opens a result
record, the next line is the path, subsequent non-blank non-numbered
lines are excerpt continuation, blank lines or the next numbered line
close the record.

Result shape (compatible with rrf_fuse):
    {
        "id":       "wiki:<page-id>#<section>",   # stable across runs
        "subject":  page-id (e.g. concept-hybrid-search-pgvector-fts-rrf),
        "predicate": section heading or "" if root-level,
        "object":   excerpt (truncated to ~400 chars),
        "score":    cos score (the dense leg's confidence; for RRF
                    only ordering matters but downstream multipliers
                    may inspect this),
        "ppr":      PageRank score from the wiki graph (informational),
        "cos":      cosine similarity,
        "lex":      lexical (BM25-ish) score,
        "wiki_path": path under the vault (e.g. wiki/concepts/.../foo.md),
        "source":   "wiki",                       # provenance tag
    }

Public API:
    wiki_search(query: str, k: int = 5, *, cli: str | None = None,
                timeout_s: float = 5.0) -> list[Result]
    is_available(*, cli: str | None = None) -> bool
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_CLI = "~/ObsidianVault/tools/scripts/llmwiki"
ENV_CLI = "BUILD_LOOP_WIKI_CLI"
DEFAULT_TIMEOUT_S = 5.0
EXCERPT_MAX_CHARS = 400

_log = logging.getLogger("build_loop.wiki_client")
if not _log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("[wiki_client] %(message)s"))
    _log.addHandler(h)
    _log.setLevel(logging.INFO)

_AVAILABILITY_CACHE: dict[str, bool] = {}

# Matches a result header line like:
#   1. [ppr 0.013 | cos 0.622 | lex 2.470] page-id § Section  (graph)
# or, for entries without a section:
#   1. [ppr 0.013 | cos 0.622 | lex 2.470] page-id  (vector)
_RESULT_HEADER_RE = re.compile(
    r"^\s*(\d+)\.\s+"
    r"\[(?:\[seedling\]\s*)?"  # tolerate [seedling] prefix inside the brackets
    r"ppr\s+([\d.]+)\s*\|\s*cos\s+([\d.]+)\s*\|\s*lex\s+([\d.]+)\]\s+"
    r"(?:\[seedling\]\s*)?"  # or as a separate token after the bracket
    r"(\S+?)"  # page-id
    r"(?:\s+§\s+(.+?))?"  # optional § Section
    r"(?:\s+\((\w+)\))?"  # optional (graph|vector) tag
    r"\s*$"
)


def _resolve_cli(cli: str | None = None) -> str | None:
    """Return an absolute path to the llmwiki CLI, or None if missing."""
    candidate = cli or os.environ.get(ENV_CLI) or DEFAULT_CLI
    p = Path(candidate).expanduser()
    if p.exists() and os.access(p, os.X_OK):
        return str(p)
    # Fall back to PATH lookup.
    found = shutil.which("llmwiki")
    return found


def is_available(*, cli: str | None = None) -> bool:
    """True iff the wiki CLI exists and runs `--help` successfully."""
    resolved = _resolve_cli(cli)
    if resolved is None:
        return False
    if resolved in _AVAILABILITY_CACHE:
        return _AVAILABILITY_CACHE[resolved]
    try:
        proc = subprocess.run(  # noqa: S603
            [resolved, "--help"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        # The help text starts with "tools/scripts/llmwiki — simple terminal".
        ok = proc.returncode == 0 or "llmwiki" in (proc.stdout + proc.stderr)
    except (subprocess.TimeoutExpired, OSError):
        ok = False
    _AVAILABILITY_CACHE[resolved] = ok
    return ok


def _truncate(text: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def parse_search_output(stdout: str) -> list[dict[str, Any]]:
    """Parse the llmwiki `search` text into structured Result dicts.

    Tolerant: blank lines and the header preamble (`query:`, `provider:`,
    `top N pages …`) are skipped. Anything that doesn't match the result
    header pattern AND comes after the first result is treated as either
    a path line or excerpt continuation.
    """
    results: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    excerpt_lines: list[str] = []
    seen_path = False

    def _close_current():
        nonlocal current, excerpt_lines, seen_path
        if current is None:
            return
        if excerpt_lines:
            current["object"] = _truncate(" ".join(excerpt_lines))
        results.append(current)
        current = None
        excerpt_lines = []
        seen_path = False

    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        m = _RESULT_HEADER_RE.match(line)
        if m:
            _close_current()
            _, ppr, cos, lex, page_id, section, _tag = m.groups()
            section = (section or "").strip()
            current = {
                "id": f"wiki:{page_id}" + (f"#{section}" if section else ""),
                "subject": page_id,
                "predicate": section,
                "object": "",
                "score": float(cos),
                "ppr": float(ppr),
                "cos": float(cos),
                "lex": float(lex),
                "wiki_path": "",
                "source": "wiki",
            }
            seen_path = False
            excerpt_lines = []
            continue
        if current is None:
            # Header preamble — skip until the first numbered result.
            continue
        if not line.strip():
            # Blank inside an entry: closes the excerpt (next non-blank
            # may be the next entry's path or a wrap).
            continue
        # First non-blank line after the header is the wiki path.
        if not seen_path:
            current["wiki_path"] = line.strip()
            seen_path = True
            continue
        # Otherwise, accumulate excerpt.
        excerpt_lines.append(line.strip())

    _close_current()
    return results


def wiki_search(
    query: str,
    k: int = 5,
    *,
    cli: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Run `llmwiki search <query> -k <k>` and parse to Result dicts.

    Phase I routing: try the in-process `wiki_local` module first
    (avoids the ~920ms subprocess fork + JSON parse cost). Fall back to
    the legacy `llmwiki` subprocess CLI when:
      - `WIKI_FORCE_SUBPROCESS=1` is set (test / safety override)
      - `wiki_local.is_available()` is False (vault store missing)
      - `wiki_local.search()` raises (parse error, unreadable file, ...)
    Subprocess remains the failure-mode floor so the leg never goes silent
    on hosts where the in-process path can't load.

    Graceful skip contract for the subprocess path:
      - CLI missing → log once, return [].
      - CLI returns non-zero → log once, return [].
      - CLI times out → log once, return [].
      - Output unparseable → log once, return [].
    Never raises.
    """
    if not query or not query.strip():
        return []

    # Phase I in-process path. Skip if user forced subprocess.
    if not os.environ.get("WIKI_FORCE_SUBPROCESS"):
        try:
            import wiki_local  # type: ignore  # noqa: PLC0415
            if wiki_local.is_available():
                return wiki_local.search(query, k=k)
        except ImportError:
            _log.debug("wiki_local not importable; falling back to subprocess")
        except Exception as e:  # noqa: BLE001
            _log.warning("wiki_local.search raised (%s); falling back to subprocess", e)

    resolved = _resolve_cli(cli)
    if resolved is None:
        _log.debug("wiki CLI not found; skipping wiki leg")
        return []
    cmd = [resolved, "search", query, "-k", str(int(k))]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.warning("wiki CLI timed out after %.1fs; skipping wiki leg", timeout_s)
        return []
    except OSError as e:
        _log.warning("wiki CLI failed to launch (%s); skipping wiki leg", e)
        return []
    if proc.returncode != 0:
        _log.warning(
            "wiki CLI returned exit %d; skipping wiki leg. stderr tail: %s",
            proc.returncode, (proc.stderr or "")[-400:],
        )
        return []
    try:
        return parse_search_output(proc.stdout)
    except Exception as e:  # noqa: BLE001
        _log.warning("wiki output parse failed (%s); skipping wiki leg", e)
        return []


# ---------------------------------------------------------------------------
# CLI passthrough for ad-hoc inspection: `python -m wiki_client <query>`
# ---------------------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415
    import json  # noqa: PLC0415

    p = argparse.ArgumentParser(description="Probe the wiki client and pretty-print results")
    p.add_argument("query", nargs="+", help="search query (joined with spaces)")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--cli", default=None, help="override path to llmwiki executable")
    args = p.parse_args(argv)
    query = " ".join(args.query)
    results = wiki_search(query, k=args.k, cli=args.cli)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
