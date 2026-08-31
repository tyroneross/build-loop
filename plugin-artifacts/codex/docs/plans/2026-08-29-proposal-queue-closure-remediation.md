# Remediation Plan — Proposal-Queue Closure Failure (2026-08-29)

Instrument note (verified on disk before planning, per the brief's directive): the evidence line "0 in archive/" is wrong as stated — `~/dev/git-folder/build-loop/.build-loop/proposals/archive/` holds **1,146 files** in daily-stamped subdirectories (20260818T160001Z … 20260825T160003Z and onward), written by an existing machine consumer, `scripts/drain_self_review_proposals.py` (dedupe → revalidate → filter → route, `--archive` never deletes). Top-level open count on disk is 614 (2 written since the 2026-08-29T19:27Z capture); composition: 500 `self-review-*`, 111 `auto-finding-*`, 1 misc, plus 38 in `enforce-from-retro/`. The oldest open file (`self-review-2026-05-31-05-user-correction-cluster-recurring-user-c.md`, May 31) was **re-emitted under the same slug on 2026-08-29** — the producer re-detects daily; the consumer archives superseded copies and keeps the newest open. Baselines below still use the brief's numbers as directed; commands are defined precisely so the ambiguity cannot recur.

Also verified: the working counterexample is `~/.claude/scripts/needs-attention.sh` (writer: one marker per detected failure, body carries a suggested fix and the literal one-command close `mv <marker> …/resolved/`) + `~/.claude/scripts/session-start-surface-markers.sh` wired as a SessionStart hook in `~/.claude/settings.json` (injects full marker text into every session). There is **no automated mover to resolved/** — the 73→2 drain was agent sessions fixing and `mv`-ing, because the hook binds the next session as the actor and closure is one command. A weekly LaunchAgent already exists for proposals (`~/Library/LaunchAgents/ai.rosslabs.proposal-drain.plist`, Mon 09:00 → `drain_proposals.py scan --notify`) but it is advisory by design ("NEVER auto-applies", per-item human `set` required).

## VERDICT

The primary root cause is (a): proposals are written with no bound actor — machine dedupe/revalidation already runs daily (1,146 archived files falsify "closure is undefined"), so validated findings wait on a human with ~2 h/week against ~15 machine writes/day, and the queue mathematically cannot drain. The needs-attention queue drains because a SessionStart hook binds the next agent session as the actor and closure is one command (`mv → resolved/`). The single highest-leverage fix is to port that binding: reversibly TTL-archive the stale tail, stop re-emission file growth at write time via a finding-key gate, and deliver a bounded working set (≤10 items/week to the human digest, top-3 into sessions at SessionStart) with drift breaches routed into the proven needs-attention channel.

## ANALYSIS

### Q1 — ROOT CAUSE: why does proposals/ never drain while needs-attention/ does?

Candidates (4), with rejections:

**(a) No owner/actor is assigned at write time — SELECTED as primary.**
Evidence: `drain_proposals.py` docstring encodes the design: "NEVER auto-applies… `set` records a human decision"; `candidate_aging.py` docstring: "it flags, nothing more." The sole closer is a human budgeted at ~2 h/week (constraint 6) against producers running twice daily (`com.tyroneross.buildloop.selfreview-light/-deep` LaunchAgents; 500 self-review files since May 31 ≈ 5.5/day in build-loop alone). The counterexample's only structural difference on the closure side is actor binding: SessionStart injects markers into the next agent session with a one-command close, and 73→2 followed. Decisive against (c): machine closure automation for proposals **already exists and runs** (drain_self_review_proposals.py, daily archive stamps, 1,146 files moved) and the open set still sits at 612 — automation without an actor for the validated survivors does not drain the queue.
**Falsifier:** after S5–S11 bind actors and bound the working set, if `proposals_open_buildloop` has not fallen below 75 within 30 days *even though digest items are being dispositioned* (decisions recorded in `drain-state.json`), (a) was wrong — the block is elsewhere (likely (d), generation outrunning any closure).

**(b) No expiry or cap makes staleness visible — REJECTED as primary, adopted as mechanism.**
Expiry is one machine-executable closure predicate; adding it drains the stale tail (~24% of queued items are stale by prior measurement — memory: `feedback_queue_items_go_stale_revalidate_at_surface`) but does nothing for validated still-true survivors (e.g. "missing test for X" stays true until fixed) and does not stop the 4× re-draft loop. Expiry without an actor silently discards true findings; it is a component of the fix (S4/S5), not the cause.

**(c) The closure action is undefined so there is nothing to automate — REJECTED.**
Empirically falsified on disk: dedupe/revalidate/filter/route + `--archive` exist and run (1,146 archived files, daily stamps). What is undefined is only the *disposition of validated survivors* — and that is precisely "no actor," i.e. (a). (c) mis-describes the system as it exists on 2026-08-29.

**(d) Generation rate exceeds any plausible triage capacity; throttling is missing — REJECTED as primary, adopted as mechanism.**
Real (111 `auto-finding-*` files landed in one batch, timestamp 20260815T235257Z; re-emission measured at 2.2× overall / 5.9× on missing-test findings per drain_self_review_proposals.py) — but needs-attention also has automated writers and drains fine. The difference is closure, not writing. Adopted as the write-time gate (S2/S3) because re-emissions as *new files* inflate the queue the actor must face.

### Q2 — INTERVENTION POINT: write, sweep, or close?

Ordering: **SWEEP first → WRITE gate second → CLOSE-time delivery third.** All three are needed; single-point options rejected:

- **Write-time only — REJECTED.** Stops refill and the 4×-duplicate-skill loop (dedup at write is where duplicates are cheapest to catch) but leaves 612 aged files and 2,830 cross-repo untouched forever.
- **Sweep only — REJECTED.** Clears the stock, but producers emit ~5.5 new files/day in build-loop alone and the same finding re-emits daily under a fresh timestamp; the queue refills within weeks, and duplicate experimental skills keep spawning.
- **Close-time only (run-close / alignment-checker drain) — REJECTED.** The autonomous-loop drain only fires inside active build-loop runs; most of the 104 repos are idle for weeks, so 2,830 files across them never meet a run. It survives as the third leg (session/digest delivery, S8/S11), not the first.

Sweep goes first because it is immediate, fully reversible (manifest + tar, constraint 3), restores a near-zero baseline the drift instrumentation needs, and unblocks a meaningful threshold for `proposals_open`. The write gate goes second so the swept state does not refill while the delivery loop is being built. Close-time delivery goes third because it only works on a bounded working set — injecting 612 items into a session is the anti-pattern; injecting 3 is the counterexample.

### Q3 — THE TOOLING/PRODUCT RATIO (59% of commits to machinery)

Candidates (3):

- **(i) This plan caps or redirects tooling commits — REJECTED.** No evidence line establishes the ratio as a cause of the measured failure; the failure is disposition, not volume. A 59% tooling share during the period the pipeline itself was being built may be deliberate investment; declaring it wrong requires product-outcome data this plan does not have. Per constraint 5, a cap here would be scope creep, and per the brief, allocation is a strategy call entangled with the human's launch plan and day-job constraints.
- **(ii) Ignore the ratio entirely — REJECTED.** The brief demands a decision, and the ratio is the cheapest *lagging* indicator of whether the machinery stops consuming attention once closure works. Some of the tooling spend is directly forced by this failure (the same closeout defect was re-drafted four times — that is tooling churn caused by missing dedup); if the plan works, the forced fraction should fall, and only a measured ratio shows it.
- **(iii) Instrument only; the human owns the decision — SELECTED.** `tooling_commit_share_90d` is computed weekly with no threshold and no breach action (see DRIFT_INSTRUMENTATION, M9). The human reviews it monthly (~10 min, inside the 2 h/week budget). This plan does not touch allocation.

## PLAN

All new files are deterministic stdlib-Python/bash scripts — extensions of the existing gate family (`report_lint.py`, `plan_verify.py`, `audit_before_commit.py`, `attestation_lint.py`, `frontier_gate.py`, pre-push-ci-gate; evidence: "Deterministic gates that already exist and work — the pattern to extend"). No LLM calls, no new agent, skill, plugin, or build-loop phase (constraint 2 satisfied — nothing generative is added; one generator behavior is *reduced*: re-emissions stop creating new files). `BL=$HOME/dev/git-folder/build-loop` throughout. Owner legend: `agent` = the executing Opus orchestrator; `cron` = launchd; `human` = Tyrone.

---

**S1 — Freeze and back up every proposals directory before anything moves.**
- action: create restorable tarballs (constraint 3 groundwork).
- command:
  ```bash
  TS=20260829; mkdir -p "$HOME/.local/state/build-loop-drain/backups"
  tar -czf "$HOME/.local/state/build-loop-drain/backups/proposals-buildloop-$TS.tar.gz" -C "$BL/.build-loop" proposals
  find "$HOME/dev/git-folder" -maxdepth 6 -type d -path '*/.build-loop/proposals' -print0 \
    | tar -czf "$HOME/.local/state/build-loop-drain/backups/proposals-allrepos-$TS.tar.gz" --null -T -
  ```
- owner: agent
- acceptance: `test $(tar -tzf "$HOME/.local/state/build-loop-drain/backups/proposals-buildloop-20260829.tar.gz" | grep -c '\.md$') -ge 600`
- depends_on: —
- reversal: n/a (read-only)
- evidence: "612 open .md files… 2,830 proposals total"; constraint 3.

**S2 — Author the write-time gate `$BL/scripts/proposal_gate.py`.**
- action: stdlib CLI + importable module. `gate --dir <proposals_dir> --title T --body-file F` → exit 0 allow (stamps frontmatter `finding_key:` = sha256(normalized title + top body tokens)[:16], `expires:` = +45d, `last_seen:`); exit 3 duplicate (an open file with the same `finding_key` exists → update that file's `last_seen:` in place, write **no new file**); exit 4 cap exceeded (open count ≥ 50 → append one JSON line to `<dir>/.suppressed.jsonl` instead of a file). Includes `--self-test` running the seeded cases in a temp dir.
- owner: agent
- acceptance: `python3 "$BL/scripts/proposal_gate.py" --self-test` → exit 0
- depends_on: —
- reversal: n/a (new file)
- evidence: "re-detected the same defect and re-drafted a fix four times"; "107 carry an 202608 datestamp"; drain_self_review_proposals.py's measured 2.2×/5.9× re-emission.

**S3 — Route every producer through the gate.**
- action: edit each write site to call `proposal_gate` before creating a file: (1) `$BL/scripts/hooks/session_end_retro_sweep.py` (enforce-from-retro write, ~line 383); (2) the self-review emitter invoked by `com.tyroneross.buildloop.selfreview-light/-deep` (locate with `grep -rln 'proposals/self-review\|self-review-2026' "$BL/scripts" "$BL/skills"` and by reading the two plists' command strings); (3) the auto-finding capture route (`$BL/skills/auto-finding-capture/SKILL.md` line 66 routes unrecognized-severity findings to proposals/ — its helper script is the write site; locate with `grep -rln 'auto-finding-' "$BL/scripts"` and the skill's referenced CLI). Re-emission of a known key updates `last_seen:` in place.
- owner: agent
- acceptance: per writer, `grep -q proposal_gate <writer_file>`; plus the seeded end-to-end case in SEEDED_VALIDATION §2 (duplicate write through each real writer's CLI path creates no new file, gate exit 3).
- depends_on: S2
- reversal: `git -C "$BL" revert <commit>` (writer edits land as one commit)
- evidence: "a self-improvement pipeline generates candidate work items and almost nothing disposes of them"; on-disk composition 500 self-review + 111 auto-finding + 38 enforce-from-retro.

**S4 — Author the unified sweep `$BL/scripts/proposal_ttl_sweep.py`.**
- action: extends (wraps, does not replace) `drain_self_review_proposals.py`'s four stages to cover ALL populations (`self-review-*`, `auto-finding-*`, `enforce-from-retro/` using `candidate_aging.py`'s disposition rules, misc `*.md`). Adds: TTL routing — open, non-terminal-status files past `expires:` (or mtime age > 45d when unstamped) move to `proposals/archive/<YYYYMM>/` with a row in `proposals/archive/manifest.jsonl` `{src,dst,ts,reason,finding_key}`; computes `finding_key` on the fly for unstamped files; `--restore --manifest M [--since TS]` replays rows in reverse (the reversal command); `--report-skill-dups` emits clusters of experimental skills whose name-token overlap ≥ 0.6; **resurrection logging** — when the gate later suppresses a key that exists in archive/, the sweep counts it (`resurrections_7d` in its JSON report). Dry-run by default; `--apply` to act; `--all-repos` iterates `build-loop-memory/registry/registry.json` `repos[].path` (same registry `drain_proposals.py` uses).
- owner: agent
- acceptance: `python3 "$BL/scripts/proposal_ttl_sweep.py" --self-test` → exit 0 (temp-dir seeded cases, SEEDED_VALIDATION §3)
- depends_on: S2 (shares the finding_key function)
- reversal: n/a (new file)
- evidence: "Oldest 2026-05-31"; counterexample "drained by a scheduled sweep"; "the pattern to extend, not replace".

**S5 — Execute the sweep on build-loop (moves >100 files — reversal stated).**
- action/command: `python3 "$BL/scripts/proposal_ttl_sweep.py" --repo "$BL" --apply`
- owner: agent (first run), then cron (S10)
- acceptance: `test $(find "$BL/.build-loop/proposals" -name '*.md' -type f -not -path '*/archive/*' | wc -l) -le 50`
- depends_on: S1, S4
- reversal: `python3 "$BL/scripts/proposal_ttl_sweep.py" --restore --manifest "$BL/.build-loop/proposals/archive/manifest.jsonl" --since <apply-ts>`; belt-and-braces: untar S1's `proposals-buildloop-20260829.tar.gz` over `.build-loop/` .
- evidence: "612 open… Oldest 2026-05-31. 107 carry an 202608 datestamp."

**S6 — Execute the sweep across all registered repos (moves >100 files — reversal stated).**
- command: `python3 "$BL/scripts/proposal_ttl_sweep.py" --all-repos --apply`
- owner: agent (first run), then cron (S10)
- acceptance: `test $(find "$HOME/dev/git-folder" -maxdepth 6 -path '*/.build-loop/proposals/*' -name '*.md' -type f -not -path '*/archive/*' | wc -l) -le 500`
- depends_on: S5 (build-loop run validates behavior before fan-out)
- reversal: per-repo `--restore --manifest <repo>/.build-loop/proposals/archive/manifest.jsonl --since <apply-ts>`; belt-and-braces: S1's all-repos tarball.
- evidence: "All 104 repos… 2,830 proposals total (1,792 in build-loop alone)."

**S7 — Consolidate the 4 duplicate experimental closeout skills.**
- action: locate the four (`run-closeout-completeness`, `recover-crash-orphaned-build-loop-runs`, `reconcile-crash-orphan-closeout`, `orderly-closeout-guard`) via `find "$HOME/dev/git-folder" -path '*skills/experimental/*' -name 'SKILL.md'`; keep the newest by mtime, move the other three directories to a sibling `experimental/superseded/`, appending `superseded_by: <kept-name>` to each moved SKILL.md frontmatter. Future clusters are caught deterministically by S4's `--report-skill-dups` in the weekly run — flag-and-supersede, no LLM judgment. Promotion of the survivor is a human decision: it rides the weekly digest (S8) as one decision item, not an automatic act.
- owner: agent
- acceptance: `test $(python3 "$BL/scripts/proposal_ttl_sweep.py" --report-skill-dups --json | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["clusters"]))') -eq 0`
- depends_on: S4
- reversal: `git mv` back (<100 files; still trivially reversible)
- evidence: "7 drafted, 0 promoted… 4 of the 7 are independent attempts at ONE problem."

**S8 — Cap the human digest at 10 decision items.**
- action: edit `$BL/scripts/drain_proposals.py` `scan` to sort new items by nearest `expires:` and emit at most 10 into the digest/notification; a single trailing line reports total open + total auto-expiring this week. Everything not surfaced auto-expires via S4 — nothing waits on the human.
- owner: agent
- acceptance: `python3 "$BL/scripts/drain_proposals.py" scan && test $(python3 "$BL/scripts/drain_proposals.py" list | grep -c '^\[new\]') -le 10` (agent adapts the grep to the digest's actual line format after reading it — `scan` writes JSON + markdown to the state dir per its docstring; use the JSON count if cleaner).
- depends_on: S4 (expires: stamps)
- reversal: `git -C "$BL" revert <commit>`
- evidence: constraint 6 (2 h/week); "Retrospective volume (accelerating): 165 files. Jun 13, Jul 39, Aug 111."

**S9 — Author `$BL/scripts/metrics_snapshot.py` (snapshot + drift check + breach action).**
- action: computes every metric in DRIFT_INSTRUMENTATION, writes `~/.local/state/build-loop-drain/metrics/latest.json` (schema below) plus `history/<YYYYMMDD>.json`; `check` compares each value to its **absolute threshold** (never to the prior snapshot — re-baseline lesson: append-only comparison makes fixed defects permanent) and exits 0 (clean) / 2 (breach). On breach it runs, per breached metric, after verifying no open marker already names that metric this week:
  `bash "$HOME/.claude/scripts/needs-attention.sh" "drift-<metric>" "<metric>=<value> breached threshold <threshold>. Remediate: <per-metric hint incl. sweep/restore command>"`
  — breaches feed the queue that demonstrably drains, and the SessionStart hook already injects them.
- owner: agent (author), cron (run)
- acceptance: `python3 "$BL/scripts/metrics_snapshot.py" --self-test` → exit 0 (SEEDED_VALIDATION §4–5)
- depends_on: S4 (uses its JSON report for resurrections/dup-clusters)
- reversal: n/a (new file)
- evidence: counterexample lines ("73 open/104 resolved → 2/176… working closure mechanism… prefer porting that mechanism"); baseline.json.

**S10 — Rewire the existing weekly LaunchAgent to sweep → digest → check.**
- action: edit the `-lc` command string in `~/Library/LaunchAgents/ai.rosslabs.proposal-drain.plist` to:
  `python3 "$HOME/dev/git-folder/build-loop/scripts/proposal_ttl_sweep.py" --all-repos --apply && python3 "$HOME/dev/git-folder/build-loop/scripts/drain_proposals.py" scan --notify; python3 "$HOME/dev/git-folder/build-loop/scripts/metrics_snapshot.py" check`
  then `launchctl bootout gui/$(id -u)/ai.rosslabs.proposal-drain 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.rosslabs.proposal-drain.plist`. Keep Mon 09:00. (Amends the existing job rather than adding sprawl; note the plist's `Program` key points at a RossLabs Background-Item launcher wrapper — edit only the `-lc` string, leave the wrapper intact.)
- owner: agent (edit), cron (recurring)
- acceptance: `launchctl kickstart -k gui/$(id -u)/ai.rosslabs.proposal-drain && sleep 20 && grep -q "ttl_sweep" "$HOME/.local/state/build-loop-drain/launchd.log" && test -f "$HOME/.local/state/build-loop-drain/metrics/latest.json"`
- depends_on: S4, S8, S9
- reversal: restore the plist from git/Time Machine or re-paste the original `-lc` string (recorded in the S10 commit message); `launchctl bootout` + `bootstrap` again.
- evidence: counterexample "drained by a scheduled sweep"; existing `ai.rosslabs.proposal-drain.plist` (verified on disk).

**S11 — Port the SessionStart binding: bounded working set + watchdog line.**
- action: add a sibling hook script `~/.claude/scripts/session-start-proposal-pressure.sh` (registered in `~/.claude/settings.json` SessionStart next to `session-start-surface-markers.sh` — do not overload the marker script; markers stay their own channel). It prints at most: (1) one line if `proposals_open_buildloop > 75`; (2) one line if `metrics/latest.json` mtime > 8 days (weekly job silently dead — independent watchdog channel); (3) the top-3 open proposals by severity-then-age **only when cwd is inside a registered repo**, each with its literal close command (`python3 …/drain_proposals.py set --key <K> --status …`). Never more than ~10 lines total — 612-item injection is the anti-pattern; 3 is the counterexample.
- owner: agent
- acceptance: `PROPOSAL_PRESSURE_TEST_DIR=<tmp with 80 stub files> bash ~/.claude/scripts/session-start-proposal-pressure.sh | grep -q "proposal pressure"` → exit 0; and with a stale stub snapshot, `… | grep -q "metrics snapshot is stale"` → exit 0.
- depends_on: S9
- reversal: remove the hook entry + script.
- evidence: counterexample mechanism (SessionStart surfacing verified in `~/.claude/settings.json`); "2 open / 176 resolved".

**S12 — Human weekly ritual (the only recurring human step).**
- action: Monday: read the ≤10-item digest, record decisions (`drain_proposals.py set --key K --status applied|rejected|deferred`), including any surviving-skill promotion card from S7. Monthly: glance at `tooling_commit_share_90d` in `metrics/latest.json` (Q3 — the human owns that decision; the plan only measures).
- owner: human — budget ≈ 30–40 min/week + 10 min/month, well under the 2 h/week ceiling (constraint 6). One-time: ~20 min reviewing the first sweep's digest after S5/S6.
- acceptance: non-performance is caught by machine, not by memory: undecided digest items simply expire (S4), and `proposals_open_allrepos` (M2) breaching fires a needs-attention marker (S9). Spot command: `python3 "$BL/scripts/drain_proposals.py" list | head -20`.
- depends_on: S8, S10
- reversal: n/a
- evidence: constraint 6; drain_proposals.py's decision model (`set`).

**Dependency spine / what unblocks what:** S1 unblocks S5. S2 unblocks S3. S4 unblocks S5→S6 (stock cleared), S7, S8, S9. S9 unblocks S10 and S11 (automation + watchdog). S5/S6 unblock meaningful thresholds for M1/M2. S10 makes the whole loop run without anyone remembering anything (constraint 1). Suggested execution order: S1, S2, S4, S3, S5, S6, S7, S8, S9, S10, S11, S12.

## DRIFT_INSTRUMENTATION

Snapshot file: `~/.local/state/build-loop-drain/metrics/latest.json` (+ dated copies in `history/`). Comparison is always value-vs-absolute-threshold, never vs the previous snapshot. Cadence: weekly (Mon 09:00, S10 job) for all metrics; M5 and snapshot-staleness additionally at every SessionStart (S11). Breach action for every thresholded metric (one command, deduped to one open marker per metric per week):

```bash
bash "$HOME/.claude/scripts/needs-attention.sh" "drift-<metric>" \
  "<metric>=<value> breached <threshold>. Run: python3 $HOME/dev/git-folder/build-loop/scripts/proposal_ttl_sweep.py --all-repos --apply  (restore: --restore --manifest <repo>/.build-loop/proposals/archive/manifest.jsonl)"
```

Machine-readable schema (emitted by `metrics_snapshot.py`, compared by its `check` subcommand):

```json
{
  "schema_version": 1,
  "captured_at": "2026-08-29T00:00:00Z",
  "metrics": {
    "<name>": {
      "value": 0,
      "baseline_20260829": 0,
      "threshold": 0,
      "direction": "max",
      "kind": "leading|lagging",
      "breach": false,
      "breach_action": "needs-attention|none"
    }
  }
}
```

| # | metric | exact command | baseline 2026-08-29 | drift threshold | kind |
|---|--------|---------------|--------------------:|----------------|------|
| M1 | `proposals_open_buildloop` | `find "$HOME/dev/git-folder/build-loop/.build-loop/proposals" -name '*.md' -type f -not -path '*/archive/*' \| wc -l` | 650 (612 top-level + 38 enforce-from-retro) | > 75 after S5 | leading |
| M2 | `proposals_open_allrepos` | `find "$HOME/dev/git-folder" -maxdepth 6 -path '*/.build-loop/proposals/*' -name '*.md' -type f -not -path '*/archive/*' \| wc -l` | 2,830 | > 500 after S6 | leading |
| M3 | `proposals_oldest_open_age_days` | `find "$HOME/dev/git-folder/build-loop/.build-loop/proposals" -name '*.md' -type f -not -path '*/archive/*' -print0 \| xargs -0 stat -f %m \| sort -n \| head -1 \| xargs -I{} python3 -c "import time;print(int((time.time()-{})//86400))"` | 90 (oldest 2026-05-31) | > 45 after S5 | leading |
| M4 | `duplicate_open_finding_keys` | `grep -rh '^finding_key:' "$HOME/dev/git-folder/build-loop/.build-loop/proposals" --include='*.md' \| sort \| uniq -d \| wc -l` | n/a (keys introduced by S2; 0 by construction after S3) | > 0 | leading |
| M5 | `needs_attention_open` | `find "$HOME/.claude/cache-telemetry/needs-attention" -maxdepth 1 -name '*.md' -type f \| wc -l` | 2 | > 10 | leading |
| M6 | `experimental_skill_dup_clusters` | `python3 "$HOME/dev/git-folder/build-loop/scripts/proposal_ttl_sweep.py" --report-skill-dups --json \| python3 -c 'import json,sys;print(len(json.load(sys.stdin)["clusters"]))'` | 1 (the 4-skill closeout cluster) | > 0 after S7 | lagging |
| M7 | `retro_files_30d` | `find "$HOME/dev/git-folder" -maxdepth 7 -path '*/.build-loop/retrospectives/*' -name '*.md' -type f -mtime -30 \| wc -l` | 111 (Aug) | > 150 | lagging |
| M8 | `archived_key_resurrections_7d` | `python3 "$HOME/dev/git-folder/build-loop/scripts/proposal_ttl_sweep.py" --report --json \| python3 -c 'import json,sys;print(json.load(sys.stdin)["resurrections_7d"])'` | 0 (counter starts at S4) | > 5 | lagging |
| M9 | `tooling_commit_share_90d` | `python3 "$HOME/dev/git-folder/build-loop/scripts/metrics_snapshot.py" --only tooling_share` (internally: `git -C <repo> log --since=90.days --oneline \| wc -l` summed over the 6 tooling + 4 product repos named in evidence; share = T/(T+P)) | 0.592 (2,096 / 3,543) | none — observe-only, human-owned (Q3) | lagging |

**Leading vs lagging, stated:** M1–M5 are leading — they move within one week of the write gate or the sweep failing and predict queue regrowth before it compounds. M6 is lagging evidence that dedup-at-draft failed *and* a defect recurred; M7 is lagging generation pressure; M8 is lagging evidence that TTL expiry is destroying true signal (the specific way the sweep, not the queue, would be the thing drifting); M9 is a lagging allocation observation with deliberately no automated consequence.

## SEEDED_VALIDATION

A gate that has never rejected a planted defect is not evidence. Each `--self-test` below runs in a temp dir and is also executed once by the agent as a standalone proof; each names the exact plant.

1. **Write-gate duplicate (S2):** temp proposals dir; write finding "orphaned run closeout" via the gate (exit 0, file created, `finding_key` stamped); write the byte-identical title+body again → REQUIRED: exit 3, no second file, first file's `last_seen:` updated. `test $(ls *.md | wc -l) -eq 1`.
2. **Write-gate cap (S2/S3):** temp dir pre-seeded with 50 gate-stamped files; 51st write → REQUIRED: exit 4, no file, one row appended to `.suppressed.jsonl`. Then repeat through each **real writer's** code path (invoke the writer's emit function against the temp dir) so the wiring in S3, not just the CLI, is proven.
3. **TTL sweep + reversal (S4):** temp repo; plant `p.md` and backdate it: `touch -t 202606010000 p.md`; run `--apply` → REQUIRED: file in `archive/<YYYYMM>/`, manifest row present, source gone. Then `--restore --manifest …` → REQUIRED: file back at source path, byte-identical (`cmp`). Also plant a file with terminal `status: adopted` and one 5 days old → REQUIRED: both untouched (proves the sweep does not over-fire — a noisy gate is worse than none).
4. **Drift breach (S9):** `metrics_snapshot.py --self-test` runs `check` against a temp state dir with a stub snapshot where `proposals_open_buildloop=999` and a temp needs-attention dir → REQUIRED: exit 2 and exactly one marker file created whose name contains `drift-proposals_open_buildloop`.
5. **Breach dedup (S9):** run the same breach twice in one self-test → REQUIRED: still exactly one marker (`test $(ls "$T/needs-attention" | wc -l) -eq 1`).
6. **Launchd wiring (S10):** `launchctl kickstart -k gui/$(id -u)/ai.rosslabs.proposal-drain`, then within 60 s grep the log for the sweep banner dated today — proves the schedule executes the new command string, not the old advisory-only scan.
7. **SessionStart pressure (S11):** run the hook script with `PROPOSAL_PRESSURE_TEST_DIR` pointing at 80 stub files → banner line appears; with a stub `latest.json` aged 9 days (`touch -t`) → staleness line appears; with 10 files and a fresh snapshot → REQUIRED: zero output (silence is the pass state; the hook must not nag below threshold).
8. **Skill-dup detector (S4/S7):** temp `skills/experimental/` with two dirs named `run-closeout-completeness` and `orderly-closeout-guard` plus one unrelated `color-token-sync` → REQUIRED: exactly one cluster reported containing exactly the two closeout dirs.

## FAILURE_MODES

- **The TTL sweep archives a true, still-valid finding (closure-by-expiry destroys signal).** Counter: never deletes (manifest + `--restore` + S1 tarballs); severity-gated TTL (only non-terminal, unsurfaced items expire); M8 `archived_key_resurrections_7d` fires a needs-attention marker at > 5/week, which is the direct empirical signature of this failure. Disposition if it fires: switch expired-class routing from auto-archive to the digest (a one-line policy change in S4).
- **The drift tracker becomes another unread queue** (the brief's named case). Counter: breaches do not get their own queue — they are written into `needs-attention/`, the one channel measured to drain (73→2), deduped to one marker per metric per week, and injected in full at SessionStart by the already-wired hook. The meta-metric M5 (> 10 open markers) catches the drain channel itself clogging, and it is evaluated at SessionStart (S11) independently of the weekly job.
- **The weekly launchd job dies silently** (unload, PATH break, python error). Counter: S11's staleness line — any session started > 8 days after the last snapshot prints the warning; this channel shares no failure mode with launchd. Log path is fixed (`~/.local/state/build-loop-drain/launchd.log`) for diagnosis.
- **A future producer writes proposals without the gate.** Counter: defense in depth — the weekly sweep enforces cap + TTL + key-dedup *at rest* regardless of writer behavior (it computes `finding_key` for unstamped files), and M4/M1 surface the refill within one week.
- **The ≤10 digest hides a growing backlog.** Counter: by design nothing waits on the human — undecided items expire; the trailing digest line states total open + expiring counts; M2 bounds the total mechanically.
- **The human ignores the digest entirely.** Counter: the system still converges (everything expires; queues stay bounded); the cost is zero adoption of good proposals, which shows up in the lagging pair M6/M8 (defects recurring + archived keys resurrecting) rather than silently.
- **Criteria I cannot fully meet (declared per the criteria rule):** (1) Seeded validation of `session_end_retro_sweep.py` through a *live* SessionEnd is not scriptable from cron — S3's plant exercises its emit function directly, one level below the real trigger; residual risk is the hook-registration layer, covered by M1 refill detection. (2) M4's baseline is undefined before S2 introduces keys ("n/a" is stated, not fabricated). (3) The brief's "0 in archive/" could not be reproduced (1,146 files found); baselines above keep the brief's numbers where they are load-bearing (M1/M2/M3/M7/M9) and the discrepancy is documented in the instrument note rather than silently corrected.

## COST_OF_BEING_WRONG

If the primary cause is not (a) missing actor but (d) raw generation rate — producers evolving new finding classes faster than any closure loop, so even a bound actor and TTL leave the working set churning — then this plan holds the *count* under threshold while the same defects cycle through emit → expire → re-emit and nothing real ever gets fixed: bounded queue, zero adoption, and the 4×-re-draft pattern continues under new names. If instead (b) was primary (pure staleness), the plan still succeeds but S8/S11/S12 were unnecessary machinery — cheap over-build, not breakage.

**Cheapest 14-day detector (already installed by this plan, no extra work):** watch two numbers for the two weeks after S6 — M8 `archived_key_resurrections_7d` and the fraction of gate-suppressed writes whose `finding_key` matches an archived key (both in the sweep's weekly JSON report, computable as `python3 "$BL/scripts/proposal_ttl_sweep.py" --report --json`). If > 20% of new emissions in that window match archived keys, closure-by-expiry is recycling true findings and (a) alone was the wrong diagnosis. The pre-decided fallback, requiring no new surface: route the recycled class through the existing `build-loop:alignment-checker` agent during runs and pin the top recurring key as the #1 digest item until dispositioned — actor escalation, ~1 h of rewiring, all inputs recoverable from `archive/manifest.jsonl`.
