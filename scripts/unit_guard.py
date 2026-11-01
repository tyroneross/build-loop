#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""unit_guard.py — make a wrong unit hard to write and easy to catch.

WHY
---
Observed 2026-11-01: a benchmark facts file stored peak memory under a key
named ``mb``. Most values were MiB (3614), but one pair came from macOS
``/usr/bin/time -l``, whose "maximum resident set size" is in BYTES
(4962435072). GNU time on Linux reports the same quantity in KILOBYTES. The
percentages derived from the pairs were right; the published raw values were
not. Naming conventions written in prose did not stop it.

This module has three parts:

* ``Quantity`` — a value bound to a unit. Converting between units of one
  dimension is exact; converting across dimensions raises ``UnitError``.
* ``parse_time_l`` — reads ``/usr/bin/time -l`` (macOS) and ``-v`` (GNU)
  output and returns keys that carry their unit (``max_rss_bytes``,
  ``real_s``). An unrecognised format raises; it never guesses.
* ``check`` — walks a JSON document and flags every number whose key does not
  name a unit, whose magnitude is implausible for its unit, or whose siblings
  differ by more than 1000x (the signature of a unit mix).

CLI
---
::

    unit_guard.py check <file.json> [--allow key,...] [--json]

Exit 0 when clean, 1 on any error finding, 2 when the file cannot be read.
Warnings (decimal KB/MB/GB names, which memory tools rarely mean) print but
do not fail the check.

Key rule: a numeric leaf's key (for arrays, the nearest named ancestor) must
end in a unit suffix such as ``_mib``, ``_s``, ``_pct``, ``_count``, or be a
bare unit name (``s``, ``pct``, ``n``). A dict holding a string ``unit`` field
supplies the unit for its ``value``/``values`` fields and for the ``value``
of each entry in its ``records`` list.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from typing import Any

# --- Units ------------------------------------------------------------------
# canonical unit -> (dimension, factor to the dimension's base unit)
# Base units: data=byte, time=second, fraction=ratio (1.0 == 100%).
UNITS: dict[str, tuple[str, float]] = {
    "bytes": ("data", 1.0),
    "kib": ("data", 1024.0),
    "mib": ("data", 1024.0 ** 2),
    "gib": ("data", 1024.0 ** 3),
    "tib": ("data", 1024.0 ** 4),
    "kb": ("data", 1e3),
    "mb": ("data", 1e6),
    "gb": ("data", 1e9),
    "tb": ("data", 1e12),
    "ns": ("time", 1e-9),
    "us": ("time", 1e-6),
    "ms": ("time", 1e-3),
    "s": ("time", 1.0),
    "min": ("time", 60.0),
    "h": ("time", 3600.0),
    "pct": ("fraction", 0.01),
    "ratio": ("fraction", 1.0),
    "count": ("count", 1.0),
    "n": ("count", 1.0),
    "tokens": ("count", 1.0),
    "px": ("length_px", 1.0),
    "usd": ("money_usd", 1.0),
}

# Spellings seen in the wild -> canonical unit. Lookup is case-insensitive,
# so "MiB" and "mib" agree; decimal "MB" and binary "MiB" stay distinct.
ALIASES: dict[str, str] = {
    "b": "bytes", "byte": "bytes", "bytes": "bytes",
    "kib": "kib", "mib": "mib", "gib": "gib", "tib": "tib",
    "kb": "kb", "mb": "mb", "gb": "gb", "tb": "tb",
    "ns": "ns", "us": "us", "µs": "us", "ms": "ms",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "h": "h", "hr": "h", "hour": "h", "hours": "h",
    "%": "pct", "pct": "pct", "percent": "pct",
    "ratio": "ratio", "x": "ratio",
    "count": "count", "n": "count", "tokens": "tokens",
    "px": "px", "usd": "usd", "$": "usd",
}

# Unitless ordinals: positions, not quantities. Accepted as bare keys.
ORDINAL_KEYS = frozenset({"index", "idx", "rank", "stage", "step", "level", "version", "seed"})

# Dimensions where a 1000x spread among siblings signals a unit mix. Counts,
# percentages, pixels and money legitimately span orders of magnitude.
MIX_DIMENSIONS = frozenset({"data", "time"})
MIX_RATIO = 1000.0
DECIMAL_DATA = frozenset({"kb", "mb", "gb", "tb"})

MAX_DATA_BYTES = 1e7 * 1024.0 ** 2   # 1e7 MiB (~10 TiB)
MAX_TIME_S = 1e7                     # ~116 days
PCT_RANGE = (-1000.0, 1000.0)
MAX_PX = 1e6


class UnitError(ValueError):
    """Raised for unknown units or conversion across dimensions."""


def canonical_unit(unit: str) -> str:
    key = unit.strip().lower()
    if key not in ALIASES:
        raise UnitError(f"unknown unit {unit!r}")
    return ALIASES[key]


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit", canonical_unit(self.unit))

    @property
    def dimension(self) -> str:
        return UNITS[self.unit][0]

    def to(self, unit: str) -> "Quantity":
        target = canonical_unit(unit)
        dim, factor = UNITS[self.unit]
        tdim, tfactor = UNITS[target]
        if dim != tdim:
            raise UnitError(f"cannot convert {self.unit} ({dim}) to {target} ({tdim})")
        return Quantity(self.value * factor / tfactor, target)

    def __str__(self) -> str:
        return f"{self.value:g} {self.unit}"


def unit_for_key(key: str) -> str | None:
    """Canonical unit named by a key's suffix or bare name, else None."""
    k = key.strip().lower()
    if k in ALIASES and k not in {"b", "x", "$", "%"}:
        return ALIASES[k]
    for suffix in sorted(UNITS, key=len, reverse=True):
        if k.endswith("_" + suffix):
            return suffix
    return None


# --- /usr/bin/time parsing --------------------------------------------------
_BSD_TIMES = re.compile(r"^\s*([\d.]+)\s+real\s+([\d.]+)\s+user\s+([\d.]+)\s+sys\s*$", re.M)
_BSD_RSS = re.compile(r"^\s*(\d+)\s+maximum resident set size\s*$", re.M)
_BSD_FOOTPRINT = re.compile(r"^\s*(\d+)\s+peak memory footprint\s*$", re.M)
_GNU_RSS = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")
_GNU_ELAPSED = re.compile(r"Elapsed \(wall clock\) time \([^)]*\):\s*([\d:.]+)")
_GNU_USER = re.compile(r"User time \(seconds\):\s*([\d.]+)")
_GNU_SYS = re.compile(r"System time \(seconds\):\s*([\d.]+)")


def _clock_to_s(text: str) -> float:
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def parse_time_l(text: str, platform: str | None = None) -> dict[str, Any]:
    """Parse ``/usr/bin/time -l`` (BSD/macOS) or ``-v`` (GNU) output.

    The format is detected from the text. BSD-style output does not state its
    RSS unit: macOS reports bytes, FreeBSD kilobytes. macOS output is
    recognised by its ``peak memory footprint`` line; without that line the
    caller must pass ``platform="darwin"`` or ``"freebsd"``, otherwise this
    raises rather than guessing.
    """
    gnu_rss = _GNU_RSS.search(text)
    if gnu_rss:
        out: dict[str, Any] = {"format": "gnu-verbose",
                               "max_rss_bytes": int(gnu_rss.group(1)) * 1024}
        if m := _GNU_ELAPSED.search(text):
            out["real_s"] = _clock_to_s(m.group(1))
        if m := _GNU_USER.search(text):
            out["user_s"] = float(m.group(1))
        if m := _GNU_SYS.search(text):
            out["sys_s"] = float(m.group(1))
        return out

    bsd_rss = _BSD_RSS.search(text)
    if bsd_rss:
        footprint = _BSD_FOOTPRINT.search(text)
        plat = (platform or "").lower()
        if footprint or plat == "darwin":
            fmt, rss_factor = "bsd-darwin", 1
        elif plat == "freebsd":
            fmt, rss_factor = "bsd-freebsd", 1024
        else:
            raise UnitError("BSD time output without 'peak memory footprint': RSS unit is bytes on "
                            "macOS but kilobytes on FreeBSD; pass platform='darwin' or 'freebsd'")
        out = {"format": fmt, "max_rss_bytes": int(bsd_rss.group(1)) * rss_factor}
        if footprint:
            out["peak_footprint_bytes"] = int(footprint.group(1))
        if m := _BSD_TIMES.search(text):
            out["real_s"], out["user_s"], out["sys_s"] = (float(g) for g in m.groups())
        return out

    raise UnitError("unrecognised time output: expected '/usr/bin/time -l' (BSD/macOS) "
                    "or '/usr/bin/time -v' (GNU)")


# --- JSON checking ----------------------------------------------------------
def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finding(kind: str, path: str, key: str | None, message: str, value: Any = None,
             severity: str = "error") -> dict:
    return {"kind": kind, "severity": severity, "path": path, "key": key,
            "message": message, "value": value}


def _plausibility(value: float, unit: str) -> str | None:
    if isinstance(value, float) and not math.isfinite(value):
        return "value is not finite"
    dim, factor = UNITS[unit]
    if dim == "data":
        if value < 0:
            return "negative data size"
        if unit == "bytes" and value != int(value):
            return "byte count is not an integer"
        if value * factor > MAX_DATA_BYTES:
            return (f"{value:g} {unit} is over {MAX_DATA_BYTES / factor:g} {unit}; "
                    "likely bytes or KB stored under a larger unit")
    elif dim == "time":
        if value < 0:
            return "negative duration"
        if value * factor > MAX_TIME_S:
            return f"{value:g} {unit} is over {MAX_TIME_S / factor:g} {unit}"
    elif unit == "pct":
        if not PCT_RANGE[0] <= value <= PCT_RANGE[1]:
            return f"{value:g}% is outside [{PCT_RANGE[0]:g}, {PCT_RANGE[1]:g}]"
    elif dim == "count":
        if value < 0 or value != int(value):
            return "count is negative or not a whole number"
    elif unit == "px" and abs(value) > MAX_PX:
        return f"{value:g} px exceeds {MAX_PX:g}"
    return None


def check(doc: Any, allow: set[str] | frozenset[str] = frozenset()) -> dict:
    """Return {"findings": [...], "checked": n} for a parsed JSON document.

    A key without a unit inherits the unit of its nearest ancestor key that
    names one (``pipeline_time_s: {"before": 15.07}`` is seconds). An
    allow-listed key exempts itself and everything below it.
    """
    findings: list[dict] = []
    groups: dict[str, list[tuple[str, float, str | None]]] = {}
    missing: dict[str, dict] = {}
    warned: dict[str | None, dict] = {}
    checked = 0

    def warn_decimal(unit: str, path: str, key: str | None, value: Any) -> None:
        if unit not in DECIMAL_DATA:
            return
        if key in warned:  # one warning per key name; count the rest
            warned[key]["occurrences"] += 1
            return
        warned[key] = _finding(
            "decimal-unit", path, key,
            f"decimal {unit.upper()} (10^n bytes); memory and file tools usually report binary "
            f"units, so confirm the source or rename to _{unit[0]}ib", value, severity="warn")
        warned[key]["occurrences"] = 1
        findings.append(warned[key])

    def leaf(value: float, path: str, key: str | None, unit: str | None, group: str,
             allowed: bool) -> None:
        nonlocal checked
        checked += 1
        if allowed:
            return
        if unit is None:
            shape = re.sub(r"\[\d+\]", "[]", path)
            if shape in missing:
                missing[shape]["occurrences"] += 1
            else:
                missing[shape] = _finding("missing-unit", shape, key,
                                          f"key {key!r} names no unit; add a suffix such as _mib, _s, "
                                          "_pct, _count or allow-list it", value)
                missing[shape]["occurrences"] = 1
                findings.append(missing[shape])
        else:
            problem = _plausibility(value, unit)
            if problem:
                findings.append(_finding("implausible", path, key, problem, value))
        if unit is None or UNITS[unit][0] in MIX_DIMENSIONS:
            groups.setdefault(group, []).append((path, value, key))

    def walk(node: Any, path: str, key: str | None, unit: str | None, group: str,
             allowed: bool) -> None:
        if _is_number(node):
            leaf(node, path, key, unit, group, allowed)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]", key, unit, group, allowed)
        elif isinstance(node, dict):
            declared = node.get("unit") if isinstance(node.get("unit"), str) else None
            declared_unit = None
            if declared is not None and not allowed:
                try:
                    declared_unit = canonical_unit(declared)
                    warn_decimal(declared_unit, f"{path}.unit" if path else "unit", "unit", declared)
                except UnitError:
                    findings.append(_finding("unknown-unit", f"{path}.unit" if path else "unit", "unit",
                                             f"unit {declared!r} is not a known unit", declared))
            unit_group = f"{path or '$'}<unit>"
            for k, v in node.items():
                child = f"{path}.{k}" if path else k
                child_allowed = allowed or k in allow
                if declared is not None and k in ("value", "values"):
                    if declared_unit is not None or allowed:
                        walk(v, child, k, declared_unit, unit_group, child_allowed)
                elif declared is not None and k == "records" and isinstance(v, list):
                    rgroup = f"{child}<unit>"
                    for i, rec in enumerate(v):
                        rpath = f"{child}[{i}]"
                        if not isinstance(rec, dict):
                            walk(rec, rpath, k, None, _key_group(k), child_allowed)
                            continue
                        for rk, rv in rec.items():
                            rallowed = child_allowed or rk in allow
                            if rk == "value":
                                if declared_unit is not None or rallowed:
                                    walk(rv, f"{rpath}.value", rk, declared_unit, rgroup, rallowed)
                            else:
                                ru = _key_unit(rk)
                                walk(rv, f"{rpath}.{rk}", rk, ru, _key_group(rk), rallowed)
                else:
                    own = _key_unit(k)
                    if own is not None:
                        if not child_allowed:
                            warn_decimal(own, child, k, None)
                        walk(v, child, k, own, _key_group(k), child_allowed)
                    else:
                        # Inherit the nearest ancestor's unit and mix group.
                        walk(v, child, k, unit, group if unit else _key_group(k), child_allowed)

    walk(doc, "", None, None, "$root", False)

    for group, items in groups.items():
        nonzero = [(p, abs(v), k) for p, v, k in items if v != 0 and math.isfinite(v)]
        if len(nonzero) < 2:
            continue
        lo = min(nonzero, key=lambda t: t[1])
        hi = max(nonzero, key=lambda t: t[1])
        if hi[1] / lo[1] > MIX_RATIO:
            findings.append(_finding(
                "mixed-magnitude", hi[0], hi[2],
                f"values sharing key {group.split(':', 1)[-1]!r} span {hi[1] / lo[1]:.3g}x "
                f"({lo[0]}={lo[1]:g} vs {hi[0]}={hi[1]:g}); likely a unit mix",
                hi[1]))
    return {"findings": findings, "checked": checked}


def _key_unit(key: str) -> str | None:
    if key.strip().lower() in ORDINAL_KEYS:
        return "count"
    return unit_for_key(key)


def _key_group(key: str) -> str:
    return f"key:{key}"


def check_file(path: str, allow: set[str] | frozenset[str] = frozenset()) -> dict:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    result = check(doc, allow)
    result["file"] = path
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check units on every number in a JSON file.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="Check a facts/benchmark JSON file")
    p_check.add_argument("file")
    p_check.add_argument("--allow", default="", help="Comma-separated keys exempt from unit rules")
    p_check.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    allow = {k.strip() for k in args.allow.split(",") if k.strip()}
    try:
        result = check_file(args.file, allow)
    except (OSError, ValueError) as exc:
        print(f"unit-guard: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2

    findings = result["findings"]
    errors = [f for f in findings if f["severity"] == "error"]
    if args.json:
        print(json.dumps({**result, "errors": len(errors),
                          "warnings": len(findings) - len(errors)}, indent=2))
    else:
        for f in findings:
            extra = f" ({f['occurrences']} occurrences)" if f.get("occurrences", 1) > 1 else ""
            print(f"{args.file}:{f['path']}: {f['severity']}: {f['kind']}: {f['message']}{extra}")
        print(f"unit-guard: {len(errors)} error(s), {len(findings) - len(errors)} warning(s) "
              f"across {result['checked']} number(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
