#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Quick latency bench for the SQLite hybrid recall path.

Not a unit test — just a measurement harness so the P1 run report can
record real numbers (cold vs warm; keyword vs hybrid). Run directly:

    PYTHONPATH=scripts python3 scripts/semantic_index/_bench_hybrid.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from semantic_index import query_facts, upsert_fact  # noqa: E402


_VEC_DIM = 1024


def _det_vec(seed: int) -> list[float]:
    """Deterministic 1024-dim vector seeded from an int."""
    rng_state = seed
    out: list[float] = []
    for _ in range(_VEC_DIM):
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((rng_state % 1000) / 1000.0 - 0.5)
    return out


def _embed_det(text: str) -> list[float]:
    return _det_vec(abs(hash(text)) & 0xFFFFFF)


def _seed(db: Path, n: int) -> None:
    """Seed with explicit embeddings (legacy path) — used for read benches."""
    for i in range(n):
        upsert_fact(
            subject=f"fact:{i}",
            predicate="describes",
            object_text=f"row {i} adapter boundary lesson token{i % 50}",
            project="bench",
            embedding=_det_vec(i),
            db_path=db,
        )


def _bench_write_path(db: Path, n: int) -> dict[str, float]:
    """Measure auto-embed-on-write latency delta vs explicit-embedding write.

    Both paths persist the same vector to the same column; the only
    difference is whether ``upsert_fact`` calls ``embed_fn`` internally
    (auto path) or the caller pre-computed it (explicit path). The delta
    is the write-time cost of the f1 dormancy fix.

    Uses a stub embed_fn so the bench reflects the in-process overhead
    of the new seam, not the (much larger) MLX/Ollama cold-load.
    """
    from semantic_index import _embed_text_for_fact  # noqa: PLC0415

    # Path A: explicit embedding (legacy).
    def write_explicit():
        for i in range(n):
            upsert_fact(
                subject=f"explicit:{i}",
                predicate="describes",
                object_text=f"row {i} content",
                project="bench-write",
                embedding=_det_vec(i),
                db_path=db,
            )

    # Path B: auto-embed via injected embed_fn (production path post-f1).
    def write_auto():
        for i in range(n):
            upsert_fact(
                subject=f"auto:{i}",
                predicate="describes",
                object_text=f"row {i} content",
                project="bench-write",
                db_path=db,
                embed_fn=_embed_det,
            )

    t0 = time.perf_counter()
    write_explicit()
    explicit_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    write_auto()
    auto_ms = (time.perf_counter() - t1) * 1000
    return {
        "explicit_total_ms": round(explicit_ms, 2),
        "auto_total_ms": round(auto_ms, 2),
        "explicit_per_row_ms": round(explicit_ms / n, 3),
        "auto_per_row_ms": round(auto_ms / n, 3),
        "delta_per_row_ms": round((auto_ms - explicit_ms) / n, 3),
        "rows": n,
    }


def _bench(fn, runs: int) -> dict[str, float]:
    """Return ms timings (min/median/mean across runs)."""
    samples: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return {
        "min_ms": round(samples[0], 2),
        "median_ms": round(samples[len(samples) // 2], 2),
        "mean_ms": round(sum(samples) / len(samples), 2),
        "max_ms": round(samples[-1], 2),
    }


def main() -> int:
    rows_n = 200
    runs_n = 20
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "bench.sqlite"
        t0 = time.perf_counter()
        _seed(db, rows_n)
        seed_ms = (time.perf_counter() - t0) * 1000
        print(f"[seed] {rows_n} rows in {seed_ms:.1f}ms")

        def run_keyword():
            return query_facts(
                query="adapter token17",
                project="bench",
                db_path=db,
                mode="keyword",
            )

        def run_hybrid():
            return query_facts(
                query="adapter token17",
                project="bench",
                db_path=db,
                mode="hybrid",
                embed_fn=_embed_det,
            )

        # First call (cold), then warm samples.
        cold_kw = _bench(run_keyword, runs=1)
        warm_kw = _bench(run_keyword, runs=runs_n)
        cold_hy = _bench(run_hybrid, runs=1)
        warm_hy = _bench(run_hybrid, runs=runs_n)

        print(f"[keyword] cold={cold_kw['min_ms']}ms  warm={warm_kw}")
        print(f"[hybrid]  cold={cold_hy['min_ms']}ms  warm={warm_hy}")

        # Write-path bench on a separate DB so seeded rows don't pollute
        # the read bench above.
        write_db = Path(td) / "bench-write.sqlite"
        wp = _bench_write_path(write_db, n=100)
        print(
            f"[write]   explicit={wp['explicit_per_row_ms']}ms/row  "
            f"auto={wp['auto_per_row_ms']}ms/row  "
            f"delta=+{wp['delta_per_row_ms']}ms/row "
            f"(n={wp['rows']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
