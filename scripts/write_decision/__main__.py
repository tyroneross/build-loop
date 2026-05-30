#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Atomic decision writer for repo-local episodic memory.

Mirrors the atomicity contract of `write_run_entry.py`:
  - fcntl.flock(LOCK_EX) on a sidecar `.lock` file
  - tempfile + os.replace for any updated file
  - exit codes 0/1/2 (success / validation / filesystem)

The "memory triad" write-once pattern (design §9.A) — every successful
invocation produces THREE artifacts atomically as a unit:
  1. `build-loop-memory/projects/<project>/decisions/NNNN-YYYY-MM-DD-slug.md`
     (canonical MADR)
  2. updated `build-loop-memory/projects/<project>/decisions/INDEX.md`
     (browseable summary)
  3. one line appended to `<repo>/.build-loop/events.jsonl` (timeline event)

Topic-identity supersession (design §10):
  Same `primary_tag + entity` triggers an overwrite check:
    - higher confidence auto-supersedes lower (prior moves to _history/)
    - equal confidence requires `--supersedes <id>` flag
    - lower confidence is rejected (exit 1)

Postgres dual-write (Phase 2, opt-in via --db / --no-db, default --db):
  After file writes succeed, embed the decision body via the
  `embed_backend` abstraction (MLX `mxbai-embed-large-v1` by default,
  Ollama `mxbai-embed-large` fallback, both 1024-dim), then INSERT a
  row into `agent_memory.<schema>.semantic_facts` over a persistent
  psycopg connection (`scripts/db.py`). DB errors LOG and continue —
  the file is canonical; DB is regenerable via
  `sync_db_from_files.py`.

Contract:
  stdout      -> decision_id (zero-padded 4-digit) on success, nothing else
  stderr      -> human-readable log lines
  exit 0      -> success
  exit 1      -> validation error (bad args, vocab violation, supersession refused)
  exit 2      -> filesystem error (permission denied, disk full, lock timeout)

Phase 1 uses stdlib only. Phase 2 DB path uses `psycopg[binary]` (added
2026-05-04 to enable batched Stop-hook writes; ~5-10ms/query vs
~50-100ms via psql subprocess).

Canonical invocation:
  python3 scripts/write_decision/__main__.py --title … --decision … --tags …
                                             --primary-tag … --entity … --confidence …

Also importable as a package:
  from write_decision import write_decision_main, parse_frontmatter, ...
  (with scripts/ on sys.path — __init__ re-exports the public surface.)
"""
from __future__ import annotations

import sys
from pathlib import Path

# When run directly (`python3 scripts/write_decision/__main__.py`), sys.path[0]
# is the package directory, so flat sibling imports work.  When imported via
# the package, __init__.py has already inserted the package dir + scripts/.
_PKG_DIR = Path(__file__).resolve().parent
for _p in (str(_PKG_DIR), str(_PKG_DIR.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from writer import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
