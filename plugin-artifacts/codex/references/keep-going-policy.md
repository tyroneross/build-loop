<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Keep going until done — do / branch / surface policy (full protocol)

Extracted from `agents/build-orchestrator.md` §"Keep going until done — do / branch / surface policy". The agent body keeps a tight summary + an explicit pointer here. Load before running any phase that may invite a "should I commit / continue / ask?" impulse.

## Core principle

Completed, validated, authorized work commits automatically. Asking "should I commit?" or "want me to commit this?" is a workflow violation — `scripts/autonomy_gate.py` classifies a plain `git commit` as `auto` (exit 0), so it is never a permission-gated action. The only commit-adjacent stops are autonomy-gate verdicts of `confirm` or `block` on a *push or deploy* command.

Once the user has accepted a plan, every phase is authorized scope. **The loop does not stop to ask — it stays on task and reports results in the end-of-run readback.** Exactly three things require human confirmation, nothing else:

1. **Production push** — a deploy/publish/migration that reaches live users (`scripts/deployment_policy.py` → `production: confirm`; common shapes: push to a protected branch that auto-deploys, `npm publish`, `gh release`, prod DB migration). Preview/testflight/unknown deploys are `auto`.
2. **Destructive delete** — archive is auto (reversible); the irreversible delete/purge confirms (`autonomy_gate.py` confirms `DROP TABLE/DATABASE`, `TRUNCATE`, remote-branch delete, `rm -rf <path>`). Prefer archiving (move to `archived/` or `git bundle`) over deleting; confirm before destroying source/data/user content. Build artifacts (node_modules, .next, dist, caches) are regenerable and may be removed freely.
3. **Major user-impacting decision** — a product/platform-direction choice the user would want to own (e.g. Android vs iOS, a user-facing product direction). The plan marks these `user_impact: major`; the orchestrator surfaces them. Implementation-tradeoff DECISIONs do NOT surface. **Autonomous/`--long` timeout:** a surfaced gate-#3 question auto-resolves to its `recommended_default` if unanswered within `autonomy.questionTimeoutMinutes` (default 10) — `scripts/question_timeout.py` returns `take_default`, the choice is logged to `autonomousDefaults[]` + captured as a DECISION, and listed in the readback. Gates #1–#2 (production push, irreversible delete) **never** time out — `question_timeout.py` holds them indefinitely (`production_hold: true`).

   **AskUserQuestion → decision capture (mandatory bridge)**: immediately after any `AskUserQuestion` resolves a steering choice (architecture direction, library/dependency choice, platform, product direction, license, or any `user_impact: major` decision), invoke `Skill("build-loop:auto-decision-capture")` — or directly `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_decision/__main__.py` — to append the answer as a DECISION record to `build-loop-memory/projects/<slug>/decisions/`. The root cause this closes: parent-session steering answers live only in context; they never reach `build-loop-memory`, so the next session starts without them. Every AskUserQuestion steering answer is a durable decision — append it immediately. Do not rely on in-context state or a NORTH_STAR file surviving the session. Systems-not-discipline: the gap is structural (no bridge existed), not an operator oversight.

Every action runs through `python3 scripts/classify_action.py` (**SAFE / RISKY / DECISION / PRODUCTION**). Mechanical response: SAFE → execute on main; RISKY → isolate to worktree-branch + log `riskyBranches[]` + continue main (not a human gate); DECISION → auto-pick `recommended_default` + log `autonomousDefaults[]` **always, regardless of mode or confidence** — UNLESS the decision is flagged `user_impact: major` (gate #3); PRODUCTION → confirm (gate #1). Catastrophic-never commands (`rm -rf /`, `rm -rf ~`, force-push to a protected branch) are auto-REFUSED by `autonomy_gate.py` blockFor — the loop logs and continues, it does not ask.

**There is NO gate for code size or complexity** — large or complex is never a reason to stop; decompose and execute, and always try to simplify. Genuine inability-to-proceed (missing credential, external blocker) is logged, worked around with other available work, and reported in the readback — not a stop-and-ask. Terminal conditions that END the run and report (not ask): 8h budget exhausted, 5 consecutive iterate failures. Full table + what is NOT a reason to surface in `references/do-branch-surface-policy.md`. **Operating doctrine for initiative + the decision-escalation ladder (decide-at-70%, self-research → memory → peers → relevant persona panel → human only for irreversible/major, pursue parallel work before idling, gauge token posture) lives in `references/leadership.md`** — consult it whenever a turn involves ambiguity, an unforced choice, or a "should I ask?" impulse.

Drain non-destructive open items via Sub-step F Auto-Resolve before the end-of-run report. One end-of-run report, not a checkpoint between every phase.

## Self-heal — reactive fix + proactive self-simplification (C-HEAL / self_heal_safe_issues)

Self-heal is **both reactive and proactive**. It is not only triggered by errors.

**Reactive arm:** when the orchestrator or any infra step encounters: (a) an error or crash from its own tooling, a hook, a script, a Bash command, or a build/test/lint failure (non-zero exit that is build-loop's own infrastructure rather than a graded target criterion); OR (b) a quality or performance issue surfaced by any Review sub-step, self-review, fact-check, simplify, or efficiency scan — ROOT-CAUSE and FIX it, then continue. Route: produce the fix, classify via `scripts/classify_action.py`. SAFE → apply, verify (re-run the failed action and relevant tests), commit, continue — no surface, no ask. RISKY → isolate to worktree-branch + log + continue main + surface in report. DECISION/PRODUCTION → surface/escalate.

**Proactive arm:** during deep self-review runs (and any build where `selfRecursive.enabled == true`), the self-review/self-heal loop ALSO proactively simplifies build-loop's own code to prevent issues, streamline work, and improve quality — reducing complexity, splitting oversized files, removing dead or duplicated logic, and adding missing tests. Driven by `self_review.py`'s `self_simplification[]` findings (deep mode, self-recursive). The proactive arm MAY also author new skills and new scripts when doing so prevents a class of issue; new scripts require a colocated `test_<name>.py`.

**MANDATORY SAFETY GATE for self-modifications:** any change to build-loop's own code (plugin repo or `build-loop-memory` durable repo) MUST pass the SELF-MODIFICATION SAFETY GATE before commit — `python3 scripts/self_mod_verify.py --scope auto --auto-revert` returns `verdict: pass`; on `verdict: fail` the gate auto-reverts and the change is not committed. Full gate protocol in `skills/build-loop/references/self-review.md` §"Self-modification of the restricted repo". Structural/architectural self-modifications (new phase, changed contract, agent-role change) surface as DECISION, never auto-apply. **Self-modifications execute — they do not stop the loop.** A self-modification that is part of the accepted plan (including edits to the gate, tests, or the self-improvement loop) executes behind the test-suite gate. Build-loop never halts a planned self-modification for human approval. Oversight is post-hoc: (a) self-modifying runs trigger an ADDITIONAL adversarial review (independent-auditor at build scope; the periodic deep self-review re-audits recent self-modifications) — non-blocking; (b) the end-of-run readback reports every self-modification and the additional-review findings. The loop stays on task and reports once, at the end.

**Banned anti-pattern:** bypassing a fixable infra or quality error and continuing — `--no-verify`, skipping/xfail-ing a test, commenting out failing code, `|| true` on a real failure — when a SAFE root-cause fix exists. A workaround is allowed ONLY when the fix classifies RISKY/DECISION/PRODUCTION or is genuinely infeasible (missing credential, external blocker); record BOTH the workaround and the surfaced issue in the report. ("Attack over defense" + "always the durable fix.")

**Guardrails:** only SAFE auto-applies. Verify after every auto-fix. A fix that fails verification routes to the existing Iterate / stuck-cascade. Existing iterate caps provide loop-protection. The autonomy gate (`scripts/autonomy_gate.py`) is the single source of truth for SAFE vs gated for target-project work; `self_mod_verify.py` is the MANDATORY additional gate for self-modifications of build-loop's own repo.

## Flagged-issue default route — disposition by WHERE the issue lives (C-HEAL/flagged_issue_default_route)

**No run ends with an issue merely flagged plus a "want me to?" question.** When work surfaces an issue — incidentally or on purpose, at any phase — the DEFAULT action is set by WHERE the issue lives. This extends C-HEAL (which handles build-loop's own repo) to cover findings in OTHER repos, which previously leaked out as prose flags in the report.

- **build-loop's OWN repo** (plugin repo or `build-loop-memory`) → **execute the fix** (C-HEAL self-heal / Iterate), behind the self-mod safety gate. Do not file, do not ask.
- **Any OTHER repo** → **file a task on the Operations Center queue** — the single shared board is the queue of record, so do NOT invent a build-loop-side cross-repo tracker. File it MECHANICALLY, not as prose: `python3 scripts/file_to_operations_center.py --repo <repo> --title <one-line> --spec <description + fix hint> --urgency <low|normal|high|critical> --json`. Urgency → priority (critical=P0 … low=P3). The helper shells out to the Operations Center CLI's documented `add` subcommand and returns a task-id receipt; it NEVER writes the sqlite store directly. On a missing/unbuildable binary it returns `filed:false` + exit 1 — surface THAT blocker as its own finding (do not drop the issue, do not hand-edit the db).
- **PRODUCTION-class or genuinely ambiguous** (irreversible, user-trust, product-direction, or the correct repo/action is unclear) → **surface to the user** per the three gates above. Ambiguity about routing is itself a reason to surface, not to guess.

The report records the disposition of every open finding (own-repo fix commit, cross-repo Operations Center task id, or surfaced), never a bare flag. Binding citation: CC memory `feedback_flagged_issue_default_route.md`; Report-contract enforcement in `references/phase-4-review.md` §"Sub-step G".

## Root cause before done — mandatory investigation + second-subagent verification (C-RCA / root_cause_before_done)

**Investigate every open issue to root cause before declaring done — verified by a second subagent.** Before any "done"/completion claim, investigate EVERY open issue — failed tests, loose ends, errors, warnings, minor issues — none are left unaddressed. For each, reach the ROOT CAUSE, not a surface patch.

**Guidance (how — operator's choice):** use the debugging skills (`build-loop:debug-loop` / `root-cause-investigator` / `systematic-debugging`) and/or a detailed **5-whys / causal-tree** analysis to determine the true cause AND how far it spans (does the same root cause affect other sites? — fix all of them, not just the reported one).

**Binding rule (non-negotiable):** the fix MUST address the root cause — a surface/symptom patch is a violation — AND MUST be **verified by another, independent subagent** (confirms the root cause was correctly identified, the fix resolves it, and introduces no regression) before "done." The investigation-before-done and the second-subagent verification are mandatory; the specific technique is the operator's choice.

This rule fires before any completion/report claim and before "done" on any chunk — it is a gate, not an advisory. The second-subagent verification reuses existing surfaces (`independent-auditor` at build scope, `fix-critique`, or a dispatched verifier); no new agent is introduced. C-RCA pairs with C-HEAL (which reactively fixes SAFE errors) — C-HEAL handles what to do when an error surfaces; C-RCA mandates that the root cause is known, the fix is durable, and a peer has confirmed both before the run closes. It also enforces the standing "attack over defense / always the durable fix / fix everything" preferences as a gate before completion.

## Follow-up auto-drain (chunk boundaries are not checkpoints)

Before emitting any final report, scan its draft for prose patterns matching `still( on the| to do| open)|deferred|next pass|will sweep|skip( these)? for now|follow.?up( list)?:|to follow up`. For each item under such a heading, write a queue entry to `.build-loop/followup/<run-id>-<NN>-<slug>.md` (NN = zero-padded ordinal) with frontmatter:

```yaml
intent_anchor: <path-or-section in intent.md the item maps to>
parent_run: <this run id>
shape: <same-shape | adjacent>
classify: <SAFE | RISKY | DECISION | PRODUCTION>   # from scripts/classify_action.py
```

Items classified `PRODUCTION` move to `.build-loop/followup/needs-confirm/` and are surfaced ONCE in the report. Everything else stays in the queue. Strip the prose follow-up section from the report; it is now the queue's job.

**Capture-time product-impact triage (mandatory at every descope point)**: before classifying each deferral as a generic followup, run `python3 -c "import sys; sys.path.insert(0,'scripts'); from backlog.triage import classify; import json; print(json.dumps(classify(sys.stdin.read())))" <<< "$DEFERRAL_TEXT"` (or invoke `backlog.triage.classify` from Python). When `product_impacting: true`, render the item with `python3 -c "from backlog.assess import build_item; ..."` (or `backlog.assess.build_item`) and write to `.build-loop/backlog/<repo>/<id>-<slug>.md` instead of `followup/`. The backlog item carries `product_impacting: true` + `impact: <one-line>` + a `## Why it matters (causal tree)` section. Non-product-impacting deferrals continue to `followup/` as today. Both paths still mirror into the active task list per the rule above.

**Mirror every captured follow-up item into the ACTIVE TASK LIST (mandatory).** For each item written to `.build-loop/followup/` (and each `needs-confirm/` item), also call `TaskCreate` so the deferred work is visible in the user-facing active task list — appended to the BACK of the list (after all current in-progress/pending work), never ahead of active work. Use: `subject` = the item's headline; `description` = the deferral reason + `intent_anchor` + `classify` + the queue-file path + (if blocked) the blocker. Tag `metadata.source = "build-loop:followup"` and `metadata.queue_file = <path>` so the task and its queue entry stay linked. This applies to deferrals captured at ANY point in the run (not only the final report scan): the moment work is descoped, blocked, or pushed to "next pass", create both the queue file AND the back-of-list task in the same step — deferred scope is never silently dropped or buried in queue files. The subsequent Phase 5 queue-drain marks the mirrored task `completed` (via `TaskUpdate`) as it drains each item.

After the report is committed, enter a fresh Phase 5 iterate cycle to drain the queue using the same alignment-checker + scope-auditor + independent-auditor wiring as the in-run iterate loop. Stop conditions match Phase 5 — iterate-cap (25 autonomous / 5 classic), budget exhausted, PRODUCTION encounter, intent_anchor that does not resolve in current `intent.md` (escalate as DECISION), 5 consecutive iterate failures, or explicit user pause.

C-FLOW/followup_auto_drain and C-FLOW/no_ask_at_chunk_boundary in `<memory-root>/constitution.md` (or the template if not yet adopted) are the binding citations. Asking the user "want me to continue with the rest?" at a chunk boundary, when the items are same-shape and same-intent, is a workflow violation — return the queue-drain answer, not the question.

## End-of-run continuation into issues + backlog (preference-gated)

After the followup drain above completes (or is skipped when `.build-loop/followup/` is empty), run the preference gate:

```python
# scripts/context_bootstrap.py — run from workdir
should_continue = should_continue_into_queues(workdir)   # SHIPPED DEFAULT (2026-06-04): unset → True
pending        = pending_queue_items(workdir)             # {"issues": N, "backlog": M}
```

**Only proceed when BOTH are true:** `should_continue is True` AND `pending["issues"] + pending["backlog"] > 0`.

If either condition is false, the run ends here. Do NOT ask the user again. **SHIPPED DEFAULT (2026-06-04)**: an unset preference (`source == "default"`) now returns `True`, so every build-loop run auto-drains its backlog/issues at end-of-thread. Existing explicit answers are still respected: `"always"` → True, `"never"` → False (the per-repo opt-out — set in `.build-loop/config.json`'s `sessionPrefs.continueFromQueues` or via `write_session_prefs(workdir, "never")`), `"ask"` (explicit) → False (legacy opt-in path; respected when the user explicitly answered). The gate enforcing this lives in `should_continue_into_queues` (`scripts/context_bootstrap.py`).

**When both conditions are met**, enter one additional Phase 5 iterate cycle targeting `.build-loop/issues/` then `.build-loop/backlog/` (issues first — active problems before deferred-wants). Use the IDENTICAL iterate machinery as the followup drain above:

- alignment-checker per item against current `intent.md`
- scope-auditor on proposed changes
- independent-auditor post-fix
- same iterate-cap (25 autonomous / 5 classic), budget check, halt sentinel, stop conditions

Items classified `PRODUCTION` or `DECISION` by `scripts/classify_action.py` → **SURFACE in report, do not auto-execute** (same rule as the main loop). Items classified `SAFE` → execute autonomously. Items classified `RISKY` → isolate to worktree-branch + continue.

User instructions given during the session always take priority over this continuation. If the user pauses or issues a new instruction mid-drain, honour it immediately.

The report's `## Queue continuation` section (added only when this path ran) lists: items processed, items deferred, items surfaced, and the stop reason.
