// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//
// Assess-grounding replay pilot. Replays prior real build-loop challenges
// (goal + repo SHA) under grounding VARIANTS, re-runs Phase-1 Assess at each
// checkout, judges groundedness on Fable, and scores every candidate against
// the run's OBJECTIVE recorded outcome with scripts/assess_grounding_score.py.
//
// COST: spawns (challenges x variants x reps) Opus Assess agents + a Fable
// judge each. This is the expensive experiment the harness exists to run —
// invoke deliberately with a budget. Subset via args to keep it bounded:
//   Workflow({ scriptPath: ".../replay-pilot.workflow.js",
//              args: { challengeIds: ["blr-f6-stop-closeout"], reps: 1 } })
//
// The workflow JS sandbox has no fs/Bash — every real action (git worktree,
// running Assess, running the Python scorer) happens inside an agent() that
// holds the real tools. Offline replay is a FILTER (candidate-finder), not the
// verdict; the verdict is the online A/B loop (see docs/assess-grounding-harness.md).

export const meta = {
  name: 'assess-grounding-replay-pilot',
  description: 'Replay prior build-loop challenges under grounding variants; judge + score Assess outputs into a Pareto scorecard',
  phases: [
    { title: 'Replay', detail: 'run Phase-1 Assess at each challenge SHA under each grounding variant' },
    { title: 'Judge', detail: 'Fable binary groundedness judge per candidate' },
    { title: 'Score', detail: 'deterministic multi-objective scorer + Pareto frontier' },
  ],
}

const REPO = '/Users/tyroneross/dev/git-folder/build-loop'
const CHALLENGES = `${REPO}/evals/assess-grounding/challenges.jsonl`
const SCORER = `${REPO}/scripts/assess_grounding_score.py`

// v1: model fixed = Opus (option a). Add a `model` factor here later (option b)
// — each variant gains a model field and the matrix grows by that axis.
const VARIANTS = [
  { id: 'G0', desc: 'baseline: phase-1-assess.md protocol as-is; step-5 architecture tier = raw-read fallback; no mandatory citation' },
  { id: 'G1', desc: 'grounded: step-5 = navgator-full architecture map injected; step-5b reads-deps enumerated; EVERY trigger must carry a file:line citation or be dropped' },
]
// args may arrive as a JSON string depending on host; normalize to an object.
const ARGS = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {})
const reps = ARGS.reps || 1

const ASSESS_SCHEMA = {
  type: 'object',
  required: ['triggers', 'evidence'],
  properties: {
    triggers: {
      type: 'object',
      properties: {
        riskSurfaceChange: { type: 'boolean' },
        structuredWriting: { type: 'boolean' },
        promptAuthoring: { type: 'boolean' },
        promptEditingExisting: { type: 'boolean' },
        runtimeServer: { type: 'boolean' },
      },
    },
    synthesis_count: { type: ['integer', 'null'] },
    synthesis_escalated: { type: ['boolean', 'null'] },
    predicted_files: { type: 'array', items: { type: 'string' } },
    evidence: {
      type: 'object',
      description: 'per-trigger file:line citation(s) backing each TRUE trigger; empty for false triggers',
    },
    cost: {
      type: 'object',
      properties: { tokens: { type: ['integer', 'null'] }, latency_ms: { type: ['integer', 'null'] } },
    },
  },
}

const GROUNDED_SCHEMA = {
  type: 'object',
  required: ['groundedness', 'per_trigger'],
  properties: {
    groundedness: { type: 'number', description: 'fraction of TRUE triggers whose cited evidence actually supports them (0..1)' },
    per_trigger: { type: 'object', description: 'trigger -> PASS|FAIL (does the cited evidence support it)' },
  },
}

// 1) Load challenges (agent reads the file; sandbox has no fs).
// StructuredOutput requires a top-level object schema, so wrap the array.
const loaded = await agent(
  `Read ${CHALLENGES}. Parse every line that is not blank and does not start with '#' as JSON. Return {"challenges": [ ...those objects ]}.`,
  { label: 'load-challenges', phase: 'Replay', schema: { type: 'object', required: ['challenges'], properties: { challenges: { type: 'array', items: { type: 'object' } } } } }
)
const all = (loaded && loaded.challenges) || []
const challenges = ARGS.challengeIds ? all.filter(c => ARGS.challengeIds.includes(c.id)) : all
log(`replaying ${challenges.length} challenge(s) x ${VARIANTS.length} variant(s) x ${reps} rep(s)`)

// 2) Build the (challenge x variant x rep) cell list.
const cells = []
for (const ch of challenges) for (const v of VARIANTS) for (let r = 0; r < reps; r++) cells.push({ ch, v, r })

// 3) Pipeline each cell: Replay (run Assess at the SHA) -> Judge (Fable groundedness).
const candidates = await pipeline(
  cells,
  ({ ch, v, r }) => agent(
    `You are replaying a build-loop Phase-1 ASSESS under grounding variant ${v.id}.
1. Create an isolated checkout: \`git -C ${REPO} worktree add --detach /tmp/agr-${ch.id}-${v.id}-${r} ${ch.sha}\` (remove it with \`git -C ${REPO} worktree remove --force\` when done).
2. In that checkout, run the Phase-1 Assess judgment for this goal ONLY (you do not need to run a full build): "${ch.goal}".
   Grounding variant ${v.id}: ${v.desc}.
3. Emit the resulting assessment object: which triggers are TRUE (${Object.keys(ASSESS_SCHEMA.properties.triggers.properties).join(', ')}), the synthesis dimension count + whether it escalates (>5), the files you predict the change will touch, and — for EVERY true trigger — a file:line 'evidence' citation from the checkout (variant G1 REQUIRES this; drop any trigger you cannot cite). Record approximate cost {tokens, latency_ms} for your assess work.
Do NOT read .build-loop/state.json or any recorded outcome for this run — that is the answer key; grounding must come only from the checked-out repo + the goal.`,
    { label: `replay:${ch.id}:${v.id}#${r}`, phase: 'Replay', schema: ASSESS_SCHEMA }
  ).then(assessment => ({ challenge_id: ch.id, variant: v.id, rep: r, ch, assessment })),

  (cand) => {
    if (!cand) return null
    const base = {
      challenge_id: cand.challenge_id, variant: cand.variant, rep: cand.rep,
      assessment: cand.assessment, cost: (cand.assessment && cand.assessment.cost) || {},
    }
    // Groundedness is the OPTIONAL judge-graded objective — a judge failure must
    // NOT discard the (expensive) replay assessment. Fall back to null; the
    // scorer treats null groundedness as not-gradable, never 0. (Org standard
    // pins the judge to Fable, but 'claude-fable-5' is unreachable from workflow
    // subagents in this environment, so the judge inherits the session model.
    // Re-pin model:'fable' once it is reachable.)
    return agent(
      `Judge GROUNDEDNESS of an Assess output (binary per trigger; eval-guide.md doctrine). For each TRUE trigger, decide PASS (its cited file:line evidence genuinely supports it) or FAIL. groundedness = PASS count / TRUE-trigger count, or 1.0 if there are no true triggers. Verify each citation by reading the file at the challenge commit with \`git -C ${REPO} show ${cand.ch.sha}:<path>\` (no worktree needed). Assessment: ${JSON.stringify(cand.assessment)}. Do not consult any recorded outcome — grounding must come only from the cited repo content.`,
      { label: `judge:${cand.challenge_id}:${cand.variant}#${cand.rep}`, phase: 'Judge', schema: GROUNDED_SCHEMA }
    ).then(j => ({ ...base, groundedness: (j && typeof j.groundedness === 'number') ? j.groundedness : null }))
     .catch(() => ({ ...base, groundedness: null }))
  }
)

const clean = candidates.filter(Boolean)
log(`collected ${clean.length}/${cells.length} candidate(s)`)

// 4) Score: one agent writes the candidates JSONL, runs the deterministic
//    Python scorer against challenges.jsonl, appends to experiments/, returns
//    the Pareto scorecard. (Scoring is code-based per the grading hierarchy.)
const scorecard = await agent(
  `Score these Assess-grounding candidates.
1. Write this JSON array, one object per line, to /tmp/agr-candidates.jsonl: ${JSON.stringify(clean)}
2. Run: python3 ${SCORER} --candidates /tmp/agr-candidates.jsonl --challenges ${CHALLENGES}
3. Append the parsed result as one line to ${REPO}/.build-loop/experiments/assess-grounding.jsonl (create the dir if missing) with an added "ts_note":"stamp-after-run" field (do not call Date in the workflow).
4. Return: a markdown Scorecard table (variant rollup: trigger_recall/precision, synthesis_calibration, groundedness, cost_tokens), the pareto_variants list, and a per-goal-type breakdown of the cells.
5. git -C ${REPO} worktree prune, and remove any /tmp/agr-* worktrees you created.`,
  { label: 'score+report', phase: 'Score' }
)

return { candidates: clean.length, scorecard }
