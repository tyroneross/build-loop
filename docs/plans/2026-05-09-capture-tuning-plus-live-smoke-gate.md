# Plan — Capture-tuning + Live HTTP/SSE Smoke Gate

**Date:** 2026-05-09
**Branch:** `feat/codex-compat-quality-gap` (existing; commits ride on top)
**From:** `364811f` (current HEAD)
**Mode:** self-recursive build-loop, per-commit. Two cleanly-scoped commits, both land sequentially.

## Headline

Two unrelated but co-shipping plugin changes: (C1) silence the 80-orphan auto-capture noise without breaking the dual stdin/stdout contract locked by decision 0091, and (C2) close the pytest-with-mocks blind spot that let local-smartz ship 27 commits' worth of green tests with two real user-facing bugs by adding a live HTTP/SSE smoke gate to Phase 4 Validate when the project actually runs a server.

## Intent

Both commits encode rules already documented in build-loop-memory and `~/.build-loop/memory/` into runnable plugin behavior. C1 implements decision `0092`; C2 implements decision `_unscoped/0003` plus the `gotcha_serve_sse_live_smoke_required` and `feedback_buildloop_pytest_insufficient_for_runtime` patterns. No new dependencies, no UI changes, no behavior changes in unrelated code paths. Default-off for projects that don't trigger.

## Commit C1 — `feat(scripts): rollup low-signal architecture violations`

**Files owned:**
- `scripts/capture_arch_violation.py` (modify)
- `tests/test_capture_violation_contract.py` (extend; preserve existing 6 tests verbatim)

**modifies_api:** No public CLI flag changes; no stdin/stdout shape changes. Adds two side-effect outputs (jsonl + rollup MD) gated on filter match.

**Behavior change** (already specified in prompt — restating the gate):

Filter triggers when ALL three hold on a single violation:
- `confidence == 'inferred'`  ← computed from severity per existing logic (`warn`/`warning`/`major`/other → inferred`)
- `source == 'auto-inferred'`  ← always set by this script when capture is automated; equals `f"auto-{confidence}"` so equivalent to confidence==inferred
- `rule in {'orphan', 'hotspot'}`  ← rule_id field, post-shape-normalization

When the filter matches:
1. Skip the `_invoke_write_decision` call entirely (do NOT create a per-violation MD).
2. Append one line to `<workdir>/.episodic/architecture/auto-violations.jsonl` with shape `{ts, project, rule, entity, confidence, source, severity}`. Project = basename of `--workdir`. Entity = first component path (or `"unknown"` if components empty). Use append-only mode with `mkdir(parents=True, exist_ok=True)`.
3. Mark the violation for the per-scan rollup.

When the filter does NOT match (every other case): existing `_invoke_write_decision` flow runs unchanged.

After the per-violation loop, if any filter-matched violations accumulated this scan: write/upsert ONE rollup decision MD per scan to `decisions/<project>/<NNNN>-<date>-architecture-violation-rollup.md`. Use the same atomic temp+rename pattern as `_atomic_write_json`. Run-id derivation: `os.environ.get("BUILD_LOOP_RUN_ID")` → else current ISO date `YYYY-MM-DD`. NNNN: scan `decisions/<project>/` for the highest existing 4-digit prefix and increment by 1; if no decisions dir or no numbered files, start at `0001`. The rollup file body is a markdown table of (rule, entity, severity, first-seen-or-carried-from-baseline) with a header line that counts total filtered entries. Re-running the same scan overwrites the file with the latest counts (idempotent).

If zero filter-matched violations this scan: do NOT create the jsonl directory if it doesn't exist, do NOT write a rollup MD, do NOT append empty content.

**Hard constraints (preserve):**
- Dual stdin envelope shapes (`{rule_id, components: [...]}` long-form AND `{rule, component_id|component_ids}` short-form) accepted by parser. Lines 333-344 of current script.
- `--no-db` propagated unchanged.
- `--dry-run` flag still works AND skips both jsonl append and rollup write.
- Stdout shape: `{new_count, dedup_count, decision_files, schema_version}` keys preserved. ADD an OPTIONAL key `rollup` (object) when filter fired this scan: `{path: ".../rollup.md", entries: N}`. Keys other than `rollup` keep their existing meaning. (Adding a new top-level key is contract-additive, not contract-breaking — existing tests check `new_count` and don't fail on extra keys.)
- All 6 existing tests in `test_capture_violation_contract.py` pass unchanged.

**New tests** (extend `test_capture_violation_contract.py`, NOT a new file — keeps the contract suite cohesive):

1. `test_filter_skips_inferred_orphan_writes_jsonl_and_rollup` — non-dry-run, with five mixed violations: two orphan-rule entries at warn severity (these resolve to `inferred` confidence so the filter matches; jsonl + rollup, no per-violation MD); one hotspot-rule entry at warn severity (filter matches); one circular-dependency entry at error severity (filter does NOT match; full per-violation MD); one orphan-rule entry at error severity (filter does NOT match because confidence resolves to `confirmed`, not `inferred`; full per-violation MD). Assert: jsonl line count equals three (the two warn-severity orphan entries plus the hotspot); exactly one rollup MD exists; exactly two per-violation decision MDs were created (the two error-severity entries); registry has five entries total.
2. `test_zero_inferred_orphan_no_jsonl_no_rollup` — non-dry-run, single circular-dependency violation only. Assert: no jsonl path created, no rollup MD created, one per-violation MD created.
3. `test_dry_run_skips_jsonl_and_rollup_even_when_filter_matches` — dry-run with two warn-severity orphan-rule entries. Assert: no jsonl, no rollup, no per-violation MDs (existing dry-run contract).

synthesis_dimensions:
  filter_location: between dedup-check and `_invoke_write_decision`, BEFORE updating `violations_out` so the registry stays correct for filtered violations too (still tracked in the registry, just not written as MD files).
  rollup_write_timing: AFTER the per-violation loop, AFTER `_atomic_write_json` of the registry (so the rollup references final counts), BEFORE the stdout JSON dump (so the `rollup` stdout key reflects the actually-written path).
  jsonl_shape: `{ts, project, rule, entity, confidence, source, severity}` — flat keys, one JSON object per line, no nesting, no version field (the file path is the schema marker).
  rollup_md_format: minimal — H1 title, count line, table of (rule, entity, severity). No frontmatter; rollups are audit-trail summaries, not full decisions; full decisions go through `write_decision.py`.
  run_id_derivation: `os.environ.get("BUILD_LOOP_RUN_ID")` → else current ISO date `YYYY-MM-DD`. Predictable for tests; survives orchestrator restart.
  numbered_prefix_collision: scan existing decision filenames in `decisions/<project>/` for the max 4-digit prefix and `+1`. Same approach `write_decision.py` uses; accept the rare race for an audit-trail file (no atomic counter needed).

LoC target: capture script grows ~80 LoC (filter branch + rollup writer + jsonl appender). Tests grow ~120 LoC (3 new tests, fixtures). All net additive.

## Commit C2 — `feat(validate): live HTTP/SSE smoke when project runs a server`

**Files owned:**
- `scripts/detect_runtime_server.py` (NEW)
- `tests/test_detect_runtime_server.py` (NEW)
- `tests/test-fixtures/runtime-server-positive/serve.py` (NEW)
- `tests/test-fixtures/runtime-server-positive/__init__.py` (NEW, empty)
- `tests/test-fixtures/runtime-server-negative/cli.py` (NEW)
- `tests/test-fixtures/runtime-server-negative/__init__.py` (NEW, empty)
- `tests/test-fixtures/runtime-server-no-ui/server_only.py` (NEW)
- `tests/test-fixtures/runtime-server-no-ui/__init__.py` (NEW, empty)
- `skills/build-loop/SKILL.md` (modify — Phase 1 ASSESS + Phase 4 Validate sections)
- `agents/build-orchestrator.md` (modify — Phase 1 + Phase 4 sections)

**modifies_api:** Adds new public detector script with stable `--workdir <repo> --json` contract. No changes to existing scripts. Adds two new state.json fields (`triggers.runtimeServer`, `runtimeServerInfo`) — additive only.

**Detector contract (stdlib only, no deps):**

Heuristic order (deterministic):
1. Walk `<workdir>` recursively; skip `node_modules/`, `.git/`, `.venv/`, `venv/`, `__pycache__/`, `.episodic/`, `.build-loop/`, `dist/`, `build/`. Read `.py` and `.js`/`.ts` files with size cap 200 KB.
2. For each file, regex-search for two paired conditions:
   - **HTTP/SSE substrate**: any of `BaseHTTPRequestHandler`, `aiohttp.web.Application`, `from fastapi`, `from flask`, `import bottle`, `wsgiref.simple_server`, `EventSourceResponse`, `text/event-stream`.
   - **Event-emit pattern**: any of `_send_event`, `yield {"type":`, `yield {'type':`, `Event(`, `EventSourceResponse`, `self.wfile.write(b"data:`, `self.wfile.write("data:`.
3. If both present in same file → that file is the `server_module`.
4. SSE route detection: in the same file, look for the path string used by an SSE handler — regex `do_POST` or `if self.path == "([^"]+)"` paired with `text/event-stream` in nearby ±20 lines, OR `@app\.(get|post)\(["']([^"']+)["']` paired with `EventSourceResponse`. First match wins.
5. Default port: regex `--port[", ]+(\d+)` or `port=(\d+)` in same file. First match.
6. Embedded UI: same file contains BOTH `<!DOCTYPE html>` (case-insensitive) AND `<script>` AND (`EventSource(` OR `function handleEvent` OR `onmessage`). If yes → `embedded_ui_module = server_module`.
7. Event-handler locations: when embedded UI is present, regex `function handleEvent` or `\.onmessage = function` to find line numbers. Return as list of `{file, line, function}` (function name extracted from match).
8. If steps 2-3 don't match → `runtimeServer: false`, all other fields null/empty arrays. SILENT.

Return JSON exactly as specified in the prompt:
```json
{
  "runtimeServer": true|false,
  "server_module": "src/...py" | null,
  "sse_route": "/api/research" | null,
  "default_port": 11435 | null,
  "embedded_ui_module": "src/...py" | null,
  "event_handler_locations": [{"file":"...","line":N,"function":"..."}],
  "evidence": ["matched ... in <file>"]
}
```

Paths are `os.path.relpath(file, workdir)`. `evidence` lists 3-6 short matched-pattern strings.

**Skill SKILL.md edits:**

Insert new step after current step 6 (Observability baseline). Renumber? No — the current list has steps numbered 1-18 with mixed integers, and inserting "step 6.5" or just a new bullet preserves git diff readability. Use the bullet form `- **Runtime-server detection** (informational, no changes): ...` matching adjacent observability/debugger steps' style. Place immediately after step 6 (Observability baseline) and before step 7 (Debugger context priming).

In Phase 4 Sub-step B Validate, insert a "Live HTTP/SSE smoke" section AFTER the "Visual validation" block (which is the current last block before the LLM-as-judge graders). Wrap entire block with `<details>` markdown collapse plus the gating logic in prose: fires when `triggers.runtimeServer == true` AND diff touches `runtimeServerInfo.server_module` OR `runtimeServerInfo.embedded_ui_module`. The 5-step procedure (start → wait → curl → parse handlers → fail on mismatch) goes here verbatim from the prompt, lightly trimmed to skill prose voice.

**Agent build-orchestrator.md edits:** mirror in the agent's Phase 1 ASSESS step list (the orchestrator already has its own enumerated step list distinct from the skill's) and Phase 4 sub-step B section. Skim agent file first to find correct insertion lines.

**Tests (`tests/test_detect_runtime_server.py`):**
- `test_positive_fixture_returns_runtime_true_with_paths` — assert all 7 fields populated correctly, `event_handler_locations` non-empty.
- `test_negative_fixture_returns_runtime_false_silent` — assert `runtimeServer: false`, all other fields null/empty, no errors, exit 0.
- `test_no_ui_fixture_returns_runtime_true_but_empty_handlers` — assert server_module set, embedded_ui_module null, event_handler_locations empty.
- `test_json_output_is_valid` — pipe `--json` output through `json.loads`; structure check.
- `test_skips_node_modules_and_venv` — fixture symlinks/dirs that would match if scanned; assert detector ignores them.

synthesis_dimensions:
  detection_scope: file-walk with extension+size filter + skip-dir set. Stdlib `os.walk` + regex; no glob library, no AST parser (overkill).
  pairing_logic: substrate AND emit pattern in the SAME file → that file is the `server_module`. Single-pattern matches don't trigger (avoid false positives on test files that import e.g. `flask` for unit tests).
  embedded_ui_detection: keep separate from server detection. A project can have `runtimeServer: true` without an embedded UI (API-only services). Tests cover both cases.
  skip_dirs: explicit allowlist of common skip directories, NOT a `.gitignore` parser. Self-recursive note: `.build-loop/`, `.episodic/` skipped.
  failure_mode: helper failure → `runtimeServer: false` + one-line warning. Phase 4 Validate treats detector outage as "no smoke gate" rather than blocking the build (smoke "doesn't fail on infrastructure issues — only on the specific contract violation").
  state_json_field_naming: `triggers.runtimeServer: bool` (joins existing trigger flags) plus `runtimeServerInfo: {...full envelope...}`. Two fields: trigger flag for cheap branch checks, full envelope for the gate body that needs paths/lines.

LoC target: detector ~180 LoC (mostly the pattern lists + walker). Tests + fixtures ~250 LoC total. Skill+agent edits ~80 LoC combined.

## Plan-acceptance gate

- **plan-verify** — runs via `python3 scripts/plan_verify.py` once this plan is on disk.
- **plan-critic** — dispatched after plan-verify clears.
- **scope-auditor** — both commits enumerate `modifies_api` notes above. C1's API surface is bounded: the stdout key list grows with one optional key, the stdin envelope shape is unchanged, and no existing caller relies on the absence of extra keys. C2 introduces no caller-site change: it ships a brand-new detector script (no callers yet) plus prose edits in skill+agent docs. No symbol rename, no signature change, no stdin/stdout contract change in any pre-existing surface. permission_tier: T0 — internal validation script with no external network call, no shell exec beyond existing curl in the smoke gate prose, read-only filesystem walk.

## Out of scope (deliberately)

- Renumbering the SKILL.md Phase 1 step list. New step inserts as a bullet matching adjacent style; renumbering risks merge conflicts with concurrent edits and adds churn.
- Re-running `regenerate_knowledge_index.py` on `decisions/build-loop/` — that's a `build-loop-memory` repo task, separate concern.
- Postgres `agent_memory.semantic_facts` re-sync of archived violations — also a `build-loop-memory` concern, decision 0092 §Consequences.
- Adapter selection in the smoke gate (Next.js vs FastAPI vs Flask). The MVP gate uses generic `curl` + observed-vs-handler check; adapter routing can come later if needed. Decision 0003 §Consequences phrases the gate exactly this way.
- A Codex-host equivalent of the gate. Codex doesn't ship a runtime gate; skill+agent text is the same for both hosts.

## Test plan (final)

After both commits:
- `cd /Users/tyroneross/dev/git-folder/build-loop && python3 -m pytest tests/ -x -q` — full suite green.
- Spot-check the new tests run in isolation: `python3 -m pytest tests/test_capture_violation_contract.py tests/test_detect_runtime_server.py -v`.
- `python3 scripts/check_cache_sync.py --host claude --source .` and same for `--host codex` — surface divergence.
