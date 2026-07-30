#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Invariant: build-loop's recency decay is computed in pure Python.

build-loop and agent-rally-point are intentionally SEPARATE coordination
systems that stay COMPATIBLE via a shared decay policy (decay.py mirrors the
canonical decay.rs; both pinned to one golden fixture). A consequence of that
separation is that build-loop's historical-message decay listing
(`_read_recent_changes`, the build-loop equivalent of ``rally room``/``recent``)
must compute the recency weight ITSELF over its own change-log store — it must
NOT shell out to the Rust ``rally`` binary for the listing.

Why this matters: a ``rally`` binary that predates the decay feature returns
UN-decayed output. If build-loop ever routed its decay listing through such a
binary, decay would silently stop working whenever an old binary was on PATH.
Today it does not (verified 2026-06-22). This test pins that guarantee so a
future change that wires the binary into the decay listing fails loudly here.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coordination_status as cs  # noqa: E402
from rally_point import changes  # noqa: E402

_DAY = 86_400


class DecayIsPythonOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.channel = self.tmp / "channel"
        self.channel.mkdir(parents=True, exist_ok=True)
        now = time.time()
        # Fresh (weight ~1.0) and 20-days-old (weight ~0.5^10 << 0.05 floor).
        changes.append_change(self.channel, {"id": "fresh", "kind": "note", "ts": now})
        changes.append_change(
            self.channel, {"id": "old", "kind": "note", "ts": now - 20 * _DAY}
        )

    def test_decay_listing_archives_old_and_orders_fresh_first(self) -> None:
        default = cs._read_recent_changes(self.channel, 50, workdir=self.tmp)
        ids = [r["id"] for r in default]
        self.assertEqual(ids, ["fresh"], "20-day-old record must be archived by default")

        with_arch = cs._read_recent_changes(
            self.channel, 50, workdir=self.tmp, include_archived=True
        )
        ids_arch = [r["id"] for r in with_arch]
        self.assertEqual(
            ids_arch[0], "fresh", "fresh record must rank first (recency-ordered)"
        )
        self.assertIn("old", ids_arch, "--include-archived must re-include the old record")

    def test_decay_listing_shells_out_to_no_binary(self) -> None:
        """The decay listing must be pure-Python: zero subprocess invocations.

        If a future change routes the listing through ``rally recent``/``room``
        (or any binary), this guard fails — that is the ONLY way the
        "old binary on PATH un-decays build-loop output" divergence could ever
        become real, and it must never pass silently.
        """
        calls: list[object] = []
        real_run = cs.subprocess.run

        def _recording_run(*args, **kwargs):  # noqa: ANN002, ANN003
            calls.append(args[0] if args else kwargs.get("args"))
            return real_run(*args, **kwargs)

        cs.subprocess.run = _recording_run  # type: ignore[assignment]
        try:
            cs._read_recent_changes(self.channel, 50, workdir=self.tmp)
            cs._read_recent_changes(
                self.channel, 50, workdir=self.tmp, include_archived=True
            )
        finally:
            cs.subprocess.run = real_run  # type: ignore[assignment]

        self.assertEqual(
            calls,
            [],
            f"decay listing must not invoke any subprocess/binary; saw: {calls!r}",
        )


if __name__ == "__main__":
    unittest.main()
