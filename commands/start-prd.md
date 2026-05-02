---
name: start-prd
description: "Start a living PRD for the current project by answering 3-5 strategic questions. Loads the prd-builder skill if installed."
argument-hint: "[appname]"
---

Load the `prd-builder` skill from RossLabs-AI-Toolkit if available. If unavailable, fall back to the inline guidance below.

{{#if ARGUMENTS}}
App name: `{{ARGUMENTS}}`

Run the prd-builder workflow for this app:
1. Confirm the skill applies (check trigger conditions).
2. Ask Q1-3 in a single message, formatted as draft inferences (not blank prompts) based on whatever you know about the project from the codebase + memory.
3. Wait for the user to redirect or confirm.
4. Optionally ask Q4-5 if Q1-3 leave specific gaps the user wants pinned down.
5. Draft the PRD at `docs/prd-{{ARGUMENTS}}.md` per the prd-builder Output Specification.
6. Run the Fidelity check; deepen any section that fails prediction.
7. Add a project-level pointer in `CLAUDE.md` (or `.claude/CLAUDE.md`): *"For non-trivial changes, read `docs/prd-{{ARGUMENTS}}.md` and apply its LLM Navigation Map."*
8. (If applicable) bidirectionally link to existing research packets, audit docs, or operationalization documents.
{{else}}
No app name provided. Either:
- Provide an appname argument: `/build-loop:start-prd myapp` → drafts `docs/prd-myapp.md`
- Or ask the user what app this PRD is for, then proceed with the workflow above.
{{/if}}

## Fallback when prd-builder skill is not installed

If the `prd-builder` skill is unavailable in this session, walk the user through the 3 core questions inline:

**Q1 — Who and when (persona + trigger):** Describe the primary user (role, life stage, what they already use that this competes with). List 1-3 trigger moments that make them open the app. List 3-7 explicit "is NOT" exclusions.

**Q2 — Outcome (the measurable change):** Pick ONE measurable change after N weeks/months of usage. Number, behavior, or self-perception.

**Q3 — Stance (the philosophy):** Three sentences:
1. Privacy/Data: "We will/won't [send/store/share] X because Y."
2. Complexity: "Regular users will/won't see [advanced feature class] because Y."
3. Cost: "App is [free/freemium/paid/subscription/...] because Y."

Then draft a markdown PRD at `docs/prd-{{ARGUMENTS}}.md` with:
- Frontmatter (name, status, revision, last_updated, load_when, evolves_when, core_principles)
- Body sections: How to use this PRD, LLM Navigation Map, Section Index, Fidelity check, Intent, North Star, Persona, Outcome, Methodology, Stance, Non-goals (illustrative), Roadmap stance, One-line summary, Open questions, Pivot log, Document maintenance.

Cite `~/dev/git-folder/RossLabs-AI-Toolkit/skills/prd-builder/SKILL.md` for the full output spec.

After drafting, run the Fidelity check by predicting answers to:
- Should the next major release prioritize speed or accuracy?
- Should new features add complexity or simplify existing ones?
- Should the home screen show many metrics or one north-star indicator?
- Should we accept a feature request from a vocal user who doesn't fit the persona?
- When should the app degrade gracefully vs fail loudly?
- When should we move work from on-device to cloud (or vice versa)?
- Should onboarding be opinionated or open?

If you can predict each cleanly with PRD section citations, the PRD passes. If not, deepen the failing section.
