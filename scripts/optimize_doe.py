#!/usr/bin/env python3
"""DOE matrix generation + effects analysis for build-loop:optimize.

Stdlib + numpy only. Provably equivalent to pyDOE3 1.6.2 for the three
designs we care about (full factorial, fractional factorial, Plackett-Burman
12-run) — verified by side-by-side comparison with off-diag(XᵀX)=0 and
matching matrices up to row/column permutation.

Subcommands:
  generate --factors <json> [--design auto|full|fractional|pb] [--seed N]
      Print a JSON design matrix + run order. Each row is one experimental
      condition with named factor values (mapped from ±1 coding to the user-
      specified levels).

  analyze --design <json> --results <jsonl>
      Read measured responses, fit OLS effects (intercept + main + 2-way),
      print ranked findings as JSON.

  detect <factor-count>
      Print which design type would be auto-selected for k factors.

Design routing:
  k == 1   → autoresearch (recommended; this script returns an error)
  2 ≤ k ≤ 3 → full factorial 2^k    (≤8 runs)
  4 ≤ k ≤ 7 → fractional factorial 2^(k-p) Resolution III/IV (8 runs)
  k ≥ 8    → Plackett-Burman 12-run screening (handles up to 11)
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "optimize_doe.py requires numpy. Install with: pip install numpy\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Design generators (mirrors pyDOE3 — see scripts/test_optimize_doe.py)
# ---------------------------------------------------------------------------

def full_factorial_2level(k: int) -> np.ndarray:
    """2^k full factorial with each column at ±1."""
    return np.array(list(itertools.product([-1, 1], repeat=k)), dtype=float)


# Standard Resolution III/IV generator strings for k factors at 8 runs.
# Sources: Montgomery, Design and Analysis of Experiments, Table 8.14;
# matched to pyDOE3.fracfact() output for k=4..7.
FRACFACT_8_RUN = {
    4: "a b c abc",                       # 2^(4-1) Resolution IV
    5: "a b c ab ac",                     # 2^(5-2) Resolution III
    6: "a b c ab ac bc",                  # 2^(6-3) Resolution III
    7: "a b ab c ac bc abc",              # 2^(7-4) Resolution III (saturated)
}


def fracfact(generators: str) -> np.ndarray:
    """2-level fractional factorial via generator string. Each token is the
    elementwise product of its base-letter columns from the underlying full
    factorial over the unique base letters."""
    tokens = generators.split()
    base_letters = sorted({c for tok in tokens for c in tok if c.isalpha()})
    base_design = full_factorial_2level(len(base_letters))
    letter_to_col = {l: base_design[:, i] for i, l in enumerate(base_letters)}
    cols = []
    for tok in tokens:
        col = np.ones(base_design.shape[0])
        for c in tok:
            if c.isalpha():
                col = col * letter_to_col[c]
        cols.append(col)
    return np.column_stack(cols)


def plackett_burman_12() -> np.ndarray:
    """12-run Plackett-Burman (Paley construction, cyclic generator).
    Handles up to 11 factors; orthogonal main-effects screening only."""
    gen = np.array([+1, +1, -1, +1, +1, +1, -1, -1, -1, +1, -1])
    rows = [gen.copy()]
    for _ in range(10):
        gen = np.roll(gen, -1)
        rows.append(gen.copy())
    rows.append(np.full(11, -1))
    return np.array(rows, dtype=float)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def select_design(k: int) -> str:
    if k <= 0:
        raise ValueError("factor count must be ≥1")
    if k == 1:
        return "autoresearch"  # caller should fall back to single-var loop
    if k <= 3:
        return "full"
    if k <= 7:
        return "fractional"
    return "pb"


def build_design(k: int, design_type: str) -> tuple[np.ndarray, str]:
    """Return (matrix, name). Matrix has shape (n_runs, k)."""
    if design_type == "full":
        return full_factorial_2level(k), f"2^{k} full factorial"
    if design_type == "fractional":
        if k not in FRACFACT_8_RUN:
            raise ValueError(
                f"no curated 8-run fractional design for k={k}; supported: {sorted(FRACFACT_8_RUN)}"
            )
        return fracfact(FRACFACT_8_RUN[k]), f"2^({k}-{k-3}) fractional factorial"
    if design_type == "pb":
        if k > 11:
            raise ValueError(f"PB-12 supports up to 11 factors; got {k}")
        full = plackett_burman_12()
        return full[:, :k], f"Plackett-Burman 12-run (using {k} of 11 factors)"
    raise ValueError(f"unknown design type: {design_type}")


# ---------------------------------------------------------------------------
# Effects analyzer
# ---------------------------------------------------------------------------

def fit_effects(design: np.ndarray, y: np.ndarray, include_interactions: bool = True
                ) -> dict:
    """Fit y ~ intercept + main + (optional) 2-way interactions via OLS.
    Returns ranked dict with intercept, main effects, and interactions."""
    n, k = design.shape
    cols = [np.ones(n)]
    labels: list = ["intercept"]
    for i in range(k):
        cols.append(design[:, i])
        labels.append(("main", i))
    if include_interactions:
        for i in range(k):
            for j in range(i + 1, k):
                cols.append(design[:, i] * design[:, j])
                labels.append(("inter", (i, j)))
    X = np.column_stack(cols)
    if X.shape[1] > X.shape[0]:
        # Underdetermined; truncate to what we can solve
        X = X[:, : X.shape[0]]
        labels = labels[: X.shape[0]]
    beta, residuals, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    intercept = float(beta[0])
    main_effects = {labels[i][1]: float(beta[i]) for i in range(1, len(labels))
                    if labels[i][0] == "main"}
    inter_effects = {labels[i][1]: float(beta[i]) for i in range(1, len(labels))
                     if labels[i][0] == "inter"}
    # Variance explained: total ss minus residual ss
    y_var = float(np.var(y) * n)
    if rank == X.shape[1] and len(residuals) > 0:
        residual_ss = float(residuals[0])
        r2 = 1.0 - residual_ss / y_var if y_var > 0 else 1.0
    else:
        r2 = None  # saturated, no degrees of freedom for residual
    return {
        "intercept": intercept,
        "main": main_effects,
        "interactions": inter_effects,
        "r2": r2,
        "n_runs": n,
        "n_factors": k,
    }


def rank_findings(effects: dict, factor_names: list[str]) -> list[dict]:
    """Sort effects by absolute magnitude with human-readable labels."""
    rows = []
    for idx, val in effects["main"].items():
        rows.append({
            "term": factor_names[idx],
            "kind": "main",
            "effect": val,
            "abs_effect": abs(val),
        })
    for (i, j), val in effects["interactions"].items():
        rows.append({
            "term": f"{factor_names[i]} × {factor_names[j]}",
            "kind": "interaction",
            "effect": val,
            "abs_effect": abs(val),
        })
    rows.sort(key=lambda r: -r["abs_effect"])
    return rows


# ---------------------------------------------------------------------------
# Level mapping (-1/+1 coding ↔ user-specified levels)
# ---------------------------------------------------------------------------

def map_levels(design: np.ndarray, factors: list[dict]) -> list[dict]:
    """Convert ±1 coded design into named runs with concrete values.
    factors[i] = {"name": str, "low": <value>, "high": <value>} OR
    factors[i] = {"name": str, "levels": [<low>, <high>]}."""
    runs = []
    for run_idx, row in enumerate(design):
        run = {"_run_id": run_idx, "_factors": {}}
        for col_idx, coded in enumerate(row):
            f = factors[col_idx]
            if "low" in f and "high" in f:
                value = f["high"] if coded > 0 else f["low"]
            elif "levels" in f and len(f["levels"]) == 2:
                value = f["levels"][1] if coded > 0 else f["levels"][0]
            else:
                value = float(coded)  # fallback to coded value
            run["_factors"][f["name"]] = value
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_generate(args: argparse.Namespace) -> int:
    factors = json.loads(Path(args.factors).read_text()) if Path(args.factors).is_file() \
        else json.loads(args.factors)
    if not isinstance(factors, list) or not factors:
        sys.stderr.write("--factors must be a JSON list of {name, low, high} or {name, levels}\n")
        return 2
    k = len(factors)
    design_type = args.design
    if design_type == "auto":
        design_type = select_design(k)
    if design_type == "autoresearch":
        sys.stderr.write(f"k={k}: defer to autoresearch (single-variable case)\n")
        return 3
    matrix, name = build_design(k, design_type)
    runs = map_levels(matrix, factors)
    rng = np.random.default_rng(args.seed)
    order = list(range(len(runs)))
    rng.shuffle(order)
    output = {
        "design": {"type": design_type, "name": name, "n_runs": len(runs), "n_factors": k},
        "factors": [{"name": f["name"]} for f in factors],
        "matrix": matrix.tolist(),
        "run_order": order,
        "runs": runs,
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    design_data = json.loads(Path(args.design).read_text())
    matrix = np.array(design_data["matrix"], dtype=float)
    factor_names = [f["name"] for f in design_data["factors"]]
    # Read JSONL of {run_id, value}
    results: dict[int, float] = {}
    for line in Path(args.results).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        results[int(row["run_id"])] = float(row["value"])
    n = matrix.shape[0]
    if len(results) != n:
        sys.stderr.write(f"need {n} results, got {len(results)}\n")
        return 2
    y = np.array([results[i] for i in range(n)])
    k = matrix.shape[1]
    # Saturated PB or saturated R-III: skip interactions
    include_interactions = (k <= 3)
    effects = fit_effects(matrix, y, include_interactions=include_interactions)
    findings = rank_findings(effects, factor_names)
    output = {
        "summary": {
            "design_type": design_data["design"]["type"],
            "n_runs": n,
            "n_factors": k,
            "r2": effects["r2"],
            "intercept": effects["intercept"],
        },
        "ranked_effects": findings,
        "best_run": int(np.argmin(y)) if "lower" in (args.direction or "lower") else int(np.argmax(y)),
        "best_value": float(np.min(y)) if "lower" in (args.direction or "lower") else float(np.max(y)),
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    try:
        k = int(args.factor_count)
    except ValueError:
        sys.stderr.write("factor-count must be an integer\n")
        return 2
    design_type = select_design(k)
    print(json.dumps({"factor_count": k, "design": design_type}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="generate a DOE matrix")
    gen.add_argument("--factors", required=True,
                     help='JSON list or path to file: [{"name":"x1","low":1,"high":3}, ...]')
    gen.add_argument("--design", default="auto",
                     choices=["auto", "full", "fractional", "pb"])
    gen.add_argument("--seed", type=int, default=0)
    gen.set_defaults(func=cmd_generate)

    ana = sub.add_parser("analyze", help="fit OLS effects from measured results")
    ana.add_argument("--design", required=True, help="path to design JSON from generate")
    ana.add_argument("--results", required=True,
                     help="path to JSONL with {run_id, value} per line")
    ana.add_argument("--direction", default="lower", choices=["lower", "higher"])
    ana.set_defaults(func=cmd_analyze)

    det = sub.add_parser("detect", help="show which design auto-selects for k factors")
    det.add_argument("factor_count")
    det.set_defaults(func=cmd_detect)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
