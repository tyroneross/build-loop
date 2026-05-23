#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Codex preflight checker for the build-loop plugin.

Verifies that the source repo is correctly configured for Codex installs:
  1. .codex-plugin/plugin.json exists and is valid JSON.
  2. Its name and version match .claude-plugin/plugin.json.
  3. package.json files[] includes .codex-plugin, .agents, and AGENTS.md.
  4. .agents/plugins/marketplace.json exists and its name matches .codex-plugin/plugin.json name.
  5. The .mcp.json server path resolves (args/command with ${CLAUDE_PLUGIN_ROOT} expanded).
  6. (Soft) Codex installed cache sync via check_cache_sync.py — warning only, never causes exit 1.

CLI:
    python3 scripts/codex_preflight.py [--source <path>] [--json]

Exit codes:
    0 — all hard checks pass (1-5)
    1 — any hard check fails
    2 — preflight runner error (malformed JSON in input files)

Zero deps, Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def load_json(path: Path, label: str) -> dict | None:
    """Load a JSON file. Returns None on missing; raises SystemExit(2) on invalid JSON."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {label} contains invalid JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def expand_plugin_root(value: str, source: Path) -> str:
    return value.replace("${CLAUDE_PLUGIN_ROOT}", str(source))


def check_mcp_server_path(source: Path) -> tuple[bool, str]:
    """Check that the .mcp.json server command/args path resolves."""
    mcp_path = source / ".mcp.json"
    data = load_json(mcp_path, ".mcp.json")
    if data is None:
        return False, ".mcp.json not found"

    servers = data.get("mcpServers", {})
    if not servers:
        return False, ".mcp.json has no mcpServers entries"

    resolved_paths: list[str] = []
    for server_name, server_cfg in servers.items():
        # Check args list first, then command
        args = server_cfg.get("args", [])
        command = server_cfg.get("command", "")

        # Find every path-like argument (ends with .js, .py, .sh, .ts, etc.)
        candidates: list[str] = []
        for arg in args:
            expanded = expand_plugin_root(arg, source)
            if "/" in expanded or expanded.endswith((".js", ".py", ".sh", ".ts", ".mjs")):
                candidates.append(expanded)
        if not candidates and command:
            candidates.append(expand_plugin_root(command, source))

        # Validate every candidate. Fail fast on the first missing path; only
        # advance to the next server when ALL candidates of this server resolve.
        for candidate in candidates:
            p = Path(candidate)
            if p.is_absolute() or p.parts[0] not in (".", ".."):
                if not p.exists():
                    return False, f"server '{server_name}' path does not exist: {candidate}"
                resolved_paths.append(f"{server_name} -> {candidate}")
            else:
                resolved = (source / candidate).resolve()
                if not resolved.exists():
                    return False, f"server '{server_name}' path does not exist: {resolved}"
                resolved_paths.append(f"{server_name} -> {resolved}")

    if resolved_paths:
        # First entry is the most informative for human-readable output;
        # multi-server installs see the full list collapsed onto one line.
        head = resolved_paths[0]
        if len(resolved_paths) > 1:
            return True, f"{len(resolved_paths)} paths resolve (first: {head})"
        return True, f"server '{head}' resolves"
    return True, "no path-bearing args found in .mcp.json (skipped path check)"


def run_cache_sync(source: Path) -> tuple[bool, str]:
    """Run check_cache_sync.py --host codex as a soft check. Never hard-fails."""
    script = source / "scripts" / "check_cache_sync.py"
    if not script.exists():
        return True, "check_cache_sync.py not found — skipped"

    try:
        result = subprocess.run(
            [sys.executable, str(script), "--host", "codex", "--source", str(source)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return True, f"cache sync warning (soft): {stderr or 'exit {}'.format(result.returncode)}"
        return True, "cache in sync"
    except subprocess.TimeoutExpired:
        return True, "cache sync check timed out — skipped"
    except OSError as exc:
        return True, f"could not run check_cache_sync.py: {exc}"


def run_checks(source: Path) -> list[dict]:
    checks: list[dict] = []

    # Check 1: .codex-plugin/plugin.json exists and is valid JSON
    codex_manifest_path = source / ".codex-plugin" / "plugin.json"
    codex_manifest = load_json(codex_manifest_path, ".codex-plugin/plugin.json")
    if codex_manifest is None:
        checks.append({
            "id": 1,
            "name": ".codex-plugin/plugin.json exists and is valid JSON",
            "pass": False,
            "reason": ".codex-plugin/plugin.json not found",
            "hard": True,
        })
    else:
        checks.append({
            "id": 1,
            "name": ".codex-plugin/plugin.json exists and is valid JSON",
            "pass": True,
            "reason": "file exists and parses cleanly",
            "hard": True,
        })

    # Check 2: codex name/version match claude name/version
    claude_manifest_path = source / ".claude-plugin" / "plugin.json"
    claude_manifest = load_json(claude_manifest_path, ".claude-plugin/plugin.json")
    if codex_manifest is None or claude_manifest is None:
        checks.append({
            "id": 2,
            "name": "codex and claude plugin.json name+version match",
            "pass": False,
            "reason": "one or both manifests missing — cannot compare",
            "hard": True,
        })
    else:
        codex_name = codex_manifest.get("name")
        codex_ver = codex_manifest.get("version")
        claude_name = claude_manifest.get("name")
        claude_ver = claude_manifest.get("version")
        if codex_name == claude_name and codex_ver == claude_ver:
            checks.append({
                "id": 2,
                "name": "codex and claude plugin.json name+version match",
                "pass": True,
                "reason": f"both: name={codex_name}, version={codex_ver}",
                "hard": True,
            })
        else:
            checks.append({
                "id": 2,
                "name": "codex and claude plugin.json name+version match",
                "pass": False,
                "reason": (
                    f"mismatch — codex: name={codex_name}, version={codex_ver}; "
                    f"claude: name={claude_name}, version={claude_ver}"
                ),
                "hard": True,
            })

    # Check 3: package.json files[] includes .codex-plugin, .agents, AGENTS.md
    pkg_path = source / "package.json"
    pkg = load_json(pkg_path, "package.json")
    required_entries = {".codex-plugin", ".agents", "AGENTS.md"}
    if pkg is None:
        checks.append({
            "id": 3,
            "name": "package.json files[] includes .codex-plugin, .agents, AGENTS.md",
            "pass": False,
            "reason": "package.json not found",
            "hard": True,
        })
    else:
        files_list = set(pkg.get("files", []))
        missing = required_entries - files_list
        if not missing:
            checks.append({
                "id": 3,
                "name": "package.json files[] includes .codex-plugin, .agents, AGENTS.md",
                "pass": True,
                "reason": "all required entries present",
                "hard": True,
            })
        else:
            checks.append({
                "id": 3,
                "name": "package.json files[] includes .codex-plugin, .agents, AGENTS.md",
                "pass": False,
                "reason": f"missing from files[]: {sorted(missing)}",
                "hard": True,
            })

    # Check 4: .agents/plugins/marketplace.json exists and name matches codex plugin name
    marketplace_path = source / ".agents" / "plugins" / "marketplace.json"
    marketplace = load_json(marketplace_path, ".agents/plugins/marketplace.json")
    if marketplace is None:
        checks.append({
            "id": 4,
            "name": ".agents/plugins/marketplace.json exists and name matches codex plugin",
            "pass": False,
            "reason": ".agents/plugins/marketplace.json not found",
            "hard": True,
        })
    elif codex_manifest is None:
        checks.append({
            "id": 4,
            "name": ".agents/plugins/marketplace.json exists and name matches codex plugin",
            "pass": False,
            "reason": "cannot verify name — codex manifest missing",
            "hard": True,
        })
    else:
        market_name = marketplace.get("name")
        expected_name = codex_manifest.get("name")
        if market_name == expected_name:
            checks.append({
                "id": 4,
                "name": ".agents/plugins/marketplace.json exists and name matches codex plugin",
                "pass": True,
                "reason": f"name={market_name} matches codex manifest",
                "hard": True,
            })
        else:
            checks.append({
                "id": 4,
                "name": ".agents/plugins/marketplace.json exists and name matches codex plugin",
                "pass": False,
                "reason": f"name mismatch: marketplace.json={market_name}, codex manifest={expected_name}",
                "hard": True,
            })

    # Check 5: .mcp.json server path resolves
    mcp_pass, mcp_reason = check_mcp_server_path(source)
    checks.append({
        "id": 5,
        "name": ".mcp.json server path resolves",
        "pass": mcp_pass,
        "reason": mcp_reason,
        "hard": True,
    })

    # Check 6 (soft): Codex installed cache sync
    cache_pass, cache_reason = run_cache_sync(source)
    checks.append({
        "id": 6,
        "name": "Codex installed cache sync (soft warning only)",
        "pass": cache_pass,
        "reason": cache_reason,
        "hard": False,
    })

    return checks


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Codex preflight checker for build-loop plugin.")
    p.add_argument("--source", default=".", help="Plugin source repo root (default: cwd)")
    p.add_argument("--json", action="store_true", help="Emit JSON report instead of human-readable")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.source).resolve()

    checks = run_checks(source)

    hard_failures = [c for c in checks if c["hard"] and not c["pass"]]
    soft_warnings = [c for c in checks if not c["hard"] and not c["pass"]]
    all_hard_pass = len(hard_failures) == 0

    if args.json:
        print(json.dumps({"checks": checks, "ok": all_hard_pass}, indent=2))
    else:
        print(f"build-loop codex preflight — source: {source}")
        print()
        for c in checks:
            prefix = "PASS" if c["pass"] else ("WARN" if not c["hard"] else "FAIL")
            print(f"  [{prefix}] {c['id']}. {c['name']}")
            print(f"        {c['reason']}")
        print()
        if all_hard_pass:
            if soft_warnings:
                print(f"preflight OK (all {sum(1 for c in checks if c['hard'])} hard checks pass, {len(soft_warnings)} soft warning(s))")
            else:
                print(f"preflight OK — all checks pass")
        else:
            print(f"preflight FAILED — {len(hard_failures)} hard check(s) failed")

        # Print soft warnings explicitly
        for w in soft_warnings:
            print(f"warning: {w['reason']}")

    return 0 if all_hard_pass else 1


if __name__ == "__main__":
    sys.exit(main())
