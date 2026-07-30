# Handoff: Complexity-Aware Deep-Mode Simplifier

Implementer reads this, not the full plan. Plan: `docs/plans/complexity-aware-simplifier.md`.

## Hard constraints (do not violate — these override any instinct)

- Detector imports **stdlib only**: `ast`, `argparse`, `json`, `pathlib`, `sys`. No third-party package. No new requirements/pyproject entry. (FC-8, ADR-001)
- No external LLM API anywhere. The refactor step is the running build-loop subagent at deep-mode time — the detector does NOT call any model; it only emits hotspots.
- No new safety cage, no perf gate, no benchmark, no cost-proxy. Apply-eligibility reuses the *existing* Review-B test subset + commit-auditor advisory only. (ADR-001, FC-9)
- Diff-scoped only — detector analyzes exactly the files passed via `--changed-files`. Never walk the repo.
- `agents/build-orchestrator.md` is at exactly 200 lines (the budget). C5 edits to it must be net-neutral or trimming. Put deep-mode detail in `phase-4-review.md` (the operative layer); the orchestrator gets at most one net-neutral pointer line. (QC-3)
- Never stage/commit the ~8 peer-session files or `.orphaned_at`. Path-scoped `git add` of only this commit's owned files. (QC-5) — NOTE: orchestrator owns the commit step; implementer leaves working-tree changes only, never calls `git add`/`git commit`.

## Commit order + pointers

- **C2 (fixtures first):** `tests/fixtures/complexity_detector/`. One file per kind with a clearly-seeded instance, plus a `clean_controls.py` with functions that must NOT trip any detector (low complexity, single pass, no nested-same-iterable loop, helpers with ≥2 call sites or public). Annotate each seeded case with a `# SEED:<kind>@<lineref>` comment so the test can assert line locality without brittleness. When implementing C2 read the F-01 detector kind list in the plan Scope §1 so the fixtures match the exact heuristics.
- **Size discipline:** keep `complexity_detector.py` focused — prefer a single `ast.NodeVisitor` subclass with one method per kind over five separate walkers. The detector must pass its own QC-4 dogfood (no high-severity self-hotspot), so practice what it detects.
- **C3 (detector):** `scripts/complexity_detector.py`. CLI `--changed-files <paths...> [--json]`. Walk each path; `ast.parse`; on `SyntaxError` append `{file, reason}` to `skipped[]` and continue (exit 0, no traceback — T-06/FC-4). Emit envelope D-01 shape exactly: `{hotspots:[{file,line,kind,reason,severity,score}], scanned_files:[], skipped:[]}`. Severity: `high`/`advisory` (clear-win-candidate vs ambiguous). The five kinds + their conservative definitions are in plan Scope §1 — implement each exactly as written; when in doubt, under-report (advisory) rather than over-report. Satisfy T-01..T-07.
- **C4 (tests):** `tests/test_complexity_detector.py`, existing pytest layout (see `tests/test_capability_registry.py` for the subprocess-invocation + `Path` conventions). Lock every kind as a true positive against C2 fixtures; assert zero hotspots on `clean_controls.py` (T-05/FC-2); assert diff-scope + skip behavior (T-06); assert envelope schema (T-07).
- **C5 (4-layer wiring):** edit all four files atomically in one commit. Operative detail in `skills/build-loop/references/phase-4-review.md` Sub-step E (additive — keep the existing light-E paragraph verbatim, add a clearly-delimited "Deep mode (opt-in)" block: the flag, detector invocation, per-hotspot subagent rewrite, apply-vs-advise tier wording, one-consolidated-diff-scoped-pass, light-E-unchanged-when-off). Then mirror concisely into `AGENTS.md` (cross-tool, no Claude-specifics), add the routing note to `skills/build-loop/references/capability-routing.md:18` area, and add ≤1 net-neutral pointer line to `agents/build-orchestrator.md` Phase 4-E (trim something equivalent if needed to stay ≤200). When implementing C5 satisfy T-08/FC-6/FC-7 (consistency grep + light-E default preserved) and read ADR-002.

## Test / verify

- `python3 -m py_compile scripts/complexity_detector.py` (QC-1).
- `pytest -q tests/test_complexity_detector.py` for the new tests.
- Full-suite delta: documented pre-existing reds are the capability-registry unknown-category test and a wiki perf flake — introduce ZERO new failures (QC-2).
- Dogfood: run the detector on its own changed files in Review-E; QC-4 expects no high-severity self-hotspot.
