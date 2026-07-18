# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Config for the post-push retrospective auto-trigger.

The GLOBAL default lives HERE, in code — ``.build-loop/config.json`` is
gitignored, so there is no tracked default file to seed. A consumer repo
overrides by adding a ``retrospective`` block to its ``.build-loop/config.json``
(mirrors ``autonomy_gate.load_autonomy_config``: read, deep-merge over defaults,
fail-open to defaults on any malformed/missing file).

Default posture: **enabled** (the user asked "trigger automatically after any
push"). Cost is bounded by the scope classifier — trivial pushes only ever run
the free zero-LLM sweep. Per-repo opt-out, configurable thresholds, and a budget
guard keep it from ever becoming an unbounded Fable trigger.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

# Risk-surface globs: any changed file matching one of these forces the
# SUBSTANTIAL tier regardless of commit count (auth / privacy / egress /
# security-hook / schema — the classes where a missed retro is most costly).
# Kept deliberately TIGHT (auditor f6): broad substrings like *token* / *network*
# escalated routine files (tokenizer.py, network_utils.py) to the expensive tier,
# fighting the "scope-gating IS the cost control" north star. Widen via config.
DEFAULT_RISK_SURFACE_GLOBS = [
    "*auth*", "*login*", "*oauth*", "*secret*", "*credential*",
    "*privacy*", "*egress*", "*security*",
    "*migration*", "*schema*", "*.sql", "hooks/**", "**/hooks/**",
]

# Docs/config-only globs: a delta whose every changed file matches one of these
# (and hits no risk-surface glob, and emits no recommendations) is TRIVIAL.
DEFAULT_DOCS_CONFIG_GLOBS = [
    "*.md", "*.mdx", "*.txt", "*.rst",
    "*.json", "*.yaml", "*.yml", "*.toml", "*.ini", "*.cfg",
    "docs/**", "**/docs/**", "CHANGELOG*", "LICENSE*", "NOTICE*",
]

# Defaults cite the Fable scope guidance: the full 3-5x pipeline is worth its
# cost only for SUBSTANTIAL work (>=2 repos OR ~10-15 commits OR a risk-surface
# change OR P0 recs OR a >=2-instance failure cluster); wasteful for trivial
# pushes. Thresholds are config-overridable.
DEFAULTS: dict[str, Any] = {
    "autoAfterPush": True,          # global default: ENABLED
    "optOut": False,               # per-repo kill switch
    "thresholds": {
        "substantial_commits": 10,  # >= this many commits => substantial
        "substantial_repos": 2,     # >= this many distinct repos => substantial
        "first_run_cap": 50,        # no checkpoint: bound coverage to last N commits
        "upgrade_stale_hours": 24,  # a queued Fable upgrade older than this escalates to fallback
        "risk_surface_globs": DEFAULT_RISK_SURFACE_GLOBS,
        "docs_config_globs": DEFAULT_DOCS_CONFIG_GLOBS,
    },
    "budget": {
        "maxTokens": None,          # None => no budget cap (guard never trips)
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto a copy of ``base`` (dicts only)."""
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load(workdir: Path | str) -> dict[str, Any]:
    """Return the effective ``retrospective`` config (defaults deep-merged with
    the repo's ``.build-loop/config.json`` override). Fail-open: a missing or
    malformed config yields the code defaults."""
    cfg_path = Path(workdir) / ".build-loop" / "config.json"
    if not cfg_path.exists():
        return json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(DEFAULTS))
    if not isinstance(raw, dict):
        return json.loads(json.dumps(DEFAULTS))
    override = raw.get("retrospective")
    if not isinstance(override, dict):
        return json.loads(json.dumps(DEFAULTS))
    return _deep_merge(DEFAULTS, override)


def is_enabled(cfg: dict[str, Any]) -> bool:
    """True when the trigger should run for this repo (enabled AND not opted out)."""
    return bool(cfg.get("autoAfterPush", True)) and not bool(cfg.get("optOut", False))


def thresholds(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("thresholds", DEFAULTS["thresholds"])


def budget_guard_exceeded(
    workdir: Path | str,
    cfg: dict[str, Any],
    *,
    budget_fn: Callable[[Path, dict], bool] | None = None,
) -> bool:
    """True when a token budget is exceeded => skip the Fable upgrade, route to
    fallback (the deterministic capture still runs). Injectable ``budget_fn`` for
    tests. Default consults ``budget_check`` if importable; ANY error => False so
    we NEVER spuriously skip a retro because a budget probe broke."""
    if budget_fn is not None:
        try:
            return bool(budget_fn(Path(workdir), cfg))
        except Exception:
            return False
    max_tokens = (cfg.get("budget") or {}).get("maxTokens")
    if not max_tokens:
        return False
    try:
        scripts_dir = str(Path(__file__).resolve().parent.parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import budget_check  # type: ignore

        fn = getattr(budget_check, "tokens_used", None)
        if callable(fn):
            used = fn(Path(workdir))
            return isinstance(used, (int, float)) and used >= max_tokens
    except Exception:
        return False
    return False
