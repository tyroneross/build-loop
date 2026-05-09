# Single-Writer Git Commit Protocol

_Linked from `agents/build-orchestrator.md` §Phase 3 Execute._

This is build-loop's commit-step protocol introduced 2026-05-07 to eliminate the parallel-commit race condition that lost 3 of 4 commits in round-3 fan-out testing. Implementers no longer call `git add` or `git commit` (per `agents/implementer.md` Hard rule 4); the orchestrator owns `.git/` as a single-writer resource. After **each parallel batch returns**, run this step before dispatching the next wave or proceeding to Phase 4.

For each implementer return envelope with `status: fixed | partial | completed`:

(For `status: "blocked"`, see `references/halt-and-ask-protocol.md` — that branch fires BEFORE the commit step and may iterate up to 3 times before producing a commit-eligible envelope.)

1. **Verify scope**: `git status --porcelain` — every modified/untracked file must appear in some implementer's `files_changed`. Files not claimed by any implementer = orchestrator-side scope-leak; investigate before committing.
2. **Stage exactly that implementer's files**: `git add -- <files_changed_list>`. Use absolute paths to avoid relative-path ambiguity when multiple worktrees coexist.
3. **Commit with the implementer's metadata**: `git commit -m "<commit_subject>" -m "<commit_body>"`. The pre-commit hook runs HERE (full-project tsc, lint-staged, betterer-strict — whatever the project has). If the hook fails, do NOT pass `--no-verify`; instead, capture the failure and route the implementer's plan back to Iterate with `additional_context: "<hook output>"`.
4. **Verify commit landed**: `git log -1 --oneline` confirms the SHA. If `git status` after the commit still shows the implementer's files as modified, the commit didn't land — investigate.
5. **Attestation lint** (NEW 2026-05-07 — synthesis-decision drift catcher): immediately after the commit lands, persist the implementer's envelope to a temp path and run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/attestation_lint.py --diff "<sha>~1..<sha>" --envelope <envelope.json>` where `<sha>` is the commit just verified. The lint cross-checks every `synthesis_attestation` entry against the actual diff for the deterministic dimensions (`placement`, `cta_tier`, `visual_weight`); subjective dims (`copy_tone`, `empty_state`) return `unverifiable` and don't grade.
   - **Exit 0** — every applied claim verified or only-unverifiable-with-some-pass: proceed silently to step 6.
   - **Exit 1** — at least one entry FAILED: a synthesis claim is contradicted by the diff. Default action: revert the commit and route to Iterate with the lint output as `additional_context` (option a). Do not stop and ask the user — this is the kind of issue the loop is built to handle. Two failure modes warrant escalation: (i) Iterate has already retried this same lint failure 3 times without clearing it, in which case surface the failing entries via `AskUserQuestion` and offer all three options (revert, accept with override, amend envelope); (ii) the synthesis claim is on a dimension the user explicitly named in the original plan as load-bearing for user-visible behavior, in which case ask before reverting because reverting destroys evidence the user wants to inspect. Otherwise: revert, iterate, keep going.
   - **Exit 2** — only unverifiable results (every dim was subjective or bare-string form): log a one-line warning to terminal output (e.g. `[Attestation] ⚠️  envelope had no graded claims — synthesis drift undetected this commit`), then proceed. This is informational, not blocking; it tells the operator the lint added zero coverage and the envelope should be richer next time.
6. **Synthesis critic** (NEW 2026-05-07 — model-based grader for the subjective dims `attestation_lint.py` cannot verify): immediately after step 5 settles, decide whether to dispatch `synthesis-critic`.
   - **UI-file gate (skip-if-no-UI-files)**: inspect the implementer's `files_changed`. If **none** of the paths match `*.tsx`, `*.jsx`, `*.vue`, or `*.svelte`, skip this step entirely and proceed to step 7 — the subjective dims (`copy_tone`, `empty_state`) only meaningfully apply to commits that change user-visible UI. Backend-only, infra-only, methodology-only, and doc-only commits never invoke the critic. Log one line: `[SynthesisCritic] skipped — no UI files in commit`.
   - **Dispatch when UI files are present**: `Agent(subagent_type="build-loop:synthesis-critic", prompt=...)` with three context blocks in the prompt: (a) the unified diff (`git diff <sha>~1..<sha>`); (b) the plan's `synthesis_dimensions` block verbatim (so the critic has the claimed phrasing); (c) the implementer's `synthesis_attestation` and `notes` from the envelope. The critic returns one JSON object: `{verdict: "pass" | "flag", flagged: [{dimension, claimed, observed, reasoning}], notes: "..."}`.
   - **`verdict: "pass"`**: log one line: `[SynthesisCritic] ✅ pass — N subjective dim(s) graded`. Proceed to step 7.
   - **`verdict: "flag"`**: log a WARN line per flagged dimension (e.g. `[SynthesisCritic] ⚠️  copy_tone — claimed "calm-precision, no exclamation points"; observed "Done!" in NewsBanner.tsx`). Append the full JSON to `.build-loop/state.json.synthesisCriticFlags[]` for Phase 6 Learn pattern detection. **Do NOT block.** Do NOT route to Iterate. Do NOT alter the implementer's `f_criteria`. The critic is WARN-only by contract — flagged dims surface for the operator to triage but never gate the build.
   - **Critic outage** (subagent dispatch fails or returns non-JSON): log `[SynthesisCritic] ⚠️  critic unavailable — subjective dims ungraded this commit` and proceed. Same WARN-only posture.
7. **Repeat sequentially** for each remaining implementer in this batch. Sequential by design — the pre-commit hook is the only serializer; implementers' parallel work landed on a clean working tree, but the commits themselves serialize through the hook.

## Concurrency contract

- Implementer side: writes to working tree, never to `.git/`. Returns `commit_subject` + `commit_body` + `files_changed` in envelope.
- Orchestrator side: reads `.git/` (status, log, diff) freely; writes to `.git/` (add, commit) only here, sequentially.
- Single writer = no race. Round-3's lost-commits issue is structurally prevented.

## Recovery from legacy implementer behavior

If you discover an implementer that ignored Hard rule 4 and called `git commit`: the working tree may show some files committed, others uncommitted. Run `git log -<N> --oneline | head` to enumerate the unexpected commits, then commit the remaining files with their owning implementer's metadata. Surface the rule-4 violation in Review-G so we can refine the implementer prompt for next run.
