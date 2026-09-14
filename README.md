<!-- build-loop@tyroneross:canary:build-loop -->
<!-- canary-end -->
# Build Loop

A verified build workflow for AI coding agents.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![npm](https://img.shields.io/npm/v/%40tyroneross%2Fbuild-loop)](https://www.npmjs.com/package/@tyroneross/build-loop)

## Install

Claude Code:

```text
/plugin marketplace add tyroneross/build-loop
/plugin install build-loop@build-loop
```

Codex:

```bash
codex plugin marketplace add tyroneross/build-loop
codex plugin add build-loop@build-loop
```

Restart the host after installation or updates.

## Use

Claude Code:

```text
/build-loop:run add billing settings with tests
/build-loop:feedback describe what went wrong
```

Codex:

```text
$build-loop add billing settings with tests
$build-loop tests pass locally but fail in CI
```

Build Loop assesses the repository, plans the work, executes it, reviews the result, fixes failures, and records reusable lessons. It resumes relevant incomplete work automatically and preserves unrelated work before starting fresh.

It asks for confirmation only before a production release, an irreversible deletion, or a major user-impacting decision.

## npm fallback

Use npm only for an exact pin or a host without plugin marketplace support:

<!-- x-release-please-start-version -->
```bash
npm install -g @tyroneross/build-loop@0.45.1
build-loop-install --host all
```
<!-- x-release-please-end -->

## Reference

Surface counts in this release: 55 skills, 29 agents, and two Claude commands.

Groundwork exchange validates `.designdoc/build-request.json` and records verified implementation evidence in `.designdoc/implementation-map.json`.

Rally verifies nothing on its own. Hosts derive agent identity with `scripts/rally_point/actor_identity.py` before `rally enter --tool "$RALLY_TOOL"`.

- [Skill index](https://github.com/tyroneross/build-loop/blob/main/docs/SKILL-INDEX.md)
- [Architecture](https://github.com/tyroneross/build-loop/blob/main/architecture/README.md)
- [Changelog](https://github.com/tyroneross/build-loop/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
