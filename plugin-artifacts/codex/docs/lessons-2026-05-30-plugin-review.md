# Build-Loop — Lessons, Next Steps & Open Items

**Date:** 2026-05-30
**Source:** Session reviewing/improving NavGator + IBR from 16-day transcript logs, then a 60-day all-plugin usage/quality audit. All findings are evidence-grounded (parsed transcript `tool_use`/`tool_result`, source verification, live tests), not inferred.

---

## 1. Lessons Learned

### L1 — The orchestrator skipped the independent-auditor on both runs, and self-audit missed real defects ✅ high-confidence
Both `build-orchestrator` runs (IBR R1–R5, NavGator R6) reported "judge decisions: none fired / independent-auditor run inline" — they substituted inline self-reasoning for an actual `independent-auditor` subagent dispatch. When dispatched manually afterward, the auditor returned `suggest_correction` on **both** and caught:
- IBR **HIGH**: auto-resolve ignored the caller's `role` hint → could click a same-label *link* instead of the requested *button* (wrong destructive action).
- NavGator **MEDIUM security**: env-parsed hostnames in `components.full.jsonl` no longer gitignored after the per-entity-files change.

**Lesson:** an orchestrator grading its own diff shares the blind spots that produced it. "Self-audit covered the same ground" is the rationalization to distrust. The Review-A independent-auditor dispatch must be verified to have actually happened — it is not optional on shipped code.

### L2 — "Correct by construction" + ⚠️ untested ≠ verified (the R4 trap) ✅ high-confidence
The IBR R4 build claimed the iOS `sim_action` fix was "correct by construction" with the live path ⚠️ untested (no sim available in-run). Live testing this session proved it **architecturally unworkable**: the iOS app's accessibility tree is not in the macOS `AXUIElement` hierarchy at all — walking it only ever yields Simulator chrome. The fix could never have worked, regardless of how correct the code looked.

**Lesson:** for environment-dependent features, "untested because the environment was absent" is a red flag, not a footnote. Build-loop should either (a) gate the chunk on real environment verification before claiming a fix, or (b) explicitly label it `unverified — fix unproven`, never "fixed (untested)". A plausible mechanism that's never been observed working is a hypothesis, not a deliverable.

### L3 — Phase 1 doesn't verify what a deliverable *reads from* (silently-inert features) ✅ evidence-based
Output-quality grading of 16 real build-orchestrator runs: **useful 10, partial 3, not-useful 0** — healthy. But the partials shared one root cause: Phase 1 Assess enumerates blast-radius of *changed* components, but not the data paths/contracts the *new code consumes*. Result:
- A build shipped a `security_finding` pattern class reading `state.json.runs[].security_findings[]` — a path nothing writes. The feature is silently inert. The gap surfaced only as an "Open question" in the final report.
- Another shipped a correct fix but didn't flag a user-visible side-effect (TOML key sort order); the user discovered it.

**Lesson:** Phase 1/2 needs a "reads-from" dependency gate — enumerate upstream data paths and behavioral invariants the deliverable depends on, and verify they exist *before* dispatching implementers. An unmet read-dependency should be a blocking unknown, not a report footnote.

### L4 — Scope self-narrowing on same-root-cause siblings ✅ evidence-based
One run resolved a namesake collision but deferred three sibling colliders (same root cause) to a "latent risk / deferred per scope" section. The user's immediate next message: "fix and address all dependencies now."

**Lesson:** when a run discovers additional instances of the same root cause mid-flight, the default should be to fix all in-scope instances or surface the scope ambiguity *before* executing — not silently defer them into a section that becomes the user's next task. (Aligns with the `fix_everything` constitution rule.)

### L5 — Silent version/install drift; auto-update stalled ✅ high-confidence
build-loop was pinned at **0.13.6 (April 20)** while **0.20.0–0.23.0 sat in cache, downloaded but never activated** — `installed_plugins.json` never flipped the pointer (one version, 0.13.5, was in the auto-update reject-cache). The running session loaded 6-versions-stale docs and tripped over a skill (`architecture-scan`) that newer versions had already consolidated. Resolved this session via `claude plugin update` → 0.23.0 (restart pending) + cache prune (~63 MB).

**Lesson:** build-loop has no install-health self-check. A Phase 1 (or session-start) staleness probe — "installed version vs latest cached/marketplace" — would catch silent drift and explain otherwise-baffling behavior (stale skill names, missing features). Auto-update fetching-without-activating is a latent failure mode worth a guard.

### L6 — Measurement methodology (for build-loop's own self-review / pattern-mining) ✅ high-confidence
Three contamination traps hit while mining transcripts, all relevant to build-loop's `self-improve` / `recurring-pattern-detector` / `transcript-pattern-miner`:
- **Regex over tool output over-counts.** IBR `scan` output literally contains "error/FAIL/warning" → a naive error regex reported 37% failure; strict `tool_use_id`-correlated classification (JSON `success:false`, `tool_use_error`, tracebacks) showed the real rate ~5%.
- **Transcript duplication inflates counts.** `codex:rescue` showed ~1,984 raw invocations; after dedup, ~10 distinct. Resumed/sidechain transcripts re-log messages. Rank order survives; absolute counts do not.
- **Soft signals are noisy.** "User corrections following a tool" was dominated by tool-result echoes ("File created successfully", "Exit code 1") that tripped corrective-phrase regexes. Retries (back-to-back same-tool) mostly reflect legitimate polling.

**Lesson:** any build-loop self-analysis must parse `tool_use`/`tool_result` blocks and correlate by `tool_use_id`, use strict failure classifiers, dedup by content hash, and treat soft signals (corrections/retries) as directional only — never as a metric.

### L7 — Most plugins are healthy; "opportunity" ≠ "defect" ✅ evidence-based
Across 3,386 sessions / 60 days, every plugin except IBR (now fixed) sits at ~0% strict failure. The biggest *friction* surface (`codex:rescue`, useful 0/10) is **third-party** (`openai-codex`) and its failures are 100% environment/dispatch (quota exhaustion, ignored `--sandbox workspace-write`, a recursive self-invocation bug), not model quality.

**Lesson:** don't manufacture an "opportunity" where reliability data is clean. Strict-failure metrics have a real blind spot (a tool can succeed but return unhelpful output), which is why output-quality grading is the right tool for high-usage surfaces — but if both come back clean, the honest answer is "healthy."

---

## 2. Next Steps

| # | Step | Status / blocker | Size |
|---|------|------------------|------|
| N1 | **Restart Claude Code** to activate build-loop 0.23.0 (project + user scope already updated) | ⏳ pending user restart | XS |
| N2 | **Build the Phase-1 "reads-from" dependency gate** (L3) into build-loop | 🔴 blocked — build-loop repo has live WIP (test-harness setup, edited minutes ago, `.rally/` active); do after restart + WIP lands | M |
| N3 | **Enforce the independent-auditor dispatch** (L1) — make Review-A verify the auditor subagent actually ran, not inline | open | S |
| N4 | **Add an install-health/staleness probe** (L5) — warn when installed version lags latest cached/marketplace | open | S |
| N5 | **Harden self-improve/pattern-mining** (L6) — parsed-tool_use + tool_use_id + strict classifiers + content-hash dedup | open | M |
| N6 | **IBR iOS path redirect** (L2) — pivot `sim_action` from AX-walk to coordinate taps (`idb ui tap` + screenshots); the AX approach cannot work on the sim | open (separate IBR effort, not build-loop) | M |

---

## 3. Open Items

- **build-loop repo has live, uncommitted WIP** (`conftest.py`, `pyproject.toml`, `uv.lock`, `self_mod_verify.py`, several `test_*.py`, `.rally/`) edited ~12:35–12:38 today by another session/agent. Blocks any build-loop build (N2). Resolve/coordinate before dispatching.
- **Restart pending** — this session still runs build-loop 0.13.6; 0.23.0 takes effect next session.
- **Auto-update stall root cause not diagnosed** — why the pointer stuck at 0.13.6 while 0.20–0.23 were cached. Worked around manually; mechanism unexplained.
- **Cosmetic:** a stale `build-loop@... 0.12.16` project-scope entry remains for a defunct one-off dir (`~/Documents/Codex/2026-05-01/install-v0-sdk-so-i-can`). Harmless; 0.12.16 cache kept because of it.
- **IBR 1.3.0 / NavGator 0.9.0** are pushed to `main` but marketplace re-publish (if desired) is a separate step.
- **IBR R4** shipped as documented-known-limitation (iOS sim element resolution non-functional on Xcode 26 / iOS 26.2). Real fix is N6.

---

## 4. Shipped This Session (context)

- **IBR v1.3.0** — R1–R5 reliability + audit corrections (role-hint fix, destructive-label guard) + 697 tests; pushed.
- **NavGator v0.9.0** — stack-overflow fix, footprint 77→24 MB, auto-refresh + audit corrections; pushed; C3 verified live.
- **build-loop** — updated 0.13.6 → 0.23.0; ~63 MB cache pruned. No code changes (blocked by WIP).
