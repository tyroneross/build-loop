# Recommended Learning Object Updates
## Source: build-loop session retrospective (2026-06-11 → 06-18)
## Generated: 2026-06-18

---

## §1. Learning Object Inventory

| Learning object | Evidence | Evidence type | Scope | Encoding target | Confidence | Encode? |
|---|---|---|---|---|---|---|
| LO1 — Worktree isolation per agent from session start | Shared-tree collisions; late worktree fix; `a1c8823` storm | Failure (recurred, multiple incidents) | Cross-project | Agent instruction + Plugin behavior | High | Yes |
| LO2 — Poll-after-post is default for pull-only coordination | Pull-vs-poll correction; `rally_poll_gate.py` built | Revealed (explicit correction + code built) | Project-specific (rally) | Agent rule + Plugin | High | Yes |
| LO3 — Recommendations always include pros AND cons | Standing rule post one-sided rec; "recommendations need pros & cons" | Explicit (user-stated standing rule) | Cross-project | Memory (cross-project) | High | Yes |
| LO4 — Packaging ships scaffolding, not the private store (allowlist + PII deny-scan + git-archive) | 1.4GB install root cause; "structure not SQLite"; `5055efe`; `cc05724` | Explicit preference + Success pattern | Cross-project | Skill + Plugin behavior | High | Yes |
| LO5 — State-mutating commands require in-scope + approval; never during read-only investigation | Unrequested `claude plugin update`; install race trigger | Failure/Hard gate (explicit out-of-scope mutation) | Cross-project | Agent rule + Approval gate | High | Yes |
| LO6 — Verification claims need evidence token; holistic review mandatory even after per-task TDD passes | Hallucinated "you tested it"; path-traversal escaped TDD; `0b49792` fix | Failure (two independent failure modes) | Cross-project | Eval + Agent rule | High | Yes |
| LO7 — Hold-not-merge into unstable/active-storm main is valid and preferred mid-build | User held P1; explicit decision over completion bias | Decision/Success (Explicit) | Cross-project | Agent rule | Med-High | Yes |
| LO8 — Peer-agent handoffs must be tracked to delivered, not just resolved/closed | Dropped DRY rewire; resolved≠delivered | Failure (recurred; systematic) | Cross-project | Plugin (task ledger) + Agent rule | High | Yes |
| LO9 — Identity-routed autonomous writes to core repo via user-writable JSON require interactive approve gate | identity.json flag; maintainer-mode mental model | Hard gate (project-specific; partially generalizable) | Project-specific | Approval gate + Project note | Med | Needs approval |
| LO10 — Instrument-before-trust: add telemetry before asserting a model/tier fired | "0 Fable dispatches"; config-correct ≠ runtime-correct; ledger wired | Success pattern + Failure escape | Cross-project | Agent rule + Eval | High | Yes |
| LO11 — Discovery-before-classification: seed scaffolds, not closed taxonomies | Prompt-loosening steering; "appropriate autonomy over max automation" | Explicit preference (already encoded in skill) | Cross-project | Skill guardrail (already encoded) | High | Yes — already encoded, verify |
| LO12 — Autonomy calibrated on reversibility: auto on reversible, gate on irreversible/mutating | Pull-default (auto) vs plugin-update (wrong); gate-merge (right gate) | Revealed (pattern across multiple incidents) | Cross-project | Agent instruction | High | Yes |
| LO13 — Convergent independent peer findings → high-confidence gap; auto-promote to eval | Claude ledger finding ≡ codex `judgment_gate.py` ("16 commits 0 Fable dispatches") | Emergent success pattern | Cross-project | Agent rule + Eval | Med | Yes |
| LO14 — "It's all local" — runtime should track local dev source; buildloop-local channel | "it's all local" steering; `buildloop-local` built | Explicit preference (project-specific, not broadly generalizable) | Project-specific | Project memory + Plugin | High | Yes (project only) |
| LO15 — SessionStart nudge visibility on installed CC version must be verified before claiming visible | Nudge unverified; additionalContext unconfirmed | Failure (incomplete verification) | Project-specific | Preflight check + Project note | Med | Yes (project only) |
| LO16 — Fable inline dispatch blocked by harness topology (nested/peer-host + model enum) | "0 Fable dispatches"; no build-loop:advisor; workaround = headless | Failure (tool/external dependency) | Project-specific | Project note + Preflight | Med | Yes (project only) — TAG:INFERRED runtime still thin |
| LO17 — DRY rewire (install_memory reads structure from manifest) still open | Dropped codex handoff; gaps list; `install_memory.py` + manifest both present | Failure (open item) | Project-specific | Project note (open task) | Med | Project note only |
| Fable firing frequently on 11 agents as config-declared | Config correct but runtime unproven — TAG:INFERRED | TAG:INFERRED | Project-specific | Do not encode as fact | — | No |
| Subagent hallucination rate/frequency | One instance noted ("you tested it"); not a recurrence count | Too weak (single instance, no frequency) | — | Do not encode | — | No |
| 1.4GB install size as a recurring pattern for all plugins | Specific to local-dir copy + .gitignore behavior; not generic | Project-specific (mechanism understood) | — | Do not encode globally | — | No |

---

## §2. Cross-Project User Preferences

| Preference | Evidence | Applies to | Stability | Recommended encoding |
|---|---|---|---|---|
| Recommendations always include cons | Explicit standing rule after one-sided rec ("recommendations need pros & cons") | Agent behavior / Communication | High | Memory (cross-project) |
| Autonomy calibrated on reversibility — auto on safe/reversible, gate on mutating/irreversible | Explicit correction (pull-default; wrong-autonomy note); revealed across multiple incidents | Agent behavior | High | Agent rule + Memory |
| Hold-not-merge into unstable main over completion bias | Explicit decision; user held P1 during codex storm | Architecture / Agent behavior | Med-High | Agent rule |
| Privacy-by-construction packaging: ship scaffolding, never private store | Explicit reframe "structure not SQLite"; allowlist + deny-scan built | Product strategy / Architecture | High | Skill + Memory |
| Discovery-before-classification in prompts and skills | Explicit prompt feedback; pushed loosening of fixed taxonomies | Agent behavior / Design | Med-High | Skill guardrail (already encoded) |
| Independent multi-layer verification before merge | Repeated redirects toward holistic + independent review; panel + Fable + judge | Testing | High | Eval + Agent rule |
| Decision-support quality: evidence tokens on verification claims | Hallucination incident; "you tested it" rejected | Agent behavior / Communication | High | Eval + Agent rule |
| Option preservation mid-build | Held P1; "don't lock in mid-build choices" | Architecture / Product strategy | Med-High | Agent rule |

---

## §3. Revealed Workflow Patterns

### Expressed preferences
| Workflow pattern | Evidence | Reusable? | System implication | Encoding target |
|---|---|---|---|---|
| Recommendations carry cons | Explicit standing rule | Yes | Default behavior for all recommendations | Memory (cross-project) |
| Privacy-by-construction | Explicit reframe; allowlist + deny-scan | Yes | Any package/ship workflow uses git-archive | Skill + Plugin |

### Revealed preferences
| Workflow pattern | Evidence | Reusable? | System implication | Encoding target |
|---|---|---|---|---|
| Autonomy reversibility-keyed | Pull-default (auto); gate-merge (right); plugin-update-unrequested (wrong) | Yes | Build a reversibility classifier for all autonomous action decisions | Agent instruction |
| Discovery-first over fixed buckets | Prompt loosening feedback | Yes | Seed scaffolds in skills, not closed taxonomy lists | Skill guardrail |

### Decision patterns
| Workflow pattern | Evidence | Reusable? | System implication | Encoding target |
|---|---|---|---|---|
| Hold-not-merge over completion bias | Held P1; "option preservation" framing | Yes | Orchestrator must surface hold as a valid, evidenced option | Agent rule |
| Instrument-before-trust | "0 Fable dispatches" → add telemetry → discover convergent codex finding | Yes | Never assert a tier/model fires without ledger-backed evidence | Agent rule + Eval |

### Intervention/failure escape patterns
| Workflow pattern | Evidence | Reusable? | System implication | Encoding target |
|---|---|---|---|---|
| Concurrent agents on shared tree → worktree isolation | Collisions → worktree fix | Yes | Provision worktrees at session start, not after first collision | Agent instruction + Plugin |
| Per-task TDD alone insufficient for cross-cutting risks | Path-traversal escaped TDD; caught by holistic review | Yes | Mandate holistic review as a second mandatory layer | Eval + Agent rule |
| Resolved≠delivered in delegation tools | Dropped DRY rewire | Yes | Track handoffs to delivered state; audit after delegation | Plugin + Agent rule |

### Success patterns
| Workflow pattern | Evidence | Reusable? | System implication | Encoding target |
|---|---|---|---|---|
| Convergent peer discovery → high-confidence gap | Ledger finding ≡ codex judgment_gate | Yes | Auto-promote convergent findings to eval/gate | Agent rule |
| Privacy-by-construction packaging with git-archive | `5055efe`; `cc05724`; clean 0.34.0 release | Yes | Template this workflow | Skill |
| Option preservation; held P1 | Held not merged; clean release shipped separately | Yes | Build hold-state into orchestrator | Agent rule |

### Automation opportunities
| Pattern | Evidence | Opportunity |
|---|---|---|
| Concurrent-agent detection at session start | Late worktree isolation (collision first) | Auto-detect ≥2 agents targeting same working tree; auto-provision worktrees |
| Handoff delivered-state tracking | Dropped DRY rewire | Task ledger with explicit delivered vs resolved state |
| Reversibility classification | Multiple autonomy incidents | Reversibility-keyed gate before any autonomous command |
| Adversarial input-safety eval | Path-traversal escape | Auto-run input-safety scan on any name→path code path |

---

## §4. Project-Specific Learning (build-loop)

| Project-specific learning | Evidence | Why it matters | Expiration / review trigger |
|---|---|---|---|
| Extensions P1 held on `feat/extensions-p1` (tip `0b49792`); do NOT merge until codex storm settles and identity-write gate built | User decision; codex storm; identity gate flagged | Merging now inherits conflict risk and unbuilt gate | Review when: codex commit storm settles; identity.json gate implemented |
| DRY rewire (install_memory reads structure from manifest) is open; rally handoff resolved≠delivered | Gaps list; both files exist; rewire unconfirmed | Duplication risk; install may diverge from manifest | Close before next release |
| SessionStart nudge (additionalContext) unverified on installed CC version | Evidence gap noted explicitly | Cannot claim nudge is visible until probed | Verify on next installed CC probe |
| Fable inline dispatch blocked by harness topology: nested/peer-host + model enum (opus/sonnet/haiku only); workaround = headless `claude --model fable -p` | Evidence: "0 Fable dispatches"; no build-loop:advisor | Runtime proof of Frontier firing still thin — TAG:INFERRED | Review when: harness model enum updated or build-loop:advisor subagent added |
| rally is pull-only: poll-after-post is the correct default; target-resolve-only → added poster-side `dispose` fallback | Pull-vs-poll correction; `rally_poll_gate.py` `6631d42` | Prevents coordination dead-ends | Stable until rally protocol changes |
| `buildloop-local` channel pins runtime to local dev repo; `sync_plugin_cache.py` uses git-archive HEAD | "it's all local"; `sync_plugin_cache.py` present | Reliable local-dev workflow | Stable |
| Main is ~`a1c8823` v0.35.0; 16 ahead of origin; codex active with commit storm | Current state | Active storm; do not merge P1 or do direct pushes without worktree isolation | Review when: codex storm clears |
| identity.json routing gate not yet built: autonomous writes to core_repo via user-writable JSON flagged but unimplemented | Hard gate identified; not built | Privilege escalation vector open | Must close before maintainer-mode ships |
| Memory files written this session: rally-pull-only, recommendations-need-pros-cons, buildloop-local-channel, build-loop-packaging-and-memory-seed, extensions-maintainer-mode-status | Evidence: 5 named memory files | Load at session start for relevant decisions | Review each on recurrence count ≥2 |
| Accepted architecture: `pending→approve→active` gate with `~/.build-loop-extensions/` overlay surviving plugin updates | Spike confirmed `pending/` stays unloaded (load-bearing) | Core extensions model; spike result is a hard constraint | Stable until extensions-p2 |
| Rejected approach: SQLite as the memory/store substrate (ships private data) | "structure not SQLite" | Privacy-by-construction constraint | Stable |

---

## §5. Agent Instructions

| Agent rule | Trigger | Expected behavior | Evidence | Scope |
|---|---|---|---|---|
| Provision a git worktree per agent before first commit when ≥2 agents target the same repo | Session start with ≥2 active agents OR concurrent peer detected | Orchestrator creates an isolated worktree for each agent; no shared working tree | Shared-tree collision failures; late worktree fix | Cross-project |
| Classify an action's reversibility before executing autonomously; auto on reversible, gate on irreversible or state-mutating | Any autonomous action decision | Reversible (poll, instrument, read) → auto; Mutating/irreversible (publish, merge, write to core store, plugin update) → require in-scope approval | Pull-default vs gate-merge vs unrequested plugin-update incidents | Cross-project |
| Never run state-mutating CLI commands outside the active task scope, including during read-only investigations | Read-only or investigation task scope | Constrain tool use to scope; flag any out-of-scope mutation need before executing | Unrequested `claude plugin update` incident | Cross-project |
| Require an evidence token on any verification claim; reject "I tested it" without a citation | Any agent makes a verification claim | Claim must reference: test file, ledger entry, direct output, or named check | Guide subagent hallucinated "you tested it" | Cross-project |
| Mandate a holistic pre-merge review as a second layer above per-task TDD; never ship on narrow tests alone | Before any feature merge | Run TDD (per-task) → adversarial input-safety check → holistic (cross-cutting) review → runtime-fired proof | Path-traversal survived TDD + direct verify; caught only by holistic opus review | Cross-project |
| Instrument before asserting: add telemetry before claiming a model/tier/gate fires | Session start OR whenever asserting a system behavior | Wire ledger or equivalent runtime evidence; then assert | "0 Fable dispatches" despite correct config | Cross-project |
| Treat convergent independent findings between peer agents as high-confidence; auto-promote to eval or gate | Two agents independently surface the same gap | Surface as strong signal; raise to eval/gate immediately without waiting for third confirmation | Claude ledger finding ≡ codex judgment_gate.py | Cross-project |
| Surface hold-not-merge as a valid, evidenced orchestrator option when main is unstable | Active commit storm on main; P-N feature ready but main unstable | Present hold as a legitimate alternative to merge; require explicit user decision to merge into storm | User held P1; "option preservation over completion bias" | Cross-project |
| Verify peer handoffs to delivered, not closed/resolved; audit after delegation before claiming complete | After delegating a sub-task to a peer agent | Check that repo diff + any verifiable artifact confirms the work landed, not just that the ticket closed | Dropped DRY rewire; resolved≠delivered | Cross-project |
| Load durable memory defaults at session start: pros&cons, privacy-by-construction, autonomy-reversibility, option-preservation | Session start for any build-loop or multi-agent task | Surface relevant memories before first recommendation or action; do not wait to be asked | Memories written but not read-first this session | Cross-project |

---

## §6. Plugin and Tool Behavior Updates

| Tool/plugin behavior | Trigger | Expected behavior | Evidence | Priority |
|---|---|---|---|---|
| rally_poll_gate.py — poll-after-post default; no permission prompt | Post to rally | Automatically poll after post; never ask for permission to poll; use dispose fallback when target-resolve-only | Explicit correction + `6631d42` | P0 (built; confirm no prompt remains) |
| rally_merge_gate.py — pre-merge conflict gate; warn-first | Before merge to main | Warn on conflict with active storm; surface conflict risk before allowing merge | `475b5b3`/`faed812` | P0 (built; wire as default pre-merge check) |
| sync_plugin_cache.py — git-archive install; never dirty-tree copy | Local install channel | Use `git archive HEAD` to build install package; exclude `.rally`, `.build-loop`, `node_modules` | `cc05724`; packaging RCA | P0 (built; verify no dirty-tree copy path remains) |
| Auto-provision worktree per agent on agent spawn | ≥2 agents targeting same repo | Orchestrator provisions a worktree before first commit; no shared working tree | Collision failures | P0 (not built; design needed) |
| Task ledger with explicit `delivered` state separate from `resolved` | Peer agent delegation | Track handoffs to delivered (verified repo diff) vs resolved/closed (ticket state only) | Dropped DRY rewire | P0 (not built) |
| NavGator — name→path dataflow trace as pre-build preflight | Any code mapping user input to filesystem path | Trace input→path flows early; flag unsanitized names | Path-traversal escaped TDD | P1 (needs integration) |
| NavGator — dirty-tree-vs-.gitignore copy risk detector | Before any local-dir install package build | Scan copy path vs .gitignore; warn if live store would be included | Packaging RCA | P1 |
| Host-mode reachability probe: can Frontier be dispatched from current topology? | Session start for any multi-agent session with Frontier config | Probe model enum + host mode; report whether Frontier is callable inline or requires headless | "0 Fable dispatches" / harness topology block | P1 |
| Scope gate on all state-mutating CLI commands | Any agent action outside its declared task scope | Block or require approval before executing mutating commands; log attempted out-of-scope mutations | Unrequested `claude plugin update` | P0 |
| SessionStart nudge (additionalContext) — verify rendering on installed CC version | Any nudge added to plugin | Probe installed CC version for nudge visibility before shipping or claiming visible | Nudge unverified | P1 |

---

## §7. App Feature Opportunities

| App feature | Problem it solves | Evidence | User value | Priority |
|---|---|---|---|---|
| Concurrent-agent detector + auto-worktree provisioner | Shared-tree collisions from late isolation | Multiple collision incidents; "worktree isolation applied late" | Prevents commit storms and swept files structurally | P0 |
| Task ledger with `delivered` vs `resolved` states | Silent handoff drops; resolved≠delivered | Dropped DRY rewire | Catches dropped delegations before they become open gaps at release time | P0 |
| Adversarial input-safety eval (name→path injection check) | Path-traversal class bugs | Path-traversal survived TDD; `0b49792` fix | Catches cross-cutting security bugs that narrow TDD misses | P0 |
| Release-readiness dashboard (clean-install-by-construction + Frontier-fired + zero held adversarial findings) | Premature merge/release claims | Multiple gaps reached release stage | Single signal for "safe to ship" | P1 |
| Autonomy classifier view (reversible→auto, irreversible/mutating→gate) | Wrong autonomy (both over- and under-reach) | Pull-permission under-reach + plugin-update over-reach | Consistent, predictable agent behavior | P1 |
| Approval dashboard for reversibility-gated actions only | Approval fatigue from over-gating | Autonomy miscalibration pattern | Human review concentrated on actions that matter | P1 |
| Handoff completion tracker (rally-aware delivered-state audit) | Dropped rally handoffs | Dropped DRY rewire; resolved≠delivered | No delegations silently lost | P1 |
| Runtime-fired proof dashboard (ledger-backed Frontier dispatch evidence) | Config-correct ≠ runtime-correct gap | "0 Fable dispatches"; `judgment_gate.py` convergent finding | Closes the "is Fable actually firing?" question with evidence | P1 |
| Identity-write approve gate for core-repo mutations via user-writable JSON | Privilege escalation via `identity.json` | Flagged but unbuilt | Required before maintainer-mode ships | P2 |

---

## §8. Skills to Create or Reuse

| Skill | Purpose | Trigger | Inputs | Outputs | Success criteria |
|---|---|---|---|---|---|
| concurrent-agent-preflight | Detect + isolate multi-agent on one repo; provision worktrees | Session start with ≥2 agents | Repo state, active agent roster | Worktree provisioning plan; isolation report | No shared-tree commits; each agent on isolated worktree |
| privacy-by-construction-packaging | Ship scaffolding not store; git-archive + allowlist + PII deny-scan | Any package/install/ship build step | Repo, .gitignore, manifest, EXCLUDE_DIRS config | git-archive seed + deny-scan report | Clean install; no PII; no live store shipped |
| runtime-fired-verifier | Prove a tier/model/gate actually fired at the right rung | Verification rung reached; any "did X fire?" question | agent-ledger.jsonl or equivalent telemetry | Fired/not-fired verdict with evidence citation | Ledger confirms Frontier fired at Review-G (or flags headless fallback) |
| handoff-completion-tracker | Verify peer handoffs delivered, not just resolved | After any delegation to a peer agent | Rally/task state + repo diff | Delivered/not-delivered verdict | No silently dropped handoffs; open gap surfaced before release |
| recursive-retrospective (already built) | Convert a retrospective into learning objects | Session review request | Retro evidence, retro output | §12 learning-object package | Already encoded on branch `feat/recursive-retrospective` |

---

## §9. Evals and Quality Gates

| Eval or gate | Catches | Runs when | Pass criteria | Failure action |
|---|---|---|---|---|
| Adversarial input-safety eval (name→path injection) | Path-traversal / injection via any name/user-input flowing to a filesystem path | Before merge of any code with input→path mapping | All adversarial inputs (traversal, null, overlong, special chars) rejected | Block merge; require fix + test |
| Holistic pre-merge review | Cross-cutting bugs that per-task TDD misses | Before any feature merge, after per-task TDD passes | Reviewer (holistic, not the implementer) finds no blocking issue | Hold merge; log finding as learning-object candidate |
| Frontier-fired ledger check | "0 Fable dispatches" class; config-correct ≠ runtime-correct | At verification rungs; before any release claiming Frontier assessed the build | Ledger shows ≥1 Frontier-tier verdict at Review-G for this build | Flag; activate headless fallback; do not claim assessed without evidence |
| Handoff-delivered check | Dropped delegations; resolved≠delivered | After any peer-agent handoff; before release | Repo diff + artifact confirms work landed, not just ticket closed | Reopen handoff; do not close release checklist |
| Verification-claim grounding eval | Hallucinated verification; "I tested it" without evidence | On any agent verification claim | Claim references: named test output, ledger entry, diff, or direct observable output | Reject claim; require evidence token |
| Concurrent-agent isolation check | Shared working tree before first commit | Session start with ≥2 agents; before any commit | Each active agent has a dedicated worktree or isolated branch | Provision worktrees; block commits until isolated |
| Scope-gate check for mutating CLI | Out-of-scope state mutation during read-only tasks | Before any CLI command that modifies state | Command is within declared task scope; reversible OR approved | Block command; log as approval-gate candidate |
| Clean-install-by-construction check | Live store / PII shipped in install package | Before any publish/release | `git archive HEAD` used; EXCLUDE_DIRS applied; PII deny-scan passes | Block publish; fix packaging |

---

## §10. Hard Gates and Approval Rules

| Gate | Why approval needed | Can be pre-captured? | Encoding target | Build Loop behavior |
|---|---|---|---|---|
| npm / GitHub Packages publish | Irreversible public release; version number is permanent | Partial — version policy yes; the publish itself must be gated interactively | Approval gate + Memory | Gate at publish step; require explicit version bump decision; log version bump as user-approved |
| Merge of P1 (or any feature branch) into main during active commit storm | Risk inheritance: conflict + unbuilt gates import into main | No — storm state is runtime | Approval gate + Agent rule | Hold by default when main has active peer-agent storm; surface risk; require explicit user merge decision |
| identity.json autonomous writes to core repo | Privilege escalation via user-writable JSON; maintainer-mode feature | Partial — policy yes; specific write must be gated | Approval gate + Project note | Interactive approve before any core-repo write routed via identity.json; not built yet — P2 |
| State-mutating CLI commands outside active task scope | Unrequested mutation (e.g., `claude plugin update` during investigation) | Yes — scope policy encodable as agent rule | Approval gate + Agent rule | Block mutating commands outside declared scope; log attempted mutation; require approval or task-scope expansion |
| Taste/design decisions with no prior durable memory | Subjective; first occurrence has no prior to compare against | Partial — categories pre-capturable | Approval gate (one-time) | Ask once; store result as durable memory; never ask again for same preference |

---

## §11. Do-Not-Encode List

| Finding | Why not encode? | Safer handling |
|---|---|---|
| "Fable fired reliably at runtime after instrumentation" | TAG:INFERRED — runtime proof still thin; ledger wired but no confirmed Frontier-fired entry in evidence | Project note only: "Frontier dispatch configured; runtime firing unconfirmed — verify via ledger before next release" |
| install_memory.py DRY rewire is still open (duplicative with manifest) | Open gap, not a learning pattern; specific to one file pair | Project note / open task: track to closure before next release; do not generalize |
| Subagent hallucination rate/frequency as a general metric | Single instance ("you tested it"); insufficient recurrence for a rate claim | Note: eval required (verification-claim grounding); do not encode a frequency claim |
| 1.4GB install size as a cross-project pattern | Mechanism fully understood (local-dir copy + .gitignore bypass); root cause fixed; not a recurrence risk once git-archive is standard | Encode the fix (git-archive) not the symptom; do not encode install size as a general warning |
| "Discovery-first prompt feedback is already encoded" (LO11) | Already encoded in the recursive-retrospective skill on the branch; re-encoding would duplicate | Verify on merge; close if present |
| SessionStart nudge unverified rendering | Project-specific transient state; unknown until probed | Project preflight: "probe installed CC for nudge visibility before claiming visible" |
| Specific rally commit hashes (a1c8823, 0b49792, etc.) | Ephemeral; not decision-relevant for future sessions | Evidence-ref only; not encodable as memory |
| codex shipped `judgment_gate.py`, cross-vendor registry, ARP/rally-interop on main | Peer-agent work products; not user preference or durable pattern | Acknowledge in project state; do not encode as user preference |
| Fable inline dispatch blocked by harness model enum (opus/sonnet/haiku) | Project-specific harness limitation; changes when enum expands or subagent added | Project note + preflight: probe model enum at session start |

---

## §12. Final Learning Object Package

### Cross-Project Memories

**1. Recommendations always include pros AND cons**
- Evidence: Explicit standing rule ("recommendations need pros & cons"); set after one-sided recommendation this session.
- Scope: Cross-project
- Encoding target: Memory (cross-project, durable user rule)
- Confidence: High
- Approval needed before storing or applying? No — user explicitly stated it as a standing rule.

**2. Autonomy calibrated on reversibility: auto on reversible, gate on irreversible/state-mutating**
- Evidence: Pull-default correction (auto is right for poll); unrequested `claude plugin update` (mutation without scope = wrong); gate-merge (irreversible = right to gate). Revealed across three separate incidents.
- Scope: Cross-project
- Encoding target: Memory + Agent instruction
- Confidence: High
- Approval needed? No — revealed explicitly through repeated correction and the explicit pull-default steering.

**3. Privacy-by-construction packaging: ship scaffolding, never private store**
- Evidence: Explicit reframe "structure not SQLite"; root-caused 1.4GB install; allowlist + PII deny-scan + git-archive built and released at 0.34.0.
- Scope: Cross-project (any plugin/package build step)
- Encoding target: Memory + Skill (privacy-by-construction-packaging)
- Confidence: High
- Approval needed? No — explicit user preference with a shipped implementation validating it.

**4. Hold-not-merge into unstable/active-storm main is valid and preferred mid-build**
- Evidence: User held P1 over completion bias; "option preservation"; framed as a deliberate, evidenced decision.
- Scope: Cross-project
- Encoding target: Memory + Agent rule
- Confidence: Med-High
- Approval needed? No — explicit user decision with explicit framing.

**5. Instrument before asserting: add telemetry before claiming a model/tier/gate fires**
- Evidence: "0 Fable dispatches" despite config declaring Fable on 11 agents; convergent codex finding; config-correct ≠ runtime-correct pattern.
- Scope: Cross-project
- Encoding target: Memory + Agent rule + Eval (Frontier-fired ledger check)
- Confidence: High
- Approval needed? No — explicit failure pattern with high recurrence signal (convergent peer discovery strengthens it).

---

### Project-Specific Memories (build-loop)

**6. rally is pull-only: poll-after-post is correct default; no permission prompt**
- Evidence: Explicit pull-vs-poll correction; `rally_poll_gate.py` built (`6631d42`); standing correction at coordination level.
- Scope: Project-specific (rally coordination in build-loop)
- Encoding target: Project memory + Plugin behavior (`rally_poll_gate.py`)
- Confidence: High
- Approval needed? No — explicit, implemented.

**7. Extensions P1 held on `feat/extensions-p1`; do not merge until: codex storm settles + identity-write gate built**
- Evidence: User decision; storm on main `a1c8823`; identity gate flagged unbuilt; conflict risk explicit.
- Scope: Project-specific
- Encoding target: Project memory (open state) + Approval gate
- Confidence: High
- Approval needed? No — records a user decision already made; the merge itself will need a gate.

**8. buildloop-local channel pins runtime to local dev repo; use sync_plugin_cache.py + git-archive**
- Evidence: "it's all local" explicit; `buildloop-local` built; race-hardened via git-archive.
- Scope: Project-specific
- Encoding target: Project memory + Plugin behavior
- Confidence: High
- Approval needed? No.

**9. Fable inline dispatch blocked by harness topology (peer-host + model enum); workaround = headless `claude --model fable -p`** — TAG:INFERRED reliable firing
- Evidence: "0 Fable dispatches"; no build-loop:advisor subagent; model enum limited. Workaround used. TAG:INFERRED that instrumentation + judgment_gate now produces Frontier verdicts reliably — unconfirmed at runtime.
- Scope: Project-specific
- Encoding target: Project note + Preflight check
- Confidence: Med (runtime still thin)
- Approval needed? No — records a known constraint.

**10. DRY rewire (install_memory reads structure from manifest) is open — resolved≠delivered**
- Evidence: Dropped codex handoff; both `install_memory.py` and manifest exist; rewire unconfirmed applied.
- Scope: Project-specific
- Encoding target: Project note (open task)
- Confidence: Med
- Approval needed? No — records an open item.

**11. SessionStart nudge (additionalContext) unverified on installed CC version**
- Evidence: Explicitly flagged as unverified in evidence package and retro.
- Scope: Project-specific
- Encoding target: Project preflight check
- Confidence: Med
- Approval needed? No.

---

### Agent Instructions

**12. Provision a git worktree per agent before first commit when ≥2 agents target the same repo**
- Evidence: Multiple shared-tree collision incidents; late worktree fix; codex storm corrupted Claude's index/HEAD. Recurred.
- Scope: Cross-project
- Encoding target: Agent instruction (orchestrator) + Plugin (auto-provision on agent spawn)
- Confidence: High
- Approval needed? No — failure recurred; prevention is clearly the right default.

**13. Gate all state-mutating CLI commands to in-scope + approved; never mutate during read-only tasks**
- Evidence: Unrequested `claude plugin update` during investigation caused install race; explicit out-of-scope mutation.
- Scope: Cross-project
- Encoding target: Agent rule + Approval gate + Eval (scope-gate check)
- Confidence: High
- Approval needed? No — hard gate class; explicitly evidenced.

**14. Require an evidence token on any verification claim; "I tested it" without citation is invalid**
- Evidence: Guide subagent hallucinated "you tested it"; path-traversal escape due to incomplete verification claims.
- Scope: Cross-project
- Encoding target: Agent rule + Eval (verification-claim grounding)
- Confidence: High
- Approval needed? No.

**15. Mandate a holistic pre-merge review as a second layer above per-task TDD; never ship on narrow tests alone**
- Evidence: Path-traversal survived per-task TDD + direct verify; caught only by final holistic opus review. High-impact failure.
- Scope: Cross-project
- Encoding target: Agent rule + Eval (holistic pre-merge review)
- Confidence: High
- Approval needed? No.

**16. Verify peer-agent handoffs to delivered (repo diff + artifact), not just to resolved/closed**
- Evidence: Dropped DRY rewire; rally resolved≠delivered; systematic failure class.
- Scope: Cross-project
- Encoding target: Agent rule + Plugin (task ledger)
- Confidence: High
- Approval needed? No.

**17. Treat convergent independent findings from peer agents as high-confidence; auto-promote to eval/gate**
- Evidence: Claude's ledger finding ≡ codex's `judgment_gate.py` ("16 commits 0 Fable dispatches") — two independent agents surfaced the exact same gap.
- Scope: Cross-project
- Encoding target: Agent rule
- Confidence: Med (single occurrence; pattern is sound)
- Approval needed? Yes — TAG:INFERRED as a generalizable rule from one event.

---

### Skills to Create or Reuse

**18. concurrent-agent-preflight (create)**
- Evidence: Repeated collision failures; isolation applied late; multiple incidents.
- Scope: Cross-project
- Encoding target: Skill
- Confidence: High
- Approval needed? No.

**19. privacy-by-construction-packaging (create)**
- Evidence: Packaging RCA; "structure not SQLite"; git-archive + allowlist + deny-scan implemented and validated at 0.34.0.
- Scope: Cross-project
- Encoding target: Skill
- Confidence: High
- Approval needed? No.

**20. runtime-fired-verifier (create)**
- Evidence: Instrument-before-trust pattern; "0 Fable dispatches"; convergent codex finding.
- Scope: Cross-project
- Encoding target: Skill
- Confidence: High
- Approval needed? No.

**21. handoff-completion-tracker (create)**
- Evidence: Dropped DRY rewire; resolved≠delivered failure class; recurs in any multi-agent delegation.
- Scope: Cross-project
- Encoding target: Skill
- Confidence: High
- Approval needed? No.

**22. recursive-retrospective (already built — verify and merge)**
- Evidence: Skill authored this session; on branch `feat/recursive-retrospective`.
- Scope: Cross-project
- Encoding target: Skill (existing — merge gate)
- Confidence: High
- Approval needed? No — already built; gate is merge into main.

---

### Plugin/Tool Behavior Updates

**23. rally_poll_gate.py — confirm no permission prompt remains for poll-after-post**
- Evidence: Explicit correction; built `6631d42`; confirm in-flight.
- Scope: Project-specific (rally / build-loop)
- Encoding target: Plugin behavior
- Confidence: High
- Approval needed? No.

**24. sync_plugin_cache.py — confirm git-archive path; no dirty-tree copy path survives**
- Evidence: Packaging RCA; `cc05724`; clean 0.34.0.
- Scope: Project-specific
- Encoding target: Plugin behavior
- Confidence: High
- Approval needed? No.

**25. Auto-provision worktree per agent on spawn (design + build)**
- Evidence: Collision failures; worktrees added manually late.
- Scope: Cross-project (plugin orchestration layer)
- Encoding target: Plugin behavior
- Confidence: High
- Approval needed? Yes — design needed before build; touches orchestration contract.

**26. Task ledger with explicit `delivered` state (design + build)**
- Evidence: Dropped DRY rewire; resolved≠delivered.
- Scope: Cross-project
- Encoding target: Plugin behavior
- Confidence: High
- Approval needed? Yes — schema design needed; touches coordination contract.

**27. Scope-gate for state-mutating CLI commands**
- Evidence: Unrequested `claude plugin update`.
- Scope: Cross-project
- Encoding target: Plugin behavior + Approval gate
- Confidence: High
- Approval needed? Yes — gate behavior needs design (allowlist vs scope-declaration).

---

### App Feature Opportunities

**28. Concurrent-agent detector + auto-worktree provisioner**
- Evidence: Collision failures; structural prevention preferred.
- Scope: Cross-project
- Encoding target: App feature (P0)
- Confidence: High
- Approval needed? Yes — design + UX for the provisioner needed.

**29. Task ledger with delivered vs resolved states**
- Evidence: Dropped DRY rewire; coordination failure class.
- Scope: Cross-project
- Encoding target: App feature (P0)
- Confidence: High
- Approval needed? Yes.

**30. Adversarial input-safety eval runner**
- Evidence: Path-traversal escape; `0b49792` fix added after holistic review.
- Scope: Cross-project
- Encoding target: App feature (P0) + Eval
- Confidence: High
- Approval needed? No — add to eval suite.

**31. Release-readiness dashboard (clean-install + Frontier-fired + zero held adversarial findings)**
- Evidence: Multiple release-stage gaps; convergent gap finding.
- Scope: Cross-project
- Encoding target: App feature (P1)
- Confidence: Med
- Approval needed? Yes.

---

### Evals and Quality Gates

**32. Adversarial input-safety eval (name→path injection)**
- Evidence: Path-traversal bug survived TDD; `0b49792`.
- Scope: Cross-project
- Encoding target: Eval (P0)
- Confidence: High
- Approval needed? No.

**33. Holistic pre-merge review (mandatory second layer)**
- Evidence: Path-traversal escape from narrow TDD; holistic caught it.
- Scope: Cross-project
- Encoding target: Eval (P0)
- Confidence: High
- Approval needed? No.

**34. Frontier-fired ledger check**
- Evidence: "0 Fable dispatches"; config-correct ≠ runtime-correct.
- Scope: Cross-project (initially project: build-loop)
- Encoding target: Eval (P1)
- Confidence: High
- Approval needed? No.

**35. Handoff-delivered check**
- Evidence: Dropped DRY rewire; resolved≠delivered.
- Scope: Cross-project
- Encoding target: Eval (P0)
- Confidence: High
- Approval needed? No.

**36. Verification-claim grounding eval**
- Evidence: Guide subagent hallucinated "you tested it."
- Scope: Cross-project
- Encoding target: Eval (P0)
- Confidence: High
- Approval needed? No.

---

### Preflight Checks

**37. "Are ≥2 agents committing to the same working tree?" — check at session start**
- Evidence: Late worktree isolation; collisions.
- Scope: Cross-project
- Encoding target: Preflight check
- Confidence: High
- Approval needed? No.

**38. "Does any name/user-input flow into a filesystem path?" — check before build**
- Evidence: Path-traversal escape.
- Scope: Cross-project
- Encoding target: Preflight check (NavGator dataflow trace)
- Confidence: High
- Approval needed? No.

**39. "Can Frontier be dispatched from the current host topology?" — check at session start for any Frontier-config session**
- Evidence: Harness topology block; model enum limitation.
- Scope: Cross-project (applies to any session declaring Frontier-tier agents)
- Encoding target: Preflight check
- Confidence: High
- Approval needed? No.

**40. "Does the installer read any live-mutating store?" — check before install build**
- Evidence: Install race; dirty-tree copy.
- Scope: Cross-project
- Encoding target: Preflight check
- Confidence: High
- Approval needed? No.

**41. "Is the SessionStart nudge visible on the installed CC version?" — check before claiming visible** (project-specific)
- Evidence: Nudge unverified.
- Scope: Project-specific (build-loop)
- Encoding target: Preflight check (project)
- Confidence: Med
- Approval needed? No.

---

### Approval Gates

**42. npm / GitHub Packages publish gate (irreversible)**
- Evidence: User explicitly chose version bump (0.34.0) before publish; hard gate encountered and respected.
- Scope: Cross-project (any npm/registry publish)
- Encoding target: Approval gate
- Confidence: High
- Approval needed? No — gate is itself the approved behavior.

**43. Merge of feature branch into main during active commit storm**
- Evidence: User held P1; codex storm + shared-file conflict risk explicit.
- Scope: Cross-project
- Encoding target: Approval gate
- Confidence: High
- Approval needed? No — gate behavior is the recommendation.

**44. identity.json autonomous writes to core repo (identity-write gate)**
- Evidence: Maintainer-mode design; flagged but unbuilt; privilege-escalation vector.
- Scope: Project-specific (build-loop maintainer-mode)
- Encoding target: Approval gate + Project note
- Confidence: Med
- Approval needed? Yes — gate design requires user input on scope and escalation path; implementation deferred.

**45. State-mutating CLI outside declared task scope**
- Evidence: Unrequested `claude plugin update` incident.
- Scope: Cross-project
- Encoding target: Approval gate + Agent rule
- Confidence: High
- Approval needed? Yes — allowlist vs scope-declaration design needed before implementing the gate.

---

### Do Not Encode

| Finding | Why not encode? | Safer handling |
|---|---|---|
| "Fable fired reliably at runtime after instrumentation" | TAG:INFERRED — no confirmed ledger entry in evidence; runtime proof still thin | Project preflight: verify via ledger before next release claim |
| install_memory.py DRY rewire is still open | Open task, not a learning pattern; specific to one file pair | Project note / open task; track to closure |
| Subagent hallucination rate/frequency as a general claim | Single instance; no recurrence count; insufficient for a frequency encoding | Encode the eval (verification-claim grounding), not a rate claim |
| 1.4GB install size as a cross-project warning | Mechanism fully understood; fixed; symptom of local-dir copy, not a general truth | Encode the fix (git-archive + allowlist) not the symptom |
| LO11 discovery-before-classification may already be encoded | Encoded on branch; verify on merge — not a new learning object | Verify post-merge; close if present |
| Specific commit hashes as durable references | Ephemeral identifiers; meaningless in a future session | Evidence refs only; not storable as memory |
| codex built judgment_gate.py, cross-vendor registry, ARP/rally-interop | Peer-agent work products; not user preference or generalizable pattern | Acknowledge in project state; do not encode as preference |
| "16 commits, 0 Fable dispatches" specific count | Transient point-in-time metric; not a durable rule | Evidence citation only for LO10; do not encode the count |
| Nudge rendering on installed CC as a confirmed fact | Explicitly unverified in evidence | Preflight check, not a confirmed fact |
| Fable inline blocked by specific model enum list | Harness-version-specific; changes when enum expands | Project preflight + note; not a cross-project memory |

---

## Summary Counts

| Category | Count |
|---|---|
| Cross-project memories | 5 |
| Project-specific memories | 6 |
| Agent instructions | 6 |
| Skills to create or reuse | 5 |
| Plugin/tool behavior updates | 5 |
| App feature opportunities | 4 |
| Evals and quality gates | 6 |
| Preflight checks | 5 |
| Approval gates | 4 |
| **Total encode=yes** | **46** |
| Do-not-encode | **10** |
