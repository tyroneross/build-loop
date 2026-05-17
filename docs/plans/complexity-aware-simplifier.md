# Plan: Complexity-Aware Deep-Mode Simplifier (Sub-step E)

<!-- checklist
Item 1 — Auth guard: N/A: internal build-loop tooling, no server routes.
Item 2 — External APIs: N/A: no new external API calls. Detector is Python stdlib `ast` only; refactor layer is the running build-loop subagent (no external LLM API). HARD CONSTRAINT.
Item 3 — Rate-limit criterion: N/A: no paid external API call in scope.
Item 4 — Discoverability: N/A: API/backend tooling only. The "surface" is the opt-in deep-mode flag, documented in all four Sub-step E layers (F-criterion FC-6).
Item 5 — Server/client boundary: N/A: single-process Python CLI script + orchestrator-doc wiring. No server/client split.
Item 6 — Concurrency: N/A: read-only diff analysis; the one write path (refactor edits) reuses the existing single-writer orchestrator git contract (Phase 3 commit step). No new write path.
Item 7 — Observability: detector emits a structured JSON envelope to stdout (`{hotspots:[{file,line,kind,reason,severity,score}], scanned_files:[], skipped:[]}`); deep-mode pass logs a one-line `[Simplify:deep] N hotspots, M applied, K advised` to Review-E terminal output and records applied/advised counts in the Review-G report. No new metrics backend (YAGNI).
Item 8 — Input validation: detector validates its `--changed-files` input (path existence + `.py` suffix + parseable by `ast.parse`); unparseable files are skipped with a `skipped[]` entry, never crash the gate. No route handler (CLI tool).
Item 9 — Stable ID traceability: U-01 → F-01 → D-01 → T-01 (detector hotspot detection); U-02 → F-04 → A-001 → T-05 (apply-vs-advise tier). Full chains in Spec Object.
Item 10 — JSON spec object: present (`## Spec Object (JSON)`).
Item 11 — Blocking-and-novel question gate: no open questions — design is locked and converged (brainstorming 2026-05-16). All decisions resolved from the approved design + repo grep. See Open Questions section (empty by construction).
Item 12 — Low-reversibility ADRs: ADR-001 (stdlib-ast detector, no deps) — low-reversibility (public CLI contract + the no-deps constraint is a standing user invariant). ADR-002 (deep-mode as opt-in flag on existing Sub-step E, not a new phase) — low-reversibility (four-layer doc contract). Both recorded.
Item 13 — Analytical lens: TRIZ — resolve the contradiction "deeper simplification (behavior/abstraction change) vs. zero new safety machinery" by reusing existing gates instead of adding a cage. Named in Locked Decisions.
Item 14 — Handoff document: docs/plans/complexity-aware-simplifier.handoff.md (sibling).
Item 15 — Synthesis dimensions: N/A: no UI surface.
Item 16 — Risk reason: N/A: no high-consequence boundary. Not a security boundary (no auth/credential change), not a persistence contract (no schema/storage), not a runtime protocol (no inter-service message shape), not deployment (no infra/pipeline change), not a user trust claim (no user-facing copy/guarantee). The detector is advisory tooling; the apply path reuses the existing Review-B + commit-auditor gates. SMALL.
Item 17 — UI input/output contract: N/A: no UI surface.
-->

## Goal

Add an **opt-in deep mode** to Phase 4 Review Sub-step E ("Simplify") that detects *obvious* inefficiency and complexity in the git-diff-scoped Python files of a build and rewrites it simpler (and, where natural, faster — as an unmeasured bonus, never proven or perf-gated). This goes *beyond* the conservative readability trimming that the default light Sub-step E (`/simplify` skill) intentionally performs: light E avoids behavior/abstraction change by mandate; deep mode fills that gap for *clear* wins only. Light E stays the **default and unchanged**. Deep mode is a flag. The user value: builds whose changed code carries an avoidable accidental-O(n²), a collapsible multi-pass, or needless single-call-site indirection get that cruft removed in the same Review pass — caught by an AST detector, rewritten by the running build-loop subagent, and gated by the build's *existing* safety machinery (no new cage).

## Locked Decisions

These are converged from the approved design (brainstorming 2026-05-16). Do not re-litigate.

- **Analytical lens: TRIZ** — the core contradiction is "perform deeper simplification (which by definition can change abstraction/behavior shape) while adding zero new safety machinery." Resolved by the inventive principle "use existing resources": the build already runs Review-B Validate, the existing test suite, and the commit-auditor behavior/AST advisory. Deep mode plugs into those rather than building a parallel verifier.
- **Detector is Python stdlib `ast` only.** Zero third-party deps. No SonarQube, no tree-sitter, no radon, no external static-analysis tool. Built from scratch. (Standing user invariant — overrides any research suggesting otherwise.)
- **Refactor layer is the running build-loop subagent.** No external LLM API (no GPT-4o/DeepSeek/Anthropic-API). "Claude Code IS the LLM."
- **Reuse existing gates.** APPLY-eligibility = existing tests still pass (Review-B subset on touched files) AND behavior/public-signature unchanged (commit-auditor advisory + the detector's own AST signature comparison). No new safety cage, no perf gate, no benchmark harness, no static cost-proxy. The perf "win" is opportunistic and never asserted.
- **Deep mode is an opt-in flag on the existing Sub-step E**, not a new phase and not a new sub-step. One consolidated, diff-scoped pass. Four-layer doc contract (ADR-002).
- **Diff-scoped only.** The detector analyzes only files changed in the build's diff (`git diff --name-only <base>..HEAD` filtered to `*.py`). Never a whole-repo scan.
- **SIZE = SMALL.** One detector module + one deep-mode hook in Sub-step E + reuse of existing gates + a from-scratch Python fixture for detector unit tests. No new agents, no deps, no perf machinery, no new test infra. Scope creep is a Critic finding.

## Scope

In scope:

1. `scripts/complexity_detector.py` — a single stdlib-`ast` module. CLI: `python3 scripts/complexity_detector.py --changed-files <f1> <f2> ... [--json]`. Walks each parseable changed `.py` file; emits a ranked hotspot envelope. Detected kinds (clear, mechanically-decidable signals only):
   - `high_complexity` — function whose cyclomatic complexity (decision-point count + 1) **and** a simple cognitive-complexity proxy (nesting-weighted branch count) both exceed thresholds.
   - `deep_nesting` — a statement nested beyond a depth threshold.
   - `accidental_quadratic` — a `for`/comprehension loop whose body contains another loop or membership test (`in`) over the *same* iterable name (the canonical avoidable O(n²)).
   - `redundant_multipass` — two or more separate top-level loops in one function over the *same* iterable that are collapsible to a single pass (no data dependency between them that forbids fusion — conservative: only flag when the second loop does not consume a name the first loop produced as a scalar/accumulator the second needs ordered).
   - `needless_indirection` — a module-level function called from exactly one site within the diff scope, with a small body, no decorator, not part of the public surface (not in `__all__`, not imported elsewhere in scope), i.e. an extracted-just-in-case helper.
2. A from-scratch Python fixture under `tests/fixtures/complexity_detector/` containing files with each seeded hotspot kind plus clean control functions (no false positives expected on the clean ones).
3. `tests/test_complexity_detector.py` — pytest, existing layout, locks every detector kind against the fixture (true positives) and asserts zero false positives on the clean controls.
4. Deep-mode wiring in Sub-step E across **all four layers** (ADR-002 contract): `skills/build-loop/references/phase-4-review.md`, `agents/build-orchestrator.md` (net-neutral/trim — file is at the 200-line budget), `AGENTS.md`, `skills/build-loop/references/capability-routing.md`. The wiring documents: the deep-mode flag, the detector invocation, the per-hotspot refactor proposal by the running subagent, the apply-vs-advise tier (apply iff clear win + existing tests pass + behavior/public-signature unchanged; else advisory variance via the existing commit-auditor surface), the one-consolidated-pass + diff-scoped contract, and that light E is unchanged when the flag is off.

### Out of scope

- Any perf guarantee, benchmark harness, micro-benchmark, or static cost-proxy. The "runs faster" outcome is an unmeasured side effect, never asserted or gated. **YAGNI — flag if tempted.**
- SonarQube, tree-sitter, radon, or any external static-analysis tool or third-party dependency.
- Any external LLM API for the refactor step.
- Whole-repo / non-diff scans.
- RAG, multi-agent architectural passes, or a new review phase/sub-step.
- A new safety cage, a new behavior-equivalence verifier, or new test infrastructure. The apply gate is strictly the *existing* Review-B test subset + existing commit-auditor advisory + the detector's own AST-signature comparison.
- Changing light Sub-step E's default behavior in any way.
- Non-Python languages. The detector is Python-`ast`-only; non-`.py` changed files are silently out of detector scope (the existing light E still covers them).

## Six-Commit Table

| # | Commit subject | Files owned | Depends on |
|---|----------------|-------------|------------|
| 1 | docs(plans): draft complexity-aware-simplifier spec | `docs/plans/complexity-aware-simplifier.md`, `docs/plans/complexity-aware-simplifier.handoff.md` (the `.build-loop/specs/` + `.build-loop/plan.md` copies are gitignored runtime data per CLAUDE.md — written for Phase 3 pickup but NOT staged) | — |
| 2 | test(complexity): add seeded Python fixtures for the detector | `tests/fixtures/complexity_detector/**` | C1 |
| 3 | feat(complexity): stdlib-ast complexity/inefficiency detector | `scripts/complexity_detector.py` | C2 |
| 4 | test(complexity): lock detector kinds + zero-false-positive on controls | `tests/test_complexity_detector.py` | C3 |
| 5 | feat(review): wire opt-in deep mode into Sub-step E (4-layer) | `skills/build-loop/references/phase-4-review.md`, `agents/build-orchestrator.md`, `AGENTS.md`, `skills/build-loop/references/capability-routing.md` | C3 |

(Five commits — the table allows fewer than six; SIZE=SMALL.)

## F-Criteria (functional)

| ID | Criterion | Pass condition | Grader |
|----|-----------|---------------|--------|
| FC-1 | Detector finds seeded hotspots | Each seeded kind in the fixture is reported with correct `file`+`line`+`kind` | `pytest tests/test_complexity_detector.py` |
| FC-2 | Zero false positives on controls | Clean control functions in the fixture produce no hotspots | `pytest tests/test_complexity_detector.py` |
| FC-3 | Diff-scoped only | Detector reports nothing for a file not in `--changed-files`; never walks the repo | pytest assertion (pass an empty / single-file list, assert no out-of-scope entries) |
| FC-4 | Graceful skip on unparseable input | A syntactically-broken `.py` in `--changed-files` yields a `skipped[]` entry, exit 0, no traceback | pytest assertion |
| FC-5 | JSON envelope shape stable | `--json` emits `{hotspots, scanned_files, skipped}` with the documented fields | pytest schema assertion |
| FC-6 | Four-layer doc consistency | Deep-mode flag + apply-vs-advise + diff-scoped + light-E-unchanged documented in all four layers; no layer omits it | grep assertion across the four files (code review + grep) |
| FC-7 | Light E default unchanged | With deep mode off, Sub-step E text/behavior is the existing light-E procedure verbatim (no semantic change to the default path) | code review of the diff to `phase-4-review.md` (deep mode is additive, gated by the flag) |
| FC-8 | No new dependency | No new entry in any requirements/pyproject; detector imports only stdlib | grep `^import`/`^from` in detector = stdlib only; diff shows no manifest change |
| FC-9 | Apply gate reuses existing machinery | Deep-mode doc states APPLY requires existing-tests-pass + behavior/signature-unchanged via Review-B + commit-auditor; defines no new verifier | code review of the wiring |

## Q-Criteria (quality)

| ID | Criterion | Pass condition | Grader |
|----|-----------|---------------|--------|
| QC-1 | Python syntax / import-clean | `python3 -m py_compile scripts/complexity_detector.py` exits 0 | shell |
| QC-2 | Full suite regression-clean | No NEW test failures vs. the documented pre-existing reds (capability-registry unknown-category; wiki perf flake) | `pytest -q` before/after diff |
| QC-3 | Orchestrator line budget | `agents/build-orchestrator.md` stays ≤ 200 lines | `wc -l` |
| QC-4 | Detector self-clean | Running the detector on its own changed files surfaces no high-severity self-hotspot (dogfood) | manual run in Review-E |
| QC-5 | Peer files untouched | The ~8 peer-session files + `.orphaned_at` are never staged/committed | `git status` + per-commit `git show --stat` review |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Detector false positives → bad auto-apply | Medium | APPLY tier is conservative: clear-win only; ambiguous → advisory, never applied. Apply gate = existing tests pass + behavior/signature unchanged. FC-2 locks zero-FP on controls. |
| `redundant_multipass` / `accidental_quadratic` heuristics over-fire | Medium | Conservative definitions (same-iterable-name only; no-data-dependency-forbidding-fusion only). When uncertain → advisory, not apply. Tuned against fixture. |
| Orchestrator edit blows the 200-line budget | High (file is exactly at 200) | C5 edits must be net-neutral or trim; deep-mode detail lives in `phase-4-review.md` (the operative layer), orchestrator gets a one-line pointer at most. QC-3 enforces. |
| Four-layer doc drift | Medium | Single commit (C5) touches all four atomically; FC-6 greps for consistency; matches the `feedback_schema_migration_full_chain` memory rule. |
| Scope creep toward a perf gate / cost-proxy | Medium | Explicit Out-of-scope; any such addition is a mandatory Critic finding and must be removed. |
| Touching peer-session files | Low | Path-scoped `git add` of only this feature's files every commit; QC-5 verifies. |

## UI Input/Output Contract

N/A: no UI surface. Internal Python tooling + documentation wiring only.

## Spec Object (JSON)

```json
{
  "needs": [
    {"id": "U-01", "need": "Builds whose changed Python carries obvious accidental complexity/inefficiency should get it removed in the same Review pass, beyond conservative readability trimming."},
    {"id": "U-02", "need": "Deeper rewrites must not ship unless they are a clear win and provably behavior-preserving via the build's existing gates — no new safety machinery."},
    {"id": "U-03", "need": "The default Review experience (light Sub-step E) must not change; deep mode is strictly opt-in."}
  ],
  "features": [
    {"id": "F-01", "need": "U-01", "feature": "stdlib-ast complexity/inefficiency detector emitting a ranked diff-scoped hotspot envelope", "data": ["D-01"], "tests": ["T-01", "T-02", "T-03", "T-04", "T-05"]},
    {"id": "F-02", "need": "U-01", "feature": "Per-hotspot simpler-rewrite proposal by the running build-loop subagent (no external LLM)", "tests": ["T-09"]},
    {"id": "F-03", "need": "U-03", "feature": "Opt-in deep-mode flag on Sub-step E; light E default unchanged", "tests": ["T-07", "T-08"]},
    {"id": "F-04", "need": "U-02", "feature": "Apply-vs-advise tier reusing existing tests + commit-auditor behavior/AST advisory; ambiguous → advisory only", "adr": ["A-001"], "tests": ["T-09"]}
  ],
  "data": [
    {"id": "D-01", "name": "HotspotEnvelope", "shape": "{hotspots:[{file:str,line:int,kind:enum,reason:str,severity:enum,score:number}], scanned_files:[str], skipped:[{file:str,reason:str}]}"}
  ],
  "adrs": [
    {"id": "A-001", "title": "Reuse existing gates for apply-eligibility; no new verifier", "reversibility": "low"},
    {"id": "A-002", "title": "Deep mode is an opt-in flag on existing Sub-step E across four doc layers, not a new phase", "reversibility": "low"}
  ],
  "tests": [
    {"id": "T-01", "feature": "F-01", "asserts": "high_complexity seeded function detected"},
    {"id": "T-02", "feature": "F-01", "asserts": "deep_nesting + accidental_quadratic seeded cases detected"},
    {"id": "T-03", "feature": "F-01", "asserts": "redundant_multipass seeded case detected"},
    {"id": "T-04", "feature": "F-01", "asserts": "needless_indirection seeded case detected"},
    {"id": "T-05", "feature": "F-01", "asserts": "zero false positives on clean control functions"},
    {"id": "T-06", "feature": "F-01", "asserts": "diff-scoped: out-of-list file never reported; unparseable file → skipped[], exit 0"},
    {"id": "T-07", "feature": "F-03", "asserts": "JSON envelope shape stable + documented"},
    {"id": "T-08", "feature": "F-03", "asserts": "four-layer doc consistency grep passes; light-E default text preserved"},
    {"id": "T-09", "feature": "F-04", "asserts": "apply-vs-advise documented to reuse existing machinery; defines no new verifier"}
  ]
}
```

## Open Questions

`[ASSUMED: design locked + converged via brainstorming 2026-05-16; blocking-test: none — every candidate is already answered by the approved design or repo grep, so no candidate meets the blocking-and-novel test and all are emitted as labelled assumptions in the spec body, not questions.]`

## ADR-001 — stdlib-`ast` detector, zero dependencies

- **Decision:** Implement the detector as a single Python module importing only the standard library (`ast`, `argparse`, `json`, `pathlib`, `sys`). No third-party static-analysis package.
- **Alternatives considered:** (a) `radon` for cyclomatic complexity — rejected: adds a dependency, violates the standing minimal-deps user invariant. (b) `tree-sitter` for multi-language — rejected: dependency + scope is Python-only. (c) SonarQube/external service — rejected: external tool, violates build-from-scratch + no-external-tool constraints.
- **Tradeoffs:** Hand-rolled cyclomatic/cognitive proxies are less battle-tested than `radon`, but the constraint is non-negotiable and the heuristics only need to catch *obvious* cases (conservative by design — borderline → advisory, never applied).
- **Rollback path:** The detector is a standalone script behind an opt-in flag. Removing the deep-mode flag wiring (C5) plus deleting `scripts/complexity_detector.py` fully reverts; light Sub-step E is untouched, so the default path is unaffected.
- **Reversibility:** Low (the no-deps invariant and the CLI envelope contract are standing commitments), hence this ADR.

## ADR-002 — Deep mode as an opt-in flag on existing Sub-step E (four-layer contract)

- **Decision:** Deep mode is a flag on the existing Phase 4 Review Sub-step E, not a new phase or sub-step. Its semantics are documented consistently across the four canonical layers (`phase-4-review.md` operative, `agents/build-orchestrator.md` pointer, `AGENTS.md` cross-tool, `capability-routing.md` routing).
- **Alternatives considered:** (a) New Sub-step E2 — rejected: duplicates E, violates minimal-complexity + the research finding that a new phase is unwarranted. (b) Always-on deeper simplification — rejected: changes the default Review experience, violates U-03 and the conservative-default principle. (c) A standalone `/deep-simplify` command divorced from Review — rejected: loses reuse of the in-pass Review-B + commit-auditor gates.
- **Tradeoffs:** Four-layer edits risk drift (mitigated: single atomic C5 commit + FC-6 grep, per `feedback_schema_migration_full_chain`). Orchestrator file is at its 200-line budget (mitigated: detail lives in the operative layer; orchestrator gets ≤1 net-neutral pointer line).
- **Rollback path:** Revert C5; the flag and all four doc references disappear together; light E remains the only behavior.
- **Reversibility:** Low (four-layer documented contract consumed by the orchestrator at runtime), hence this ADR.

## Out of Scope

(Mirror of Scope §Out of scope.) No perf guarantee/benchmark/cost-proxy. No SonarQube/tree-sitter/radon/external tool/new dependency. No external LLM API for refactor. No whole-repo/non-diff scan. No RAG/multi-agent/new phase. No new safety cage or behavior-equivalence verifier or new test infra. No change to light Sub-step E's default. No non-Python language support.
