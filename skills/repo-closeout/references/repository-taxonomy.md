# Repository Taxonomy and Module Boundaries

Use this reference when classifying a repository, choosing Git boundaries, changing modules, or evaluating a proposed folder tree. This is a working decision taxonomy, not an industry-standard list of mutually exclusive repository types.

## Contents

1. Classification model
2. Mapping common topology labels
3. Repository-boundary decision
4. Internal organization patterns
5. Module-boundary decision
6. Evolution and validation
7. Output contract

## 1. Classification model

Classify each axis independently. A repository can be a product-scoped monorepo, use several language workspaces, contain a modular monolith plus a daemon, and release some packages independently. One label cannot express all of that.

| Axis | Values | Question answered |
| --- | --- | --- |
| Portfolio strategy | `monorepo`, `polyrepo`, `hybrid` | How is source divided across Git repositories? |
| Repository scope | `single-component`, `product`, `organization`, `integration-orchestration` | What product or coordination boundary does this repository own? |
| Composition | `single-package`, `workspace`, `multi-app`, `multi-service`, `package-family`, `mixed` | What independently buildable units are coordinated inside the repository? |
| Release coupling | `coordinated`, `independent`, `mixed` | Which units version and ship together? |
| Runtime deployment | `single-deployable`, `modular-monolith`, `multiple-deployables`, `library-package`, `mixed` | What executes or distributes independently? |
| Internal organization | `technical-layer`, `feature-first`, `domain-oriented`, `layered-feature`, `package-module`, `deployable-first`, `hybrid` | What is the primary grouping dimension inside a unit? |
| Ownership and risk | team owners, security boundaries, compliance boundaries | Who can change what, and which boundaries must be enforced? |
| Physical layout | stack-specific paths | How do the selected build systems express these decisions on disk? |

Do not infer portfolio strategy from a workspace manifest. Cargo, Gradle, SwiftPM, pnpm, and similar workspaces coordinate packages within a checkout; they do not decide whether the organization uses a monorepo or polyrepo.

## 2. Mapping common topology labels

Use familiar labels as shorthand, then expand them into the independent axes above.

| Common label | Normalized meaning |
| --- | --- |
| Single-project repo | Usually `single-component` scope plus `single-package` composition; may still contain several targets. |
| Workspace repo | A `workspace` composition signal. Portfolio strategy and repository scope remain undecided. |
| Product monorepo | `monorepo` portfolio plus `product` scope; composition may be multi-app, multi-service, package-family, or mixed. |
| Organization monorepo | `monorepo` portfolio plus `organization` scope. Requires strong tooling and ownership controls. |
| Service-per-repo | Usually `polyrepo` portfolio, service-sized scope, and independent releases. |
| Package-per-repo | Usually `polyrepo` portfolio, library-package deployment, and independent versioning. |
| Hybrid portfolio | `hybrid` portfolio; record the rule that determines which units share a repo. |
| Meta/orchestration repo | `integration-orchestration` scope; source of truth should remain in the repositories it coordinates unless explicitly vendored or generated. |

These are useful recurring shapes, not eight values on one axis.

## 3. Repository-boundary decision

Start with the product and release boundary, not the current folder tree.

Prefer one repository when most of these are true:

- units normally change in one user-visible feature or compatibility transaction;
- the same integration gate is required before release;
- releases are coordinated and there is no external compatibility promise;
- one owner and security boundary controls the units;
- consumers are internal to the same product;
- atomic changes reduce integration risk more than repository size increases tooling cost.

Prefer separate repositories when one or more strong boundaries exist:

- independent public contract and semantic versioning;
- independent external consumers or distribution channel;
- independent release, availability, security, compliance, or ownership boundary;
- access control cannot be expressed safely inside one repository;
- checkout, history, or CI cost remains materially high after ordinary optimization;
- the unit must evolve without coordinating with the parent product.

Use evidence, not repository count aesthetics. Record:

1. product and consumers;
2. change coupling from recent history;
3. release and compatibility coupling;
4. build and test fan-out;
5. ownership, access, and operational boundaries;
6. migration cost and recovery plan.

Monorepo does not mean monolith. Polyrepo does not mean microservices. Repository placement, module boundaries, and runtime deployment are separate decisions.

## 4. Internal organization patterns

Choose one primary grouping dimension at each level. Hybrid structures are valid when the transition point is explicit.

| Pattern | Best fit | Main risk |
| --- | --- | --- |
| Technical-layer | Small, stable applications where flow across UI/domain/data remains easy to trace | Feature changes fan out across distant directories. |
| Feature-first | Product applications with independently evolving user capabilities | Shared code can become an unowned junk drawer. |
| Domain-oriented | Complex business rules and durable bounded contexts | Domain ceremony can exceed product complexity. |
| Layered-feature | Feature ownership with internal UI/domain/data separation | Repeated scaffolding and inconsistent local layers. |
| Package-module | Enforceable compile, visibility, ownership, or reuse boundaries | Too many modules increase configuration and build-graph overhead. |
| Deployable-first | Multiple services, workers, apps, or independently operated runtimes | Shared packages can couple deployables invisibly. |
| Hybrid | Products with genuinely different scales or runtime shapes | Ambiguous transition rules make navigation unpredictable. |

For a hybrid, state the rule, for example: `apps/` and `services/` are deployable-first; each deployable is feature-first; reusable contracts live under `packages/`.

## 5. Module-boundary decision

Create or retain a module when it provides at least one enforceable benefit:

- an independently testable capability or volatile implementation is hidden behind a stable interface;
- compile-time visibility or dependency direction needs enforcement;
- ownership or security responsibility differs;
- reuse is real and already has more than one consumer;
- change history shows cohesive files repeatedly moving together;
- independent build caching or release behavior measurably reduces cost.

Consolidate modules when configuration, adapters, and boilerplate dominate the capability; boundaries are routinely bypassed; most changes require coordinated edits across them; or no independent consumer, owner, test, or release exists.

For every proposed module, record:

```text
Capability:
Public contract:
Hidden decision:
Consumers:
Owner:
Allowed dependencies:
Verification gate:
Split trigger:
Merge-back trigger:
```

Agent/configuration overlays such as `.claude/`, `.codex/`, or repository-local skills may improve how tools operate on the repository. They are not product modules and must not substitute for runtime architecture, package boundaries, or product documentation.

## 6. Evolution and validation

Treat structure as a hypothesis. Prefer the smallest reversible transition that addresses observed cost.

Split a package, module, or repository when measured evidence shows growing independent ownership, release, security, consumer, or build boundaries. Merge or flatten when the proposed boundary has no enforceable contract and increases navigation or change fan-out.

Validate a structural change with:

- the canonical build and targeted tests;
- dependency-direction or visibility checks;
- change fan-out on representative recent features;
- navigation cost: files and manifests needed to understand or edit one capability;
- generated/bundled artifact parity;
- release and rollback procedure;
- checkout, CI, or build metrics when performance was the reason for change.

Do not claim correctness improvement from tidiness alone. Cleaner module shape can reduce navigation and maintenance cost; correctness still requires behavior-specific verification.

## 7. Output contract

Separate three truth levels:

- **Observed** — manifests, paths, Git history, dependency edges, owners, build commands, and releases directly inspected.
- **Inferred** — likely classification with evidence and confidence; never present a filename heuristic as architectural fact.
- **Decided** — current or target structure chosen by the product owner, with rationale, migration, and validation gates.

Report all applicable axes, the current and target state, the smallest transition, rejected alternatives, and the evidence that would reverse the decision.

## Source basis

- Cargo workspaces coordinate related packages through a shared lockfile and target directory: https://doc.rust-lang.org/book/ch14-03-cargo-workspaces.html
- Android's modularization guide describes benefits and warns about both overly fine and overly coarse granularity: https://developer.android.com/topic/modularization
- GitHub documents repository size, width, depth, branch, and activity limits as operational constraints: https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits
- Research on multi-repository microservice architecture reconstruction documents the difficulty of maintaining accurate architecture across independently evolving services: https://arxiv.org/abs/2602.08166
- A systematic grey-literature review describes modular monoliths as an alternative to, and possible transition point toward, microservices: https://arxiv.org/abs/2401.11867
