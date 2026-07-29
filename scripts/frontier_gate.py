#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""frontier_gate.py — should THIS role on THIS repo route back to `fable` instead of the T1 default `opus`?

Opus 5 is the default at the frontier/T1 rung in `references/model-taxonomy.json`.
Fable is retained as the SECOND T1 entry because of one specific evidence gap,
and this gate is the ONLY sanctioned way to reach it.

WHY THE GAP EXISTS
------------------
`prompt-model-benchmark-lab/observations/2026-07-28-audit-bakeoff-fable-vs-opus5-vs-sonnet5.json`
shows Opus 5 was a strict SUPERSET of Fable across 3/3 adversarial-audit rounds
(same findings plus four neither other arm found). That evidence is
VERIFICATION-shaped: it grades auditing an artifact that already exists.

No Fable-vs-Opus-5 head-to-head exists on PLAN / SPEC AUTHORING. The only
spec-authoring bakeoff on file
(`observations/2026-07-09-spec-authoring-bakeoff-fable-vs-codex.json`) is
Fable vs Codex, and Fable won it. Authoring is a different task shape from
auditing: generating a decomposition is harder than evaluating one, and the
audit result does not transfer.

So the gate is asymmetric BY DESIGN:
  * VERIFICATION roles stay on opus unconditionally — the 2026-07-28 audit
    bakeoff positively covers them, and a superset result is not a coin flip.
  * PLANNING / AUTHORING roles may route back to fable, but only where planning
    is hardest: tightly-coupled codebases where a change ripples widely.

GATING PROPERTY: COUPLING DENSITY, NOT SIZE
-------------------------------------------
A 900-file repo of independent scripts plans easily. A 40-file repo where every
module reaches into every other does not. The signals below measure ripple, not
file count.

SIGNALS
-------
Each signal is independently `true` / `false` / `"unknown"`, with the source that
produced it recorded. No signal is ever self-reported model confidence — every
one is read off a file on disk.

  tight_integration   (deciding)
      repo slug appears in `config.frontierGate.tightIntegrationRepos`
      (seeded `["atomize-ai", "easy-terminal"]` — the user-named archetypes).
      `unknown` only when the repo slug cannot be determined at all.

  coupling_density    (deciding)
      Derived from an architecture graph, searched in order:
          .build-loop/architecture/graph.json
          .navgator/architecture/graph.json
      METRIC: mean (fan-in + fan-out) per component, counting INTERNAL edges
      only — an edge is internal when BOTH endpoints are FIRST-PARTY component
      nodes present in the graph's own node list. Package / service / LLM /
      infra endpoints are excluded (by `layer` and by id marker): depending on
      40 npm packages is not internal coupling and does not make a change
      ripple through your own modules.
          density = (2 * internal_edge_count) / internal_component_count
      Each internal edge contributes 1 to its source's fan-out and 1 to its
      target's fan-in, hence the factor of 2. The denominator is ALL internal
      components, including isolated ones — an isolated module is real evidence
      of loose coupling and must not be dropped from the mean.
      Scope: when `--touched` paths are supplied they are resolved to components
      via the graph's `file_map.json` and the mean is taken over just those
      components (their degree still counts edges to the whole graph). With no
      `--touched`, the mean is whole-repo.
      Fires `true` when density > `couplingDensityThreshold` (default 6.0).
      `unknown` when no graph file exists, the graph is unparseable, or it
      contains zero internal components. Calibration: build-loop itself measures
      ~1.07 (836 components, 449 internal edges) — a loosely-coupled script repo,
      comfortably `false`.

  synthesis_density   (corroborating)
      `.build-loop/state.json` synthesisDensity > 5. Accepts both the canonical
      Phase 1 Assess dict shape `{count, escalated, reason}` and a bare int,
      matching `judgment_gate.py`. Read from the latest `runs[]` record when
      present, else from the top level.

  risk_surface_change (corroborating)
      `.build-loop/state.json` `triggers.riskSurfaceChange` (latest run record
      first, then top level; a bare `riskSurfaceChange` key is also honored).

Deciding signals measure the gating property itself (coupling). Corroborating
signals measure how hard THIS run's planning is; they escalate but never decide
alone, because a high-stakes run in a loosely-coupled repo is exactly the case
the audit evidence says opus handles well.

VERDICT
-------
  1. Role is not in the planning allowlist          -> opus
  2. `config.frontierGate.enabled` is false          -> opus
  3. Any DECIDING signal is true                     -> fable
  4. A CORROBORATING signal is true AND
     coupling_density is `unknown`                   -> fable   (see below)
  5. Otherwise                                       -> opus

UNKNOWN HANDLING — FAIL TOWARD THE MORE APPROPRIATE MODEL
---------------------------------------------------------
Rule 4 is the fail-toward clause. "No architecture graph" is NOT evidence that a
repo is loosely coupled; it is the absence of evidence either way. When a
planning role is running against a repo whose coupling we cannot measure AND the
run itself is already flagged high-stakes (dense synthesis or a risk-surface
change), the gate resolves the uncertainty toward fable — the model with the only
positive spec-authoring evidence on file.

It deliberately does NOT route to fable on an unknown coupling signal alone. That
would invert the standing default ("opus everywhere except tightly-coupled
codebases") for every repo that has never been architecture-scanned, which is
most of them, and would make the gate meaningless. Absence of evidence escalates
only when something else already says this run is hard.

CONFIG SCHEMA (.build-loop/config.json)
---------------------------------------
    {
      "frontierGate": {
        "enabled": true,
        "tightIntegrationRepos": ["atomize-ai", "easy-terminal"],
        "couplingDensityThreshold": 6.0,
        "synthesisDensityThreshold": 5,
        "planningRoles": ["advisor", "plan-synthesis"]
      }
    }

Every key is optional and fail-soft: a missing file, unparseable JSON, or a
wrong-typed value degrades to the default for that key alone (the `_config`
pattern from `parallelism.py`). `planningRoles` may ADD roles but can never add
a verification role — the verification deny-list is hard-coded and wins, so a
config edit cannot route `independent-auditor` to fable.

CLI
---
    python3 scripts/frontier_gate.py --role advisor [--workdir .] [--repo SLUG]
                                     [--touched PATH ...] [--json | --plain]

    --json    emit the full envelope (DEFAULT)
    --plain   one-line human summary
    exit 0    ALWAYS — advisory routing hint, never blocks a build

ENVELOPE
--------
    {
      "verdict": "opus" | "fable",
      "role":    "<normalized role>",
      "fired":   ["<signal>", ...],
      "signals": {"<signal>": {"value": true|false|"unknown",
                               "source": "<where it came from>"}, ...},
      "reason":  "<one-line trace>"
    }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIER_DEFAULT_MODEL = "opus"
GATED_MODEL = "fable"

#: The audit evidence that justifies keeping verification on opus.
AUDIT_OBSERVATION = "2026-07-28-audit-bakeoff-fable-vs-opus5-vs-sonnet5.json"
#: The only spec-authoring evidence on file (Fable vs Codex — Fable won).
AUTHORING_OBSERVATION = "2026-07-09-spec-authoring-bakeoff-fable-vs-codex.json"

DEFAULT_TIGHT_INTEGRATION_REPOS: tuple[str, ...] = ("atomize-ai", "easy-terminal")
DEFAULT_COUPLING_THRESHOLD: float = 6.0
DEFAULT_SYNTHESIS_THRESHOLD: int = 5

#: Roles eligible to route back to fable. Authoring only — see module docstring.
DEFAULT_PLANNING_ROLES: tuple[str, ...] = ("advisor", "plan-synthesis")

#: Hard deny-list. These roles NEVER route to fable, regardless of signals or
#: config, because AUDIT_OBSERVATION positively covers verification: opus was a
#: strict superset of fable in 3/3 adversarial-audit rounds.
VERIFICATION_ROLES: frozenset[str] = frozenset(
    {
        "independent-auditor",
        "plan-critic",
        "security-reviewer",
        "scope-auditor",
        "fix-critique",
        "fact-checker",
        "overfitting-reviewer",
        "promotion-reviewer",
        "synthesis-critic",
        "alignment-checker",
    }
)

#: Spelling variants that mean "Phase 2 plan synthesis".
ROLE_ALIASES: dict[str, str] = {
    "plan_synthesis": "plan-synthesis",
    "phase2-plan-synthesis": "plan-synthesis",
    "phase-2-plan-synthesis": "plan-synthesis",
    "phase2_plan_synthesis": "plan-synthesis",
    "plan-synthesis": "plan-synthesis",
    "advisor": "advisor",
}

DECIDING_SIGNALS: tuple[str, ...] = ("tight_integration", "coupling_density")
CORROBORATING_SIGNALS: tuple[str, ...] = ("synthesis_density", "risk_surface_change")

UNKNOWN = "unknown"

ARCHITECTURE_DIRS: tuple[str, ...] = (
    ".build-loop/architecture",
    ".navgator/architecture",
)

#: Node layers that are third-party, not first-party code.
EXTERNAL_LAYERS: frozenset[str] = frozenset({"external", "package", "vendor", "third-party"})

#: Id substrings marking a non-first-party node, for graph producers that do not
#: set `layer` (belt-and-braces alongside EXTERNAL_LAYERS).
EXTERNAL_ID_MARKERS: tuple[str, ...] = ("_package_", "_llm_", "_service_", "_infra_")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_slug(raw: str | None) -> str | None:
    """Lowercase, collapse any non-alphanumeric run to a single '-', trim.

    "Atomize AI" / "atomize_ai" / "Atomize-AI" all resolve to "atomize-ai" so a
    config list written once matches however the directory happens to be named.
    Returns None when nothing usable survives.
    """
    if not raw:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", str(raw).strip().lower()).strip("-")
    return slug or None


def normalize_role(raw: str | None) -> str:
    """Canonicalize a role token; unrecognized roles pass through normalized."""
    slug = normalize_slug(raw)
    if slug is None:
        return ""
    return ROLE_ALIASES.get(slug, slug)


def repo_slug(workdir: Path, override: str | None = None) -> str | None:
    """Repo slug from --repo when given, else the workdir's directory name."""
    if override:
        return normalize_slug(override)
    name = workdir.resolve().name
    return normalize_slug(name)


# ---------------------------------------------------------------------------
# Config (fail-soft, per-key — mirrors parallelism.py::_config_max)
# ---------------------------------------------------------------------------

def _raw_config(workdir: Path) -> dict:
    """Read `.build-loop/config.json` frontierGate block; {} on any failure."""
    try:
        data = json.loads((workdir / ".build-loop" / "config.json").read_text(encoding="utf-8"))
        block = data["frontierGate"]
        return block if isinstance(block, dict) else {}
    except Exception:  # missing file, bad json, missing key, wrong type
        return {}


def load_config(workdir: Path) -> dict:
    """Resolve the frontierGate config with per-key fail-soft defaults."""
    raw = _raw_config(workdir)

    repos = raw.get("tightIntegrationRepos")
    if isinstance(repos, list):
        cleaned = [s for s in (normalize_slug(r) for r in repos) if s]
        tight = cleaned if cleaned else list(DEFAULT_TIGHT_INTEGRATION_REPOS)
    else:
        tight = list(DEFAULT_TIGHT_INTEGRATION_REPOS)

    threshold = raw.get("couplingDensityThreshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or threshold <= 0:
        threshold = DEFAULT_COUPLING_THRESHOLD

    syn = raw.get("synthesisDensityThreshold")
    if not isinstance(syn, int) or isinstance(syn, bool) or syn < 0:
        syn = DEFAULT_SYNTHESIS_THRESHOLD

    roles = raw.get("planningRoles")
    if isinstance(roles, list):
        extra = {normalize_role(r) for r in roles}
        planning = set(DEFAULT_PLANNING_ROLES) | {r for r in extra if r}
    else:
        planning = set(DEFAULT_PLANNING_ROLES)
    # Hard deny-list wins: config can never route a verification role to fable.
    planning -= VERIFICATION_ROLES

    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        enabled = True

    return {
        "enabled": enabled,
        "tightIntegrationRepos": sorted(set(tight)),
        "couplingDensityThreshold": float(threshold),
        "synthesisDensityThreshold": int(syn),
        "planningRoles": sorted(planning),
    }


# ---------------------------------------------------------------------------
# State (.build-loop/state.json)
# ---------------------------------------------------------------------------

def _load_state(workdir: Path) -> dict:
    try:
        data = json.loads((workdir / ".build-loop" / "state.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _latest_run(state: dict) -> dict:
    runs = state.get("runs")
    if not isinstance(runs, list):
        return {}
    for record in reversed(runs):
        if isinstance(record, dict):
            return record
    return {}


def _synthesis_density(state: dict) -> tuple[int | None, str]:
    """Return (density, source). None when the key is absent everywhere.

    Accepts the canonical Phase 1 Assess dict `{count, escalated, reason}` and a
    bare int — the two shapes writers actually produce (see judgment_gate.py).
    """
    for scope, blob in (("runs[-1]", _latest_run(state)), ("top-level", state)):
        if "synthesisDensity" not in blob:
            continue
        raw = blob.get("synthesisDensity")
        if isinstance(raw, dict):
            raw = raw.get("count")
        try:
            return int(raw), f".build-loop/state.json {scope} synthesisDensity"
        except (TypeError, ValueError):
            return None, f".build-loop/state.json {scope} synthesisDensity (unreadable)"
    return None, ".build-loop/state.json synthesisDensity (absent)"


def _risk_surface_change(state: dict) -> tuple[bool | None, str]:
    for scope, blob in (("runs[-1]", _latest_run(state)), ("top-level", state)):
        triggers = blob.get("triggers")
        if isinstance(triggers, dict) and "riskSurfaceChange" in triggers:
            return bool(triggers["riskSurfaceChange"]), f".build-loop/state.json {scope} triggers.riskSurfaceChange"
        if "riskSurfaceChange" in blob:
            return bool(blob["riskSurfaceChange"]), f".build-loop/state.json {scope} riskSurfaceChange"
    return None, ".build-loop/state.json triggers.riskSurfaceChange (absent)"


# ---------------------------------------------------------------------------
# Coupling density
# ---------------------------------------------------------------------------

def find_architecture_dir(workdir: Path) -> Path | None:
    """First architecture dir containing a readable graph.json, else None."""
    for rel in ARCHITECTURE_DIRS:
        candidate = workdir / rel
        if (candidate / "graph.json").is_file():
            return candidate
    return None


def _is_internal(node: dict) -> bool:
    """First-party component? Excludes package / service / LLM / infra nodes.

    Architecture graphs list third-party endpoints as nodes alongside first-party
    components (build-loop's own graph carries 16 such nodes at `layer: external`
    reached by 172 `uses-package` / `service-call` edges). Counting them would
    read "this repo has many dependencies" as "this repo is tightly coupled".
    """
    layer = str(node.get("layer", "") or "").strip().lower()
    if layer in EXTERNAL_LAYERS:
        return False
    node_id = str(node.get("id", ""))
    return not any(marker in node_id for marker in EXTERNAL_ID_MARKERS)


def _resolve_touched(arch_dir: Path, touched: list[str]) -> set[str]:
    """Map touched repo-relative paths to component ids via file_map.json."""
    try:
        blob = json.loads((arch_dir / "file_map.json").read_text(encoding="utf-8"))
        files = blob.get("files") if isinstance(blob, dict) else None
        if not isinstance(files, dict):
            return set()
    except Exception:
        return set()
    wanted = {str(p).strip().lstrip("./") for p in touched}
    return {cid for path, cid in files.items() if str(path).lstrip("./") in wanted}


def coupling_density(
    workdir: Path,
    touched: list[str] | None = None,
) -> tuple[float | None, str]:
    """Mean (fan-in + fan-out) per internal component. (density, source).

    Returns (None, reason) — i.e. `unknown` — when no graph exists, the graph is
    unparseable, or it holds no internal components. See the module docstring for
    the full metric definition and why external endpoints are excluded.
    """
    arch_dir = find_architecture_dir(workdir)
    if arch_dir is None:
        return None, "no architecture graph (.build-loop/ or .navgator/architecture/graph.json)"

    rel = arch_dir.relative_to(workdir) if arch_dir.is_relative_to(workdir) else arch_dir
    src = f"{rel}/graph.json"
    try:
        graph = json.loads((arch_dir / "graph.json").read_text(encoding="utf-8"))
        nodes = graph["nodes"]
        edges = graph["edges"]
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise TypeError("nodes/edges must be lists")
    except Exception:
        return None, f"{src} unreadable"

    internal = {n["id"] for n in nodes if isinstance(n, dict) and n.get("id") and _is_internal(n)}
    if not internal:
        return None, f"{src} has no components"

    degree: dict[str, int] = {cid: 0 for cid in internal}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src_id, dst_id = edge.get("from"), edge.get("to")
        # Internal edge only: BOTH endpoints must be components in this graph.
        if src_id in internal and dst_id in internal:
            degree[src_id] += 1   # fan-out
            degree[dst_id] += 1   # fan-in

    scope = internal
    scope_label = "whole-repo"
    if touched:
        resolved = _resolve_touched(arch_dir, touched) & internal
        if resolved:
            scope = resolved
            scope_label = f"{len(resolved)} touched component(s)"
        else:
            scope_label = "whole-repo (no touched path resolved to a component)"

    density = sum(degree[cid] for cid in scope) / len(scope)
    return density, f"{src} ({scope_label}, {len(scope)} components)"


# ---------------------------------------------------------------------------
# Signal assembly
# ---------------------------------------------------------------------------

def _signal(value: bool | None, source: str) -> dict:
    return {"value": UNKNOWN if value is None else bool(value), "source": source}


def collect_signals(
    workdir: Path,
    config: dict,
    slug: str | None,
    touched: list[str] | None = None,
) -> dict:
    """Evaluate all four signals to true / false / "unknown" with sources."""
    signals: dict[str, dict] = {}

    # tight_integration — objective list membership.
    if slug is None:
        signals["tight_integration"] = _signal(None, "repo slug undeterminable")
    else:
        hit = slug in config["tightIntegrationRepos"]
        signals["tight_integration"] = _signal(
            hit, f"config.frontierGate.tightIntegrationRepos vs repo slug '{slug}'"
        )

    # coupling_density — derived from an architecture graph, or unknown.
    density, density_src = coupling_density(workdir, touched)
    threshold = config["couplingDensityThreshold"]
    if density is None:
        signals["coupling_density"] = _signal(None, density_src)
    else:
        signals["coupling_density"] = _signal(
            density > threshold, f"{density_src}: mean degree {density:.2f} vs threshold {threshold:g}"
        )

    state = _load_state(workdir)

    density_syn, syn_src = _synthesis_density(state)
    syn_threshold = config["synthesisDensityThreshold"]
    if density_syn is None:
        signals["synthesis_density"] = _signal(None, syn_src)
    else:
        signals["synthesis_density"] = _signal(
            density_syn > syn_threshold, f"{syn_src}={density_syn} vs threshold >{syn_threshold}"
        )

    risk, risk_src = _risk_surface_change(state)
    signals["risk_surface_change"] = _signal(risk, risk_src)

    return signals


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def evaluate(
    workdir: Path,
    role: str,
    repo: str | None = None,
    touched: list[str] | None = None,
) -> dict:
    """Return the routing envelope for *role* on *workdir*. Never raises."""
    config = load_config(workdir)
    slug = repo_slug(workdir, repo)
    canon_role = normalize_role(role)
    signals = collect_signals(workdir, config, slug, touched)
    fired = [name for name, sig in signals.items() if sig["value"] is True]

    def envelope(verdict: str, reason: str) -> dict:
        return {
            "verdict": verdict,
            "role": canon_role,
            "fired": fired,
            "signals": signals,
            "reason": reason,
        }

    # 1. Verification and every non-planning role stay on the tier default.
    if canon_role in VERIFICATION_ROLES:
        return envelope(
            TIER_DEFAULT_MODEL,
            f"role '{canon_role}' is a verification role — opus was a strict superset of "
            f"fable in 3/3 adversarial-audit rounds ({AUDIT_OBSERVATION}); never gated",
        )
    if canon_role not in config["planningRoles"]:
        return envelope(
            TIER_DEFAULT_MODEL,
            f"role '{canon_role}' is not in the planning allowlist "
            f"{config['planningRoles']}; tier default applies",
        )

    # 2. Explicit off-switch.
    if not config["enabled"]:
        return envelope(TIER_DEFAULT_MODEL, "config.frontierGate.enabled is false; tier default applies")

    # 3. A deciding signal settles it — the repo IS tightly coupled.
    deciding = [n for n in DECIDING_SIGNALS if signals[n]["value"] is True]
    if deciding:
        return envelope(
            GATED_MODEL,
            f"planning role '{canon_role}' on a tightly-coupled repo ({', '.join(deciding)}); "
            f"no fable-vs-opus5 head-to-head exists on plan authoring and fable won the only "
            f"spec-authoring bakeoff on file ({AUTHORING_OBSERVATION})",
        )

    # 4. Fail-toward: high-stakes run + coupling we cannot measure.
    corroborating = [n for n in CORROBORATING_SIGNALS if signals[n]["value"] is True]
    if corroborating and signals["coupling_density"]["value"] == UNKNOWN:
        return envelope(
            GATED_MODEL,
            f"planning role '{canon_role}': {', '.join(corroborating)} fired and coupling_density is "
            "unknown — absence of a graph is not evidence of loose coupling, so the gate fails "
            "toward the model with the only positive spec-authoring evidence",
        )

    # 5. Nothing fired, or stakes fired but coupling is measurably low.
    return envelope(
        TIER_DEFAULT_MODEL,
        f"planning role '{canon_role}': no deciding signal fired; tier default applies",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_plain(result: dict) -> str:
    fired = ", ".join(result["fired"]) or "none"
    return f"{result['verdict']}  role={result['role']}  fired={fired}  — {result['reason']}"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Decide whether a role on a repo routes to fable instead of the T1 default opus."
    )
    p.add_argument("--role", required=True, metavar="ROLE",
                   help="Agent role, e.g. 'advisor', 'plan-synthesis', 'independent-auditor'.")
    p.add_argument("--workdir", type=Path, default=Path("."), metavar="DIR")
    p.add_argument("--repo", default=None, metavar="SLUG",
                   help="Repo slug override (defaults to the workdir directory name).")
    p.add_argument("--touched", action="append", default=None, metavar="PATH",
                   help="Repo-relative touched file; repeatable. Scopes coupling_density "
                        "to just those components.")
    p.add_argument("--json", action="store_true", help="Emit the full JSON envelope (default).")
    p.add_argument("--plain", action="store_true", help="Emit a one-line human summary instead.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = evaluate(
        workdir=args.workdir.resolve(),
        role=args.role,
        repo=args.repo,
        touched=args.touched,
    )
    if args.plain and not args.json:
        print(format_plain(result))
    else:
        print(json.dumps(result, indent=2))
    return 0  # advisory only — never blocks


if __name__ == "__main__":
    sys.exit(main())
