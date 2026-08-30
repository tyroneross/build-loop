#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the forward-compatible rally fact-kind gate.

The defect this closes: ``post._native_kind`` mapped onto a hardcoded literal set
of kinds build-loop *believed* rally accepts, while the binary resolver only
checked that ``say`` exists. A stale install therefore rejected a kind build-loop
had learned, after the local ledger append had already succeeded.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kind_capability  # noqa: E402

# Verbatim from ``rally 0.2.5 say --help`` (2026-08-29), wrapping included.
REAL_HELP = """Usage: rally say [--json] --tool=TOOL [--subject=SUBJECT] KIND

Available positional items:
    KIND                     fact kind to post; one of: claim, claim.expired, release, blocker,
                             resolve, decision, artifact, handoff, risk, lesson, session, wake,
                             presence, read, backlog-item, receipt, standby, mission

Available options:
        --json
        --tool=TOOL
"""

REAL_KINDS = frozenset(
    {
        "claim",
        "claim.expired",
        "release",
        "blocker",
        "resolve",
        "decision",
        "artifact",
        "handoff",
        "risk",
        "lesson",
        "session",
        "wake",
        "presence",
        "read",
        "backlog-item",
        "receipt",
        "standby",
        "mission",
    }
)


def write_fake_rally(directory: Path, help_text: str, *, exit_code: int = 0) -> Path:
    """Write an executable stub that prints ``help_text`` for ``say --help``."""
    path = directory / "rally"
    path.write_text(
        "#!/bin/sh\n"
        f"cat <<'RALLY_HELP_EOF'\n{help_text}\nRALLY_HELP_EOF\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class ParseKindsTests(unittest.TestCase):
    def test_parses_wrapped_real_help(self) -> None:
        self.assertEqual(kind_capability._parse_kinds(REAL_HELP), REAL_KINDS)

    def test_stops_at_section_boundary(self) -> None:
        """Trailing sections must not be swallowed into the vocabulary."""
        parsed = kind_capability._parse_kinds(REAL_HELP)
        assert parsed is not None
        self.assertNotIn("--json", parsed)
        self.assertNotIn("Available", parsed)

    def test_missing_marker_is_unparseable(self) -> None:
        self.assertIsNone(kind_capability._parse_kinds("Usage: rally say KIND\n"))

    def test_too_few_kinds_is_untrusted(self) -> None:
        """A one-token match is far likelier a mis-slice than a real vocabulary."""
        self.assertIsNone(
            kind_capability._parse_kinds("KIND  fact kind to post; one of: artifact\n")
        )


class SupportedKindsTests(unittest.TestCase):
    def setUp(self) -> None:
        kind_capability.clear_cache()
        self.addCleanup(kind_capability.clear_cache)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_probes_a_real_binary_surface(self) -> None:
        binary = write_fake_rally(self.dir, REAL_HELP)
        self.assertEqual(kind_capability.supported_kinds(binary), REAL_KINDS)

    def test_none_binary_is_unknown(self) -> None:
        self.assertIsNone(kind_capability.supported_kinds(None))
        self.assertIsNone(kind_capability.supported_kinds(""))

    def test_missing_binary_fails_open(self) -> None:
        self.assertIsNone(kind_capability.supported_kinds(self.dir / "absent"))

    def test_unparseable_help_fails_open(self) -> None:
        binary = write_fake_rally(self.dir, "Usage: rally say KIND\n")
        self.assertIsNone(kind_capability.supported_kinds(binary))

    def test_result_is_cached_per_binary_identity(self) -> None:
        binary = write_fake_rally(self.dir, REAL_HELP)
        self.assertEqual(kind_capability.supported_kinds(binary), REAL_KINDS)

        calls: list[list[str]] = []
        original = subprocess.run

        def counting_run(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(list(argv))
            return original(argv, *args, **kwargs)

        kind_capability.subprocess.run = counting_run  # type: ignore[assignment]
        try:
            self.assertEqual(kind_capability.supported_kinds(binary), REAL_KINDS)
        finally:
            kind_capability.subprocess.run = original  # type: ignore[assignment]
        self.assertEqual(calls, [], "cached probe must not re-spawn the binary")

    def test_cache_invalidates_when_the_binary_changes(self) -> None:
        """An upgraded install must not be judged by the stale install's answer."""
        binary = write_fake_rally(self.dir, "one of: alpha, beta, gamma\n")
        self.assertEqual(
            kind_capability.supported_kinds(binary), frozenset({"alpha", "beta", "gamma"})
        )
        os.utime(binary, (0, 0))
        write_fake_rally(self.dir, REAL_HELP)
        self.assertEqual(kind_capability.supported_kinds(binary), REAL_KINDS)


class NegotiateKindTests(unittest.TestCase):
    def setUp(self) -> None:
        kind_capability.clear_cache()
        self.addCleanup(kind_capability.clear_cache)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_supported_kind_passes_through_unchanged(self) -> None:
        binary = write_fake_rally(self.dir, REAL_HELP)
        self.assertEqual(kind_capability.negotiate_kind("handoff", binary), ("handoff", None))

    def test_unsupported_kind_demotes_to_artifact_with_a_reason(self) -> None:
        """The observed failure: a kind build-loop knows and the binary does not."""
        stale = write_fake_rally(
            self.dir,
            "KIND  fact kind to post; one of: claim, release, artifact, handoff\n",
        )
        sent, reason = kind_capability.negotiate_kind("session.closed", stale)
        self.assertEqual(sent, "artifact")
        assert reason is not None
        self.assertIn("session.closed", reason)
        self.assertIn("artifact", reason)

    def test_unknown_binary_keeps_the_static_mapping(self) -> None:
        self.assertEqual(
            kind_capability.negotiate_kind("session.closed", None), ("session.closed", None)
        )

    def test_binary_without_the_fallback_is_left_to_rallys_own_error(self) -> None:
        """Guessing a second fallback would hide rally's diagnostic. Do not."""
        odd = write_fake_rally(self.dir, "one of: alpha, beta, gamma\n")
        self.assertEqual(
            kind_capability.negotiate_kind("session.closed", odd), ("session.closed", None)
        )

    def test_gate_never_promotes_an_untested_kind(self) -> None:
        """The probe may only REMOVE a kind build-loop would send, never add one."""
        binary = write_fake_rally(self.dir, REAL_HELP)
        for kind in sorted(REAL_KINDS):
            sent, _ = kind_capability.negotiate_kind(kind, binary)
            self.assertEqual(sent, kind)


if __name__ == "__main__":
    unittest.main()
