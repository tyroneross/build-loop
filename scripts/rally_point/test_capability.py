#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the coordination capability contract (capability.py)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import capability  # noqa: E402


class CapabilityContractTests(unittest.TestCase):
    def test_full_only_operations_refused_below_full(self) -> None:
        for op in capability.FULL_ONLY_OPERATIONS:
            self.assertTrue(capability.operation_allowed(capability.FULL, op))
            self.assertFalse(
                capability.operation_allowed(capability.DEGRADED_BREADCRUMB, op),
                f"{op} must NOT be allowed in degraded-breadcrumb mode",
            )
            self.assertFalse(
                capability.operation_allowed(capability.UNAVAILABLE, op)
            )

    def test_breadcrumb_ops_allowed_in_degraded_not_unavailable(self) -> None:
        # A non-full-only op (e.g. a presence breadcrumb post).
        op = "presence_breadcrumb"
        self.assertNotIn(op, capability.FULL_ONLY_OPERATIONS)
        self.assertTrue(capability.operation_allowed(capability.FULL, op))
        self.assertTrue(
            capability.operation_allowed(capability.DEGRADED_BREADCRUMB, op)
        )
        self.assertFalse(capability.operation_allowed(capability.UNAVAILABLE, op))

    def test_mark_full_forces_none_reason(self) -> None:
        env = capability.mark({"ok": True}, capability.FULL, "no_binary")
        self.assertEqual(env["capability_level"], capability.FULL)
        self.assertIsNone(env["coordination_unavailable"])

    def test_mark_subfull_keeps_reason(self) -> None:
        env = capability.mark(
            {}, capability.DEGRADED_BREADCRUMB, capability.REASON_NO_BINARY
        )
        self.assertEqual(env["coordination_unavailable"], "no_binary")
        env2 = capability.mark({}, capability.UNAVAILABLE)
        # Default reason is never silently empty.
        self.assertEqual(env2["coordination_unavailable"], "no_binary")

    def test_unavailable_envelope_is_loud(self) -> None:
        env = capability.unavailable_envelope(
            "claim", capability.REASON_UNSUPPORTED_HOST, channel_dir="/x"
        )
        self.assertFalse(env["ok"])
        self.assertEqual(env["capability_level"], capability.UNAVAILABLE)
        self.assertEqual(env["coordination_unavailable"], "unsupported_host")
        self.assertEqual(env["operation"], "claim")
        self.assertIn("shadow", env["detail"])
        self.assertEqual(env["channel_dir"], "/x")

    def test_level_for_resolved_via(self) -> None:
        # Native binary, healthy → full.
        self.assertEqual(
            capability.level_for_resolved_via("rust-cli", None), capability.FULL
        )
        # Incompatible protocol → loud unavailable, never breadcrumb.
        self.assertEqual(
            capability.level_for_resolved_via("rust-cli", "incompatible_protocol"),
            capability.UNAVAILABLE,
        )
        # Embedded fallback → degraded-breadcrumb.
        self.assertEqual(
            capability.level_for_resolved_via("build-loop-internal", None),
            capability.DEGRADED_BREADCRUMB,
        )
        # discover() degraded flag → degraded-breadcrumb.
        self.assertEqual(
            capability.level_for_resolved_via("rust-cli", "degraded"),
            capability.DEGRADED_BREADCRUMB,
        )


if __name__ == "__main__":
    unittest.main()
