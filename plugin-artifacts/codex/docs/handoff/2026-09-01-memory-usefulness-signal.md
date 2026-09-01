The memory usefulness loop is built and proven end to end, and it has produced zero outcome rows from unseeded work so far.

⚠️ Verified by running `memory_store_stats.py --check-targets`, the reconciler
dry-run across all three join strategies, and 188 tests across the memory and
installer suites. NOT verified: that the loop fires during ordinary agent work,
because no unseeded traffic has accumulated yet. That is the open question this
handoff exists to carry.

**Absolute path:** `/Users/tyroneross/dev/git-folder/build-loop/docs/handoff/2026-09-01-memory-usefulness-signal.md`
**Retrospective:** `/Users/tyroneross/dev/git-folder/build-loop-memory/projects/build-loop/retrospectives/2026-09-01/memory-usefulness-signal.md`
**Targets (executable):** `/Users/tyroneross/dev/git-folder/build-loop/references/memory-signal-targets.json`

## State in one line

The loop is built and proven end-to-end; `use_rows` is still **0**, so nothing
can yet be ranked by usefulness. Everything below is instrumentation waiting for
ordinary traffic.

## Verify the state yourself — do this before trusting anything here

```bash
cd /Users/tyroneross/dev/git-folder/build-loop
python3 scripts/memory_store_stats.py --check-targets      # live vs declared targets
python3 scripts/memory_reconcile.py                        # dry-run, ALL strategies
python3 -m pytest scripts/test_memory_*.py scripts/test_content_index.py \
                 scripts/rally_point/test_install_git_hook.py -q
```

Expect: targets `below` on four metrics and `use_rows` below; reconciler
matching a small number under `path`, fewer under the strict join; tests green.

## Baseline, 2026-09-01

| metric | value | target | why not higher yet |
|---|---:|---|---|
| hit rate | 0.737 | 0.90 | genuine retrieval gap |
| joinable | 0.263 (0.357 of reads with results) | = hit rate | fix landed hours ago |
| session | 0.061 | 1.00 | same |
| exposure | 0.032 | = hit rate | same, plus locator emits none |
| use rows | 0 | > 0 unseeded | **the one that matters** |

Rates are CLEAN TIER ONLY (`schema_version != 1.0` and `source == runtime`).
40,843 of 41,145 read rows are legacy fixtures; blending them measures the test
suite. `memory_health.py` and `memory_store_stats.py` share `tier_of()`.

## Next actions, ordered

1. **Let it run.** The single most useful next step is ordinary work. Re-check
   `--check-targets` in a week; joinable/session/exposure should climb toward
   the hit rate on their own. If they do not, a caller is bypassing the path.
2. **Emit rank + score from `memory_locator`.** It produces 73 clean reads at
   98.6% paths and **0% ranks**, so those reads are joinable but permanently
   undebiasable for position. Only `memory_facade.recall` runs the ranker today.
3. **Run the reconciler with `--emit`** once unseeded matches appear. Confirm
   first that matched reads carry session ids from sessions a human did not
   drive — otherwise it proves the mechanism, not the loop.
4. **Then, and only then**, revisit `DEFAULT_LIMIT` / embeddings / pruning.

## Invariants — do not break these

- An open proves **INSPECTED**, never **HELPED**. `effect` stays unset on use rows.
- Opens never feed the ranker or the pruner. Six architects reached this
  independently; position bias makes opens partly a function of rank, and
  feeding them back builds a rich-get-richer loop.
- A surfaced-and-unopened memory is never labelled `ignored`. Silence is not
  evidence of non-use.
- No rate is ever blended across tiers.
- `BUILD_LOOP_TELEMETRY_SOURCE=test` for anything experimental. The production
  store must show zero fixture rows.

## Traps that cost time this session

- **Read paths are relative, span paths absolute.** The first reconciler run
  returned 0 matches on every strategy for this reason alone. Normalisation now
  lives in the reconciler so historical rows are repaired too.
- **Trace files are not all under one parent.** The hook writes to
  `$CLAUDE_PROJECT_DIR/.build-loop/telemetry/`; a session whose project dir is
  `$HOME` writes to `~/.build-loop/telemetry/`. A repo-rooted glob missed 10 MB
  of its own traffic.
- **The lexical oracle penalises topically-correct results.** It measures term
  presence, not relevance. Do not tune ranking against it past the point where
  manual inspection disagrees — that already happened once.
- **Guard hooks pinned to versioned plugin-cache paths die on every upgrade.**
  Six repos were silently unable to commit. Fixed at all sites and in the
  generator; if you see a `FileNotFoundError` from `.git/hooks/`, this is it.

## Open, not blocking

- `memory_locator` exposure (action 2 above).
- Decision index: 1,208 rows against 6,798 files; the backend reports its own
  staleness in every recall and nothing consumes the warning.
- Write telemetry observes ~9% of file changes, so the store grows unobserved.
- `persona encounter save` writes to the encounters root when `persona_id` is
  null. Data repaired; upstream defect remains in persona-lab.
