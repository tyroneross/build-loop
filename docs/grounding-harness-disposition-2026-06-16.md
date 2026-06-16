# grounding-harness — disposition & fold decision

Date: 2026-06-16 · Decision: **do NOT fold into build-loop; keep as a documented, recoverable research tool.**

## What grounding-harness is
A live-model grounding/faithfulness eval harness built 2026-06-13/14 for the Opus-grounding
study (`~/dev/git-folder/grounding-harness`, **local-only, no git remote**). Drives `claude -p`
headless as a DoE on top of `agent-doe-engine`. Capabilities:
- no-tools + **evidence-withheld** faithfulness probing (the regime where grounding actually fails)
- seeded **synthetic substrate generator** (exact deterministic truth, zero leakage)
- **deterministic-first grading** + citation-validity + Wilson CIs + cluster bootstrap
- reusable scripts: `run_arm.py`, `run_notools.py`, `gen_substrate.py`, `analyze_contrast.py`

## Is it redundant with build-loop's eval surface?
No. build-loop's "golden-grading harness" is a **static RCA golden-corpus**
(`docs/test-fixtures/systemic-rca/golden-corpus.json`, validated with pytest) for *RCA-discipline*
testing. grounding-harness measures **live-model grounding/faithfulness** — a distinct capability
build-loop does not have.

## Fold decision: NO (does not add value now)
1. The study **concluded no current Opus grounding deficit** → low ongoing need for a standing grounding eval.
2. Folding a research one-off into build-loop adds maintained surface for something rarely run.
3. build-loop's eval need (RCA golden corpus) is already covered and is a different thing.

Folding would add maintenance cost without present payoff. The *durable value* — the lessons — is
already captured canonically.

## Durability & resume path
- ⚠️ The harness **code is local-only** (no remote) → at risk of loss. If grounding work resumes —
  the untested frontiers where gaps actually live: **long-context (lost-in-the-middle),
  adversarial/contradictory evidence, multi-source synthesis** — push grounding-harness as its own
  standalone repo at that point and reuse the scripts above.
- Until then, the durable capture is sufficient:
  - Canonical lesson: `build-loop-memory/projects/build-loop/lessons/2026-06-16-lesson-grounding-eval-measures-its-instruments.md`
  - Harness memory: `reference_opus_grounding_at_ceiling`, `reference_llm_judge_grades_from_prior`
  - Full detail + retrospective: `grounding-harness/docs/2026-06-13-grounding-harness-design.md`, `…/2026-06-14-grounding-harness-retrospective.md` (local)
