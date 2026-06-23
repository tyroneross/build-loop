# Changelog

## Unreleased

### Added

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
