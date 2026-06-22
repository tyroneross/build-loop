# Living architecture diagram — format spec

A version-controlled, self-updating diagram of the build-loop architecture. You edit one
markdown doc; the diagram regenerates from it and from the repo, so it cannot silently drift.

**Open the diagram:** [`docs/build-loop-flow-mockup.html`](../docs/build-loop-flow-mockup.html) — standalone, the model is inlined.
**Source of truth:** [`ARCHITECTURE.md`](ARCHITECTURE.md) — you edit this.

---

## The two layers

| Layer | Where | Who maintains | How it reaches the diagram |
|---|---|---|---|
| **Components** — agents, skills, scripts, hooks | the repo (`agents/*.md`, `skills/**/SKILL.md`, `scripts/**/*.py`, `hooks/hooks.json`) | nobody — auto-discovered | `generate.py` reads them every run; counts + lists are written into `ARCHITECTURE.md` "Components" |
| **Flow** — phases, sub-steps, gates, dispatch edges, current-vs-proposed | the `yaml` block under `<!-- arch:flow -->` in `ARCHITECTURE.md` | **you** | `generate.py` parses the block, enriches component references from the repo, emits `model.json`, injects it into the HTML |

**You never hand-list agents/skills/scripts.** You reference them by name in the flow; the generator fills in their real tier/description and the drift-linter rejects any name that doesn't exist.

---

## Workflow

```bash
# 1. edit architecture/ARCHITECTURE.md  (the yaml flow block, and/or add a component file to the repo)
# 2. regenerate
python3 scripts/architecture_diagram/generate.py
# 3. gate (drift + freshness) — wire into CI / pre-commit
bash scripts/architecture_diagram/check.sh
```

- **Added/renamed/re-tiered an agent, added a skill/script/hook** → just run `generate.py`; the
  Components inventory + any flow chips that reference it update automatically.
- **Changed the flow** (phase, gate, sub-step, dispatch edge, current↔proposed) → edit the
  `yaml` block, run `generate.py`. The linter rejects references to anything that doesn't exist.

---

## Flow `yaml` schema (the block under `<!-- arch:flow -->`)

```yaml
pipeline:
  in:  [ ... ]          # pipeline-level inputs (left edge)
  out: [ ... ]          # pipeline-level outputs (right edge)

proposed: [ <id>, ... ] # ids rendered with a ⊕ PROPOSED badge (redesign, not current behavior)

gate_after:             # a ◇ gate diamond ON the connector after a phase
  <phase-id>: { step: <step-id>, label: <text>, tier: <T0|T1|T2 ...>, note: <text?>, prop: <bool?> }

roles:                  # the top-level role cards
  <role-id>: { type: <text>, name: <text>, desc: <text>, proposed: <bool?> }

subagents:              # click-for-goal registry; keys must be real agent names (or a group)
  <agent-name>: { goal: <text>, does: <text> }

hook_overrides:         # event auto-derived from hooks.json; purpose text authored here
  <hook-basename>: { event: <text?>, purpose: <text>, proposed: <bool?> }

phases:                 # the spine, left → right
  - id: <phase-id>
    "no": "<Phase N>"   # MUST be quoted — bare `no` is YAML boolean false
    name: <text>
    lane: <text>        # "Orchestrator", "Auditor (independent)", or a combo string
    hasGate: <bool?>    # shows a "◇ contains a gate" flag on the card
    loop: <bool?>       # renders as a ↺ loop card (e.g. Iterate), not a forward stage
    desc: <text>
    in:  [ ... ]
    out: [ ... ]
    agents: [ [<name>, "", <by>], ... ]   # tier "" → auto-filled from agents/*.md frontmatter
    steps:
      - id: <step-id>
        name: <text>
        kind: process | dispatch | gate | decision | new
        tier: <T0|T1|T2 ...>              # gate steps only
        desc: <text>
        hooks: [ <hook-basename>, ... ]   # must exist in hooks.json (or be synthetic "(post)/(stop) …" / proposed)
        agents: [ [<name>, "", <by>], ... ]
        branches: "PASS → …\nfail → …"    # decision/gate only

agent_aliases:          # chip display name -> real agent name (or "group")
  "<display>": <agent-name | group>
agent_groups:           # synthetic group chip -> the real agents it stands for (for coverage)
  "<group-name>": [ <agent-name>, ... ]
coverage_exempt:        # real agents intentionally NOT on the spine (coordinators, mode-specific)
  - <agent-name>
```

### Field reference

- **`by`** — `Orchestrator` (dispatched child) or `Auditor (independent)` (the oversight lane; rendered dashed/bold).
- **`kind`** — `process` (box), `dispatch` (box, names subagents), `gate` (bar + tier badge + branches), `decision` (dashed), `new` (⊕ proposed feature).
- **`tier`** — purely a label on the gate; the control-tier legend explains T0/T1/T2. See "Control tiers" in the diagram.
- **agent `tier` slot** — leave `""`; the generator fills the real model tier from the agent's frontmatter. Hardcoding it would drift.

---

## Drift gate (`check.sh`)

Mirrors the `scripts/sync_skills.py` drift-detector contract. Read-only, structured findings.

- **ERROR (blocks):** the flow names an agent/hook that is not in `agents/` / `hooks/hooks.json`
  and is not a declared alias/group or a PROPOSED-new item.
- **WARN:** a real `agents/*.md` agent is absent from the flow and not on `coverage_exempt`.
- **Freshness:** `generate.py --check` fails if `model.json`, the HTML injection, or the
  `ARCHITECTURE.md` Components section is stale vs source.

`BL_ARCH_ADVISORY=1 bash scripts/architecture_diagram/check.sh` warns instead of blocking (local use).

---

## Source-of-truth detail + version control

Each agent (and skill) carries, auto-derived from its definition file:

- **`model`** — its tier (from frontmatter).
- **`description`** — what it does, extracted from the agent's frontmatter (examples stripped).
  Click any agent chip in the diagram to read it — the diagram is a source of truth for agent behavior.
- **`last_updated`** — `{author, date}` from `git log -1` on the file, so you see **who last
  changed each component and when**. Component files aren't touched by the generator, so this is
  stable and doesn't churn `model.json`.

## Capturing feedback → backlog (open items)

Comments left on the diagram (autosaved, then **⬇ Backup** to a `.json`) become assessable open
items in the build-loop backlog:

```bash
python3 scripts/architecture_diagram/comments_to_backlog.py <backup.json>      # --dry-run to preview
```

Each commented element becomes one `backlog.py` item (`status: open`, `provenance: architecture-diagram`,
`--type decision` by default) under the repo's `.build-loop/backlog/`, then `backlog.py sync` indexes
it — ready for assessment / triage into improvements. Segmentation (`repo`/`branch`) follows the
backlog system's rules; pass `--repo` for a different target.

## Provenance

`model.json._provenance.content_sha256` is a hash of the derived model — **no git sha**, so the
file changes only when the architecture actually changes (the diagram's `git log` is the
architecture changelog). Regeneration is deterministic: same source → same hash.

## Files

- `ARCHITECTURE.md` — source you edit (Components auto + Flow authored).
- `model.json` — generated merged model (git-tracked).
- `../docs/build-loop-flow-mockup.html` — renderer (reads the injected `window.BL_MODEL`).
- `../scripts/architecture_diagram/` — `generate.py`, `drift_lint.py`, `check.sh`, `test_drift_lint.py`.

## v1 limitations / follow-ups

- The HTML shows agents + hooks in the flow and a Components strip (counts + lists for
  skills/scripts); wiring individual skills/scripts into specific phase steps is supported by the
  schema (`steps[].agents`) but skills/scripts-per-step rendering is a follow-up.
- Role cards render from static HTML chips; their data lives in `roles:`.
- The HTML keeps inline literals as a fallback; a later pass can excise them once CI trusts the
  generated path.
