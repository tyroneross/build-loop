# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/mece_gate.py — MECE + lateral-limits validation.

Covers:
  - the four file-ownership fields (owns/does_not_own/interface_contract/
    integration_checkpoint)
  - the two lateral-limit fields (allowed_tools/denied_tools) — G2, 2026-05-22
  - kind=handoff rejected when a lateral-limit field is missing or non-list
  - kind=handoff accepted with a full packet (empty allowed_tools is valid)
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import mece_gate as mg  # noqa: E402


def _full_ownership() -> dict:
    """A complete, valid ownership packet including lateral limits."""
    return {
        "owns": ["scripts/foo.py"],
        "does_not_own": ["scripts/bar.py"],
        "interface_contract": "exports foo()",
        "integration_checkpoint": "foo() callable from bar",
        "allowed_tools": ["Read", "Edit", "Bash:pytest"],
        "denied_tools": ["Bash:git push"],
    }


def test_full_packet_passes():
    valid, rej = mg.validate_handoff({"ownership": _full_ownership()}, tool="claude_code")
    assert valid is True
    assert rej == {}


def test_empty_allowed_tools_is_valid():
    """An empty allowed_tools list is an explicit boundary, not an omission."""
    ownership = _full_ownership()
    ownership["allowed_tools"] = []
    ownership["denied_tools"] = []
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="codex")
    assert valid is True, rej


def test_missing_allowed_tools_rejects():
    ownership = _full_ownership()
    del ownership["allowed_tools"]
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="claude_code")
    assert valid is False
    assert "allowed_tools" in rej["missing_or_invalid"]
    assert rej["reason"] == "missing_mece_fields"


def test_missing_denied_tools_rejects():
    ownership = _full_ownership()
    del ownership["denied_tools"]
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="claude_code")
    assert valid is False
    assert "denied_tools" in rej["missing_or_invalid"]


def test_non_list_allowed_tools_rejects():
    ownership = _full_ownership()
    ownership["allowed_tools"] = "Read,Edit"  # str, not list
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="claude_code")
    assert valid is False
    assert "allowed_tools" in rej["missing_or_invalid"]
    assert rej["reason"] == "invalid_field_type"


def test_missing_file_ownership_still_rejects():
    """Pre-existing file-ownership checks remain intact alongside G2 fields."""
    ownership = _full_ownership()
    del ownership["owns"]
    del ownership["interface_contract"]
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="claude_code")
    assert valid is False
    assert "owns" in rej["missing_or_invalid"]
    assert "interface_contract" in rej["missing_or_invalid"]


def test_missing_ownership_dict_rejects():
    valid, rej = mg.validate_handoff({}, tool="claude_code")
    assert valid is False
    assert rej["missing_or_invalid"] == ["ownership"]


# ---------------------------------------------------------------------------
# Ownership scope rule (relaxed 2026-05-25):
# ``owns`` may be empty IFF ``does_not_own`` is non-empty AND
# ``interface_contract`` is non-empty.
# ---------------------------------------------------------------------------


def test_empty_owns_allowed_when_does_not_own_present_and_interface_contract():
    """Presence-only rally: empty owns + non-empty does_not_own + contract passes."""
    ownership = _full_ownership()
    ownership["owns"] = []
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="codex")
    assert valid is True, rej


def test_empty_owns_rejected_when_does_not_own_also_empty():
    """Both lists empty is a vacuous claim and is rejected."""
    ownership = _full_ownership()
    ownership["owns"] = []
    ownership["does_not_own"] = []
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="claude_code")
    assert valid is False
    assert rej["reason"] == "empty_ownership_scope"
    assert "owns" in rej["missing_or_invalid"]
    assert "does_not_own" in rej["missing_or_invalid"]


def test_empty_owns_rejected_when_interface_contract_empty():
    """interface_contract is already required non-empty; empty owns can't bypass."""
    ownership = _full_ownership()
    ownership["owns"] = []
    ownership["interface_contract"] = "   "  # whitespace only
    valid, rej = mg.validate_handoff({"ownership": ownership}, tool="claude_code")
    assert valid is False
    assert rej["reason"] == "empty_required_string"
    assert "interface_contract" in rej["missing_or_invalid"]
