# Optimization Profiles

## simplify (always available)

Reduce code complexity in files touched by the current build.

- **Metric**: `wc -l <scope_files> | tail -1 | awk '{print $1}'` (total lines)
- **Guard**: `npm run build` or `npm test` (must still compile/pass)
- **Direction**: lower
- **Budget**: 5
- **Scope**: Files changed in current build (`git diff --name-only HEAD~N`)
- **What it finds**: Dead imports, unused variables, redundant files, extractable constants, inlinable one-use helpers

## optimize-build

- **Metric**: `/usr/bin/time -p npm run build 2>&1 | grep ^real | awk '{print $2}'`
- **Guard**: `npm test -- --passWithNoTests`
- **Direction**: lower
- **Budget**: 5
- **Scope**: Build configs, bundler configs, tsconfig

## optimize-tests

- **Metric**: Coverage % from test runner
- **Guard**: All existing tests pass
- **Direction**: higher
- **Budget**: 5
- **Scope**: Test files only

## optimize-bundle

- **Metric**: `du -sk .next/static 2>/dev/null | awk '{print $1}'` (KB)
- **Guard**: `npm run build`
- **Direction**: lower
- **Budget**: 5
- **Scope**: Source files importing large dependencies

## optimize-perf

Use when a workload has a benchmark command but no specialized preset.

- **Metric**: Custom benchmark command that prints one numeric value
- **Guard**: Test suite passes
- **Direction**: lower
- **Budget**: 10
- **Scope**: Hot-path source files
- **Benchmark guidance**: Prefer representative workloads over microbenchmarks. For latency, run repeated samples with `--metric-samples 5-9`, discard cold-start noise with `--metric-warmups 1-2`, and aggregate with `median` or `p95`.

## semantic-search-latency

Use when optimizing end-to-end semantic-search response time in a consumer repo.

- **Metric**: A semantic-search benchmark command over a fixed query set, for example `python3 scripts/bench_semantic_search.py --queries bench/semantic_search_queries.txt --stat p95`
- **Guard**: Relevance / regression checks for the same search flow plus the normal test suite
- **Direction**: lower
- **Budget**: 10
- **Scope**: Search hot path only — embedding prep, query rewriting, ANN/vector lookup, reranking, caching, result shaping
- **Runner settings**: Start with `--metric-samples 7 --metric-warmups 1 --metric-aggregate p95`
- **What it finds**: Cold-start penalties, redundant embeddings, unnecessary reranks, inefficient filters, cache misses, slow result formatting
