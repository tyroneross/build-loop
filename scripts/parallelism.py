#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
parallelism.py — resource-aware subagent fan-out ceiling for build-loop.

Fan-out buys speed on work the plan already split; it must not raise total
tokens. Running N planned chunks in parallel costs the same work tokens as
running them in sequence, plus one worker setup (system prompt, tools, brief)
per extra worker. The token check therefore limits that setup overhead
(default 10% of the sequential total) instead of capping tokens in flight.
Measured per-worker usage drives the check; without a measurement it is
reported as unchecked. Local inference is CPU-led: larger local models reserve
more cores per worker. Every path honors the project cap, CPU headroom, and the
hard safety ceiling. An explicit ``cloudTokenBudget`` (or ``--token-budget``)
still caps tokens in flight, for provider rate limits.

Config schema (.build-loop/config.json):
    {
      "parallelism": {
        "maxImplementers": 8,
        "workerSetupTokens": 20000,
        "maxFanoutOverhead": 0.10,
        "cloudTokenBudget": null
      }
    }

CLI usage:
    python3 scripts/parallelism.py --workdir . [--requested N] [--describe] --json

    --describe  prints full diagnostic dict instead of bare integer
    --json      emit JSON (always set alongside --describe; omit for plain int)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

import model_taxonomy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HARD_CEILING: int = 20  # absolute admission ceiling (owner policy 2026-11-19); resource caps stay binding
DEFAULT_MAX: int = 8    # default project preference; never a dispatch target
# Waves wider than this need a validated work partition (disjoint owned files,
# an interface per chunk, and a named integration point) plus a fresh CPU sample.
SEGMENTATION_FREE_LIMIT: int = 4
# Per-worker fixed cost (system prompt, tool schemas, brief) that a single
# sequential worker pays once. TAG:ASSUMED until measured; override with
# parallelism.workerSetupTokens.
DEFAULT_WORKER_SETUP_TOKENS: int = 20_000
# Owner policy 2026-11-19: fan-out may add at most this share of the sequential
# token total. Override with parallelism.maxFanoutOverhead.
DEFAULT_MAX_FANOUT_OVERHEAD: float = 0.10

MODEL_SIZES = ("small", "medium", "large", "xlarge")
OUTPUT_SIZES = ("small", "medium", "large")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")
EXECUTION_LOCATIONS = ("auto", "cloud", "local")

# Raw-token demand per worker when no measured usage is available. These are
# intentionally rough T-shirt values, not provider pricing claims.
MODEL_TOKEN_HEURISTIC = {
    "small": 8_000,
    "medium": 16_000,
    "large": 24_000,
    "xlarge": 32_000,
}
OUTPUT_MULTIPLIER = {"small": 0.5, "medium": 1.0, "large": 1.75}
EFFORT_MULTIPLIER = {
    "low": 0.75,
    "medium": 1.0,
    "high": 1.25,
    "xhigh": 1.75,
    "max": 2.25,
    "ultra": 3.0,
}
LOCAL_CPU_PER_WORKER = {"small": 1, "medium": 2, "large": 4, "xlarge": 8}

_LOCAL_PROVIDER_MARKERS = ("ollama", "mlx", "lmstudio", "llama.cpp", "local")
_MODEL_SIZE_MARKERS = {
    "small": ("haiku", "luna", "pattern", "nano", "mini", "3b", "7b"),
    "medium": ("sonnet", "terra", "code", "14b", "32b"),
    "large": ("opus", "thinking", "70b"),
    "xlarge": ("fable", "mythos", "astra", "sol", "frontier", "ultra-frontier"),
}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _cpu_budget() -> int:
    """Leave 2 cores for the main loop + OS; floor at 1."""
    return max(1, (os.cpu_count() or 4) - 2)


def _parallelism_config(workdir: Path) -> dict:
    """Read the optional parallelism block; malformed input degrades to defaults."""
    try:
        cfg_path = workdir / ".build-loop" / "config.json"
        data = json.loads(cfg_path.read_text())
        block = data.get("parallelism") or {}
        return block if isinstance(block, dict) else {}
    except Exception:  # missing file, json error, type error
        return {}


def _positive_int(value, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _config_max(workdir: Path) -> int:
    """Read parallelism.maxImplementers; fail-soft to DEFAULT_MAX."""
    return _positive_int(_parallelism_config(workdir).get("maxImplementers"), DEFAULT_MAX)


def _config_token_budget(workdir: Path) -> int | None:
    """Optional in-flight token cap for provider rate limits; off by default."""
    value = _parallelism_config(workdir).get("cloudTokenBudget")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _config_overhead(workdir: Path) -> tuple[int, float]:
    config = _parallelism_config(workdir)
    setup = _positive_int(config.get("workerSetupTokens"), DEFAULT_WORKER_SETUP_TOKENS)
    ratio = config.get("maxFanoutOverhead")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 < ratio <= 1:
        ratio = DEFAULT_MAX_FANOUT_OVERHEAD
    return setup, float(ratio)


def token_neutral_cap(tokens_per_worker: int, setup_tokens: int, max_overhead: float) -> int | None:
    """Largest N whose extra setup tokens stay within ``max_overhead`` of sequential.

    Sequential total = N*work + setup; parallel total = N*(work + setup), so
    the extra cost is (N-1)*setup. Returns None when every N qualifies.
    """
    work = max(0, tokens_per_worker - setup_tokens)
    denominator = setup_tokens - max_overhead * work
    if denominator <= 0:
        return None
    return max(1, int(setup_tokens * (1 + max_overhead) // denominator))


def classify_execution_location(
    model: str | None = None,
    provider: str | None = None,
    explicit: str = "auto",
) -> str:
    """Resolve local vs cloud without treating open-weight model names as local."""
    if explicit in {"cloud", "local"}:
        return explicit
    joined = f"{provider or ''} {model or ''}".lower()
    return "local" if any(marker in joined for marker in _LOCAL_PROVIDER_MARKERS) else "cloud"


def classify_model_size(model: str | None = None, explicit: str | None = None) -> str:
    """Map a model to a conservative token/CPU T-shirt size."""
    if explicit in MODEL_SIZES:
        return str(explicit)
    lowered = (model or "").lower()
    # Larger classes win if a model id contains more than one marker.
    for size in reversed(MODEL_SIZES):
        if any(marker in lowered for marker in _MODEL_SIZE_MARKERS[size]):
            return size
    return "medium"


def estimate_tokens_per_worker(
    model_size: str = "medium",
    output_size: str = "medium",
    effort: str = "medium",
) -> int:
    """Return a rough raw-token demand when provider usage is unavailable."""
    size = model_size if model_size in MODEL_SIZES else "medium"
    output = output_size if output_size in OUTPUT_SIZES else "medium"
    effort_level = effort if effort in EFFORT_LEVELS else "medium"
    return max(
        1,
        int(MODEL_TOKEN_HEURISTIC[size] * OUTPUT_MULTIPLIER[output] * EFFORT_MULTIPLIER[effort_level]),
    )


def measured_tokens_per_worker(
    ledger_path: Path,
    *,
    model: str | None,
    agent: str | None = None,
) -> int | None:
    """Return median measured raw tokens for matching completed ledger rows."""
    if not model or not ledger_path.exists():
        return None
    samples: dict[str, int | None] = {}
    try:
        for index, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines()):
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            actual = row.get("actual_model") or row.get("model") or ""
            if str(actual).lower() != model.lower() or (agent and row.get("agent") != agent):
                continue
            if row.get("status") == "dispatched":
                continue
            # Latest terminal attempt wins; partial records cannot masquerade as
            # a complete worker measurement or crowd out other tasks.
            key = json.dumps([row.get("run_id"), row.get("task_id") or f"legacy-line-{index}"])
            buckets = (row.get("input_tokens"), row.get("output_tokens"),
                       row.get("cache_read_input_tokens", 0), row.get("cache_creation_input_tokens", 0))
            complete = all(type(value) is int and value >= 0 for value in buckets)
            samples[key] = sum(buckets) if complete and row.get("status") == "completed" else None
    except OSError:
        return None
    totals = [value for value in samples.values() if value is not None and value > 0]
    return int(statistics.median(totals[-20:])) if totals else None


def resolve_fanout(
    workdir: Path,
    requested: int | None = None,
    *,
    execution_location: str = "auto",
    provider: str | None = None,
    model: str | None = None,
    model_size: str | None = None,
    output_size: str = "medium",
    effort: str | None = None,
    segment: str | None = None,
    tier: str | None = None,
    token_budget: int | None = None,
    measured_tokens: int | None = None,
    agent: str | None = None,
    ledger_path: Path | None = None,
    independent_items: int | None = None,
    shared_capacity: int | None = None,
    active_elsewhere: int = 0,
) -> dict:
    """Resolve fan-out and expose every constraint that produced the answer."""
    workdir = Path(workdir)
    cpu_count = os.cpu_count() or 4
    cpu_budget = _cpu_budget()
    config_max = _config_max(workdir)
    candidate = requested if requested is not None and requested > 0 else config_max
    location = classify_execution_location(model, provider, execution_location)
    size = classify_model_size(model, model_size)
    output = output_size if output_size in OUTPUT_SIZES else "medium"
    preferred_effort = model_taxonomy.preferred_effort(segment, tier)
    if effort in EFFORT_LEVELS:
        effort_level = str(effort)
        effort_source = "explicit"
    elif preferred_effort in EFFORT_LEVELS:
        effort_level = str(preferred_effort)
        effort_source = "role-preferred"
    else:
        effort_level = "medium"
        effort_source = "fallback"

    cpu_per_worker = LOCAL_CPU_PER_WORKER[size] if location == "local" else 1
    cpu_cap = max(1, cpu_budget // cpu_per_worker)

    measured = measured_tokens if isinstance(measured_tokens, int) and measured_tokens > 0 else None
    if measured is None and ledger_path is not None:
        measured = measured_tokens_per_worker(Path(ledger_path), model=model, agent=agent)
    tokens_per_worker = measured or estimate_tokens_per_worker(size, output, effort_level)
    token_source = "measured" if measured is not None else "heuristic"

    # Tokens in flight are capped only when a budget is configured or passed
    # (provider rate limits); concurrency alone does not change total tokens.
    applied_token_budget = token_budget
    if applied_token_budget is None and location == "cloud":
        applied_token_budget = _config_token_budget(workdir)
    setup_tokens, max_overhead = _config_overhead(workdir)
    overhead_cap = token_neutral_cap(tokens_per_worker, setup_tokens, max_overhead) if measured is not None else None
    token_cap = (
        max(1, applied_token_budget // tokens_per_worker)
        if isinstance(applied_token_budget, int) and applied_token_budget > 0
        else None
    )

    caps = {
        "requested_or_config": candidate,
        "hard_ceiling": HARD_CEILING,
    }
    if location == "local":
        caps["cpu"] = cpu_cap
    if isinstance(independent_items, int) and independent_items >= 0:
        caps["independent_work"] = independent_items
    available_shared_capacity = None
    if isinstance(shared_capacity, int) and shared_capacity >= 0:
        available_shared_capacity = max(0, shared_capacity - max(0, active_elsewhere))
        caps["shared_capacity"] = available_shared_capacity
    if token_cap is not None:
        caps["token"] = token_cap
    if overhead_cap is not None:
        caps["token_overhead"] = overhead_cap
    effective = max(0, min(caps.values()))

    return {
        "execution_location": location,
        "primary_constraint": "cpu" if location == "local" else "token",
        "cpu_count": cpu_count,
        "cpu_budget": cpu_budget,
        "cpu_per_worker": cpu_per_worker,
        "cpu_cap": cpu_cap,
        "config_max": config_max,
        "requested": requested,
        "hard_ceiling": HARD_CEILING,
        "admission_policy": "adaptive_minimum",
        "independent_items": independent_items,
        "shared_capacity": shared_capacity,
        "active_elsewhere": max(0, active_elsewhere),
        "available_shared_capacity": available_shared_capacity,
        "model": model,
        "segment": segment,
        "tier": tier,
        "model_size": size,
        "output_size": output,
        "effort": effort_level,
        "effort_source": effort_source,
        "token_budget": applied_token_budget,
        "tokens_per_worker": tokens_per_worker,
        "token_estimate_source": token_source,
        "token_cap": token_cap,
        "worker_setup_tokens": setup_tokens,
        "max_fanout_overhead": max_overhead,
        "token_overhead_cap": overhead_cap,
        "token_overhead_check": "measured" if measured is not None else "unchecked_no_measurement",
        "limiting_factors": sorted(name for name, cap in caps.items() if cap == effective),
        "effective_max": effective,
    }


def effective_max_implementers(
    workdir: Path,
    requested: int | None = None,
    **resource_hints,
) -> int:
    """Return the effective parallelism ceiling for *workdir*.

    Resolution order:
        1. ``requested`` (caller-supplied override)
        2. ``parallelism.maxImplementers`` from ``.build-loop/config.json``
        3. ``DEFAULT_MAX`` (8)

    The result is resource-aware. Cloud calls are token-led; local calls are
    CPU-led. Existing callers remain valid and receive conservative cloud
    defaults when they provide no resource hints.
    """
    return resolve_fanout(workdir, requested, **resource_hints)["effective_max"]


def plan_batches(items: list, batch_size: int) -> list[list]:
    """Split *items* into consecutive batches of *batch_size*.

    ``batch_size < 1`` is treated as 1 (each item its own singleton batch).
    """
    if not items:
        return []
    size = max(1, batch_size)
    return [items[i : i + size] for i in range(0, len(items), size)]


_WILDCARDS = ("*", "?")  # brackets stay literal: app/[id]/page.tsx is a real path


def normalize_owned_path(value: str) -> str:
    """Normalize an owned path for overlap checks (case-folded on macOS)."""
    text = str(value).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    return text.casefold() if sys.platform == "darwin" else text


def _literal_prefix(path: str) -> str:
    cut = min((path.index(ch) for ch in _WILDCARDS if ch in path), default=len(path))
    return path[:cut]


def owned_paths_overlap(first: str, second: str) -> bool:
    """True when two owned paths or globs could name the same file.

    Conservative by design: a glob overlaps anything that shares its literal
    prefix before the first wildcard, so a false "overlap" costs one chunk merge
    while a missed overlap lets two workers race on one file. Literal paths
    overlap when equal or when one is a directory containing the other.
    """
    a, b = normalize_owned_path(first), normalize_owned_path(second)
    if not a or not b or a in {".", "*", "**"} or b in {".", "*", "**"}:
        return True
    if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
        return True
    if any(ch in a for ch in _WILDCARDS) or any(ch in b for ch in _WILDCARDS):
        pa, pb = _literal_prefix(a), _literal_prefix(b)
        return pa.startswith(pb) or pb.startswith(pa)
    return False


def partition_overlap(assignments: dict[str, list[str]]) -> dict[str, list[str]]:
    """Find files claimed by more than one parallel agent (a MECE violation).

    Parallel implementers must own DISJOINT file sets — two writers on the same
    file race on the working tree, and (without per-agent worktree isolation) on
    HEAD/index. `brief_mece_validator.py` checks a single brief *has* the seven
    ownership fields; this checks that the fields are *mutually exclusive* across
    the whole fan-out, BEFORE dispatch.

    *assignments* maps agent label -> list of owned paths/globs. Returns a map of
    each overlapping path -> the sorted list of agents that claimed it. Empty dict
    means the partition is clean (safe to fan out in a shared worktree). Comparison
    uses owned_paths_overlap (shared with autonomy_supervisor.validate_partition), so
    directory containment and glob prefixes count as overlaps.
    """
    owned = [
        (agent, sorted({str(raw).strip().strip("/") for raw in paths if str(raw).strip().strip("/")}))
        for agent, paths in assignments.items()
    ]
    claims: dict[str, set[str]] = {}
    for left in range(len(owned)):
        for right in range(left + 1, len(owned)):
            for first in owned[left][1]:
                for second in owned[right][1]:
                    if owned_paths_overlap(first, second):
                        claims.setdefault(first, set()).update({owned[left][0], owned[right][0]})
    return {path: sorted(agents) for path, agents in claims.items()}


def describe(workdir: Path, requested: int | None = None, **resource_hints) -> dict:
    """Return a diagnostic snapshot for reporting / --describe CLI."""
    return resolve_fanout(workdir, requested, **resource_hints)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Print the effective subagent parallelism ceiling for a build-loop workdir."
    )
    p.add_argument("--workdir", type=Path, default=Path("."), metavar="DIR")
    p.add_argument("--requested", type=int, default=None, metavar="N",
                   help="Caller-requested parallelism (overrides config when provided).")
    p.add_argument("--execution-location", choices=EXECUTION_LOCATIONS, default="auto")
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--model-size", choices=MODEL_SIZES, default=None)
    p.add_argument("--output-size", choices=OUTPUT_SIZES, default="medium")
    p.add_argument("--effort", choices=EFFORT_LEVELS, default=None)
    p.add_argument("--segment", choices=model_taxonomy.segments(), default=None)
    p.add_argument(
        "--tier",
        choices=tuple(model_taxonomy.legacy_aliases()) + model_taxonomy.tier_ladder(),
        default=None,
    )
    p.add_argument("--token-budget", type=int, default=None)
    p.add_argument("--measured-tokens-per-worker", type=int, default=None)
    p.add_argument("--agent", default=None)
    p.add_argument("--independent-items", type=int, default=None)
    p.add_argument("--shared-capacity", type=int, default=None)
    p.add_argument("--active-elsewhere", type=int, default=0)
    p.add_argument(
        "--ledger-path",
        type=Path,
        default=Path.home() / ".bookmark" / "cost-ledger.jsonl",
    )
    p.add_argument("--describe", action="store_true",
                   help="Emit full diagnostic dict instead of bare integer.")
    p.add_argument("--json", action="store_true",
                   help="Force JSON output (implied when --describe is set).")
    p.add_argument("--check-partition", metavar="JSON",
                   help=("Validate a parallel-dispatch partition is MECE. Arg is a path "
                         "(or '-' for stdin) to a JSON object {agent: [owned_paths]}. "
                         "Exit 0 if disjoint; exit 1 + overlap report if any file is "
                         "claimed by >1 agent."))
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    workdir = args.workdir.resolve()

    if args.check_partition:
        raw = sys.stdin.read() if args.check_partition == "-" else Path(args.check_partition).read_text(encoding="utf-8")
        assignments = json.loads(raw)
        overlaps = partition_overlap(assignments)
        print(json.dumps({"mece": not overlaps, "overlaps": overlaps}, indent=2))
        sys.exit(1 if overlaps else 0)

    hints = {
        "execution_location": args.execution_location,
        "provider": args.provider,
        "model": args.model,
        "model_size": args.model_size,
        "output_size": args.output_size,
        "effort": args.effort,
        "segment": args.segment,
        "tier": args.tier,
        "token_budget": args.token_budget,
        "measured_tokens": args.measured_tokens_per_worker,
        "agent": args.agent,
        "independent_items": args.independent_items,
        "shared_capacity": args.shared_capacity,
        "active_elsewhere": args.active_elsewhere,
        "ledger_path": args.ledger_path,
    }
    if args.describe:
        print(json.dumps(describe(workdir, requested=args.requested, **hints), indent=2))
    elif args.json:
        result = effective_max_implementers(workdir, requested=args.requested, **hints)
        print(json.dumps({"effective_max": result}))
    else:
        result = effective_max_implementers(workdir, requested=args.requested, **hints)
        print(result)


if __name__ == "__main__":
    main()
