---
name: build-loop:prd-bridge
description: Check for a project-level PRD in Phase 1 Assess; surface its always-true principles and Navigation Map so subsequent phases reason from strategic frame. Recommend the prd-builder skill if no PRD exists.
version: 0.1.0
user-invocable: false
---

# PRD Bridge

Lets build-loop ground every phase decision in a project's strategic frame (its PRD) when one exists, and surfaces a recommendation when one doesn't. The PRD is the source of strategic truth; this bridge consumes it without coupling to its content.

**Use:**
- **Phase 1 Assess** — load PRD frontmatter and Navigation Map; mirror always-true principles into `.build-loop/state.json.prd`. Phase 2 Plan and Phase 5 Review consult this for scoping, criterion design, and "is this on-vision" gates.
- **Phase 5 Review-D Fact-Check** — verify the build doesn't violate any PRD `core_principles` (e.g., a change that exposes admin-only complexity to regular users when the PRD's complexity stance forbids that).
- **Phase 6 Learn (optional)** — flag if the build's lessons contradict a PRD principle. The user decides: was the principle wrong (update PRD), or was the build off-vision (don't promote learning)?

## Cherry-pick principle

**The PRD remains owned by the project, not by build-loop.** This bridge does not author, modify, or shadow PRDs — it only consumes the relevant fields:

- Reads `docs/prd-*.md` frontmatter (`core_principles`, `load_when`, `evolves_when`, `revision`, `status`) — file-only
- Reads the body's "LLM Navigation Map" and "Section Index" tables to enable targeted offset/limit reads in later phases
- Writes to `.build-loop/state.json.prd.*` — bridge's own namespace
- Surfaces a one-line recommendation when no PRD exists; does not auto-create one

What this bridge does NOT do:
- Write or edit the PRD
- Cache PRD content (always reads live from disk)
- Resolve ambiguous principles by interpretation (if a principle is unclear, surface it as a question for Phase 2 Plan)
- Block the build when a PRD is missing — only recommends one

## Pre-flight

Before phase logic runs, check:

```bash
ls docs/prd-*.md 2>/dev/null | head -1 && echo "HAVE_PRD" || echo "NO_PRD"
```

If `HAVE_PRD`, run the steps below.

If `NO_PRD`, surface this one-line note in `state.json.prd.recommendation`:

> No PRD found. Tactical decisions during this build will lack strategic grounding. Consider running the **`prd-builder`** skill from RossLabs-AI-Toolkit (or `/build-loop:start-prd`) to draft a living PRD before iterating further. This is a recommendation, not a blocker.

The note appears in Phase 5 Report's "Open recommendations" section so the user can act between builds.

## Phase 1 Assess — Load PRD into state

When a PRD is present:

1. **Read frontmatter** with a YAML parser (or grep for the documented fields). Extract:
   - `name`
   - `status`
   - `revision`
   - `last_updated`
   - `load_when` (array)
   - `evolves_when` (array)
   - `core_principles` (array of 1-line statements)

2. **Read the Navigation Map table** if present. Parse rows into `{decision_type: section_name}` mapping. This is what Phase 2 Plan and Phase 5 Review consult to load only the relevant section.

3. **Read the Section Index table** if present. Parse rows into `{section_name: line_range}`. This enables targeted `Read --offset --limit` calls.

4. **Write to `.build-loop/state.json.prd`:**
   ```json
   {
     "path": "docs/prd-speaksavvy.md",
     "status": "living",
     "revision": "0.1",
     "last_updated": "2026-05-01",
     "core_principles": ["..."],
     "load_when": ["..."],
     "navigation_map": {
       "should_i_add_this_feature": "Persona + Outcome + Methodology",
       "...": "..."
     },
     "section_index": {
       "intent": [95, 98],
       "...": [0, 0]
     }
   }
   ```

5. **Surface staleness signals**:
   - If `last_updated` is more than 90 days old AND `status: living` → flag `prd_stale: true` so Phase 5 Report includes a "PRD review due" note.
   - If `status: pivoting` → flag `prd_pivoting: true` so Phase 2 Plan asks the user before locking scope.
   - If any `evolves_when` trigger condition is true (best-effort detection) → surface as a "PRD review recommended" note.

## Phase 2 Plan — Consult navigation map

When Phase 2 is about to lock scope:

1. Identify the **decision type** the build represents (new feature, UX change, architectural choice, rubric/scoring change, etc.).
2. Look up the matching row in `state.json.prd.navigation_map`.
3. Read the indicated section(s) using `Read --offset --limit` per `section_index`.
4. Cite the section in the plan's rationale (e.g., *"per Persona, Director+ users won't tolerate hidden-required complexity, so this UI surfaces the toggle by default"*).

If no row matches the decision type, default to **Persona + Outcome** (the universal fallback per most PRDs).

## Phase 5 Review-D Fact-Check — Principle violation check

After implementation, scan the diff for changes that contradict any `core_principles`:

1. For each `core_principle`, attempt a lightweight grep against the diff for keywords that suggest a violation. Examples:
   - Principle: *"Hide complexity from regular users"* → grep diff for new public Settings UI with admin-tier features
   - Principle: *"On-device first; cloud fallback only when..."* → grep diff for new always-cloud calls
   - Principle: *"Don't add new drills next; polish, fold, kill"* → grep diff for new drill ViewModel/View pairs
2. **Heuristic only** — surface findings as `principle_check_findings` for human review, not auto-block.
3. If any finding suggests a principle violation, route to Phase 5 Report with the principle citation + the diff hunk that triggered it.

If the heuristics produce no findings, write `principle_check: clean`. The clean-result is honest: "no obvious contradictions; not a guarantee."

## Standalone fallback

If `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md` `#prd` exists, follow its degraded recipe. Otherwise:

- **If no PRD frontmatter parser is available** → grep `^- ` lines under `core_principles:` (works for simple list format), surface as raw text.
- **If no Navigation Map exists in PRD** → fall back to reading the entire body once and asking Phase 2 Plan to derive the relevant section by reading section headers.
- **If no Section Index exists** → fall back to reading the section by `grep -n '^## '` to find line ranges.

The fallback degrades gracefully — partial signal is better than no signal.

## Files referenced

- `docs/prd-*.md` (project-level PRD; pattern allows multi-app monorepos)
- `.build-loop/state.json.prd` (bridge output)
- `.build-loop/issues/principle-violations.md` (Phase 5 fact-check findings, when populated)

## Related

- `prd-builder` skill (RossLabs-AI-Toolkit) — drafts a new PRD from 3-5 strategic questions when none exists.
- `/build-loop:start-prd` command — explicit invocation path; loads `prd-builder` from the toolkit if installed.
