# Changelog

## Unreleased

### Added
- Privileged-command broker: a macOS administrator-password request is now named before the dialog appears, coalesced with any identical read-only request already in flight, and appended to a hash-chained ledger. `scripts/privileged_broker.py` (coordinator + CLI), `scripts/privileged_commands.json` (registry data), `scripts/hooks/pre_bash_privileged.py` (PreToolUse:Bash redirect), `scripts/privileged_audit.py` (read-only forensics + before/after counts). Mutating requests never coalesce and never inherit another task's approval; a denial is remembered for a bounded window so a retry cannot open a second dialog; the password is never read, stored, or replayed. Earned by the 2026-08-20 incident where one Codex turn produced two anonymous `sfltool` dialogs 14 seconds apart. Contract: `skills/build-loop/references/privileged-request-broker.md`.
- Groundwork BuildRequest v1 intake and digest-bound ImplementationMap v1 output connect intended product architecture to verified repository implementation evidence.
- Claude and Codex artifacts ship the same stdlib-only exchange adapter and prove checksum plus execution parity in isolated caches.

### Fixed
- Phase 6 Learn writes its `runs[].learn` summary to the LAST `state.json.runs[]` row carrying the run_id, matching the row `run_close_lint.py --require-learn` grades. `scripts/learn/runner.py::_persist_state_summary` and `_load_run_context` both took the FIRST match, so a run_id owning two rows (`write_run_entry` blind-appends when the existing row is already orchestrator-grade) left the graded row without a summary and `--require-learn` returned `learn_missing` on a run that had genuinely finished Phase 6. Observed 2026-08-31 on `bl-20260831T070458Z-codex:01a0569d-buildloop-01-899329`, rows 14 and 15. New `_canonical_run()` is the single writer-side rule; `scripts/test_run_close_lint.py::DuplicateRunIdTest` pins both consumers together and fails against the pre-fix front-scan.
- `append_run` corroborates the caller's commit and goal before writing `state.json.runs[]` instead of trusting them (enforce-candidate E3). A SHA reachable from neither the run's push range nor HEAD is recorded as `pending`, with the refused value kept on `provenance.supplied_commit`; a goal that diverges from the run's own `intent.md` warns without changing the record. New `scripts/run_provenance.py` (ported from agent-rally-point, where the logic was first tested) plus `--push-range` / `--intent` / `--strict-provenance` flags.

### Changed
- Verification-verdict agents (`fix-critique`, `fact-checker`, `overfitting-reviewer`, `promotion-reviewer`, `scope-auditor`) repinned `model: opus` → `model: fable`, closing the drift against the standing model org (2026-06-09: Fable owns verification verdicts). Orchestrators stay Opus; execution stays Sonnet; pattern scanners stay Haiku.
- Renamed the repository-governance entrypoint to `repo-maintenance`; `repo-closeout` remains a one-release compatibility alias.
- Repository artifact audits now discover nested caches recursively, protect canonical build roots by default, distinguish distributable release artifacts from ordinary build products, and surface live processes that reference already-removed artifact roots.

## [0.45.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.44.0...build-loop-v0.45.0) (2026-09-13)


### Features

* add deterministic run status observer ([af554f2](https://github.com/tyroneross/build-loop/commit/af554f2ac0624605f61dc416a68a23fa4d40b64a))
* **airlock:** gate foreign-repo instruction files as untrusted data ([f977967](https://github.com/tyroneross/build-loop/commit/f977967833a81e28c5f2bfd4f6a4187966a51798))
* **models:** register gpt-6-astra (alias astra) in the routing index ([677377a](https://github.com/tyroneross/build-loop/commit/677377a033b64067605a7099036aad3e77713590))
* **research:** add opt-in matched architecture pilot ([f69b72b](https://github.com/tyroneross/build-loop/commit/f69b72b96595f1e51a92cfd57c43ffd00fa99531))


### Bug Fixes

* **airlock:** follow CLAUDE.md [@imports](https://github.com/imports), which hid the payload one hop away ([a85a142](https://github.com/tyroneross/build-loop/commit/a85a14216ea2f44fe8c324b8cc3a1fab7c757368))
* **ci:** isolate dashboard service reads and keep orchestration compact ([61d932c](https://github.com/tyroneross/build-loop/commit/61d932cbcccd7d832a065c9ac34c4534ae44b356))
* **dashboard:** read a terminal execution phase as closed, not mid-Review ([7628e55](https://github.com/tyroneross/build-loop/commit/7628e55dec1fbf1657627db334f84ba0a3cd8ef7))
* **docs:** make 8 retrospective citations repo-relative so the portability guard passes ([25f8630](https://github.com/tyroneross/build-loop/commit/25f863040b387e7f065895462f5642cd35d13098))
* **docs:** repair mojibake pushed in fd15506f and restore two deleted paragraphs ([a7095b6](https://github.com/tyroneross/build-loop/commit/a7095b687b7254e15108fd36c2d086a2415af5ac))
* **memory:** share scoring snapshots and attribute bootstrap reads ([2d19bea](https://github.com/tyroneross/build-loop/commit/2d19bea489933e6b4d2b3f3696d0394c69b7e7fc))
* resolve stale builds autonomously ([a5bf9eb](https://github.com/tyroneross/build-loop/commit/a5bf9ebfe2c4f30a81765f81b5d0179ec86cb48b))
* **run-ledger,worktree-lifecycle:** close five findings from the closure audit ([34d18b7](https://github.com/tyroneross/build-loop/commit/34d18b7dd4229da233cae033c33b833cd5ac5388))
* **run-ledger,worktree-lifecycle:** close four findings from the round-4 audit ([52699fd](https://github.com/tyroneross/build-loop/commit/52699fdb9cc09dee2e807a2d76db29e1ec411285))
* **run-ledger,worktree-lifecycle:** close four re-audit findings ([0fd7eba](https://github.com/tyroneross/build-loop/commit/0fd7ebaa0674240769bcf7cab4d51a9eae258234))
* **run-ledger,worktree-lifecycle:** close the auditor's two HIGH findings ([d1e0287](https://github.com/tyroneross/build-loop/commit/d1e02870a6f7fa36910b8cbd983e49c4d15d4d69))
* **run-ledger,worktree-lifecycle:** close the five low findings from the yay verdict ([79b3c2b](https://github.com/tyroneross/build-loop/commit/79b3c2bf4338dc7d2c5b760ceaac9ebf4ff715fc))
* **run-ledger:** drop the floor writer's source label on a Review-G merge ([3666dad](https://github.com/tyroneross/build-loop/commit/3666dad28a4e2c1e661b879489daee4d839f211e))
* **run-ledger:** log the write action the writer actually took ([03efd34](https://github.com/tyroneross/build-loop/commit/03efd34b80a204b2a1c3c87ede9a6b45f4d8e17a))
* **run-ledger:** upsert runs[] on run_id instead of appending a second row ([f1b3b95](https://github.com/tyroneross/build-loop/commit/f1b3b9547af780946dbf093b45bc76183ae3fb2d))
* **self-mod-gate:** close eight revert paths a cross-vendor audit found still live ([2798c0b](https://github.com/tyroneross/build-loop/commit/2798c0b3be8e666a87d58078cc59bf86a4cb0faf))
* **self-mod-gate:** make the destructive auto-revert inexpressible ([50c505d](https://github.com/tyroneross/build-loop/commit/50c505d567da7ecd691f1f7441e1e1c8e57ef6ad))
* **supply-chain:** close four findings from the cooldown commit audit ([1e51887](https://github.com/tyroneross/build-loop/commit/1e518873a711125b0192365e0190e29119cc55d4))
* **supply-chain:** version-gate pnpm cooldown and seed the packages field ([bd9ec11](https://github.com/tyroneross/build-loop/commit/bd9ec1119f845d4c5813b5ca4133be31ea667aec))
* **worktree-lifecycle,run-ledger:** close two Review-D gaps in the prior commits ([4ccc5c5](https://github.com/tyroneross/build-loop/commit/4ccc5c5e9182e9ceef682a54ce0629f310063ebf))
* **worktree-lifecycle:** inventory ignored files before a worktree removal ([c60d66e](https://github.com/tyroneross/build-loop/commit/c60d66ed69295431d0ed1e556da5bbb4c74712b7))
* **worktree-lifecycle:** stop discarding git's own incomplete-listing warning ([dbed2aa](https://github.com/tyroneross/build-loop/commit/dbed2aa75ef81b30e0886ae0effaf9c7575c116e))
* **worktree-lifecycle:** treat a symlinked subdirectory as uninspected ([963cca7](https://github.com/tyroneross/build-loop/commit/963cca75e3587330d32647b4b3c8ea2150513fb0))

## [0.44.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.43.0...build-loop-v0.44.0) (2026-09-07)


### Features

* **release:** detect a release that was cut but never reached the registry ([387b059](https://github.com/tyroneross/build-loop/commit/387b05908918d063b4b60e42130693edf0a48f04))

## [0.43.0](https://github.com/tyroneross/build-loop/compare/build-loop-v0.42.5...build-loop-v0.43.0) (2026-09-06)


### Features

* **database-practice:** table map script, vector/graph tuning reference, Sonnet-tier runbook ([308949b](https://github.com/tyroneross/build-loop/commit/308949bde63d372b23e635b292ea76ea2cfa92a8))
* **dispatch:** make every agent brief declare where it reports, before launch ([c04a803](https://github.com/tyroneross/build-loop/commit/c04a803102e7c36c004e8dc737ecf2215f515089))
* **release:** cut a release automatically, once a week, and notice when it stops ([7820cea](https://github.com/tyroneross/build-loop/commit/7820cea89181ddf5682d10320a039d6b39eb1850))


### Bug Fixes

* **artifact:** the shipped Codex gate would auto-apply a production migration ([faa91ad](https://github.com/tyroneross/build-loop/commit/faa91ad27d4b647062ecc32c8b96109d55175def))
* **ci:** make main green again — stale skill index, README skill count, claim-scope disambiguator past char 300 ([d2f64c2](https://github.com/tyroneross/build-loop/commit/d2f64c2ce2d672f78d304d1fddceb028be74e911))
* **ci:** stop a workflow filename reading as a test invocation ([71a10b4](https://github.com/tyroneross/build-loop/commit/71a10b4f3e9f7c0a6d66a41ad74d595289b4c36e))
* **ci:** stop two dead workflows going red, and name a bad npm token before it publishes ([d3440d0](https://github.com/tyroneross/build-loop/commit/d3440d026bad26cd105a35959a30c4fc338229c4))
* **database-practice:** describe the connection as host:port/db, never as a URL ([7765cfa](https://github.com/tyroneross/build-loop/commit/7765cfad4dbc36e07d5120b8edfcc2512fb5923f))
* **docs:** resync the surface counts, and unbury a new skill's routing boundary ([776a463](https://github.com/tyroneross/build-loop/commit/776a4632c38fbdce99b6928580a41d188f580439))
* **hooks:** close five false-clearance paths the independent auditor found in the push-scan fix ([a6105a9](https://github.com/tyroneross/build-loop/commit/a6105a9bb4accd5410426a007f555bcab11ec36b))
* **hooks:** couple --tracked-only to --diff, and close five more findings from the closure audit ([b50abee](https://github.com/tyroneross/build-loop/commit/b50abee81a9b85d9e27a61fd456da510f6d89405))
* **hooks:** reject a condition-poll that cannot terminate, not just a literal infinite loop ([1d76b55](https://github.com/tyroneross/build-loop/commit/1d76b55e23808175ec9d28443b93c24158aa7337))
* **hooks:** stop the pre-push scan from full-scanning a plain push and from blocking on untracked files ([e917dcf](https://github.com/tyroneross/build-loop/commit/e917dcf93f4c71a8b30e2c3ecc7f60e052052657))
* **release:** do not treat a parked bot workflow run as a red release PR ([0bb00c6](https://github.com/tyroneross/build-loop/commit/0bb00c6199e1e13d1b45bc8f352b17fe64a66859))
* **release:** flip the pre-push version gate to the invariant release-please needs ([50aef21](https://github.com/tyroneross/build-loop/commit/50aef21e99111e6f67f3fc7c9036ea8622011e69))
* **release:** put last-release-sha where the schema reads it ([8772a0f](https://github.com/tyroneross/build-loop/commit/8772a0f5625dc030154ac4c74aee274a8b2a23e4))
* **resume:** reconcile terminal closeout residue ([b93ebb8](https://github.com/tyroneross/build-loop/commit/b93ebb8e97a019ffce2afb9b810ec873e68df21e))
* **runtime:** bound and identify synthetic load probes ([9909821](https://github.com/tyroneross/build-loop/commit/99098216c3cb5ff29c2a7069d8787b99e9fc7446))

## 0.36.4 — 2026-07-11

First published release since v0.36.1. The 0.36.2/0.36.3 entries below were
staged (plugin manifests + CHANGELOG only) but never tagged, released, or
published; their changes first ship here. Release notes: `docs/releases/v0.36.4.md`.

### Fixed
- Git commit/push hook trigger is segment-parsed instead of substring-globbed, ending classifier false-fires on prose/paths that merely contain "git commit" (`54268bd`).
- Retrospective temporal-membership preflight stops attaching wrong-run records to a run's retro (`422a5c1`).
- Independent-auditor finding closures: wrapper push detection, heredoc over-strip, audit-guard test (`93fe6c8`); idle-scan skip when an unterminated heredoc has no git (`673a7fa`).

### Changed
- Worktree-isolation doctrine widened to commit-less file editors: any long-running background writer that edits files must use a dedicated worktree — commit authority no longer required; lint rule `wake-path-grew-a-file-edit` added (`e45cfa6`).

### Added
- runtime-parity-verification: doc↔interface parity checks for CLIs, tools, and flows (`7dc1854`).

## 0.36.3 — 2026-07-10 (not published; first shipped in v0.36.4)

### Added
- Eager Phase-6 Learn detector-pass for inline runs: `stop_closeout.py` runs the deterministic pattern detector on the record path and folds an owed "Phase-6 Learn drafting" item into the existing `closeout-pending` marker (surfaced once at SessionStart) when `runs[]>=3` and a root-cause cluster >= threshold has no experimental draft yet. Fills the `learn/pending` lane's "not yet an automated detector pass" TODO so inline runs no longer leave the learning loop dark until the user asks.


## 0.36.2 — 2026-07-10 (not published; first shipped in v0.36.4)

### Fixed
- Pre-push security gate scans the **push delta**, not the whole tree, so a pre-existing HIGH in an unrelated file no longer hard-blocks unrelated pushes (delta-scoping + 9 false-negative closures + conservative-by-construction classifier).
- `perturbation_spotcheck --check-cmd` now runs as an argv list (`shell=False`), closing the A03/LLM02 shell-injection HIGH (metacharacters in the path/template are inert).


## 0.36.0 — 2026-06-26

### Changed

- **Model resolution — availability fallback + outage TTL.** A tier role observed
  unavailable at dispatch auto-falls-back to the next host-reachable model in its
  tier (a frontier/judgment role degrades at most to the thinking tier, never below
  the floor); a model the current host cannot dispatch is filtered out. Outages are
  recorded with a timestamp and auto-expire after a TTL (default 1800s,
  `BUILD_LOOP_OUTAGE_TTL_SECONDS`); legacy untimestamped records self-heal on read.
  (`scripts/model_resolver.py`, `scripts/dispatch_fallback.py`,
  `scripts/model_availability_store.py`, `scripts/classify_model_tier.py`)
- **Rust-rally coordination facade — retire the Python policy mirror (4-phase migration).**
  build-loop's coordination layer is now a thin facade over the canonical Rust
  `rally` binary that FAILS LOUD when the binary is unavailable, replacing the
  cross-language Python parity mirror and eliminating the parity tax (golden
  fixtures + drift manifest).
  - **Capability contract** (`scripts/rally_point/capability.py`): every
    coordination envelope carries `capability_level` (`full` /
    `degraded-breadcrumb` / `unavailable`) + a `coordination_unavailable` reason.
    `FULL_ONLY_OPERATIONS` (claim/reclaim/lead/reap/liveness/before_write/
    checkpoint) are permitted only at full capability.
  - **Reaper is Rust-only** (`reaper.py` facade): shells `rally sessions --reap`
    at full capability; refuses (no shadow sweep) below it. `presence.reap_stale`
    and `leadership` reclaim gain fail-closed Rust-only guards; the EMPTY-seat
    claim and self-relinquish stay breadcrumb-class.
  - **Degraded breadcrumb path**: a binary-less but supported host may write only
    capability-marked presence/handoff breadcrumb facts — never ownership,
    reclaim, liveness, reap, or before-write protection.
  - **Fetch-on-install** (`binary_fetch.py`): when no system/sibling/PATH binary
    is found, build-loop fetches the host-platform asset from the PINNED
    agent-rally-point `v0.1.3` release, SHA256-verifies it (fail-closed), strips
    the macOS quarantine xattr, version-pins (refuses != 0.1.3), and caches under
    `$XDG_CACHE_HOME/build-loop/rally`. Wired as a `discovery_bridge` tier. An
    UNSUPPORTED host (no matching asset — Intel mac / musl / exotic arch) → loud
    `coordination_unavailable: unsupported_host`, never a mirror.
  - **One-way legacy migration**: `discovery_bridge.maybe_auto_migrate` fires on
    any full-capability resolution and replays a stranded `build-loop-internal`
    fallback store into `.rally` via `rally migrate-legacy` (idempotent).
  - **Parity tax removed**: deleted `decay_vectors.json`, `liveness_vectors.json`,
    `heartbeat_parity_vectors.json` and 6 `_provenance.json` drift entries;
    `decay.py`/`liveness.py` survive as in-process math helpers (window/weight
    only). `test_decay.py`/`test_liveness.py` rewritten as inline unit tests;
    `test_reaper.py` rewritten as facade wire-contract tests; `test_binary_fetch.py`
    adds a native integration test running the FETCHED v0.1.3 binary.

### Added

- **Zombie-tmux prevention — shared decision policy (Python mirror).**
  `scripts/rally_point/liveness.py` mirrors agent-rally-point's two new shared
  reaper/self-exit authorities: `reapable(liveness, parent_alive)` (the single
  "may this session be killed?" decision — Live/Unknown never reaped; parent-dead
  reaps only a `Stale` session; missing parent info degrades to the window
  criterion alone) and `completion_self_exit_eligible(...)` (a task-scoped session
  self-exits only when work is resolved AND `rally next` is non-actionable for a
  sustained streak; `--persistent` opts out). Parity double-pinned by the
  byte-identical `liveness_vectors.json` (now carries `reapable_cases` +
  `self_exit_cases`, ≡ the Rust fixture; `_provenance.json` updated to the new
  upstream `liveness.rs` hash). `test_liveness.py` asserts the same vectors the
  Rust suite asserts. `references/coordination-rules.md` §"Zombie-tmux prevention"
  documents all three layers + the fail-safe directions. The tmux/CLI plumbing
  (Layers 1–3 actuators) is Rust-canonical; this Python layer mirrors the decision
  policy only, as it does for `is_live`.
- **Adaptive, multi-signal session liveness (Python mirror).**
  `scripts/rally_point/liveness.py` mirrors agent-rally-point's canonical
  `liveness.rs`: staleness ADAPTS to each session's planned heartbeat cadence
  (`window = planned_interval * MISS_MULTIPLIER + GRACE`; defaults 300 s / 6 / 60 s
  → 5-min cadence stale at ~31 min, 5-hour cadence at ~30 h) and weighs four
  signals — LIVE if ANY is fresh. `presence.reap_stale` is now adaptive
  (per-record cadence from `planned_heartbeat_secs`, legacy `heartbeat_minutes` as
  a fallback cadence source) with a code-progress keep-alive (a session whose
  branch HEAD moved between polls survives a lapsed heartbeat, tracked via
  `liveness-sha-cache.json`). `coordination_policy.py` gains `default_cadence_secs`,
  `miss_multiplier`, `grace_secs` tunables. Parity double-pinned by the
  byte-identical `liveness_vectors.json` (≡ the Rust fixture, tracked in
  `_provenance.json`); `test_liveness.py` asserts the same vectors the Rust suite
  asserts. `references/coordination-rules.md` documents the model + the FAIL-OPEN
  (squad visibility) vs FAIL-CLOSED (reaper removal) split.
- `scripts/rally_point/reaper.py` — Python fallback reaper that physically removes
  over-TTL presence files, expired claims, and reclaimable lead leases. FAIL-CLOSED
  on unprovable timestamps. Respects the resolved-via rule: defers claim-index.json
  rewrites to Rust when `resolved_via == "repo-local-rally-cli"`. Callable CLI:
  `python3 scripts/rally_point/reaper.py --workdir <path> [--apply] [--json]`.

- `scripts/rally_point/test_reaper.py` — Pytest suite for the reaper covering
  presence unlink, claims expired/missing/future/rust-deferred, lead
  expired/valid/missing, idempotency, dry-run semantics, heartbeat parity vectors.

- `scripts/rally_point/heartbeat_parity_vectors.json` — Golden parity fixture
  (byte-identical to the Rust counterpart in
  `crates/rally-cli/tests/fixtures/heartbeat_parity_vectors.json`). Proves that
  claude and codex sessions decay identically; `test_reaper.py` asserts each
  vector's `expected_weight` and `stale_at_15m` verdict via `decay.recency_weight`.

- **Actuator wiring** (`hooks/session-start-rally-point.sh`) — fire-and-forget
  `reaper.py --apply` call added at Step 3 so every session-start opportunistically
  cleans over-TTL coordination state.

- **Codex parity hook** (`.codex/hooks.json` `SessionStart`) — codex sessions now
  emit a presence record via `session_probe.py --tool codex`, so their
  presence/claims/lead decay identically to claude sessions.

- **Session-end self-release** (`.codex/hooks.json` `Stop` + `scripts/hooks/stop_finalize.sh`)
  — both codex and claude emit `rally stop <tool>` at turn completion, containing
  accretion at the source instead of relying on TTL expiry.

### Changed

- `scripts/rally_point/presence.py` — `reap_stale` gains an optional `apply: bool = True`
  parameter (backward-compatible). `apply=False` returns the would-reap session IDs
  without unlinking files, enabling reaper dry-run inspection.

- `scripts/rally_point/_provenance.json` — added entries for `reaper.py` (build-loop
  original; `source: null`) and `heartbeat_parity_vectors.json` (Rust fixture parity;
  sha256 `8d88c3e23fd8688b9a536ad06e3bdc89ede71a0637ff0455e87889f2869099c3`).

- `references/coordination-rules.md` — new subsection "In-room stale-state reaper
  (actuator) & codex parity" under "Recency decay & size-scaled lead/ownership
  auto-reclaim", documenting the actuator, FAIL-CLOSED invariant, Rust-vs-Python
  claim store rule, codex parity proof, and session-end self-release.

- `AGENTS.md` — one-line codex heartbeat-parity note added near the rally section.
