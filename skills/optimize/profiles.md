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

- **Metric**: Custom benchmark command
- **Guard**: Test suite passes
- **Direction**: lower
- **Budget**: 10
- **Scope**: Hot-path source files
