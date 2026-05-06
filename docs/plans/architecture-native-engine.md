# Plan: native architecture engine for build-loop

Status: **draft, not started**
Target version: **v0.11.0**
Authoring date: 2026-05-05

## Context

The README at `RossLabs-AI-Toolkit/README.md:26` already commits to this direction: *"Native architecture awareness: a Python-native engine (scan/impact/trace/rules/dead/lessons) under `src/build_loop/architecture/` with NavGator demoted from hard dep to optional escalation adapter (auto/native/navgator modes)."* This plan turns that promise into shipping code.

Today, build-loop's six architecture skills (`skills/architecture/{scan,impact,trace,rules,dead,review}/SKILL.md`) are methodology mirrors of NavGator's skills. They carry `source:` + `source_hash:` provenance and a `sync-skills` drift checker. The actual graph-building, impact analysis, and rule checking happen in NavGator's TypeScript engine (`~/dev/git-folder/NavGator/src/`, 1.5 MB / 112 files). When NavGator isn't installed, build-loop's skills give guidance but no data.

This plan ports the engine to Python and folds it into build-loop, while keeping NavGator installable standalone.

## Goals

1. **Self-sufficient build-loop.** No "install NavGator first" step. Architecture awareness is always on at Phase 1 Assess, Phase 4 Review-D (rules), Phase 5 Iterate (impact-aware fan-out), and debug-loop (impact during stuck-iteration cascade).
2. **Python-native engine.** Match build-loop's existing language; pip-installable with no Node runtime requirement.
3. **NavGator standalone unchanged.** `npm i -g @rosslabs/navgator` keeps working; `gator scan|review|trace|impact` CLIs preserved; the standalone marketplace listing stays.
4. **Three modes** wired through a single facade: `auto` (use bundled native), `native` (force native, ignore any installed NavGator), `navgator` (escalate to installed NavGator binary when present, fall back to native otherwise). Default `auto`.
5. **Aggressive freshness.** SessionStart + PreToolUse `Edit`/`Write` hooks trigger incremental scans, single-flight, extension-allowlisted (no doc-edit churn).
6. **Architecture Context Pack (ACP)** auto-injected into Phase 2/3/4/5 subagent briefs.
7. **`architecture-scout`** read-only Sonnet subagent dispatched at Phase 1 baseline / Phase 2 chunk-impact fan-out / Phase 4-D rules / Phase 5 iterate-subgraph / Phase 6 learn-sync.

## Non-goals

- Diagrams (`navgator diagram`). Stays in NavGator standalone.
- Dashboard / UI / web interface. Stays in NavGator standalone.
- LLM use-case map (`mcp__navgator__llm_map` tool). Available via NavGator MCP if installed; not folded.
- Runtime topology annotations. Stays in NavGator standalone.
- Real-time file watcher daemon. Hook-driven scans are enough for build-time use cases.

## Target architecture

```
build-loop/
├── src/build_loop/                       (NEW — Python package)
│   └── architecture/
│       ├── __init__.py                   public facade: scan(), impact(), trace(), rules(), dead(), lessons()
│       ├── _ignore.py                    .navgatorignore + sensible defaults
│       ├── _walker.py                    glob/pathlib file enumeration with ignore filtering
│       ├── scanner/
│       │   ├── __init__.py               main entry: scan(workdir, mode='incremental'|'full') -> ScanResult
│       │   ├── _components.py            component classification (npm pkg, pip pkg, swift pkg, file, route, model, …)
│       │   ├── _connections.py           edge extraction (imports, service calls, prisma calls, queue refs, env refs, llm calls)
│       │   ├── packages/{npm,pip,swift}.py
│       │   ├── infrastructure/{prisma,env,queue,cron,deploy}.py
│       │   └── connections/{import,ast,service,prisma_call,llm}.py
│       ├── impact.py                     blast radius (transitive reverse-deps with depth + edge-type filters)
│       ├── trace.py                      directed traversal between two components, multi-path
│       ├── rules.py                      rule engine: circular-dep, layer-violation, hotspot, orphan, fan-out, db-isolation, frontend-direct-db
│       ├── dead.py                       orphan detection (components with no edges, packages declared-but-unused)
│       ├── lessons.py                    Lessons store: read/write `.navgator/lessons/lessons.json` + sync to personal_memory
│       ├── store.py                      `.navgator/architecture/{index,graph,components/,reverse-deps,manifest,hashes,timeline}.json` writer/reader
│       ├── adapters/
│       │   ├── navgator_cli.py           shells out to `gator` binary when mode='navgator' and present
│       │   └── escalation.py             auto-fallback logic
│       ├── acp.py                        Architecture Context Pack builder (compact subagent brief)
│       ├── scout.py                      `architecture-scout` subagent dispatcher (Phase 1/2/4-D/5/6 entry points)
│       └── types.py                      typed dicts: Component, Connection, ScanResult, ImpactResult, RulesReport, …
├── tests/architecture/                   (NEW)
│   ├── conftest.py
│   ├── fixtures/                         minimal sample repos (TS web, Python ML, Swift iOS)
│   ├── test_scan.py
│   ├── test_impact.py
│   ├── test_trace.py
│   ├── test_rules.py
│   ├── test_dead.py
│   ├── test_lessons.py
│   ├── test_acp.py
│   └── test_modes.py                     parity vs `gator` binary when available
├── agents/architecture-scout.md          (NEW — Sonnet read-only)
├── hooks/hooks.json                      (MODIFY — add SessionStart + PreToolUse Edit/Write hook)
├── agents/build-orchestrator.md          (MODIFY — Phase 1 mandatory invocation; ACP injection at Phase 2/3/4/5)
└── skills/architecture/                  (MODIFY — methodology stays; engine call points flip from `gator` → bundled)
```

## Module-by-module port mapping

NavGator → build-loop translation. Numbers are TS LoC; Python ports typically come out 30–50% smaller.

| NavGator file | LoC (TS) | build-loop module | Notes |
|---|---|---|---|
| `src/scanner.ts` | 2007 | `architecture/scanner/__init__.py` + `_components.py` + `_connections.py` | Main orchestrator. Splits into sub-modules. Mostly glob + dispatch. |
| `src/scanners/packages/npm.ts` | ~150 | `architecture/scanner/packages/npm.py` | Reads `package.json`. Trivial. |
| `src/scanners/packages/pip.ts` | ~120 | `architecture/scanner/packages/pip.py` | Reads `pyproject.toml` + `requirements*.txt` + `uv.lock`. |
| `src/scanners/packages/swift.ts` | ~120 | `architecture/scanner/packages/swift.py` | Reads `Package.swift`. |
| `src/scanners/infrastructure/{prisma,env,queue,cron,deploy}.ts` | ~600 total | `architecture/scanner/infrastructure/*.py` | Each scanner is regex + file existence check. |
| `src/scanners/connections/{import,ast,service,prisma_call,llm}.ts` | ~800 total | `architecture/scanner/connections/*.py` | Mostly regex. The TS-AST scanner uses `ts.createSourceFile`; Python port uses `tree-sitter-typescript` bindings (or skip TS deep-AST and stay regex-only — investigate during port). |
| `src/impact.ts` | 129 | `architecture/impact.py` | BFS over reverse-deps with depth limit + edge-type filter. Trivial. |
| `src/trace.ts` | 373 | `architecture/trace.py` | Directed BFS/DFS with multi-path collection. Moderate. |
| `src/rules.ts` | 576 | `architecture/rules.py` | Rule engine: cycle detection, layer order check, fan-in/out thresholds, db-isolation pattern, frontend-direct-db pattern. Moderate. |
| `src/dead-code-detection.ts` | (lookup) | `architecture/dead.py` | Orphan walker: zero in-edges + zero out-edges. Cross-checks declared-vs-imported packages. |
| `src/lessons-store.ts` | 384 | `architecture/lessons.py` + sync hook | Read/write JSON + new: write-through to `personal_memory.semantic_facts` with `domain='architecture'`. |
| `src/cli/commands/*.ts` | ~600 total | `architecture/__init__.py` public functions | Wrapped by Python-side CLI in `scripts/architecture.py` (matches existing build-loop CLI patterns). |

**Estimated effort:** 3–5 days of focused implementation, plus 2 days of test parity work against NavGator's existing test fixtures.

## Three modes — facade behavior

Single entry: `from build_loop.architecture import scan, impact, trace, rules, dead, lessons`

```python
# .build-loop/config.json or env: BUILD_LOOP_ARCH_MODE=auto|native|navgator
def _resolve_mode() -> str:
    return (read_config_or_env("ARCH_MODE") or "auto").lower()

def scan(workdir: Path, mode: str | None = None) -> ScanResult:
    mode = mode or _resolve_mode()
    if mode == "native":
        return _native_scan(workdir)
    if mode == "navgator":
        if _gator_available():
            return _gator_scan(workdir)
        # Fall through: log warning, run native.
    # auto: prefer native; the user can flip to navgator for parity verification.
    return _native_scan(workdir)
```

`_gator_available()` checks for the `gator` binary on PATH AND a recent enough version. Output schemas are normalized to a single `ScanResult` shape so callers don't care which path produced it.

## Hook wiring (aggressive freshness)

Two new hook entries in `hooks/hooks.json`:

```json
{
  "SessionStart": [
    {
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 \"$CLAUDE_PROJECT_DIR/scripts/architecture.py\" scan --incremental --background --quiet </dev/null >/dev/null 2>&1 & exit 0",
        "timeout": 5000
      }]
    }
  ],
  "PreToolUse": [
    {
      "matcher": "Edit|Write",
      "hooks": [{
        "type": "command",
        "command": "python3 \"$CLAUDE_PROJECT_DIR/scripts/architecture.py\" scan-file --tool-input \"$TOOL_INPUT\" --quiet || exit 0",
        "timeout": 3000
      }]
    }
  ]
}
```

**Single-flight + allowlist guards** inside the script:
- Lock file `.navgator/architecture/.scan.lock`; subsequent invocations no-op.
- Only re-scan if the touched file's extension is in `{.ts, .tsx, .js, .jsx, .py, .swift, .json, .toml, .yaml, .yml, .prisma, .env*}`.
- Doc-only edits (`.md`, `.txt`, `.html` in docs/) never trigger.
- Skip when inside `.git/`, `node_modules/`, `dist/`, `.venv/`, `__pycache__/`.
- `--background` mode uses `nohup ... &` so Edit returns immediately; Stop hook waits for the pending scan via lock.

## Architecture Context Pack (ACP)

`architecture/acp.py` builds a compact ≤2 KB JSON brief for subagents:

```json
{
  "schema": "acp/v1",
  "scan_age_seconds": 12,
  "components_in_scope": ["src/build_loop/architecture/scanner", "src/build_loop/architecture/rules"],
  "fan_in": {"src/build_loop/architecture/scanner/__init__.py": 4},
  "fan_out": {"src/build_loop/architecture/scanner/__init__.py": 7},
  "blast_radius": {
    "src/build_loop/architecture/scanner/__init__.py": ["scanner/__init__.py", "store.py", "scout.py"]
  },
  "active_rules": ["circular-dependency:error", "layer-violation:error", "hotspot:warn"],
  "open_violations": [],
  "lessons_in_scope": ["lesson-data-flow-prisma-isolation"]
}
```

Injected into:
- **Phase 2 Plan**: full-project ACP so the planner sees structure.
- **Phase 3 Execute**: per-subagent ACP scoped to the subagent's owned files.
- **Phase 4-D Validate**: full ACP for the rule-check sub-step.
- **Phase 5 Iterate**: impact-scoped ACP (only the affected subgraph).

## `architecture-scout` subagent

`agents/architecture-scout.md` (NEW): read-only Sonnet, tools `[Read, Grep, Glob, Bash]`. Dispatched by the orchestrator at:

| Phase | Purpose |
|---|---|
| Phase 1 baseline | Run incremental scan + write ACP to state. |
| Phase 2 chunk-impact fan-out | For each plan chunk, compute its blast radius, attach to chunk metadata. |
| Phase 4-D rules | Run `rules()` against the changed-file subgraph; classify findings. |
| Phase 5 iterate | When iteration adds files, re-scan only the dirty subgraph. |
| Phase 6 learn-sync | Pull NavGator-style lessons from `lessons.json` and project decisions, sync to `personal_memory`. |

Read-only enforced via tool allowlist; never `Edit`/`Write`. Returns structured JSON the orchestrator parses.

## Lessons store unification

`architecture/lessons.py` does TWO things:

1. **Authoritative writer for `.navgator/lessons/lessons.json`** (NavGator-compatible schema, so `gator` standalone keeps working against the same file).
2. **Write-through to `personal_memory.semantic_facts`** with:
   - `subject = "lesson:nav:<id>"`
   - `predicate = lesson["category"]`
   - `object = lesson["pattern"]`
   - `metadata` = full lesson object
   - `domain = "architecture"`
   - `confidence_source = "auto-confirmed"` if from rule recurrence; `"manual"` if user-written

This unifies cross-project lesson recall via `recall.py`, while keeping the per-project JSON file as a portable artifact NavGator standalone can read.

## CLI surface

`scripts/architecture.py` (NEW): Python-side CLI. Command parity with `gator`:

```sh
build-loop arch scan [--incremental] [--background] [--mode auto|native|navgator]
build-loop arch impact <component> [--depth 2] [--edge-types import,service]
build-loop arch trace <from> <to> [--depth 4]
build-loop arch rules [--json] [--severity error|warn]
build-loop arch dead [--json]
build-loop arch lessons [--list|--add|--query <pattern>]
build-loop arch acp [--scope <component-list>] [--json]
```

Wrapping shell commands in `scripts/architecture.py` keeps the build-loop CLI consistent. The existing `skills/architecture/{scan,impact,…}/SKILL.md` files are updated to call this CLI instead of `gator`.

## Compatibility / migration

| Concern | Resolution |
|---|---|
| Existing `.navgator/architecture/*.json` data | Read-compatible with the same schema NavGator emits. New scans write the same files. |
| Existing `.navgator/lessons/lessons.json` | Same schema. Read by `lessons.py`; new write-through to Postgres is additive. |
| Users with NavGator installed | Mode `auto` runs native by default; can opt to `navgator` mode for parity verification or to use NavGator for rule definitions not yet ported. |
| `skills/architecture/*/SKILL.md` provenance machinery | `source:` + `source_hash:` fields stay (point at the canonical NavGator skills) but become advisory. The drift-checker (`sync-skills`) is downgraded to a one-line warning, then removed in v0.12. |
| `skills/navgator-bridge` deprecation stub | Already flagged deprecated; remove in v0.11 cleanup commit. |

## Testing strategy

1. **Fixture-based unit tests** per module against minimal sample repos (`tests/architecture/fixtures/{ts-web,python-ml,swift-ios}/`). Each fixture has known components/edges; tests assert exact match.
2. **Parity tests** (`test_modes.py`): when `gator` binary is available on PATH, run both `mode=native` and `mode=navgator` against the same fixture, diff the output. Tolerated drift: ordering, transient timestamps. Anything else fails the test.
3. **Hook smoke tests**: SessionStart + PreToolUse simulate a session; verify scan fires for `.py`/`.ts` edits, doesn't fire for `.md`, doesn't pile up under burst edits (single-flight).
4. **ACP shape tests**: assert ACP is ≤2 KB for the build-loop repo itself; never includes raw file content.
5. **End-to-end via `/build-loop:run`**: dispatch a small build, verify Phase 1 Assess fires `scan`, Phase 4-D fires `rules`, Phase 5 iterate-subgraph fires `impact`. Grep `.build-loop/state.json` for `architecture.scan_age_seconds`.

## Phase delivery

Five small commits, each independently revertable. No grand-merge.

| Commit | Scope | Acceptance |
|---|---|---|
| 1 | `architecture/types.py` + `_ignore.py` + `_walker.py` + `scanner/packages/*.py` + tests | npm + pip + swift package detection matches NavGator output on fixtures |
| 2 | `scanner/infrastructure/*.py` + `scanner/connections/*.py` + `scanner/__init__.py` + tests | Full scan output matches NavGator on `tests/architecture/fixtures/ts-web/` |
| 3 | `impact.py` + `trace.py` + `rules.py` + `dead.py` + tests | Rule engine reproduces NavGator's findings on the same fixtures; impact + trace pass parity tests |
| 4 | `lessons.py` + Postgres write-through + `personal_memory` migration | Lessons appear in `recall.py` queries with `domain=architecture` |
| 5 | `acp.py` + `scout.py` + `agents/architecture-scout.md` + hook wiring + `agents/build-orchestrator.md` integration + CLI + skill updates | `/build-loop:run` triggers ACP injection at Phase 2/3/4/5; SessionStart hook scans incrementally; architecture-scout dispatched on a real build |

Each commit ships with green tests for the module being added; later commits don't break earlier ones.

## Risk + open questions

1. **TypeScript AST scanner.** NavGator's `ast-scanner.ts` may need real AST parsing for accuracy on complex TS. **Resolution path**: start with regex (matches what NavGator's other connection scanners do); upgrade to `tree-sitter-typescript` Python bindings only if parity tests fail.
2. **Performance on large monorepos.** NavGator scans 10K-file repos in ~3s. Python may be 2-3× slower. **Resolution**: incremental-by-default; only full-scan on demand; profile against a real monorepo (the user's atomize-ai repo would be a good benchmark).
3. **Hook noise.** PreToolUse Edit/Write hooks fire frequently. **Resolution**: extension allowlist + single-flight + 3s timeout + background spawn. Keep an off-switch (`BUILD_LOOP_ARCH_HOOK=0`).
4. **Schema drift between native and NavGator output.** Could surface only after weeks of use. **Resolution**: run `mode=auto` and `mode=navgator` in parallel for the first project that adopts it; diff regularly.
5. **NavGator lesson schema evolution.** If NavGator introduces a v2 schema while build-loop is on v1, the write-through breaks. **Resolution**: version-gate the write-through; emit a warning if NavGator JSON has a schema version we don't know.

## Out of scope (future)

- **Multi-repo / monorepo aggregation** (NavGator has some support; build-loop port v1 stays single-repo).
- **Live file watcher daemon** (replace polled hooks with `fswatch`-style watcher) — only if hook latency proves insufficient.
- **Diagram generation** in the native engine — explicit non-goal.
- **LLM use-case map** — defer to NavGator standalone or future v0.12.

## Decision points to confirm before starting

1. **`src/build_loop/` layout.** Does build-loop already have an `src/` layout, or are scripts at the top level? Today scripts are at `scripts/*.py`. Plan currently says `src/build_loop/architecture/` per the README. Either restructure into `src/build_loop/` (cleaner Python package; bigger PR) or put it under `scripts/architecture/` (matches existing convention; less aligned with the README).
2. **Tree-sitter dependency.** OK to add `tree-sitter` + `tree-sitter-typescript` as optional deps for deeper TS parsing? Pure-regex stays the default.
3. **Postgres lessons sync.** Auto-on or opt-in via config? Default proposed: auto-on when `personal_memory` schema is reachable; silent no-op otherwise.
4. **Hook off-switch.** Per-project (`.build-loop/config.json`) or env var (`BUILD_LOOP_ARCH_HOOK=0`)? Plan currently says env; per-project may be friendlier for opt-out.
5. **`gator` binary discovery.** Look only on PATH, or also search `~/.npm-global/bin` and other common npm install dirs?

I'll start commit 1 (types + walker + package scanners) once you sign off on the layout question (decision point 1).
