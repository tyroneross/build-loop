# App Pulse — cross-session/cross-tool liveness + live data-flow architecture map

**Date:** 2026-05-17 · **Status:** design approved, pre-plan · **Ships in:** central build-loop plugin
**Predecessor research:** `.build-loop/research/2026-05-17-live-architecture-dependency-map.md`

## Context

Two build-loop sessions (one Claude, one Codex/ChatGPT) routinely work the *same app*
concurrently. Today neither knows what the other just changed; the intended coordinator
(`session_registry.py` → `~/.build-loop/sessions/`) is dead (never fires — KNOWN-ISSUE).
Separately, build-loop already maintains a per-app architecture map, but its native
scanner is import-graph-only and deliberately excludes the things that matter for
coordination: API/MCP calls, LLM call sites, infra components, and dependency manifests.

These two needs share one primitive. This spec collapses them: a single per-app shared
channel that carries both *what peers are doing* and *how the app's architecture/data-flow
just changed*, so any session — regardless of tool or checkout — reacts quickly.

This session **empirically proved** Claude and Codex resolve the same `$HOME`-keyed
build-loop paths and the same Postgres store. App Pulse builds directly on that proof.

## Goals

1. Any change to an app (commit, dependency-manifest change, build-loop phase transition,
   architecture-scan completion) is communicated to any other build-loop session on the
   same app within one checkpoint poll, across Claude **and** Codex.
2. A live, **structural data-flow** architecture map per app: where LLM/MCP/API calls are,
   what model-class + purpose + runtime, and what data flows in→through→out of every
   infra/code/external component — rendered as a diagram-ready artifact.
3. Self-contained in central build-loop. No NavGator runtime dependency (its taxonomy is
   reference inspiration only).

## Non-goals (explicit)

- **No observability / usage-frequency inventory.** "How often an LLM/API is called" is
  out of scope. Structure and data-flow only.
- No daemon / real-time push. Checkpoint-poll only (user decision).
- No locking/blocking of peers. Awareness + soft-claim warnings only.
- No external API for the semantic step — the running Claude / `architecture-scout`
  subagent does interpretation (Claude-is-the-LLM rule).

## Locked decisions

| # | Decision |
|---|---|
| D1 | Channel lives at `~/.build-loop/apps/<app-slug>/`, slug via the existing `scripts/_paths.derive_slug_from_cwd` (worktree/clone-independent — proven shared) |
| D2 | Architecture scan stays project-local in `.build-loop/architecture/`; only a compact `arch/digest.json` (+ pointer) is published to the shared channel |
| D3 | Delivery = checkpoint poll (no daemon) |
| D4 | Coordination = awareness + soft-claim warning, never lock |
| D5 | Detection deterministic/stdlib; semantic labelling by in-session Claude/scout, no external API |
| D6 | LLM nodes are model-agnostic: `model_class` (open vocab) is durable; `model_example` is illustrative/may-go-stale |
| D7 | Node/edge taxonomy is an open, growable controlled vocabulary (`arch/_taxonomy.json`); validator warns-not-drops on unknown; unknown threads full chain |
| D8 | No NavGator runtime dep; enrich build-loop's own native scanner |

## Architecture spine

`~/.build-loop/apps/<app-slug>/`:

| Artifact | Shape | Role |
|---|---|---|
| `revision` | cross-process-locked integer | cheap "did anything change" — one stat+read per checkpoint |
| `changes.jsonl` | append-only, immutable | durable event log (commit / dep-change / phase / arch-scan-complete) |
| `sessions/<session-id>.json` | overwrite-in-place | live presence: tool, model, run_id, files-in-flight, phase, `heartbeat_ts`, per-session read cursor (`revision` + `changes.jsonl` byte offset) |
| `arch/digest.json` + `arch/pointer` | compact JSON | per-type node counts, API/MCP/LLM call-inventory hash, dep-manifest hash, stable-ID adjacency matrix; pointer → the project-local full graph |

## Architecture model (sub-project 2)

**Node taxonomy** (extends, does not replace, the one-component-per-file model):
`code-component` · `infra-component` (kind: redis/kv-cache/queue·bullmq/db/object-store; +purpose +runtime) · `llm-callsite` (`model_class`, `model_example`, provider, purpose, runtime_location) · `mcp-callsite` (server+tool, purpose, trigger) · `api-callsite` (target, purpose, trigger) · `external-service` · `dependency` (manifest entry). Open vocab per D7.

**Edge taxonomy is data-flow-first:** `data-in` (source→node: what data, from where) · `transforms` (what happens here) · `data-out` (node→sink: what data, to where) · `invokes` (callsite→target) · `runs-on` (node→runtime) · plus existing `imports`.

**Detection/labelling split (D5):**
- Deterministic, stdlib, no LLM: locate call sites + components — AST/regex for
  anthropic/openai/fetch/MCP-tool/redis/bullmq + manifest parse. Reproducible, hook-safe.
- Semantic, in-session Claude/scout at scan time: `purpose`, `model_class`, the
  data-in/out prose, the trigger/why. Scanner hands located sites + context; Claude annotates.

**Output (diagram-ready):**
1. `.build-loop/architecture/graph.json` — enriched nodes+edges, stable IDs, layer rank, dataflow payload descriptors. Machine canonical.
2. `.build-loop/architecture/diagram.{mmd,dot}` — **deterministically** generated from graph.json (no LLM): vertical = layer rank (UI/edge → service → queue/cache → store → external), horizontal = data-flow peers.
3. `arch/digest.json` (shared channel) — counts + inventory hashes + adjacency matrix; full graph only via the pointer.

## Capture points (Section 3 — cross-tool, three-mechanism)

| Event | Writer | Cross-tool rationale |
|---|---|---|
| commit | idempotent `.git/hooks/post-commit` build-loop installs | git fires regardless of tool |
| dep-manifest change | post-commit hook diffs changed paths vs manifest glob → high-priority `dep-change`; orchestrator hash-checks at phase boundary as backstop | commit is the durable boundary; un-blocks what pre-edit allowlist excludes |
| phase / presence | `build-orchestrator` writes presence + phase record per phase (explicit call) | orchestrator runs under both tools |
| arch-scan-complete | existing `_arch_scan_bg.py` + enriched scanner → `arch/digest.json` | reuses existing single-flight `flock` |

All writes atomic (JSON tmp+rename, JSONL `O_APPEND`), fire-and-forget, never block/fail
the host action; `revision` bump under short-timeout lock — on timeout skip the bump
(one-cycle staleness, never corrupt).

## Read / consume flow (Section 4 — checkpoint poll)

Per-session cursor in the session's own presence file → delta-only reads.

- **SessionStart** (Claude hook / Codex via orchestrator preamble): `revision` newer →
  read changes tail + active presence + `arch/digest.json`; surface compact restore.
- **Phase start** (orchestrator): peer owns overlapping files → soft-claim warning (D4).
- **Pre-edit** (existing PreToolUse): cheap `revision` stat; on change → determinate hint
  (dep-change → reinstall; arch-change → re-baseline).
- Cost: one stat + (on change only) one tail read.

## Codex cross-tool validation plan

First-class, not an afterthought — reuses the empirical method proven this session.

**V1 — channel round-trip.** From a Codex session on app `X`: trigger a commit →
assert `~/.build-loop/apps/<slug-X>/changes.jsonl` gains a `commit` record with
`tool: codex` and `revision` bumped. From a Claude session on the same app: run the
SessionStart checkpoint read → assert it surfaces the Codex commit + Codex presence.
Reverse (Claude writes, Codex reads). Both via the `$HOME`-keyed slug path **and** each
tool's installed-plugin-cache code path (the dual-path check we used for the DB-resolver
proof).

**V2 — presence/soft-claim.** Codex orchestrator enters Phase 3 owning files A,B →
Claude phase-start read surfaces "peer owns A,B" warning. Kill the Codex session →
after the heartbeat window, Claude read no longer reports a live peer (reaper works).

**V3 — arch digest cross-tool.** Codex run completes an enriched scan → `arch/digest.json`
inventory hash changes → Claude pre-edit read reports "API/LLM surface changed since last
edit." Confirms the digest (not full graph) is sufficient for the reaction.

**V4 — plain-language Codex runbook.** Ship a `docs/_inbox/`-style plain-language
procedure (like `codex-postgres-validation.md`) so a Codex session can self-run V1–V3 and
hand results back, closing the loop empirically each release.

## Failure modes

Graceful absence (read no-ops, lazy-create — zero regression) · stale presence reaped by
`heartbeat_ts` · `changes.jsonl` append-only/immutable · reader never locks · worktree
divergence solved by D1 · unknown node type warns-not-drops + diagram generic-layer
fallback.

## Testing

- Cross-tool parity (V1–V3 above) — the empirical acceptance, not just units.
- Scanner-enrichment fixtures: known anthropic/openai/fetch/MCP/redis/bullmq sites +
  manifests → assert node types, `model_class` abstraction, dataflow edges, `diagram.mmd`
  renders.
- **Full-chain new-node-type test:** emit a brand-new node type → assert it survives
  writer→digest→diagram→validator→read without drop (guards the schema-migration
  silent-drop class).
- Concurrency race on revision bump → monotonic-or-skip, no corruption.
- Fire-and-forget latency bar (host action unaffected; ≤ the 28 ms Stop-hook precedent).

## Risks

- Native-scanner enrichment re-implements work NavGator already solved (accepted per D8
  for zero external coupling) — mitigate by keeping the `.build-loop/architecture/`
  consumer schema frozen, enrich-only.
- Semantic step depends on in-session Claude attention (scout) — mitigate via explicit
  build-orchestrator invocation (same mechanism that fixed memory's subagent gap).
- Slug collisions across unrelated repos sharing a basename — reuse `_paths` validation
  that already defends the memory store.

## Open questions for the plan phase

1. `app-slug` for multi-root / monorepo apps — reuse memory's `<slug>/workers`
   sub-component convention, or a new rule?
2. Heartbeat window + reaper interval defaults (propose 15 min stale, configurable).
3. Does the enriched scanner run inline in `_arch_scan_bg.py` or as a separate pass the
   scout triggers (token-cost tradeoff)?

## Next

Brainstorming complete → `superpowers:writing-plans` to produce the implementation plan
(staged: spine + capture first, scanner enrichment second, Codex validation third).
