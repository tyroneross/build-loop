#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Validate peer handoff briefs for the four MECE ownership fields."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MECE_FIELDS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "owns",
        "owns",
        re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?owns(?:\*\*)?\s*(?:\(|:|-|$)"),
    ),
    (
        "does_not_own",
        "does-not-own",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"does\s*[-_ ]?not\s*[-_ ]?own(?:s)?(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
    (
        "interface_contract",
        "interface-contract",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"interface\s*[-_ ]?contract(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
    (
        "integration_checkpoint",
        "integration-checkpoint",
        re.compile(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
            r"integration\s*[-_ ]?checkpoint(?:\*\*)?\s*(?:\(|:|-|$)"
        ),
    ),
)


def validate_brief(brief: str) -> dict[str, Any]:
    """Return MECE validation for a handoff brief.

    A valid peer-handoff packet names all four ownership elements: owns,
    does-not-own, interface-contract, and integration-checkpoint. The validator
    accepts markdown headings, bold bullet labels, or explicit colon fields.
    """
    text = brief or ""
    present: list[str] = []
    missing: list[str] = []
    for key, label, pattern in MECE_FIELDS:
        if pattern.search(text):
            present.append(key)
        else:
            missing.append(label)

    warnings: list[str] = []
    if not text.strip():
        warnings.append("brief is empty")

    return {
        "valid": not missing,
        "missing": missing,
        "warnings": warnings,
        "present": present,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brief-file", required=True, help="Path to the handoff brief to lint")
    p.add_argument("--json", action="store_true", help="Accepted for explicitness; output is always JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.brief_file).expanduser()
    try:
        brief = path.read_text(encoding="utf-8")
    except OSError as exc:
        result = {
            "valid": False,
            "missing": [label for _key, label, _pattern in MECE_FIELDS],
            "warnings": [f"could not read brief file: {exc}"],
            "present": [],
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    result = validate_brief(brief)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
