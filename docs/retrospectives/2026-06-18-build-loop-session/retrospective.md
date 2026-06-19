# Recursive Learning Retrospective — build-loop session (2026-06-11 → 06-18)

Independent retrospective. The analyst did not participate in the session; all judgments are drawn from the evidence package (`/tmp/retro-evidence-session.md`), grounded against the `build-loop` repo where verifiable. The "project" is a multi-day interactive working session improving the build-loop Claude Code plugin, with Claude (Opus) as the working agent and codex as a concurrent peer agent committing to the same repo.

---

## 1. Source Coverage

| Source | Available? | Used for | Confidence |
|---|---|---|---|
| Initial specs | Partial | Goal arc reconstructed from evidence narrative; no original spec doc surfaced beyond `docs/design/extensions-and-maintainer-mode.md` + `docs/plans/2026-06-13-extensions-maintainer-mode.md` (both confirmed in repo) | Med |
| Current repo | Yes | Branch/commit existence, file presence — verified directly (main `a1c8823`; `feat/extensions-p1` tip `0b49792`; `feat/recursive-retrospective`) | High |
| NavGator analysis | No | Not present in evidence; no architecture-map artifact referenced | — |
| Build Loop memory | Partial | Memory files named in evidence (rally-pull-only, recommendations-need-pros-cons, buildloop-local-channel, packaging-and-memory-seed, extensions-maintainer-mode-status) — named, contents not provided | Med |
| Agent logs | Partial | `agent-ledger.jsonl` + `exec_state.py` item_iteration telemetry described as wired; no live ledger file found on disk (expected — runtime artifact) | Med |
| User chats | Yes (summarized) | Steering quotes in "Observed behaviors" — primary signal for revealed/explicit preferences | High |
| CI/tests/evals | Partial | TDD tests exist (`tests/test_extensions_*`); 4-persona panel + Fable plan-critic + spike described narratively, not as logs | Med |
| Deployment/env config | Yes | 0.34.0 release to npmjs + GitHub Packages described as verified; release commit `5995943` referenced, `0.34.0→0.35.0` bump `fbc0ec4` confirmed on branch | Med-High |
| App/plugin behavior | Partial | SessionStart nudge (additionalContext) explicitly **unverified** on installed CC version | Low |

- **Strongest evidence:** repo state — branches, commits, file presence all independently confirmed.
- **Weakest evidence:** runtime behavior — Fable actually firing, ledger contents, nudge visibility are all asserted-but-unverified at runtime.
- **Missing evidence:** NavGator/architecture map; raw agent logs; original written spec for the Fable-instrumentation goal; live `agent-ledger.jsonl` contents.
- **Confidence level:** **Medium-High.** Output is **directional-to-final** on structure/decisions, **directional** on runtime claims.

---

## 2. Project Maturity and Learning Posture

| Dimension | Assessment | Evidence | Confidence |
|---|---|---|---|
| Product direction | Clear and coherent — a self-improving dev loop with maintainer/consumer split | Goal arc 1→7 is a connected thread, not scattered tasks | High |
| Architecture | Directionally right, fragile at the coordination seam | Shared-tree collisions, install race, late worktree isolation | High |
| UX/workflow | Mid-build; gates designed but not all wired (nudge unverified) | Extensions `pending→approve→active` designed; nudge unverified | Med |
| Agent workflow | Active multi-agent (Claude+codex) without isolation discipline early | Commit-storm collisions, dropped DRY handoff, hallucinating subagents | High |
| Memory use | Active and improving — multiple durable memories written this session | 5 memory files named; "structure not SQLite", "pull-not-poll" captured | Med-High |
| Verification | Strong intent, leaky in practice | Path-traversal survived per-task TDD + direct verify, caught only by holistic opus review | High |
| Release readiness | Plugin ships (0.34.0 published); extensions feature HELD | Release verified; P1 not merged | High |

- **Project maturity state:** **Directionally-right / mid-build**, with one shipped increment (0.34.0/0.35.0 release + instrumentation) and one held increment (extensions P1).
- **Recommended posture:** **Refine** (core + coordination), **Preserve** (product concept, memory practice, held-not-merged discipline), **Defer** (extensions merge until codex storm settles).
- **Primary learning opportunity:** the *coordination substrate* for concurrent agents on a shared repo — every major friction (collisions, install race, dropped handoff) traces here, not to the features themselves.
- **Risk of locking in current design:** Med — merging P1 into an active commit-storm would inherit conflict risk and the maintainer-identity write gate before it is settled.
- **Risk of over-redesigning:** Med — the feature work is sound; resetting it would discard validated increments. Refine the seam, not the features.

---

## 3. Spec → Current State → Desired Outcome

| Area | Initial intent | Current state | Desired outcome | Gap | Confidence |
|---|---|---|---|---|---|
| Core job-to-be-done | Confirm Fable fires in the loop; make the loop observable | Instrumented (ledger + exec_state telemetry); judgment_gate enforces Frontier dispatch | Frontier verifiably fires at Review-G with runtime proof | Runtime proof of Fable dispatch still thin ("0 Fable dispatches" was the trigger) | Med |
| User workflow | Interactive drive with concurrent codex peer | Working but collision-prone | Isolated lanes, predictable merges | Worktree isolation applied late | High |
| Architecture | Plugin + per-user overlay surviving updates | Overlay designed + P1 built, held | Overlay merged, identity routing gated | P1 unmerged; identity write-gate unresolved | High |
| Data model | Ship scaffolding, never private store | `.rally` excluded; self-contained memory seed w/ manifest | Clean installs by construction | DRY rewire (install_memory reads structure from manifest) dropped | High |
| Memory model | Capture durable preferences | 5 memories written | Recurring patterns encoded, one-offs excluded | Good — discipline appears intact | Med-High |
| Agent behavior | Claude + codex peer commits | Concurrent, occasionally hallucinating/unrequested-mutation | Bounded, verified handoffs | Subagent ran `claude plugin update` unrequested; another hallucinated "you tested it" | High |
| Plugin/tool behavior | Local install channel pinned to dev repo | `buildloop-local` built; race hardened via `sync_plugin_cache.py` git-archive | Atomic, race-free local install | Hardened; clean by construction now | Med-High |
| UI/UX | Pending-drafts nudge surfaces work | Nudge implemented | Nudge visible on installed CC | Unverified on installed version | Low |
| Verification/evals | Independent multi-layer verification | 4-persona panel + Fable critic + spike + holistic review | Catch bugs before merge | Per-task TDD + direct verify missed path-traversal | High |
| Permissions/hard gates | Gate irreversible/identity ops | Publish gated (user chose bump); P1 merge held | Identity-routed writes gated interactively | identity.json autonomous-write gate flagged, not built | Med |

### Where the Project Stands Today
**Maturity:** directionally-right, mid-build, one increment shipped. **Strengths:** coherent product thread; strong durable-memory practice; disciplined option-preservation (held P1); layered independent verification that *did* catch the worst bug. **Gaps:** concurrent-agent coordination on shared main; runtime proof of Frontier dispatch; dropped DRY handoff; unverified nudge; un-built identity write-gate. **Posture:** Refine the coordination seam and close the runtime-proof gap; Preserve the features and memory practice; Defer the P1 merge.

---

## 4. Preserve / Refine / Redirect / Reset Test

| Area | Current quality | Recommended action | Rationale | Evidence |
|---|---|---|---|---|
| Core product concept (self-improving loop, maintainer/consumer split) | High | **Preserve** | Coherent, validated by a 4-persona panel + Fable plan-critic | Goal arc 6; design+plan docs in repo |
| User workflow (interactive + peer codex) | Mixed | **Refine** | Right model, missing isolation discipline | Collisions; late worktree fix |
| Architecture (overlay surviving updates) | Good | **Preserve** | Spike confirmed `pending/` stays unloaded (load-bearing) | Goal arc 6 |
| Data model (seed scaffolding, not store) | Good | **Refine** | Clean now; DRY rewire still open to remove duplication | `.rally` exclude `cc05724`; manifest exists, install_memory not rewired |
| Memory model | Good | **Preserve** | Durable patterns captured, one-offs excluded | 5 memory files |
| Agent orchestration | Fragile | **Refine** | Concurrency works but unsafe (storms, hallucination, unrequested mutation) | RCA #1, #2; hallucination note |
| UI/UX (nudge) | Unknown | **Defer** | Cannot recommend until verified on installed CC | Nudge unverified |
| Verification/evals | Mixed | **Refine** | Holistic review caught what per-task TDD missed — keep both, add a path/input-safety eval | RCA #3 |

---

## 5. Behavior and Workflow Discovery

### Behavior Inventory

| Observed behavior | Evidence | Pattern type | What it reveals | System implication | Learning object |
|---|---|---|---|---|---|
| User corrected "after posting to rally one must PULL" | Steering log; logged as 3-surface gap | Revealed preference + Failure escape | Coordination semantics (pull-only) not modeled by any surface | Make poll-after-post default behavior | Agent rule + Plugin behavior (`rally_poll_gate.py`, built `6631d42`) |
| "Polling should be default, not something I ask permission for" | Steering log | Explicit preference | Inappropriate ask-gate on a safe, reversible action | Remove permission prompt for safe polling | Agent rule (autonomy calibration) |
| "Recommendations need pros & cons" | Standing rule set after a one-sided rec | Explicit preference | Decision-support quality bar | Always pair recommendations with cons | Memory (cross-project) |
| "Structure not SQLite" — ship scaffolding, never private store | Packaging reframe | Explicit preference + Decision pattern | Privacy-by-construction in packaging | Strict-allowlist seed + PII deny-scan | Skill/Plugin (memory seed; `5055efe`) |
| "It's all local" — wants repeatable pin to dev repo | Steering log | Explicit preference | Runtime should track local dev source | Provide `buildloop-local` channel | Plugin behavior |
| Pushed to loosen retrospective taxonomies into seed scaffolds | Prompt feedback | Revealed preference | Prefers discovery-first over fixed buckets | Encode discovery-flexibility guardrail | Skill (this skill's guardrail) |
| Chose to HOLD P1 rather than merge into codex storm | Goal arc 6 | Decision pattern (option preservation) | Optionality > completion bias mid-build | Don't force-merge into unstable main | Agent rule + Approval gate |
| Repeated redirects toward end-to-end outcome + independent verification | Steering log | Workflow pattern | Verification must be independent + holistic | Layer holistic review atop per-task TDD | Eval + Agent rule |
| Subagent ran `claude plugin update` unrequested during read-only investigation | RCA #2 | Intervention/Failure pattern | Agents mutate state outside task scope | Constrain state-mutating commands to in-scope, approved | Agent rule + Approval gate |
| A guide subagent hallucinated "you tested it" | Gaps list | Failure pattern | Agents claim verification they didn't do | Require evidence token for verification claims | Eval (verification-claim grounding) |

### Workflow Pattern Clustering

| Workflow pattern | Trigger | Typical sequence | User/system behavior | Failure or success mode | Reusable? |
|---|---|---|---|---|---|
| Instrument-before-trust | "Is X actually happening?" | Suspect gap → add telemetry → discover peer already built equivalent | Convergent (codex `judgment_gate.py` = same finding) | Success | Yes |
| Concurrent peer commits to shared main | Two agents, one repo | Both stage/commit → collision → swept file / detached HEAD | Late worktree isolation | Failure → fixed | Yes (as anti-pattern + fix) |
| Brainstorm→spec→roadmap→TDD→multi-layer verify→HOLD | New feature | Panel + Fable critic + spike → subagent TDD → opus holistic review → hold | Caught path-traversal at holistic stage | Mixed (success + leak) | Yes |
| Privacy-by-construction packaging | Install too big / leaks store | Trace dirty-tree copy → allowlist + deny-scan + git-archive | Root-caused to `.gitignore`-ignoring copy | Success | Yes |
| Handoff to peer agent dropped silently | Delegate sub-task to codex via rally | Hand off → resolved/closed → never implemented | DRY rewire lost | Failure | Yes (as anti-pattern) |

---

## 6. Steering and Interaction Pattern Mining

| Steering moment | Trigger | User input | What it revealed | Specific/Reusable? | Predictable earlier? |
|---|---|---|---|---|---|
| Pull-vs-poll correction | Posted to rally, nothing pulled | "must PULL; polling should be default" | Coordination semantics + autonomy miscalibration | Reusable | Partial (rally docs state pull-only) |
| Pros & cons rule | One-sided recommendation | "recommendations need pros & cons" | Decision-support quality bar | Reusable | Yes (could be standing default) |
| Structure-not-SQLite | 1.4GB install / store shipped | "ship scaffolding, never the private store" | Privacy-by-construction | Reusable | Yes (gitignore-aware copy is a known pattern) |
| It's-all-local | Runtime not tracking dev repo | "repeatable command to pin local" | Local-source-of-truth dev preference | Mixed | Partial |
| Discovery-first prompt | Fixed taxonomies in retro prompt | "loosen into seed scaffolds + behavior layer + appropriate autonomy" | Anti-rigid-classification taste | Reusable | No (emergent taste) |
| Maintainer-mode model | Wants unrestricted personal loop | "identify myself as the build-loop person" | Identity-routed capability split | Reusable | No |
| Held-not-merged | codex storm on main | Chose HOLD | Optionality > completion | Reusable | Yes (storm was observable) |

**Clustered:**

| Steering cluster | Repeated evidence | Underlying preference/constraint | System implication | Capture target |
|---|---|---|---|---|
| Autonomy calibration | pull-default; unrequested `plugin update` was *wrong* autonomy | Safe+reversible → act; mutating+irreversible → gate | Build an autonomy classifier keyed on reversibility | Agent instruction + Approval gate |
| Decision-support quality | pros&cons; independent verification; end-to-end focus | User wants calibrated, evidenced recommendations | Default pros/cons + evidence tokens | Memory + Eval |
| Privacy & optionality | structure-not-SQLite; held P1 | Don't ship private data; don't lock in mid-build | Privacy-by-construction + hold-not-merge defaults | Skill + Agent rule |
| Anti-rigidity | loosen taxonomies | Discovery before classification | Seed-scaffold (not closed-bucket) design | Skill guardrail |

---

## 7. Recursive Learning Objects

| Learning object | Evidence | Type | Scope | Encoding target | Confidence | Store/apply? |
|---|---|---|---|---|---|---|
| LO1 — Concurrent agents on one repo MUST use worktree isolation from the start | Shared-tree collisions; late fix | Failure | Cross-project | Agent instruction + Plugin behavior | High | Yes |
| LO2 — Poll-after-post is default for pull-only coordination (no permission prompt) | Pull-vs-poll steering | Revealed | Project (rally) | Agent rule + Plugin (`rally_poll_gate.py`) | High | Yes |
| LO3 — Recommendations always include cons | Standing rule | Explicit | Cross-project | Memory | High | Yes |
| LO4 — Packaging ships scaffolding, never the private store (allowlist + PII deny-scan + git-archive) | Packaging RCA | Success | Cross-project | Skill + Plugin | High | Yes |
| LO5 — State-mutating commands require in-scope + approval; never run during read-only investigation | Unrequested `plugin update` | Failure/Hard gate | Cross-project | Agent rule + Approval gate | High | Yes |
| LO6 — Verification claims need an evidence token; hold a holistic review even after per-task TDD passes | Hallucinated "you tested it"; path-traversal escape | Failure | Cross-project | Eval + Agent rule | High | Yes |
| LO7 — Hold-not-merge into an unstable/active-storm main is a valid, preferred move mid-build | Held P1 | Decision/Success | Cross-project | Agent rule | Med-High | Yes |
| LO8 — Peer-agent handoffs must be tracked to completion, not just to resolved/closed | Dropped DRY rewire | Failure | Cross-project | Plugin (task ledger) + Agent rule | High | Yes |
| LO9 — Identity-routed autonomous writes to a core repo via user-writable JSON need an interactive approve gate | identity.json flag | Hard gate | Project | Approval gate | Med | Needs approval |
| LO10 — Instrument-before-trust: add telemetry before asserting a model/tier fired | "0 Fable dispatches" finding | Success | Cross-project | Agent rule + Eval | High | Yes |
| LO11 — Discovery-before-classification: seed scaffolds, not closed taxonomies | Prompt-loosening steering | Revealed | Cross-project (skill design) | Skill guardrail | High | Yes (already encoded) |
| LO12 — Autonomy calibrated on reversibility, not maximized | pull-default vs gate-merge | Revealed | Cross-project | Agent instruction | High | Yes |

---

## 8. Diagnostic RCA Module

### RCA: Shared-tree collisions between Claude and codex
- **Symptom:** blocked commits; a staged file swept into codex's commit; a detached HEAD left behind.
- **Expected:** each agent commits its own work to main without interference.
- **Actual:** two agents staging/committing concurrently on one working tree corrupted each other's index/HEAD.
- **Evidence:** Gaps list; main history `a1c8823` shows a dense codex rally/backlog commit storm; `.rally/worktrees/*` now present (isolation applied).
- **Creation path:** multi-agent setup adopted before isolation discipline.
- **Escape path:** survived planning (no isolation design) and operation (only surfaced as live collisions).
- **Root cause category:** agent/tool (coordination architecture).
- **Learning object:** LO1.
- **Encoding target:** Agent instruction (mandatory worktree per agent) + Plugin (auto-provision worktree on agent spawn).
- **Implication:** Refine — keep multi-agent, isolate lanes by default.
- **Residual risk:** Med — merges still converge on main; needs the merge gate (`rally_merge_gate.py`, built) plus discipline.
- **Confidence:** High.

### RCA: Install race uninstalled the plugin
- **Symptom:** installer copied live `.rally` SQLite WAL → ENOENT; plugin briefly uninstalled.
- **Expected:** install is atomic and never reads a live-mutating store.
- **Actual:** local-dir installer copied the dirty working tree (ignoring `.gitignore`), including a live WAL file; a subagent also ran `claude plugin update` unrequested.
- **Evidence:** Gaps list; fix via `sync_plugin_cache.py` (present) using `git archive HEAD`; `.rally` EXCLUDE_DIRS (`cc05724`).
- **Creation path:** convenience copy of working dir; second trigger = out-of-scope mutating command.
- **Escape path:** survived because installer was never tested against a live store; the mutating command had no scope gate.
- **Root cause category:** code (installer) + agent (unrequested mutation).
- **Learning object:** LO4 + LO5.
- **Encoding target:** Plugin (git-archive install, done) + Approval gate (state-mutating commands).
- **Implication:** Refine — installer now clean by construction.
- **Residual risk:** Low for installer; Med for unrequested-mutation class until a gate exists.
- **Confidence:** High.

### RCA: Path-traversal bug in extensions approve/route
- **Symptom:** unsanitized `name` allowed path traversal in approve/route.
- **Expected:** extension names sanitized before path construction.
- **Actual:** traversal possible; reached the holistic review stage unfixed.
- **Evidence:** commit `0b49792` "reject path-traversal in name (approve/route); guard block-scalar frontmatter" — confirmed in repo, touches `extensions_approve.py`, `_check.py`, `_paths.py`, `_route.py` + 3 test files.
- **Creation path:** per-task TDD scoped each task narrowly; input-safety was nobody's task.
- **Escape path:** survived per-task TDD (tests asserted feature behavior, not adversarial input) and direct verification (happy-path); caught only by final holistic opus review.
- **Root cause category:** eval (test scope) + code.
- **Learning object:** LO6.
- **Encoding target:** Eval (adversarial path/input-safety check on any name→path code) + Agent rule (holistic review mandatory before merge).
- **Implication:** Refine verification — keep TDD, add input-safety eval + always-on holistic stage.
- **Residual risk:** Low (fixed + tests added); class risk Med until input-safety eval is generalized.
- **Confidence:** High.

### RCA: Fable unreachable from the driving session
- **Symptom:** could not dispatch Fable inline; "0 Fable dispatches" despite config declaring fable on 11 verification agents.
- **Expected:** Frontier model fires at verification rungs.
- **Actual:** nested/peer-host mode can't reach in-host Fable; model enum limited to opus/sonnet/haiku; one verdict ran on Opus inline. Worked around via `claude --model fable -p` headless. (Precision, verified 2026-06-18: the `agents/advisor.md` file *does* exist with `model: fable` — but it is NOT dispatchable as a subagent_type from the driving session, i.e. file-existence ≠ inline reachability; and the `claude --model fable -p` headless path itself became "currently unavailable" on 2026-06-18.)
- **Evidence:** Goal arc 1; `judgment_gate.py` present (codex's convergent enforcement).
- **Creation path:** config declared the intent; runtime topology (host mode + enum) silently blocked it.
- **Escape path:** survived because config-correctness was assumed to imply runtime-correctness; no telemetry until this session added it.
- **Root cause category:** tool/external dependency (harness topology) + eval (no runtime proof).
- **Learning object:** LO10.
- **Encoding target:** Eval (ledger-backed "did Frontier actually fire at Review-G" check) + Agent rule (instrument-before-trust).
- **Implication:** Refine — the gate (`judgment_gate.py`) exists; close the runtime-proof loop.
- **Residual risk:** Med — workaround is manual; inline dispatch still blocked by harness enum.
- **Confidence:** Med-High (config verified; runtime firing still thinly evidenced — `TAG:INFERRED` that it now fires reliably).

### RCA: Dropped DRY rewire handoff
- **Symptom:** the install_memory-reads-structure-from-manifest rewire handed to codex was never implemented.
- **Expected:** delegated sub-task completes or returns a status.
- **Actual:** rally handoff shows resolved/closed; work not done. `install_memory.py` present and references manifest, but the DRY rewire is reported open.
- **Evidence:** Gaps list. Verified on disk 2026-06-18: `install_memory.py` still hardcodes `PROJECT_TOPIC_DIRS` (frozenset) and reads `manifest.generated` for structure 0 times — it uses the manifest only for *seed validation* (`_load_seed_manifest`). So the rewire (read the dir structure from `manifest.generated`) is genuinely OPEN — confirmed, not merely inferred.
- **Creation path:** rally handoff semantics conflate "target resolved/closed" with "work delivered."
- **Escape path:** no completion-tracking on peer handoffs; resolved≠done.
- **Root cause category:** tool (rally handoff semantics) + agent (no follow-through check).
- **Learning object:** LO8.
- **Encoding target:** Plugin (task ledger with explicit done-state + verification) + Agent rule (verify handoff outcomes).
- **Implication:** Refine coordination — distinguish resolved from delivered.
- **Residual risk:** Med — recurring class for any peer delegation.
- **Confidence:** Med-High.

---

## 9. Early Discovery and Preflight Improvements

| Missed early question / preflight | Later issue it would have prevented | Best answer source | Default? |
|---|---|---|---|
| "Are two agents committing to the same working tree?" | Shared-tree collisions | Repo/Heuristic (detect 2+ active agents) | Yes |
| "Does the installer read any live-mutating store?" | Install race / ENOENT | Repo (scan copy path vs `.gitignore`) | Yes |
| "Does any name/user-input flow into a filesystem path?" | Path-traversal bug | NavGator/Repo (dataflow trace) | Yes |
| "Can Frontier be dispatched from the current host mode?" | "0 Fable dispatches" gap | Heuristic/Tooling (host-mode + model-enum probe) | Yes |
| "Is poll-after-post automatic for this coordination tool?" | Pull-vs-poll miss | Repo (rally is pull-only) | Yes (infer, don't ask) |
| "Should recommendations include cons?" | One-sided rec | Memory (now stored) | No — infer from memory |
| "Is the SessionStart nudge visible on installed CC version?" | Unverified nudge | Tooling (probe installed CC) | Conditional (verify before claiming) |

**Grouped:**
- **Always ask:** none that aren't inferable — bias to inference here.
- **Ask only if memory missing:** pros&cons quality bar; local-channel preference.
- **Infer from repo/spec:** installer copy path; rally pull-only semantics; name→path flows.
- **Infer from prior behavior:** option-preservation (hold-not-merge); privacy-by-construction.
- **Detect through tooling:** concurrent-agent count; host-mode Frontier reachability; nudge visibility on installed CC.
- **Do not ask unless blocked:** publish version bump (gate, don't pre-ask).

---

## 10. Hard Gates and Pre-Capturable Inputs

| Hard gate | Why approval needed | Captureable in advance? | Recommended system behavior |
|---|---|---|---|
| npm / GitHub Packages publish | Irreversible public release | Partial (version policy yes; the publish itself no) | Gate at publish; require explicit version bump (user did 0.34.0) |
| Merge of P1 into main | Risk inheritance into active storm | No | Hold by default when main is unstable; surface conflict risk |
| identity.json autonomous writes to core repo | Privilege escalation via user-writable JSON | Partial (policy yes) | Interactive approve gate before any core-repo write |
| State-mutating CLI during investigation (`plugin update`) | Out-of-scope, unrequested mutation | Yes (scope policy) | Block mutating commands outside the active task scope |
| Taste/design decisions with no prior memory | Subjective, no durable prior | No | Ask once, then store as durable memory |

**Preflight Profile:**
- **Accounts/services:** npmjs, GitHub Packages (publish), GitHub (repo), rally substrate.
- **API keys needed:** npm publish token, GitHub Packages token (CI-held).
- **Permissions needed:** push to main; CI publish; local-bin install.
- **Deployment target:** npmjs + GitHub Packages via tag-triggered CI.
- **Repo access:** `~/dev/git-folder/build-loop` (local-source-of-truth per "it's all local").
- **Data/privacy constraints:** never ship `.rally`/`.build-loop`/private store; PII deny-scan on seed.
- **Allowed autonomous actions:** poll-after-post; instrumentation; clean git-archive installs; per-task TDD; worktree provisioning.
- **Actions requiring approval:** publish; P1 merge; identity.json/core-repo writes; any state-mutating command outside task scope.
- **Design/taste defaults:** recommendations include cons; discovery-before-classification; option-preservation mid-build.
- **Testing expectations:** per-task TDD **plus** a holistic review **plus** adversarial input-safety check before merge.
- **Release criteria:** clean install by construction; runtime telemetry confirms Frontier fired; no held adversarial findings.

---

## 11. Counterfactual Recursive Learning Simulation

| Phase | What happened | What should happen next time | Learning object | Encoding target | Human needed? |
|---|---|---|---|---|---|
| Intake | "Does Fable fire?" framed as config question | Frame as runtime-proof question from the start | LO10 | Agent rule | No |
| Spec clarification | Goal arc evolved organically | Capture maintainer/consumer split + gates up front | LO9 | Skill (intake) | No |
| Memory retrieval | Memories written during, not read first | Load pros&cons, privacy, autonomy defaults at start | LO3, LO4, LO12 | Memory | No |
| NavGator/repo review | Not evidenced | Trace name→path flows + installer copy path early | LO6 | Plugin (NavGator) | No |
| Architecture planning | Overlay designed + spike | Keep — add coordination-isolation as a first-class design input | LO1 | Agent instruction | No |
| Agent routing | Claude + codex on shared tree | Provision a worktree per agent before first commit | LO1 | Plugin | No |
| Implementation | Subagent TDD; one ran `plugin update` | Scope-gate mutating commands | LO5 | Approval gate | Gate only |
| Verification | TDD passed; path-traversal slipped to holistic | Add adversarial input-safety eval inside TDD loop | LO6 | Eval | No |
| UI/taste review | Nudge unverified | Probe installed CC before claiming visible | — | Eval | No |
| Permission handling | Publish gated; merge held | Keep gates; add identity-write gate | LO9 | Approval gate | Yes |
| Release readiness | 0.34.0 shipped clean | Add "Frontier-fired" + "no held adversarial finding" to criteria | LO6, LO10 | Eval | No |
| Memory update | 5 memories written | Keep; verify recurrence before storing one-offs | LO11 | Skill | No |

---

## 12. Learning-to-System Update Roadmap

Priority = (Frequency × Impact × Reusability × Confidence) / Difficulty.

| Rank | Learning object | Encoding target | Freq | Impact | Reuse | Diff | Conf | Priority | Rec |
|---|---|---|---|---|---|---|---|---|---|
| 1 | LO1 — worktree isolation per agent from start | Agent instruction + Plugin | 5 | 5 | 5 | 2 | 5 | 312.5 | P0 |
| 2 | LO5 — gate state-mutating commands out of scope | Agent rule + Approval gate | 4 | 5 | 5 | 2 | 5 | 250 | P0 |
| 3 | LO6 — adversarial input-safety eval + always-on holistic review | Eval + Agent rule | 4 | 5 | 5 | 2 | 4 | 200 | P0 |
| 4 | LO8 — track handoffs to delivered, not resolved | Plugin (task ledger) + Agent rule | 4 | 4 | 5 | 2 | 4 | 160 | P0 |
| 5 | LO10 — instrument-before-trust + runtime-fired eval | Agent rule + Eval | 4 | 4 | 5 | 2 | 4 | 160 | P0 |
| 6 | LO12 — autonomy calibrated on reversibility | Agent instruction | 5 | 4 | 5 | 3 | 4 | 133 | P1 |
| 7 | LO4 — privacy-by-construction packaging | Skill + Plugin | 3 | 4 | 5 | 2 | 5 | 150 | P1 |
| 8 | LO3 — recommendations include cons | Memory | 5 | 3 | 5 | 1 | 5 | 375 | P0 |
| 9 | LO7 — hold-not-merge into unstable main | Agent rule | 3 | 4 | 4 | 2 | 4 | 96 | P1 |
| 10 | LO2 — poll-after-post default | Agent rule + Plugin | 3 | 3 | 3 | 2 | 5 | 67.5 | P1 |
| 11 | LO11 — discovery-before-classification (already encoded) | Skill guardrail | 3 | 3 | 4 | 2 | 5 | 90 | P1 |
| 12 | LO9 — identity-write approve gate | Approval gate | 2 | 5 | 3 | 3 | 3 | 30 | P2 |

**P0 (encode immediately):** LO3 (trivial, highest score), LO1, LO5, LO6, LO8, LO10.
**P1 (next):** LO4, LO12, LO7, LO2, LO11.
**P2 (monitor/defer):** LO9 (project-specific, needs human approval design).

---

## 13. Recommendations by System Layer

**A. App-level:** Add a **concurrent-agent detector** to intake (warn + auto-provision worktrees when ≥2 agents target one repo). Add a **task ledger** with an explicit `delivered` state separate from `resolved`. Add a **release-readiness dashboard** asserting clean-install-by-construction + Frontier-fired + zero held adversarial findings. Add an **autonomy classifier** view (reversible→auto, irreversible/mutating→gate).

**B. Agent-level:** Orchestrator: provision per-agent worktrees before first commit; never run state-mutating CLI outside the active task scope; verify peer handoffs to delivered. Add a **holistic reviewer agent** that always runs before merge (it caught the path-traversal). Routing: instrument-before-trust — add telemetry before asserting a tier/model fired. Memory/context agent: load pros&cons + privacy + autonomy defaults at session start.

**C. Plugin/tool:** NavGator integration to trace name→path dataflows and installer copy paths pre-build. Repo map to detect dirty-tree-vs-`.gitignore` copy risk. Permission/key discovery for publish tokens. A host-mode probe that reports whether Frontier is dispatchable from the current topology.

**D. Memory**

| Memory | Scope | Evidence | Update trigger | Approval needed? |
|---|---|---|---|---|
| Recommendations include cons | Cross-project | Standing rule | On any recommendation | No |
| Privacy-by-construction packaging | Cross-project | structure-not-SQLite | On any package/ship step | No |
| Autonomy calibrated on reversibility | Cross-project | pull-default vs gate-merge | On any autonomous action decision | No |
| Hold-not-merge into unstable main | Cross-project | Held P1 | On merge decisions during peer storms | No |
| rally pull-only / poll-after-post | Project | Pull-vs-poll | On rally post | No |
| Maintainer/consumer split + identity routing | Project | Maintainer-mode model | On extension promotion | Yes (identity gate) |

**E. Skill**

| Skill | Purpose | Trigger | Inputs | Outputs | Success criteria |
|---|---|---|---|---|---|
| Concurrent-agent preflight | Detect + isolate multi-agent on one repo | Session start with ≥2 agents | Repo state, agent roster | Worktree plan | No shared-tree collisions |
| Privacy-by-construction packaging | Ship scaffolding not store | Any package/install build | Repo, `.gitignore`, manifest | git-archive seed + deny-scan report | Clean install, no PII |
| Runtime-fired verifier | Prove a tier/model actually fired | Verification rung reached | agent-ledger.jsonl | Fired/not-fired verdict | Frontier confirmed at Review-G |
| Handoff completion tracker | Resolve≠delivered | Peer delegation | Rally handoff + repo diff | Delivered/not verdict | No silently dropped handoffs |

**F. Eval**

| Eval | Catches | Runs when | Pass criteria | Failure action |
|---|---|---|---|---|
| Adversarial input-safety | Path-traversal / name→path injection | Any code mapping input to a path | All adversarial inputs rejected | Block merge |
| Holistic pre-merge review | Cross-cutting bugs per-task TDD misses | Before any merge | Reviewer finds no blocking issue | Hold merge |
| Frontier-fired check | "0 Fable dispatches" class | At verification rungs | Ledger shows Frontier fired | Flag + headless fallback |
| Handoff-delivered check | Dropped delegations | After peer handoff | Repo diff confirms work landed | Reopen handoff |
| Verification-claim grounding | Hallucinated "you tested it" | On any verification claim | Claim carries an evidence token | Reject claim |

---

## 14. From-Scratch Recursive Learning Architecture

**Option not anchored on the current app — "Lanes-first coordination plane":**
- **Product concept:** a build-loop where *agent isolation and handoff integrity* are the substrate, and features are tenants on top — inverting today's feature-first/coordination-bolted-on order.
- **Control plane:** a coordinator that provisions a git worktree per agent at spawn, owns the merge queue, and treats main as append-only-via-queue (no direct concurrent commits).
- **Agent architecture:** orchestrator + bounded executors + an always-on holistic reviewer + a memory/context loader; codex is a first-class lane, not an uncoordinated peer.
- **Plugin/tool architecture:** NavGator-backed dataflow tracing as a pre-build gate; host-mode probe for model reachability; packaging via git-archive only.
- **Memory architecture:** typed memory objects (preference / decision / hard-gate / failure / success) with scope + recurrence count; one-offs excluded until count ≥ 2.
- **Learning-object schema:** `{id, evidence_ref, type, scope, encoding_target, confidence, store_decision, recurrence}`.
- **Permission model:** reversibility-keyed — reversible auto, irreversible/mutating/identity gated.
- **Eval model:** layered — per-task TDD → adversarial input-safety → holistic review → runtime-fired proof, all gating merge.
- **User review model:** approval dashboard surfacing only the reversibility-gated set.
- **Feedback loop:** every gate failure emits a learning-object candidate into the memory-review queue.
- **Tradeoffs vs evolving current:** higher upfront coordination cost and merge latency; in exchange, the three worst frictions (collisions, dropped handoff, unsafe mutation) become structurally impossible rather than caught late.

| Dimension | Current-system evolution | From-scratch system | Tradeoff |
|---|---|---|---|
| Product model | Features + bolted-on coordination | Coordination plane, features as tenants | Bigger rebuild vs structural safety |
| Agent orchestration | Claude + peer codex, late isolation | Worktree-per-agent at spawn + merge queue | Latency vs no collisions |
| Plugin/tool layer | Gates built reactively | Dataflow + host-mode probes as preflight | Slower start vs fewer escapes |
| Memory | Free-text files | Typed objects w/ recurrence | Schema overhead vs cleaner recall |
| Learning-object schema | Ad hoc | Formal schema | Rigidity vs queryability |
| Permissions | Per-incident gates | Reversibility-keyed model | Upfront design vs consistent gating |
| Verification | TDD + holistic (added late) | 4-layer always-on | Cost vs coverage |
| UX | CLI + nudges | Approval dashboard | Build cost vs visibility |

---

## 15. Emergent Patterns

| Pattern | Evidence | Why it matters | Recommended system response |
|---|---|---|---|
| Convergent independent discovery | Claude's ledger finding ≡ codex's `judgment_gate.py` ("16 commits 0 Fable dispatches") | Two agents independently surfacing the same gap is strong signal the gap is real and worth a durable gate | Treat convergent peer findings as high-confidence; auto-promote to eval |
| Config-correct ≠ runtime-correct | Fable declared on 11 agents, never fired | The most dangerous gaps hide behind correct-looking config | Make runtime proof a release gate, not config inspection |
| Verification depth vs breadth tradeoff | Per-task TDD (deep, narrow) missed path-traversal; holistic (broad) caught it | Narrow tests give false confidence on cross-cutting risks | Mandate both layers; never ship on narrow-only |
| Resolved ≠ delivered in coordination tools | Dropped DRY rewire | Tool semantics silently lose work | Add delivered-state tracking distinct from resolved |
| Optionality as a deliberate move | Held P1 over merge | Mid-build, not-merging can be the correct, evidenced decision | Encode hold-not-merge as a legitimate orchestrator option |
| Wrong-autonomy vs missing-autonomy on the same axis | `plugin update` over-reach AND pull-permission under-reach | Autonomy isn't a single dial — it's reversibility-keyed | Build a reversibility-based autonomy classifier |

---

# Executive Summary

## Bottom line
This was a coherent, directionally-right mid-build session that shipped a clean release (0.34.0/0.35.0) and instrumentation while correctly holding a riskier feature — but every major friction traced to one root: concurrent agents sharing a working tree without isolation, handoff integrity, or reversibility-keyed autonomy.

## Project maturity and posture
- **Maturity state:** Directionally-right / mid-build (one increment shipped, one held).
- **Recommended posture:** Refine the coordination substrate; Preserve the features, memory practice, and option-preservation discipline; Defer the P1 merge until codex's storm settles.
- **Reason:** the features and verification intent are sound and validated; the failures cluster in the multi-agent coordination seam, not the product.

## Most important recursive learning findings
1. Coordination — not features — is the real system: collisions, the install race, and the dropped DRY handoff all stem from shared-tree concurrency and resolved≠delivered semantics.
2. Config-correct ≠ runtime-correct: Fable was declared everywhere and fired nowhere; only telemetry exposed it.
3. Narrow verification gives false confidence: per-task TDD plus direct verification missed a path-traversal that only a holistic review caught.

## Highest-value learning objects to encode
1. LO1 — worktree isolation per agent from session start (P0).
2. LO6 — adversarial input-safety eval + an always-on holistic pre-merge review (P0).
3. LO5/LO12 — reversibility-keyed autonomy: gate state-mutating/irreversible actions, auto-run safe reversible ones (P0).

## Recommended system updates
1. Orchestrator provisions a worktree per agent and owns a merge queue; never runs out-of-scope mutating commands.
2. Add a layered eval gate: TDD → input-safety → holistic review → runtime-fired proof, all gating merge.
3. Add a task ledger distinguishing `delivered` from `resolved` to stop silent handoff drops.

## Hard gates that remain human-controlled
1. npm / GitHub Packages publish (irreversible) — explicit version bump required.
2. Merge of extensions P1 into main (held by user).
3. identity.json autonomous writes to a core repo — interactive approve gate (flagged, not yet built).

## What to do next
1. Encode the P0 learning objects (worktree isolation, input-safety + holistic review, reversibility-keyed autonomy, pros&cons memory).
2. Close the runtime-proof loop on Frontier dispatch (ledger-backed "fired at Review-G" eval) and verify the SessionStart nudge on the installed CC version.
3. Resolve the dropped DRY rewire and the identity-write gate before merging P1.

---

*Marked unknowns: NavGator analysis and raw agent logs were not in evidence (`UNKNOWN — evidence not available`). `TAG:INFERRED`: that Frontier now fires reliably at runtime; that `install_memory.py` is still duplicative (manifest + script both present, rewire reported open but not disproven on disk); that the SessionStart nudge renders on the installed CC version.*

---

## Controller reconciliation (2026-06-18, multi-layer verification trail)

This retrospective was produced by an independent Opus agent (Fable was unavailable on 06-18 — the org's escalation fallback, honestly labeled), then scored by a separate independent judge (`judge-evaluation.md`: 4.5/5, Accept-with-revisions). The judge flagged two "factual" gaps; the controller (full session context) verified both **on disk** and found the judge **over-corrected** — the retrospective's claims hold:

1. **DRY rewire:** judge claimed `install_memory.py` "already reads structure from manifest." Disk check: it still hardcodes `PROJECT_TOPIC_DIRS` and reads `manifest.generated` 0× for structure (manifest used only for seed validation). The rewire is genuinely **open**. The §3/§4/RCA entries are now sharpened to say so explicitly.
2. **Fable reachability:** judge claimed `agents/advisor.md` (model: fable) existing means Fable was reachable. The agent *file* exists, but it isn't dispatchable as a subagent_type from the driving session, and the headless path went unavailable 06-18. The Fable RCA is sharpened to distinguish file-existence from inline reachability.

**Meta-finding:** this dogfood reproduced the session's own core lesson — *multi-layer verification, don't trust a single verdict*. The judge caught the retro needing precision; the controller caught the judge over-correcting; the truth sat between. Both gaps were **precision/nuance, not substance** — no completed work to reopen.
