# Living architecture diagram

A version-controlled, self-updating diagram of the build-loop flow. It regenerates
from source so it cannot silently drift from the real agents, hooks, and tiers.

## Layers

1. **Auto-derived (mechanical):** agent model tiers from `agents/*.md` frontmatter,
   hook events from `hooks/hooks.json`. These come straight from source on every
   regenerate — change an agent's `model:` and the diagram's tier chip changes.
2. **Authored manifest (`flow.yaml`):** phases, sub-steps, gates, dispatch edges,
   and current-vs-proposed flags — the semantics that can't be grepped out of prose.
   Drift-linted against layer 1.
3. **Generated model (`model.json`):** layers 1+2 merged. Git-tracked, so its
   `git log` is the architecture changelog and any two commits diff cleanly.
4. **Renderer (`docs/build-loop-flow-mockup.html`):** reads `window.BL_MODEL`
   (injected from `model.json` between the `BL_MODEL` markers). The inline literals
   remain only as a fallback.

## Commands

```bash
# regenerate model.json + re-inject into the HTML
python3 scripts/architecture_diagram/generate.py

# CI/pre-commit gate: drift (referenced agent/hook must exist) + freshness
bash scripts/architecture_diagram/check.sh        # BL_ARCH_ADVISORY=1 to warn-not-block

# run the linter / generator tests
python3 scripts/architecture_diagram/test_drift_lint.py
```

## Drift contract

- **ERROR (blocks):** `flow.yaml` names an agent/hook that is not in `agents/` /
  `hooks/hooks.json` (and is not a declared alias/group or a PROPOSED-new item).
- **WARN:** a real `agents/*.md` agent is missing from the diagram and is not on
  `coverage_exempt`. Mirrors the `scripts/sync_skills.py` drift-detector pattern.

## When the architecture changes

- Added/renamed/re-tiered an **agent** or a **hook** → just run `generate.py`; the
  tiers/events update automatically. `check.sh` fails CI until you regenerate.
- Changed the **flow** (a phase, gate, sub-step, dispatch edge, current↔proposed) →
  edit `flow.yaml`, run `generate.py`. The linter rejects references to anything
  that doesn't exist.

## v1 scope / follow-ups

- Role cards (orchestrator / auditor / launch-hook) are still authored in `flow.yaml`
  `roles:` but rendered from static HTML chips; only their data is living.
- The HTML keeps inline literals as a fallback (belt-and-suspenders); a later pass
  can excise them entirely once the generated path is trusted in CI.
- Provenance stamps `source_commit` (no wall-clock), so regeneration is deterministic.
