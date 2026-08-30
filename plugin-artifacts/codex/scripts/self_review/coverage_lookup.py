#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""coverage_lookup.py — the single answer to "does a test cover this script?".

Two components asked that question and answered it differently, so the deep
self-review generated work the re-validator immediately closed.  Measured on the
2026-08-30 deep run: `selfscan` emitted **66** `self_missing_test` findings and
`revalidate_self_review_findings.py` dispositioned **53 (80%)** as already-covered
in the same session.  The detector matched only `scripts/test_<name>.py`; the
re-validator also accepted a test under any name that IMPORTS the script.  This
module owns that rule so the generator and the closer cannot disagree again.

A test module that imports X is a test file for X.  That is not proof of thorough
coverage, but the finding says "no test file for X" — asserting depth here would
invent a stronger claim than the finding makes.
"""
from __future__ import annotations

import re
from pathlib import Path

# Paths that are copies, caches, or other runs — never the live source of truth.
EXCLUDED_PARTS = ("plugin-artifacts", "worktrees", "__pycache__", ".build-loop")

# Captures the identifier in `import X`, `from X import`, and `X.py`.
# Per-stem equivalent of the re-validator's original per-stem pattern: the
# maximal-identifier capture makes `import foobar` fail to cover stem `foo`
# exactly as the anchored `\bimport\s+foo\b` did.
_REFERENCE_RE = re.compile(
    r"\bimport\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|\bfrom\s+([A-Za-z_][A-Za-z0-9_]*)\s+import"
    # Filenames may contain hyphens (transcript-pattern-miner.py); module
    # identifiers may not, so the literal-filename alternative is wider.
    r"|\b([A-Za-z_][A-Za-z0-9_-]*)\.py\b"
)


def is_live(path: Path) -> bool:
    """Return True when `path` is live source, not a mirror/cache/run artifact."""
    return not any(part in str(path) for part in EXCLUDED_PARTS)


class CoverageIndex:
    """One pass over the repo's live `test_*.py`, reusable across many stems.

    Built once per scan.  Answering per-stem by re-walking the tree costs
    O(stems x test-files) file reads — ~160 scripts against ~400 test files on
    this repo — which is why the detector gets an index and not a loop.
    """

    def __init__(self, root: Path) -> None:
        self.by_filename: dict[str, Path] = {}
        self.by_reference: dict[str, Path] = {}
        for tf in sorted(Path(root).rglob("test_*.py")):
            if not is_live(tf):
                continue
            self.by_filename.setdefault(tf.stem[len("test_"):], tf)
            try:
                text = tf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in _REFERENCE_RE.finditer(text):
                token = match.group(1) or match.group(2) or match.group(3)
                if token:
                    self.by_reference.setdefault(token, tf)

    def lookup(self, stem: str) -> tuple[Path | None, str]:
        """Return (test_path, how) where how is "filename" | "import" | ""."""
        named = self.by_filename.get(stem)
        if named is not None:
            return named, "filename"
        referenced = self.by_reference.get(stem)
        if referenced is not None:
            return referenced, "import"
        return None, ""


def find_test(root: Path, stem: str, index: CoverageIndex | None = None) -> tuple[Path | None, str]:
    """Locate a test covering `stem`. Returns (path, how).

    Pass `index` when asking about many stems; omit it for a one-off lookup.
    """
    return (index or CoverageIndex(root)).lookup(stem)
