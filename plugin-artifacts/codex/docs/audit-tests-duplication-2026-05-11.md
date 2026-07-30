# Audit — Test-Location Duplication (2026-05-11)

## Summary

Build-loop's test suite is split across **two directories with two frameworks** and no documented rule. Audit confirms the split is real and load-bearing — there's a literal name collision and partial coverage overlap. Recommend consolidation in a follow-up PR; this brief captures the facts for that work.

## Facts

| Location | Files | Framework | Runner |
|---|---|---|---|
| `scripts/test_*.py` | 39 | stdlib `unittest` (0 import pytest) | `python3 scripts/test_<name>.py` |
| `tests/test_*.py` | 39 | pytest (34 import pytest) | `uvx pytest tests/` |

**Total: 78 test files.** 5 files under `tests/` don't import pytest — they may be framework-agnostic or use plain `def test_*` discovered by pytest's auto-collection. Worth a closer look in the consolidation PR.

### Literal name collision

`test_detect_runtime_server.py` exists in **both** `scripts/` and `tests/`. The two implementations:

- `scripts/test_detect_runtime_server.py` — stdlib unittest, exercises `_is_test_path()` directly + in-memory `detect()` against synthesized in-memory fixtures.
- `tests/test_detect_runtime_server.py` — pytest, exercises the CLI via `subprocess.run` against on-disk fixtures at `tests/test-fixtures/runtime-server-{positive,negative,no-ui}/`.

Both add coverage; neither is a strict superset. Renaming or merging requires care.

### Fixtures asymmetry

- `tests/test-fixtures/` exists (used by 4+ tests under `tests/`).
- No equivalent `scripts/fixtures/` directory — `scripts/` tests use in-memory or `tempfile.mkdtemp()` fixtures.

### Effective coverage

- The pytest suite under `tests/` is **the only one that exercises CLI surface end-to-end** (subprocess invocations, on-disk fixtures, exit-code checks).
- The unittest suite under `scripts/` is **the only one that exercises module-internal helpers** (private functions, edge cases, dict-vs-string normalization).

## Why this matters

1. **Runner inconsistency.** `python3 scripts/test_*.py` runs only stdlib tests; CI or developers running "all tests" must invoke both runners or miss half.
2. **Discoverability.** Developers adding a test don't know which location to use. The session test scripts (`test_session_registry.py`, `test_memory_writer.py`, `test_memory_index.py`, `test_sse_consumer_normalization.py`) landed under `scripts/` by my own choice based on the existing pattern — but no documented rule made that obvious.
3. **Dependency on `uvx pytest`.** Half the suite requires pytest, which isn't a project dependency — only available via `uvx` (one-shot) or a separately maintained dev env. Stdlib half runs anywhere.

## Three consolidation paths

### Path A — Consolidate to `scripts/` + stdlib unittest

**Pros:** Zero deps. Anyone with Python 3.11+ can run tests. Matches build-loop's "minimal dependencies" CLAUDE.md mandate.
**Cons:** Lose pytest's fixture system + parameterized tests. Migrating 34 pytest files to unittest is ~3–5 hours.
**Cost:** ~3–5 hr migration + 1 PR.

### Path B — Consolidate to `tests/` + pytest

**Pros:** Better fixture system, parameterize via `@pytest.mark.parametrize`, plugins available.
**Cons:** Adds pytest as a build-loop dependency, violates "minimal dependencies" mandate. Existing `python3 scripts/test_*.py` workflow breaks.
**Cost:** ~2 hr migration + add pytest to pyproject + 1 PR + agent doc updates referencing the new runner.

### Path C — Document the split as intentional

**Pros:** Zero migration cost.
**Cons:** Doesn't resolve the literal name collision. Doesn't help future developers know where to add tests.
**Cost:** ~30 min doc write-up + name-collision resolution + 1 PR.

## Recommendation

**Path A (consolidate to `scripts/` + stdlib unittest).** Reasons:

1. Aligns with CLAUDE.md's "minimal dependencies" + "build from scratch" mandate.
2. The stdlib suite already covers more module-internal surface; migrating CLI surface tests to subprocess-based unittest is straightforward.
3. Resolves the runner-inconsistency problem cleanly: `python3 -m unittest discover scripts/` runs everything.
4. Fixtures move from `tests/test-fixtures/` → `scripts/fixtures/` with one `git mv` + `sed -i` for path strings.

If pytest features become genuinely needed (parameterize is the strongest case), add them via stdlib `subTest` patterns or a tiny stdlib parametrizer — don't take pytest as a dep just for ergonomics.

## Suggested PR scope (when this work happens)

| Commit | Size | What |
|---|---|---|
| 1 | S | Migrate `tests/test_*.py` pytest patterns to stdlib unittest (34 files, mostly `assert` → `self.assertX`, fixtures → `setUp/tearDown`). |
| 2 | XS | `git mv tests/test-fixtures scripts/fixtures` + path strings. |
| 3 | XS | Resolve `test_detect_runtime_server.py` collision by merging both into one consolidated test file under `scripts/`. |
| 4 | XS | Delete empty `tests/` directory. Update `CLAUDE.md` and `AGENTS.md` to document the canonical test runner: `python3 -m unittest discover scripts/`. |

Total: **M** sized. Est. 2–4 hr including verification that all 78 test cases still pass post-migration.

## What this audit does NOT cover

- The 5 `tests/test_*.py` files that don't import pytest — need individual inspection to determine if they use pytest auto-collection or are framework-agnostic.
- Test coverage gaps (this is a duplication audit, not a coverage audit).
- CI configuration (if any).
- Whether the legacy pytest tests pass on the current main — last verified one (`tests/test_detect_runtime_server.py`) did pass via `uvx pytest` mid-session.
