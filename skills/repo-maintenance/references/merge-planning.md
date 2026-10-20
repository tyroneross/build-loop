# Merge planning for several branches

Plan the order and batches before merging two or more branches. Merging one at a time re-runs the build and tests once per branch and lets one conflicting branch stall the rest.

## When to plan

- Plan when asked to merge, land, or "multi merge" 2+ branches, or to land all ready branches.
- Skip for a single branch; use `merge_risk.py` and the reconciliation contract.
- Planning is read-only. Merging still follows [safety-protocol.md](safety-protocol.md) and [reconciliation.md](reconciliation.md).

## Prefer a native merge queue

If the repo has its own queue, use it instead of `merge_plan.py`.

- Easy Terminal: `et merge plan` and `et merge next --batch N`. Detect it with `grep -q "merge plan" bin/et` (or `et merge --help`).
- Otherwise check for a hosted queue (GitHub merge queue, Mergify, Trunk) in repo settings or config.
- No native queue: run

```bash
python3 "$REPO_MAINTENANCE_ROOT/scripts/merge_plan.py" --repo "$PWD" --auto --match 'claude/*' --batch 3 --json
```

Branches may also be listed explicitly. `--base` defaults to the current branch's upstream, else `main`. Output is JSON (per-branch commits ahead, changed files, base verdict; per-pair shared files and conflict; ordered `batches`; `needs_resolution`), followed by a short summary unless `--json` is set.

## Choosing N from the measured failure rate

Batching runs one build/test pass over N branches. If the per-branch failure rate is p, a batch passes with probability (1 - p)^N. Pick the largest N that keeps that at or above about 0.85:

    N = floor( ln(0.85) / ln(1 - p) )

Measure p from recent history: failed runs / total runs on merged branches. Cross-check against Mergify's guidance ([source](https://mergify.com/learn/merge-queue/batching)):

| Build failure rate | Suggested batch size |
|---|---|
| under 2% | 5-10 |
| 2-5% | 3-5 |
| above 5% | 2-3 |

Trunk suggests starting at 5 ([source](https://docs.trunk.io/merge-queue/optimizations/batching)). With no history, use the script default of 3 and adjust.

## Grouping: disjoint files, sequence conflicts

Uber's SubmitQueue and "CI at Scale: Lean, Green, and Fast" ([arXiv 2501.03440](https://arxiv.org/html/2501.03440v2)) found that large changes blocking unrelated small ones are a main source of delay. The planner therefore:

1. Puts branches in the same batch only if they share no changed files and `git merge-tree --write-tree --merge-base <base> a b` is clean.
2. Sequences overlapping or conflicting branches into later batches.
3. Orders by ancestry: a branch containing another's tip lands after it.
4. Lists branches that conflict with base under `needs_resolution`; resolve or rebase them first, then re-plan.

Plans go stale. Re-run after each batch lands, since base has moved.

## Bisecting a failed batch

When the batch's combined build/test fails:

1. Split the batch in half; test each half on base (merge only the half's branches in a scratch worktree).
2. Recurse into the failing half until one branch fails alone: the culprit.
3. Land the passing branches, re-plan, and send the culprit back for a fix.

Cost is about log2(N) extra runs. This is why small N is right when failures are common.

## Trade-offs

- Batching gives up proof that each branch is safe in isolation; a pass shows only that the combination passes ([Trunk](https://docs.trunk.io/merge-queue/optimizations/batching)). Disjoint-file batches reduce but do not remove interaction risk (semantic coupling, shared config).
- Larger N saves CI time but raises the chance of a batch failure and bisect cost.
- Speculative parallel builds (testing batch 2 on top of batch 1 before 1 lands) pay off only with several build slots. With one slot they only queue behind each other; run batches serially.
- The planner reads file overlap and textual conflicts only. It cannot see semantic conflicts, so still run the canonical verifier on the final tip.
