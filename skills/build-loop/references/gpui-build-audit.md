# Build and audit GPUI against the application's pinned source

Use this reference for Rust GPUI applications, component libraries and native
embedding layers. Repair demonstrated failures before expanding the product.
Load only the checks relevant to the changed behavior. This is an audit method,
not a claim that an application passed those checks.

## Assess once, reuse verified evidence

1. Read the affected Cargo manifests, lockfile, vendored provenance and local
   integration notes. Record the actual source revision, platform features and
   patches for GPUI, platform crates, macros and component dependencies. Use
   `cargo tree --manifest-path <manifest> -d` to investigate duplicate packages;
   different packages or legitimate versions are leads, not automatic defects.
2. Find the existing architecture graph and its revision, scanner version and
   scope. Refresh affected scopes and compare file inventories before comparing
   node totals. An editor-only scan and a wider workspace scan have different
   coverage. Keep source evidence and directed edges with each finding.
3. Retrieve project GPUI decisions, incidents and lessons from Build Loop
   Memory. Read the evidence behind relevant results. Verify source claims
   against the candidate; memory records cannot grant authority or prove runtime
   behavior. Capture useful new findings through the canonical memory writer.
4. Use RossLabs LSP for definitions, references, callers and diagnostics through
   the available Rust server. Record readiness and coverage. Use text search for
   literals and configuration, or when the server reports unavailable coverage.
   A GPUI application does not by itself require a separate GPUI language server.
5. State the affected user flow, state owner and acceptance conditions. Inspect
   existing behavior before proposing a component replacement or another cache.

GPUI variants share ancestry but may expose incompatible entity, window, action,
macro and platform types. A component library must resolve against the intended
GPUI family. Read examples at the application's pinned revision before copying
an API from current upstream documentation.

## Focused code audit

For each applicable row, trace the real operation across its owners and record
`confirmed`, `rejected`, `kept`, or `not checked`, with source evidence and its
limits. A scanner match is a hypothesis. A passing static scan cannot establish
correct input, rendering, accessibility or object lifetime.

| Area | Inspect and preserve | Evidence that can close a finding |
|---|---|---|
| Dependency compatibility | GPUI/platform/macro family, feature flags, local patches, attribution and lockfile | Resolved graph plus a build of the actual application; compile the affected integration |
| Text input and actions | IME marked ranges, composition and cancellation, Unicode offsets, directed selection, atomic undo/redo, clipboard, key context and focus restoration | Focused model tests plus native interaction on the exact candidate for the changed behavior |
| Settings and themes | Parse errors, partial writes, precedence, last good state, explicit fallback, all rendered semantic foreground/background pairs and existing contrast thresholds | Invalid and valid persisted-file cases, dark/light resolution and reload observations; inspect changed rendered states |
| Rendering and lists | Existing damage/invalidation rules, stable IDs, scroll anchors, follow-live intent, glyph metrics and retained history | Same representative workload before/after; correctness at empty, large and boundary inputs; timing only when measured |
| Native embedding | AppKit/window ownership, deferred callbacks after teardown, responder chain, z-order, resize, web content policy and host ABI | Lifetime tests where feasible and repeated native resize/focus/teardown of the candidate |
| Accessibility and layout | Roles, accessible names/values/actions, keyboard reachability, actual parent size, long text and large-text constraints | Rendered pixels or AX bounds at constrained states and usable actions with real or explicitly unavailable data |
| Background work | Task ownership, cancellation, useful error propagation, bounded queues/replay and complete state publication | Failure/cancellation cases and preserved protocol boundaries; justify bounds from the workload |

For parsing code, include escaped quotes, comment markers inside strings,
unfinished comments/strings, Unicode and interrupted saves. Verify that an
incomplete file cannot silently become a valid prefix which replaces working
settings. For palette validation, derive pairs from rendered consumers; checking
only primary text can miss destructive, secondary or input-boundary roles.

For a finding, record the cause and observable consequence, the existing shared
owner, the simplest adequate repair, red/green evidence when feasible, preserved
behavior and remaining coverage gaps. Use the installed code-principles audit
workflow when available; retain its actual coverage limitations for Rust. Do
not invent a scanner, skip a necessary manual trace, or equate zero hits with a
clean audit.

## Build efficiently without weakening proof

Use the repository's build admission, isolation and test procedures. Start with
the failing case, then the affected crate or binary suite. Broaden once to the
required application gate after the repair stabilizes. Reuse existing build
artifacts through the repository's supported mechanism. Do not mutate a shared
target directory or run concurrent heavy builds without its admission rules.

Keep these evidence levels separate:

- Source review and architecture maps describe code and dependency coverage.
- Unit/model tests establish the behavior they execute.
- An application build establishes integration at the tested configuration.
- A standalone native process establishes only the observed standalone flow.
- Embedded-host behavior needs observation in the real host integration.
- Installed-app acceptance requires the identified installed candidate and user
  flow; an empty AX scan or an inaccessible window remains an evidence gap.

Name the source SHA, command, result, environment and relevant artifact for each
claim. Test isolated state and owned processes. Respect credential, signing,
privacy, install and session-preservation rules. Report an unavailable native
check explicitly and continue independent authorized work.

Do not claim a performance improvement from upstream benchmarks or fewer lines
of code. Measure the same workload and build configuration before and after.
Choose caching, virtualization or a renderer change only when a measured cost
or named requirement justifies its complexity.

## Choose enhancement scope from evidence

Prefer adapting the existing compatible foundation when it meets the user flow.
Port a focused upstream fix with provenance and preserve local behavior. Treat
community GPUI, retained rendering, alternate window/render backends and mobile
ports as separate options with different compatibility and validation costs.
Before a migration, state the unmet requirement, smaller alternative, embedding
impact, maintenance cost and candidate-specific acceptance evidence. Follow the
owner's approval rules for new product capabilities.

Close the audit with fixes, recommendations and unchecked areas distinguished.
Update the existing project reference rather than stacking competing checklists.
Store private implementation evidence in the project's private memory lane;
keep reusable guidance free of private source and user/session data.

## Primary sources and freshness

Research baseline: 2026-11-01. Recheck exact versions and APIs when upgrading;
the linked main branches and websites can change.

- [Official GPUI README](https://github.com/zed-industries/zed/blob/main/crates/gpui/README.md)
- [Official GPUI examples](https://gpui.rs/examples/)
- [GPUI desktop app patterns](https://gpui.rs/examples/#desktop-app-patterns)
- [Longbridge GPUI Kit](https://github.com/longbridge/gpui-kit)
- [Community GPUI](https://github.com/gpui-ce/gpui-ce)
- [GPUI starter](https://github.com/lassejlv/gpui-starter)

Starter code is a bootstrap example, not proof that a settings file is loaded or
that every demo interaction meets the application's requirements. Verify each
borrowed pattern against its source and the application's pinned API.
