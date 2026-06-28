# Plan P0 — Status mechanism (make build-loop the de-facto source of truth)

> Workstream **P0**. Repo: build-loop (+ vendored rally). Self-modification of the
> build-loop plugin — the self-modification safety gate applies (auto-revert on
> test failure). Origin: the Spectra source-of-truth assessment (codex + Claude
> agreed build-loop's artifacts oversold completeness; only a code-grounded
> `CURRENT.md` + a reconciliation step + rally binding closes the gap).

## Goal
A fresh terminal gets accurate project status from build-loop artifacts alone —
no manual code audit. Achieved by making the per-project `CURRENT.md` (already
seeded for Spectra) self-maintaining, surfaced by the standard "what's open?"
path, and mirrored to rally.

## Deliverables
1. `scripts/status_refresh.py` + `scripts/test_status_refresh.py`
2. `scripts/task_surface.py` reads `CURRENT.md` (+ updated test)
3. rally `status post` / `status read` typed-status + stale-claim expiry

## Approach (chunks)
- **P0-1 — `status_refresh.py` (validate + stamp, NOT generate).** At Review-G,
  for a project with `build-loop-memory/projects/<slug>/status/CURRENT.md`:
  compare `as_of_commit` vs repo HEAD; if HEAD moved, mark the file stale and
  re-run its embedded "Validation evidence" block; append a `milestones.jsonl`
  entry when it changes. **v1 deliberately validates/stamps an existing
  CURRENT.md — it does NOT auto-generate one** (generic code→status generation
  is a separate research problem; out of scope). Colocated `test_status_refresh.py`.
- **P0-2 — `task_surface.py` reads `CURRENT.md`.** Add a surface tuple that parses
  the "Current open work" section into `open_items` so `open_count` ≠ 0 when the
  status file records stubs. Update `test_task_surface.py`.
- **P0-3 — rally typed-status.** Add `rally status post --tool <t> --file
  <CURRENT.md> --committed-sha <HEAD>` (write) + `status read` (read), and expire
  claims past their lease so stale handoffs (the 57-expired-leases bug) don't
  show as active.

## Risks / gates
- Self-modifies build-loop → SELF-MODIFICATION SAFETY GATE (auto-revert on test fail).
- Keeping `status_refresh.py` **generic** is the hard part — v1 sidesteps it by
  validating-not-generating. Full generation is a follow-up.
- rally changes touch the vendored rally boundary — validate with
  `agent_rally.py boundary --check`.

## Acceptance
- `task_surface.py --workdir <spectra>` returns the CURRENT.md open items (not 0).
- `status_refresh.py` flags stale + stamps; tests pass; self-mod gate green.
- `rally status read` returns the typed status pointing at CURRENT.md.

## Backlog
Items `BL*-TOOLING-*` / `BL*-RALLY-*` in build-loop's `.build-loop/backlog/`.
