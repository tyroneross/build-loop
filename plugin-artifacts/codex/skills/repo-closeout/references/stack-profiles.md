# Application and Build-System Profiles

Use this reference when a maintenance task depends on application type, language, framework, build system, generated directories, release shape, or expected physical layout. A stack profile is an evidence packet for maintenance decisions, not a template that every repository must copy.

## Contents

1. Stack-profile schema
2. Application profile families
3. Build-system layers
4. Physical-layout guidance
5. Classification workflow
6. Maintenance implications

## 1. Stack-profile schema

Record the profile as multi-value data. Mixed products are normal.

```yaml
application_profiles: []
languages: []
frameworks: []
build_system_layers: []
package_managers: []
repository_composition: []
runtime_deployables: []
canonical_source_roots: []
generated_or_cache_roots: []
canonical_build_commands: []
targeted_test_commands: []
artifact_or_bundle_parity_gates: []
release_units: []
ownership_or_security_boundaries: []
split_triggers: []
consolidation_triggers: []
evidence: []
confidence: high | medium | low
```

The audit script emits signals for some fields. Confirm them from manifests, source, CI, release configuration, and repository instructions before making structural changes.
The JSON report also records manifest scan counts and truncation. Treat a truncated content scan as partial evidence and inspect the relevant workspace with its native tooling before deciding.

## 2. Application profile families

Application profiles describe the product or component shape. A repository may match several rows.

| Profile family | Common evidence | Typical structure pressure | Maintenance focus |
| --- | --- | --- | --- |
| Apple native app | Swift/Objective-C, Xcode project or XcodeGen, SwiftPM packages | app targets, extensions, local packages, platform-specific UI and services | generated project parity, signing, Xcode/SwiftPM build truth, DerivedData retention |
| Android native app | Kotlin/Java, Gradle settings, Android manifests | app plus feature/core modules and build-logic | module granularity, Gradle graph, variants, build-cache behavior |
| Web framework app | Next.js, Nuxt, SvelteKit, Remix, similar framework manifest/dependencies | route conventions plus feature/domain code | framework-reserved paths, server/client boundaries, generated output cleanup |
| Web SPA | Vite/Webpack/Rspack plus React/Vue/Svelte/Angular | UI features, state/data clients, assets | bundle/test commands, build output, shared UI boundaries |
| Backend API or service | server framework, routes/handlers, migrations, Docker/Procfile/deploy config | domain/application/adapters or deployable-first | API/schema compatibility, migrations, runtime config, independent deployability |
| Full-stack product | web/mobile clients plus server/API/packages | product monorepo or coordinated polyrepo | end-to-end contracts, shared types, atomic changes, deployment fan-out |
| Desktop cross-platform app | Electron, Tauri, Flutter, .NET MAUI, Qt | native shell plus web/shared core | dual build systems, packaging, platform-specific outputs |
| CLI, library, or SDK | binary/library targets, package metadata, public API | command/core/adapters or package-module | compatibility, release/versioning, install smoke tests, external consumers |
| Daemon, worker, or agent runtime | long-lived process entrypoints, IPC/protocols, queues, schedulers | runtime core plus transports/adapters | lifecycle, protocol compatibility, recovery, observability, bundled binary parity |
| Plugin or extension | plugin manifests, skills/agents/hooks, browser/IDE manifests | component-type directories around a manifest | host compatibility, discovery, packaging, permission boundaries |
| Data or ML system | notebooks, training/eval scripts, model/data configs, pipelines | data, features, training, serving, evals | data/model provenance, large artifact exclusion, reproducibility, serving contract |
| Infrastructure repo | Terraform, Pulumi, Helm, Kubernetes, deployment modules | environment or reusable module grouping | state boundaries, plan validation, credentials, promotion between environments |
| Documentation/content product | site generator or docs build config, content collections | content/type/version organization | links, generated site output, publishing gate |
| Mixed native product | native UI plus daemon/CLI/shared protocol | multi-language workspace or product monorepo | source-of-truth boundaries, bundle generation, cross-language contract tests |

Treat more specific archetypes—watch apps, app extensions, white-label apps, offline-first apps, multi-tenant SaaS, model-serving systems—as refinements of these profile families. Add the refinement only when it changes boundaries, build gates, generated state, ownership, or release behavior.

## 3. Build-system layers

A repository can use several build systems at once. Classify each by role instead of choosing one winner.

| Role | Examples | Maintenance implication |
| --- | --- | --- |
| Project generator | XcodeGen, CMake generators, code generators | Generated files need a canonical writer and parity check. |
| Language package/build | Cargo, SwiftPM, Gradle, Maven, Go, Python build backends, MSBuild | Manifests and lockfiles define package and dependency boundaries. |
| Framework compiler/bundler | Next.js, Vite, Webpack, Turbopack, esbuild | Framework outputs are generated caches/artifacts, not source. |
| Workspace coordinator | Cargo workspace, pnpm/yarn/npm workspaces, Turborepo, Nx, Gradle multi-project | Coordinates units inside a checkout; does not determine Git portfolio strategy. |
| Task orchestration | Make, shell scripts, Task, Just, Bazel targets | Identify the canonical entry command and subordinate tools it invokes. |
| Container/package | Docker/BuildKit, Xcode archive, app/package builders | Produces deployable or distributable artifacts; record reproducibility and signing. |
| Infrastructure | Terraform, Pulumi, Helm, CloudFormation | State and environment boundaries may require stricter repository or access separation. |
| Release automation | GitHub Actions, Fastlane, semantic-release, custom scripts | Release units and credentials reveal real operational coupling. |

Do not call every manifest a build system. A package manager, workspace coordinator, project generator, compiler, bundler, and release pipeline may all participate in one canonical build.

## 4. Physical-layout guidance

Derive physical layout from the application profiles and build systems. Preserve framework-reserved directories and established conventions unless changing them solves a measured problem.

Useful top-level roles include:

- `apps/` or named client roots for independently runnable applications;
- `services/`, `workers/`, or `daemon/` for operated runtimes;
- `packages/`, `crates/`, `modules/`, or `libs/` for enforceable reusable units;
- `infrastructure/` or `deploy/` for deployment definitions;
- `tools/` or `scripts/` for repository automation;
- `docs/` for current documentation with lifecycle rules;
- stack-native roots such as `Sources/`, `src/`, route directories, Xcode projects, or Gradle modules.

Names are evidence, not proof. `packages/` does not establish real modularity if all code bypasses package interfaces. `services/` does not prove independent deployability. `build-*` directories do not prove source duplication.

Keep source-owned, generated, vendored, cached, and runtime-state paths visibly distinct. Generated or cache roots should be ignored when appropriate, reproducible, attributable to a writer, and covered by retention rules.

## 5. Classification workflow

1. Run the maintenance audit against the canonical base ref.
2. Read manifests, lockfiles, project generators, CI, release configuration, and repository instructions.
3. Identify product components and runtime entrypoints in code.
4. Record application and build-system signals with evidence.
5. Classify the independent repository axes from `repository-taxonomy.md`.
6. Distinguish current structure from target structure.
7. Test the proposed change against representative feature changes and canonical verification.

Use confidence conservatively:

- `high`: manifests, source entrypoints, build commands, and release configuration agree;
- `medium`: several code/manifests agree but release or ownership evidence is missing;
- `low`: based mainly on directory names, dependencies, notes, or unverified conventions.

If signals conflict, report a mixed profile rather than forcing one classification.

## 6. Maintenance implications

Use the profile to drive—not replace—judgment:

- choose stack-correct build and test commands;
- protect active and canonical caches while pruning stale isolated build roots;
- preserve framework-reserved layouts;
- trace cross-language and generated contracts;
- identify which modules can enforce visibility and which are only folders;
- separate agent/tool configuration overlays from shipped architecture;
- decide whether a sibling repository is an independent product or historical source;
- set split/merge triggers based on consumers, releases, owners, security, and measured change coupling.

For mixed native products, explicitly map: native app source, daemon/service source, shared protocol, generated/bundled artifact, writer command, parity test, and final integration build. This prevents a bundled runtime from silently becoming a second canonical source.

## Source basis

- Cargo workspace behavior and shared outputs: https://doc.rust-lang.org/book/ch14-03-cargo-workspaces.html
- Android module benefits and granularity pitfalls: https://developer.android.com/topic/modularization
- Apple local-package organization guidance: https://developer.apple.com/documentation/xcode/organizing-your-code-with-local-packages
- Claude Code project subagents as repository tooling overlay: https://code.claude.com/docs/en/sub-agents
