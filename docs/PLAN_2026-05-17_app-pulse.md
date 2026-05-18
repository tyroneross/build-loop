# App Pulse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: execution routes through **build-loop**
> (`build-loop:build-orchestrator`, subagent-driven, worktree-isolated) per project
> convention — NOT superpowers:subagent-driven-development. build-loop Phase 2 will
> decompose each task below into TDD micro-steps under plan-verify + plan-critic.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship, in central build-loop, a per-app shared channel that communicates every
app change across concurrent Claude/Codex sessions, plus an enriched live data-flow
architecture map (LLM/MCP/API/infra nodes, model-class-agnostic, diagram-ready).

**Architecture:** One `$HOME`-keyed per-app channel (`~/.build-loop/apps/<slug>/`) with
`revision` + append-only `changes.jsonl` + `sessions/*` presence + compact `arch/digest.json`.
Capture via three tool-agnostic mechanisms (git post-commit hook, orchestrator explicit
writes, existing bg-scan completion). Read via checkpoint poll. Native scanner enriched
with new node/edge taxonomy; semantic labelling by in-session Claude/scout.

**Tech Stack:** Python stdlib only (matches existing `scripts/`), `fcntl` locking,
JSON/JSONL, git hooks (sh), build-loop hook surface, Mermaid/DOT text generation.

**Resolved open questions (PLAN DECISIONS — flagged):**
- **OQ1 monorepo slug** → reuse memory's `_paths` `<slug>/workers` sub-component convention verbatim; no new rule.
- **OQ2 heartbeat** → 15-minute stale window, reaper runs at each checkpoint read; configurable via `~/.build-loop/apps/<slug>/config.json` `heartbeat_minutes`.
- **OQ3 enriched scan placement** → a **separate scout-triggered pass** (`build_loop.architecture enrich`), NOT inline in `_arch_scan_bg.py`, to keep the fire-and-forget bg hook token-light. The bg hook still does the cheap structural scan; enrichment is dispatched by `architecture-scout` in Phase 1/4.

---

## File Structure

**Stage 1 — spine + capture**
- Create `scripts/app_pulse/_paths.py` — resolve `~/.build-loop/apps/<slug>/`, reuse `scripts/_paths.derive_slug_from_cwd`; lazy-create; path-traversal validation (mirror memory store guard).
- Create `scripts/app_pulse/revision.py` — `read_revision()`, `bump_revision()` (fcntl short-timeout lock, skip-on-timeout, monotonic).
- Create `scripts/app_pulse/changes.py` — `append_change(record)` (O_APPEND atomic), `read_changes_since(offset)`; record schema + validator.
- Create `scripts/app_pulse/presence.py` — `write_presence()`, `read_active_presence()`, `reap_stale()`, per-session cursor get/set.
- Create `scripts/app_pulse/checkpoint.py` — `checkpoint_read(session_id)` → delta envelope (changes tail + active peers + arch digest); the one entry point hooks/orchestrator call.
- Create `hooks/post-commit` (sh, idempotent installer + body) — emits `commit` + `dep-change` (manifest-glob diff) records, bumps revision; fire-and-forget.
- Create `scripts/app_pulse/install_git_hook.py` — idempotent install/verify of `.git/hooks/post-commit` into a consumer repo.
- Modify `agents/build-orchestrator.md` — Phase preamble writes presence; each phase-start writes phase record + runs `checkpoint_read`; surfaces peer/soft-claim + dep/arch reactions.
- Modify `hooks/hooks.json` + add `hooks/session-start-apppulse.sh` — SessionStart runs `checkpoint_read`, surfaces restore line; PreToolUse(Edit) cheap revision-stat hint.
- Test `scripts/app_pulse/test_*.py` — unit per module + concurrency + graceful-absence.

**Stage 2 — native-scanner enrichment**
- Modify `src/build_loop/architecture/schemas.py` — add open node types (`infra-component`, `llm-callsite`, `mcp-callsite`, `api-callsite`, `external-service`, `dependency`) + dataflow edge types (`data-in`, `data-out`, `transforms`, `invokes`, `runs-on`); type field is open-vocab (warn-not-reject).
- Create `src/build_loop/architecture/_taxonomy.py` + `arch/_taxonomy.json` — registry of known types; additive; `register_type()`; consumed by validator/digest/diagram.
- Create `src/build_loop/architecture/detectors.py` — deterministic stdlib AST/regex detectors: anthropic/openai SDK, generic `fetch`/`requests`/http client, MCP tool calls, redis/bullmq/db/object-store imports, manifest parse (npm/pip/uv). Emits *unlabelled* call-site nodes + located context.
- Create `src/build_loop/architecture/enrich.py` — orchestrates detectors → graph nodes/edges; emits a `semantic_todo` list (sites needing Claude labelling) rather than guessing.
- Modify `agents/architecture-scout.md` — new `task: enrich` that runs `enrich.py`, then the scout (in-session Claude) fills `purpose`/`model_class`/`model_example`/data-in-out prose for each `semantic_todo`, writes back to graph.json. No external API.
- Create `src/build_loop/architecture/diagram.py` — deterministic `graph.json` → `diagram.mmd` + `diagram.dot`; vertical=layer rank, horizontal=dataflow peers; unknown types → generic layer.
- Modify `scripts/app_pulse/...` digest writer — include per-type counts, API/MCP/LLM inventory hash, dep-manifest hash, stable-ID adjacency matrix.
- Modify `hooks/pre-edit-architecture.sh` — stop excluding dependency manifests; route manifest change to mark-enrich-needed (actual enrich deferred to scout pass, OQ3).
- Test `src/build_loop/architecture/test_*.py` — detector fixtures, model-class abstraction, dataflow edges, diagram render, **full-chain new-node-type survival** (writer→digest→diagram→validator→read).

**Stage 3 — Codex cross-tool validation**
- Create `docs/_inbox/codex-apppulse-validation.md` — plain-language V1–V4 runbook (sibling of `codex-postgres-validation.md`).
- Create `scripts/app_pulse/test_cross_tool.py` — automated V1–V3 using the dual-path method proven this session (write under `tool=codex` identity, read via `$HOME` slug path AND installed-cache code path).
- Modify `docs/DESIGN_2026-05-17_app-pulse...md` — append "validated" status block after V-runs.

---

## Stage 1 — Shared-channel spine + cross-tool capture

Produces working, testable software alone: a usable cross-session change channel even
before scanner enrichment.

### Task 1: Channel path resolver
**Files:** Create `scripts/app_pulse/_paths.py`; Test `scripts/app_pulse/test_paths.py`
- [ ] Test: slug derivation reuses `_paths.derive_slug_from_cwd`; `apps/<slug>/` resolves under `~/.build-loop/`; traversal-y slug raises (reuse memory `_safe_project_tag`); `<slug>/workers` sub-component path joins (OQ1).
- [ ] Test: lazy-create is idempotent; absent HOME path → resolver returns path, never creates outside root.
- [ ] Implement; run tests; commit.

### Task 2: Revision counter
**Files:** Create `scripts/app_pulse/revision.py`; Test `test_revision.py`
- [ ] Test: `bump_revision` monotonic; concurrent bumps (fork/thread) never corrupt, end value == successful-bump count or fewer (skip-on-timeout allowed), never higher; reader needs no lock; missing file == revision 0.
- [ ] Implement fcntl flock w/ short timeout, skip-on-timeout; tmp+rename write; commit.

### Task 3: Append-only change log
**Files:** Create `scripts/app_pulse/changes.py`; Test `test_changes.py`
- [ ] Define record schema: `{ts, kind: commit|dep-change|phase|arch-scan-complete, tool, model, run_id, app_slug, payload{...}, revision}`. Validator warns-not-drops unknown `kind` (D7 shape).
- [ ] Test: `append_change` is O_APPEND atomic under concurrency (N writers → N well-formed lines, no torn lines); `read_changes_since(byte_offset)` returns only new records + new offset; immutable (no rewrite API exists).
- [ ] Implement; commit.

### Task 4: Presence + reaper + cursor
**Files:** Create `scripts/app_pulse/presence.py`; Test `test_presence.py`
- [ ] Presence schema: `{session_id, tool, model, run_id, app_slug, phase, files_in_flight[], heartbeat_ts, cursor:{revision, changes_offset}}`. Overwrite-in-place tmp+rename.
- [ ] Test: `reap_stale` drops presence older than `heartbeat_minutes` (default 15, config-overridable — OQ2); `read_active_presence` excludes self + reaped; cursor round-trips.
- [ ] Implement; commit.

### Task 5: Checkpoint read (the single consume entry point)
**Files:** Create `scripts/app_pulse/checkpoint.py`; Test `test_checkpoint.py`
- [ ] Test: when `revision` unchanged vs session cursor → returns empty envelope (no tail read). When changed → envelope = {new_changes[], active_peers[], arch_digest|null, reactions:[reinstall?|re-baseline?|soft-claim files]}. Graceful when channel/dir absent → empty envelope, lazy-create, zero error.
- [ ] Test: reader never writes except advancing its own cursor; never locks the log.
- [ ] Implement; commit.

### Task 6: Git post-commit capture (cross-tool)
**Files:** Create `hooks/post-commit`, `scripts/app_pulse/install_git_hook.py`; Test `test_install_git_hook.py`, `test_post_commit.py`
- [ ] Test: installer is idempotent (re-run = no dup), preserves any existing post-commit by chaining, only installs in a git repo, never overwrites unrelated hook content.
- [ ] Test (integration): a commit in a temp repo writes one `commit` record (tool/run_id from env, default `unknown`), bumps revision; if the commit's changed paths match the manifest glob (`package.json|package-lock.json|pnpm-lock.yaml|requirements.txt|pyproject.toml|uv.lock|Cargo.toml|go.mod|Gemfile`) it ALSO writes a `dep-change` record. Hook body is fire-and-forget (backgrounded, `exit 0`, ≤ the 28 ms precedent, never fails the commit).
- [ ] Implement; commit.

### Task 7: Orchestrator presence/phase writes + read surfacing
**Files:** Modify `agents/build-orchestrator.md`; Test `scripts/app_pulse/test_orchestrator_contract.py` (dry-run envelope assertions, mirroring existing `test_per_commit_dispatch_dryrun.py` style)
- [ ] Test: orchestrator preamble writes presence; each phase-start appends a `phase` record + calls `checkpoint_read` and, when peers/dep/arch reactions present, the documented surfacing block appears; soft-claim is a WARNING never a block (D4).
- [ ] Implement doc + the contract test; commit.

### Task 8: SessionStart + pre-edit hook wiring
**Files:** Modify `hooks/hooks.json`; Create `hooks/session-start-apppulse.sh`; Test `hooks/test_hooks.sh` cases
- [ ] Test: SessionStart runs `checkpoint_read`, prints a one-line restore only when delta exists, silent otherwise, `exit 0` always, no stdout on empty (Stop-hook discipline from memory). PreToolUse(Edit) does a cheap revision-stat-only hint, never blocks.
- [ ] Implement; commit.

**Stage 1 acceptance:** two local sessions (simulated via two `run_id`s) on the same app — one commits, the other's `checkpoint_read` surfaces it within one call; dep-manifest commit triggers a `dep-change`; reaper clears a killed session; all hooks fire-and-forget.

---

## Stage 2 — Native-scanner enrichment

### Task 9: Open taxonomy registry
**Files:** Create `src/build_loop/architecture/_taxonomy.py`, `arch/_taxonomy.json`; Test `test_taxonomy.py`
- [ ] Test: known types seeded; `register_type(new)` is additive + persisted; lookup of unknown returns a generic descriptor (never raises); no schema-version bump on add (D7).
- [ ] Implement; commit.

### Task 10: Schema extension
**Files:** Modify `src/build_loop/architecture/schemas.py`; Test `test_schemas_enriched.py`
- [ ] Test: new node types + dataflow edge types accepted; `type` is open-vocab (unknown → warn, retained, not dropped); existing import-graph behavior unchanged (regression).
- [ ] Implement; commit.

### Task 11: Deterministic detectors
**Files:** Create `src/build_loop/architecture/detectors.py`; Test `test_detectors.py` + fixtures `tests/test-fixtures/arch-enrich/*`
- [ ] Fixtures with known sites: anthropic SDK call, openai call, generic `fetch`/`requests` to a URL, an MCP tool call, `redis`/`bullmq`/db/object-store import, and each manifest type.
- [ ] Test: detector emits correct *unlabelled* node type + located file:line + surrounding context slice; emits zero usage-count fields (non-goal guard — assert no frequency/count keys exist).
- [ ] Implement (stdlib `ast` + regex); commit.

### Task 12: Enrich orchestration + semantic-todo handoff
**Files:** Create `src/build_loop/architecture/enrich.py`; Modify `agents/architecture-scout.md` (new `task: enrich`); Test `test_enrich.py`
- [ ] Test: `enrich.py` builds nodes/edges from detectors + a `semantic_todo[]` of sites needing labels; it NEVER fabricates `purpose`/`model_class` (those stay null pending scout). Scout doc spec'd to fill `model_class`(open vocab)+`model_example`(marked illustrative, D6)+`purpose`+data-in/out prose, write back, no external API (D5).
- [ ] Implement enrich.py + scout doc + the contract test; commit.

### Task 13: Diagram generator
**Files:** Create `src/build_loop/architecture/diagram.py`; Test `test_diagram.py`
- [ ] Test: deterministic `graph.json`→`diagram.mmd`+`diagram.dot`; vertical layer rank ordering (UI/edge→service→queue/cache→store→external), horizontal dataflow peers; unknown node type → generic layer (no crash); stable across reruns (byte-identical for identical graph).
- [ ] Implement; commit.

### Task 14: Digest writer + manifest trigger unblock
**Files:** Modify Stage-1 digest path; Modify `hooks/pre-edit-architecture.sh`; Test `test_digest.py`, `hooks/test_hooks.sh`
- [ ] Test: `arch/digest.json` carries per-type counts + API/MCP/LLM inventory hash + dep-manifest hash + adjacency matrix (no frequency data — non-goal guard). pre-edit hook no longer excludes manifests; manifest edit marks enrich-needed (defers actual enrich to scout pass per OQ3), does not run enrichment inline.
- [ ] Implement; commit.

**Stage 2 acceptance + full-chain test:** a fixture app yields llm/mcp/api/infra nodes
with dataflow edges; scout labelling produces model-class-agnostic LLM nodes; diagram
renders; a deliberately novel node type survives writer→digest→diagram→validator→
checkpoint-read without being dropped.

---

## Stage 3 — Codex cross-tool validation

### Task 15: Automated cross-tool tests (V1–V3)
**Files:** Create `scripts/app_pulse/test_cross_tool.py`
- [ ] V1: write `commit`+`dep-change` records under a `tool=codex` identity to the `$HOME` slug path; assert a Claude-identity `checkpoint_read` surfaces them via the slug path AND via the installed-cache code path (`sys.path` → cache scripts dir, the dual-path method proven this session); reverse direction.
- [ ] V2: codex-identity presence in Phase 3 owning files → claude phase read warns; advance clock past heartbeat → reaped, no live peer.
- [ ] V3: codex enrich changes inventory hash → claude pre-edit read reports "API/LLM surface changed."
- [ ] Implement; commit.

### Task 16: Plain-language Codex runbook
**Files:** Create `docs/_inbox/codex-apppulse-validation.md`
- [ ] Sibling of `codex-postgres-validation.md`: numbered steps, exact commands, expected output, what to report back, cleanup. Covers V1–V4 (V4 = the human-run loop closing).
- [ ] Commit.

### Task 17: Live Codex validation + status block
**Files:** Modify `docs/DESIGN_2026-05-17_app-pulse...md`
- [ ] After a real Codex session runs the runbook and Claude confirms via dual-path read, append a "Validated <date>" block with the evidence (mirrors the DB-resolver close this session). If Codex unavailable, mark ⚠️ pending with the exact command, do not claim closed.

---

## Self-Review

**Spec coverage:** Goals 1–3 → Stages 1/2 + Task 7/8 (Goal 1), Tasks 9–14 (Goal 2),
Stage 3 (validation); self-contained / no NavGator dep → Stage 2 uses only stdlib + own
scanner (D8 ✓); non-goal "no observability" → explicit guard assertions in Tasks 11 & 14.
D1–D8 each map to a task (D1→T1, D2→T14 digest+pointer, D3/D4→T7, D5→T12, D6→T12,
D7→T9/T10, D8→Stage 2). OQ1–3 resolved in header + Tasks 1/4/12/14. No gap found.

**Placeholder scan:** no TBD/TODO; each task names exact files + concrete test
obligations + commit. Code-literal micro-steps intentionally delegated to build-loop
Phase 2 (flagged major decision) — acceptance gates are concrete and testable.

**Type consistency:** `checkpoint_read` envelope shape consistent across T5/T7/T8/T15;
record schema defined once (T3) and referenced; presence schema once (T4); taxonomy
registry single source (T9) consumed by T10/T13/T14.

---

## Execution Handoff

Per project convention, execution does NOT use superpowers:subagent-driven-development.
It routes through **build-loop** (`build-loop:build-orchestrator`, subagent-driven,
worktree-isolated), staged 1 → 2 → 3, each stage its own build-loop run with
plan-verify + plan-critic + the cross-tool acceptance gate before advancing.
