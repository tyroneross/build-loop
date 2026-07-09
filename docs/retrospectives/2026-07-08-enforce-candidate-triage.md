# Enforce-candidate triage — 2026-07-08

**Headline: 9 DONE · 3 ADOPT · 0 REJECT · 1 DEFER** (13 candidates).

Classification pass only — no ADOPT items were implemented. Scope: the 13
auto-drafted enforce-candidates in `.build-loop/proposals/enforce-from-retro/`
(main checkout, gitignored runtime data), classified against current build-loop
code/docs in this worktree. A candidate is DONE only where a named control was
grep/read-confirmed to satisfy it today.

## Summary table

| Candidate | Verdict | Rationale (1 line) | Evidence |
|---|---|---|---|
| acceptance-probe-contract | DONE | Probe/baseline/boundary contract + Phase-1 classify + Phase-4 rerun gate + autonomy_gate deferral all shipped | `scripts/acceptance_probe.py`; `references/phase-1-assess.md:214`; `references/phase-4-review.md:134` |
| E1 · judgment_gate self-application | DONE | Gate is run-scoped (E3 fixed) and fires on build-loop's own runs — BLOCKING at Review-G + structurally at Stop | `scripts/judgment_gate.py`; `references/phase-4-review.md:274`; `scripts/stop_closeout.py:_run_gate` |
| f6 · structural closeout at run-close | DONE | Stop hook runs append_run + judgment_gate + writes retro/memory marker structurally (Option A) | `scripts/stop_closeout.py`; `hooks/closeout.sh:53`; `hooks/hooks.json` Stop |
| EC-01 rca · mandatory closeout artifacts | DONE | Retro auto-generated at SessionEnd (no run needed); memory-closeout surfaced by Stop marker — agent-recall dependency removed | `scripts/hooks/session_end_retro_sweep.py`; `scripts/stop_closeout.py:_write_marker` |
| EC-02 rca · countermeasure self-activation | ADOPT (S) | `plan_verify` activation-map rule blocks a *missing* `verified-live:` key but accepts `pending`; no brief `activation_proof`; no post-commit `verified-live:true` assertion | `scripts/plan_verify.py:1385-1414`; `scripts/brief_mece_validator.py` (no match) |
| EC-03 rca · worktree isolation pre-flight | ADOPT (S) | SessionStart surfaces peers but has no commit-collision WARN ("peer on same workdir → mint a worktree"); the lint only covers background *job configs*, not live interactive sessions | `hooks/session-start-rally-point.sh:3`; `scripts/worktree_isolation_lint.py` |
| EC-04 rca · experiment quality adjectives | DONE | Template carries a Blinding field, mandatory `n`, certainty, and an explicit "earn every adjective" rail forbidding unearned fair/blind/proven | `references/experiment-results-template.md` |
| session-0509e3ca-01 · independent-auditor gate | DONE | Auto-stub; independent-auditor already a wired Review-A gate + dispatch enforced by judgment_gate | `agents/independent-auditor` (definition); `references/phase-4-review.md:274` |
| session-0509e3ca-02 · inline-self-verification gate | DONE | Auto-stub; judgment_gate explicitly closes the inline-self-audit-masquerade hole | `scripts/judgment_gate.py`; `references/phase-4-review.md:274` |
| EC-01 coord · accruing triggers mining | DONE | SessionEnd auto-fires transcript-pattern-miner deterministically (never idles); Phase-6 accruing runs detector+consolidation; cross-session enforce signals | `scripts/hooks/session_end_retro_sweep.py`; `references/phase-6-learn.md:19,40` |
| EC-02 coord · venv isolation for bg runners | ADOPT (M) | `worktree_isolation_lint` checks cwd/notify-only isolation but NOT venv-pinned Python vs bare `python3`; live plist still bare | `scripts/worktree_isolation_lint.py` (no venv check) |
| EC-03 coord · verify-dispatch as Phase-5 default | DONE | Orchestrator mandates walking verify-dispatch after ANY dispatched agent claims commits/tests (broader than the ≥2 asked) | `agents/build-orchestrator.md:180`; `references/verify-dispatch.md` |
| EC-04 coord · orphan reaper on session start | DEFER | Claims-half auto-cleaned at SessionStart (`reaper.py --apply`); process-count dry-run + threshold warn not wired, and reaper was deliberately retired from physical deletion (rally-owned) | `hooks/session-start-rally-point.sh:43`; `scripts/rally_point/reaper.py:8` |

---

## Per-candidate detail

### acceptance-probe-contract — DONE
`scripts/acceptance_probe.py` implements the exact three-field contract
(`acceptance_probe` / `baseline` / `boundary`), the Phase-1 `classify`
(verifiable / unverifiable / invalid) and the Phase-4 `rerun` gate. A criterion
still at its baseline-failure state cannot be marked `passed` and cannot be
deferred inline — deferral routes through `autonomy_gate.py` as a DECISION
surface, matching item 2 of the proposal. Wired: `phase-1-assess.md:214-227`
(capture + `classify`) and `phase-4-review.md:134-149` (`rerun`, exit 1 blocks,
boundary discipline enforced). Colocated `scripts/test_acceptance_probe.py`
exists. All four "not yet enforced — needs" items are satisfied.

### E1 — judgment_gate self-application — DONE
The E3 prerequisite (stale top-level `triggers.riskSurfaceChange` latch) is
fixed: the module docstring and `evaluate(...run_id...)` scope stakes, statuses
and the ledger to the *current* run only, so no stale trigger can latch future
runs. Self-application is achieved two ways: (1) BLOCKING at Review-G
(`phase-4-review.md:274`) for any stakes-gated run, and self-modifying build-loop
runs are structurally stakes-gated; (2) `scripts/stop_closeout.py:_run_gate`
runs `judgment_gate.evaluate` at every Stop in a `.build-loop/` workdir — and
build-loop dogfoods `.build-loop/` on itself — so an inline self-modifying
session triggers the gate without a human prompt. The proposal's narrower
pre-push wiring was not added, but its two offered alternatives (pre-push OR
checklist) are superseded by the stronger Stop + Review-G coverage.

### f6 — structural closeout at run-close — DONE
`scripts/stop_closeout.py` is exactly proposal Option A (Stop-hook), wired via
`hooks/closeout.sh` (`hooks.json` Stop matcher). On every Stop it releases Rally
claims, records the run through `append_run` (Learn-visible `runs[]`), runs
`judgment_gate` (agent_tool_available=False), and — since a Stop hook cannot
dispatch agents — writes a `closeout-pending/<run-id>.md` marker plus a
`followup/judgment-owed-<run-id>.md` that Phase-5 Iterate drains. Honest scope
limit is documented in-file: it auto-records + auto-surfaces, it does not itself
make the Frontier judgment happen.

### EC-01 rca — mandatory closeout artifacts — DONE
The failure mode ("memory closeout + retrospective deferred until the user
prompts") is structurally closed. `scripts/hooks/session_end_retro_sweep.py`
(SessionEnd hook) auto-fires the deterministic `retrospective` synthesizer for
any non-trivial project session that never opened a run — the retro is now
*generated*, not merely checked-for. `stop_closeout.py`'s pending marker (surfaced
at the next SessionStart) carries the memory-closeout reminder. Note: the
proposal's literal "check existence of lessons/ entry + retro file → set
`closeout_incomplete` flag" is not implemented as a check, but the stronger
auto-generation + mechanical marker removes the agent-recall dependency that was
the actual defect. An existence-verification refinement remains optional.

### EC-02 rca — countermeasure self-activation — ADOPT (S)
Real residual gap. `scripts/plan_verify.py:1385` has the `activation-map-required`
rule, but it BLOCKs only when the `verified-live:` *key is missing* — it accepts
both `yes` and `pending` as valid values (`:1410-1414`). The proposal's core
complaint (`verified-live: pending` accepted as a terminal acceptance state) is
therefore unaddressed. `scripts/brief_mece_validator.py` has no `activation_proof`
field (grep-confirmed empty). `scripts/reference_activation_audit.py` audits
reference-doc *reachability*, not enforcement-mechanism `verified-live` state, so
it does not cover this. **Change:** (a) `plan_verify.py` — WARN when
`verified-live: pending`; (b) `brief_mece_validator.py` — require non-empty
`activation_proof` (not literal "pending") on enforcement briefs; (c) optional
post-commit re-run asserting `verified-live: true`. Target files named. Effort S.

### EC-03 rca — worktree isolation pre-flight — ADOPT (S)
`scripts/worktree_isolation_lint.py` guards *background job configs* (cwd hazard,
notify-only exemptions, `BUILD_LOOP_WORKTREE_ISOLATED`) — it does not cover a
live interactive multi-agent session sharing a checkout. `session-start-rally-
point.sh` surfaces peers but emits no commit-collision warning, and
`pre-edit-rally-point.sh` only re-joins presence + prints a revision hint. None
warn "peer <id> is active on this workdir; commits may land on the wrong branch —
mint a worktree." **Change:** add a fail-open peer-on-same-workdir WARN at
SessionStart (and/or first Edit/Write) in `hooks/session-start-rally-point.sh` /
`pre-edit-rally-point.sh`. Effort S.

### EC-04 rca — experiment quality adjectives — DONE
`references/experiment-results-template.md` carries a Blinding field
(`none | labels withheld | fully blinded`, with "if a tell leaked, say so"),
a mandatory `n = <N>` ("never omit"), a certainty rail, a threats-to-validity
line calling out weak blinding, and Usage-guide item 4 — an explicit rule
forbidding "fair / blind / robust / significant / proven" unless the Method
section earns it. The exact field name `blinding_conditions` differs, but the
substance (blinding conditions + n + directional/certainty + anti-overclaim lint
guidance) is present and stronger than proposed.

### session-0509e3ca-01 — independent-auditor gate — DONE
Content-free auto-stub from this session's own retrospective. independent-auditor
is already a wired Phase-4 Review-A agent, and its Frontier dispatch on
stakes-gated runs is enforced by `judgment_gate` (`phase-4-review.md:274`).
Nothing new to adopt.

### session-0509e3ca-02 — inline-self-verification gate — DONE
Content-free auto-stub. `scripts/judgment_gate.py` explicitly closes "the inline-
substitution hole — the same class as the inline self-audit masquerading as the
independent auditor." Already enforced.

### EC-01 coord — accruing triggers mining — DONE
`scripts/hooks/session_end_retro_sweep.py` runs the deterministic
`transcript-pattern-miner` (pure stdlib, no LLM) over the recent window on every
non-trivial session — the loop never idles on `accruing`. `phase-6-learn.md:40`
routes `accruing` (`runs[] < 3`) to "Detector + consolidation," and `:19` adds
cross-session enforce-signal recurrence. Intent (mine toward n≥3 rather than
treat accruing as terminal) met; stronger than the "only on accruing" trigger the
proposal asked for.

### EC-02 coord — venv isolation for background runners — ADOPT (M)
`scripts/worktree_isolation_lint.py` checks the cwd/worktree hazard and notify-only
exemptions but has no assertion that a launch program references a venv-pinned
Python rather than a bare `python3` (grep-confirmed no venv/interpreter check).
The F1 freeze (peer reinstalled `python@3.14`, killing the shared interpreter)
remains unguarded. **Change:** extend the lint to flag bare `python3` as a plist
`program`, then update the live launchd plist to a `.venv`-pinned path. Effort M;
the live-plist edit needs a staging pass before applying (per the proposal's own
"medium risk" note).

### EC-03 coord — verify-dispatch as Phase-5 default — DONE
`agents/build-orchestrator.md:180` makes walking `references/verify-dispatch.md`
(the 5-step git/test ground-truth checklist) mandatory after *any* dispatched
agent (incl. background/headless) claims commits landed + tests passed — broader
than the proposal's `subagent_count >= 2` condition. The skill exists and is
referenced from `skills/build-loop/SKILL.md`.

### EC-04 coord — orphan reaper on session start — DEFER
The stale-claims half of the problem (94 stale room claims) is auto-cleaned:
`hooks/session-start-rally-point.sh:43` runs `reaper.py --apply` (TTL-gated) at
SessionStart, and `session-start-worktree-gc.sh` prunes orphan worktrees. The
orphan-*process* half (27 codex/SkyComputerUseClient processes) is not wired as a
session-start dry-run count + threshold warning. Deferred, not adopted, because
`reaper.py` was deliberately retired from physical deletion — "a Python process
physically deleting coordination records the Rust binary owns is the exact
shadow-implementation Codex flagged as worse than no" (`reaper.py:8`). A
process-reaper-at-start needs a decision on whether the `rally` CLI exposes
`--reap-processes` and whether build-loop should own that surface at all. Low
priority now that the claims-half is handled.

---

## Next actions (prioritized — NOT implemented in this pass)

1. **EC-02 rca (S)** — `plan_verify.py`: WARN on `verified-live: pending`;
   `brief_mece_validator.py`: require non-empty `activation_proof` on enforcement
   briefs. Highest value: it's the self-referential "the dormancy rule was itself
   dormant" class, and the fix is a bounded lint change with colocated tests.
2. **EC-03 rca (S)** — add a fail-open peer-on-same-workdir commit-collision WARN
   at SessionStart / first Edit-Write in the rally-point hooks.
3. **EC-02 coord (M)** — extend `worktree_isolation_lint.py` to flag bare
   `python3` launch programs; update the live launchd plist to a `.venv`-pinned
   interpreter (stage before applying).
4. **EC-04 coord (DEFER)** — decide whether build-loop owns a session-start
   process-count warning given the rally-binary ownership constraint; if yes,
   add a dry-run `--reap-processes` count + threshold warn to the session
   preamble.

<!-- Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com> -->
