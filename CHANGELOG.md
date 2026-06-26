# Changelog

## Unreleased

### Changed

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
