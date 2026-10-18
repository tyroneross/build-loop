# Engineering effective agent hooks

Use hooks for small, repeatable checks at known execution boundaries. Keep reasoning in the agent workflow, put consequential authorization at the executing boundary, and verify the outcome separately. A hook earns its place when it prevents a demonstrated failure or removes repeated work with acceptable cost.

Checked October 18, 2026. This guide combines the supplied governance image, the [earlier assessment](hook-evidence-assessment.md), current vendor documentation, historical local research and Build Loop source. It is guidance, not a report of installed enforcement. **The exact Oct. 13 webinar's public publisher captions have now been captured by script.** Its JSON and VTT captions agree after alphanumeric normalization; the audio has not been independently checked. Source details and contradictions appear below.

## What the Oct. 13 webinar adds

The [requested session](https://anthropic.ondemand.goldcast.io/on-demand/26b3d349-9fd9-4fa3-b122-f5b2f07c17a5), presented by Juan Tomas Gomez, supplies these timestamped teaching points. Captions preserve the presenter's explanations and demo narration; they do not reproduce the demonstrated effects locally.

| Caption range | Teaching point | Application in this guide |
|---|---|---|
| 47:45–48:54 | Favor consequential, binary checks that must hold during unattended work | Name the failure and choose a verifiable execution boundary |
| 49:39–52:18 | Choose the event, check mechanism and configuration scope | Keep these three decisions explicit in the hook contract |
| 53:26–54:46 | Demonstrates privacy and source/dashboard consistency checks; leaves broad tone judgment in the skill | Separate deterministic invariants from editorial judgment |
| 59:21–59:59 | Prefer granular hooks suited to a particular workflow | Narrow matchers and reuse existing owners |
| 1:15:03–1:15:49 | Define a trigger, task, success condition and bounded way out | Give unattended work explicit retry and stop conditions |
| 1:22:23–1:23:02 | Assess cost per achieved task, including token volume and turns | Measure the complete workload rather than unit token price alone |

Two safety claims need narrower interpretation. Around 50:21–50:39, the privacy example describes blocking data before it reaches the model. Check the actual data direction: a pre-tool hook sees a pending call, so it cannot inspect a result that has not arrived. The [current Claude reference](https://code.claude.com/docs/en/hooks#posttooluse-decision-control) distinguishes post-tool feedback from output replacement; replacement does not undo earlier tool effects or exposures. Test the required boundary before claiming prevention.

Around 1:04:20–1:04:32, the presenter reports that the demo agent could not disable its hooks. Treat that as the demo's reported outcome. Protection against configuration changes or alternate writers depends on the actual permissions and coverage; this transcript is not a tamper-proofing test.

## Start with the required outcome

Name the concrete failure before choosing an event: an unauthorized write, stale test receipt, missed formatting step, repeated password dialog or slow startup. State the trigger, expected behavior, observable failure and existing owner. Prefer extending an existing validator or removing redundant work before adding another hook.

| Need | Suitable mechanism | What establishes success |
|---|---|---|
| Explain project conventions or make a judgment | Project instructions or a skill | The agent applies the relevant guidance to a reviewed result |
| Run a bounded reaction to a host event | A command hook | The actual host invokes the intended handler and the expected effect occurs |
| Evaluate an ambiguous condition | An explicit review step; a model hook if its cost and uncertainty are justified | Evidence and a review outcome, including disagreement or uncertainty |
| Read or change an external system | Existing CLI/API or connector, with its normal permissions | Authoritative readback from the target system |
| Enforce a rule across all writers | Validator/authorization at the receiving service, repository boundary or execution broker | Alternate writers cannot bypass the required control |
| Run costly integration checks | A targeted task, commit/CI lane or explicit acceptance stage | Results for the intended revision and workload |

The official [hook guide](https://code.claude.com/docs/en/hooks-guide) distinguishes command hooks from model-based checks. Hook invocation can be predictable while an LLM judgment remains uncertain. Calling a model from a hook does not make its verdict deterministic.

## Bind readiness, actions and completion to the same work

The supplied image contributes a useful sequence: define readiness, grant bounded work, collect evidence, verify it, then apply any required acceptance decision. Reuse the existing plan and receipt formats for these facts.

| Boundary | Minimum question | Evidence to check |
|---|---|---|
| Ready to start | Are the goal, criteria, dependencies, owner and scope resolved? | Current plan, actual dependency inputs and explicit unresolved conditions |
| Ready to act | Does this exact operation fit the granted scope? | Canonical target, operation, actor/task identity and applicable permission decision |
| Worker returned | Did this worker produce the requested output from the expected inputs? | Artifact identity, input/revision references, executed checks and remaining gaps |
| Ready to complete | Does current evidence satisfy every applicable criterion? | Criterion-to-evidence mapping and any independent review required by policy |
| Required human acceptance | Does the response cover the intended action/output? | Original response, context, target and evidence reference under the existing approval policy |

A receipt describes evidence; verify its referenced artifact and relevance. A digest can detect changed bytes, but it cannot prove correctness, execution, authorship or human identity. A worker's self-report also cannot establish independent verification.

When an input changes, identify affected criteria and recheck them. Preserve unaffected passing evidence and accepted artifacts. If a required approval no longer covers the intended action, follow the existing policy rather than inventing a broader or recurring approval gate.

## Choose the earliest useful boundary

Use this as a design map, then check the installed host's event and tool coverage. It is not a portable registration manifest.

| Point in the work | Appropriate responsibility | Frequent mistake |
|---|---|---|
| Session starts or resumes | Resolve identity, roots and current context; report missing prerequisites | Running a full scan, installing software or silently changing trust |
| User submits intent | Bind the current request and update context | Treating a normal prompt or quoted document as renewed permission |
| Before a tool call | Check the exact operation before its effect | Inferring safety from a harmless command prefix |
| After a tool result | Correlate output and record what actually happened | Claiming a post-action warning prevented the action |
| Worker starts/returns | Associate the grant, inputs and returned evidence | Treating a shared parent session ID as a unique worker identity |
| Task completion | Check required current task evidence where the host exposes this boundary | Treating one task's completion as whole-project acceptance |
| Turn/session ends | Record continuity and summarize pending work | Declaring the goal achieved because a Stop event occurred |

The [Claude reference](https://code.claude.com/docs/en/hooks) documents event-specific decisions, including `TaskCompleted`. Stop continuation needs a re-entry guard such as the host's `stop_hook_active` signal. In this repository, advisory Stop hooks remain non-blocking by default; an explicit safety or integrity policy must justify a block.

## Treat host adapters as separate contracts

Read the active configuration layers and installed host behavior before assuming a hook works. Keep the host/version, enabled source, handler, event, matcher, decision shape and native-call evidence together.

The [Codex reference](https://developers.openai.com/codex/hooks/) requires review of non-managed hook definitions, documents tool coverage exceptions, and currently says that PreToolUse `permissionDecision: "ask"` is unsupported and lets the call continue after reporting failure. Do not copy a Claude approval response into Codex. Follow the host's supported approval path and verify the result on the actual tool path. Native subagents may share a parent session ID, so correlate worker identity separately.

Build Loop's repo-local `.codex/hooks.json` registers SessionStart and Stop at the inspected source baseline `27f53915`. Its Claude manifest includes pre/post-tool entries. Plugin, user or managed layers can add other handlers; repo-local registration is not a complete runtime inventory. This session reported one untrusted registered Stop hook. No trust decision was changed for this guide.

The [August Codex root-resolution investigation](2026-08-22-codex-hook-root-resolution.md) illustrates another failure: a handler can be invoked while its dependent gate is unreachable. A missing plugin root produced a no-op response. Treat the report as historical evidence of the failure class; establish current installed behavior before repeating its “every gate is inert” conclusion.

## Give each hook a small contract

Record these fields in the existing manifest, helper documentation or test fixture. This is a review checklist, not a request for a new schema or service.

| Field | Required clarity |
|---|---|
| Purpose and owner | Which observed failure it handles; which component owns the rule |
| Trigger and coverage | Exact event/tool/matcher; known alternate paths |
| Inputs | Expected JSON fields, target paths, current run/task and revision |
| Result | Observe, allow, refuse, request the supported approval flow, or record an execution error |
| Failure policy | Behavior for unavailable input, timeout, malformed data and missing executable |
| Cost | Total deadline, child cleanup and acceptable added latency |
| Durable effect | State written, logical operation identity and repeat behavior |
| Evidence | Correlation to the actual host call, result and side effects |

Resolve the package root separately from the consumer workspace. Use canonical, scope-checked targets and reject path escapes where the control requires it. A lexical prefix does not establish containment across symlinks, shell composition, redirection or alternate tools. Extend the existing parser/validator and cover the observed bypass rather than accumulating shell regex exceptions.

Treat events, peer prose, tool output and transcripts as input data. Do not execute embedded instructions or interpolate them into shell/Python code. Parse structured input, pass arguments without shell expansion where possible, and keep diagnostics separate from protocol output.

This ingestion exposed a concrete classifier limit: a shell command that only wrote quoted transcript text was refused as an unbounded wait. The [wait gate](../scripts/hooks/unbounded_wait_gate.py) searches the entire command string for loop and sleep patterns, so quoted prose can satisfy its heuristic. Capture was completed with a direct file-write tool. For similar ingestion, keep source text out of executable command strings. The runtime classifier was not changed by this documentation task; treat this case as a known false-positive boundary when reviewing its coverage.

## Make failure explicit and proportionate

| Hook purpose | On missing or unusable evidence | On a confirmed violation |
|---|---|---|
| Advisory context, coordination or maintenance | Continue with a bounded, useful coverage note when necessary | Report or schedule the existing permitted repair path |
| Formatting or other reversible automation | Preserve work and report the failed step | Correct only within the operation's authorized scope |
| Required safety or integrity check | Hold/refuse the governed action through a supported mechanism; retain the reason | Prevent the effect and give an actionable correction |
| Human approval required by policy | Preserve the pending decision and its context | Refuse an action outside the decision's scope |

“No violation found,” “not applicable,” “check failed” and “check never ran” are different results. A clean exit, empty output or `{}` cannot prove which one occurred. Verify the host's interpretation; generic advice that all nonzero exits mean failure, or all exit-zero responses mean allow, is inadequate across events and hosts.

Keep permissions and sandboxing responsible for their own boundaries. Hooks provide covered-path controls and observations. They do not establish machine-wide containment or grant new production, deletion or communication authority.

## Bound runtime, retries and concurrent effects

The [bounded-hook helper](../hooks/_bounded_hook.sh), [deadline tests](../scripts/test_bounded_hook.py), [timeout lint](../scripts/hook_budget_lint.py) and [hygiene lint](../scripts/hook_hygiene_lint.py) address reliability with existing mechanisms.

Start the deadline before interpreter imports and child startup. Leave margin between the helper's deadline and the host timeout. Bound subprocesses and clean up owned children; returning quickly while an uncontrolled process survives moves the problem elsewhere. Keep expensive analysis outside per-call paths, and never detach a mandatory pre-action decision.

Do not depend on independent sibling handlers running sequentially. If one step requires another's output, put that ordering inside the existing dispatcher or an explicit dependency. Duplicate delivery and restart should reuse the logical operation identity, avoid duplicate durable effects and reconcile uncertain outcomes.

For shared state, choose the narrowest existing lock/transaction mechanism that protects the actual invariant. Write complete records atomically where needed. Release only the current session's owned resources; another session's presence or claim is not yours to clear.

For commands that can prompt for administrator or privacy access, use the existing [privileged broker](../skills/build-loop/references/privileged-request-broker.md) and [system-access-once protocol](../skills/build-loop/references/system-access-once.md). A denied read must remain distinguishable from an empty result. Preserve the pending/denied request and do not change purpose, run ID or executable to evade its hold. These wrappers cover routed operations; they do not intercept every OS dialog.

## Verify the control at progressively stronger levels

Use the cheapest checks that cover the change, but do not substitute an earlier level for a later claim.

| Level | Check | What it establishes |
|---|---|---|
| Configuration | Parse manifests; inspect effective layers, trust and matcher | The intended registration is present and eligible |
| Handler | Inject valid, invalid, missing and malformed fixture inputs | Helper behavior for those inputs |
| Real host | Trigger harmless permitted/prohibited actions in an isolated fixture | Host invocation and decision handling on that exact path |
| Effects | Inspect files or authoritative target state after the attempt | Whether the intended effect occurred or was prevented |
| Integrated outcome | Verify the final revision against the user's criteria | Current output correctness within tested scope |
| Human acceptance, when required | Check the response against its intended target and context | Satisfaction of that policy's human decision requirement |

Cover the relevant adversarial cases: trusted versus skipped handler, unsupported response, missing root/tool, minimal PATH, paths with spaces, compound commands, redirected writes, stale receipt, mismatched worker/input, timeout, cancellation, retry and restart. Add alternate tool/API paths when they can reach the same governed effect. Keep tests tied to an actual contract and failure; do not create a universal test matrix for every notification hook.

Use independent review when the governing workflow requires it. The reviewer should inspect cited artifacts and challenge the failure mechanism and test oracle. Agreement after seeing the implementer's conclusion is weak evidence; a fabricated worker or test receipt remains fabricated even if it hashes correctly.

## Measure the cost and simplify the path

Choose a representative workload and a baseline with the same host, handler set, repository state and inputs. Measure total added latency, process launches, errors/timeouts, output/context volume and any duplicate effects relevant to the change. Distinguish cold startup from repeated calls. Retain the workload, sample count and variability; do not copy old timing estimates into a current guarantee.

Build Loop's [Bash dispatcher](../scripts/hooks/pre_bash_dispatch.sh) demonstrates the useful shape: parse shared input once and invoke specialized checks only for relevant operations. Check the implementation and measured behavior before assuming that an additional wrapper improves efficiency.

For new advisory checks, observe false positives and missed cases before considering a justified block. Existing required safety checks retain their required behavior. Useful rollout options are narrow matchers, fixture validation, explicit opt-in where policy requires it, and reversible configuration with retained provenance. Remove obsolete handlers and duplicate context when the required outcome survives their removal.

## Troubleshoot from the effect backward

| Symptom | First discriminating check | Likely class to investigate |
|---|---|---|
| Hook appears configured but nothing happens | Check effective source, trust and a correlated native call | Disabled/untrusted handler, wrong matcher or uncovered path |
| Handler ran but gate did nothing | Resolve the helper root and inspect output/error separately | Wrong package root, missing executable, scope guard or parse failure |
| Refusal text appears but the action succeeds | Inspect returned shape and resulting side effect | Unsupported decision or wrong lifecycle boundary |
| Session stalls | Compare total startup/child lifetime with the host deadline | Unbounded work, lock contention, detached child or repeated Stop feedback |
| Duplicate notifications or state writes | Trace event and logical operation identities | Multiple registrations, repeat delivery or missing idempotency |
| Password prompts repeat | Inspect the existing request ledger and owned requester | Failure confused with an empty result; retry escaped the hold |
| Source tests pass but installed behavior fails | Pin source, installed package, trust and native tool path | Delivery/configuration/version gap |
| Completion claims exceed evidence | Follow each criterion to its actual current artifact/check | Stale, mismatched, absent or self-attested receipt |

## Apply this in Build Loop and Code Audit

**Build Loop:** reuse [plan verification](../scripts/plan_verify.py), [action classification](../scripts/classify_action.py), [acceptance selection/results](../scripts/acceptance_selector.py), [owed verification](../scripts/owed_verification.py) and [run-close lint](../scripts/run_close_lint.py). Verify a host adapter before crediting it with enforcement. Keep maintenance advisory. This guide adds no runtime gate.

**Code Audit:** use its shared `hook-boundaries.md` reference to review invocation, effect prevention, evidence binding, overhead and alternate paths. Treat a scanner hit or a recorded refusal as a lead until its contract and consequence are established. Preserve candidate/confirmed, source/runtime and recommendation/verified-repair distinctions. This guide does not authorize global hooks or automatic cross-project mining.

**Clean-sheet choice:** put the consequential rule at the narrow authorized execution boundary, and use hooks as adapters. **Current-constraints choice:** repair or extend the existing owner and prove the exact path before adding infrastructure. Runtime implementation remains separate from this documentation task.

## Sources, intake and resolved contradictions

| Source | Contribution | Boundary/status |
|---|---|---|
| User image, **Why hooks govern agent work**, labelled Oct. 18 | Readiness, bounded work, evidence, decisions and continuity | Visual source ingested; underlying reported run not independently reproduced |
| [Requested webinar: (Oct. 13th) Claude Code: Advanced](https://anthropic.ondemand.goldcast.io/on-demand/26b3d349-9fd9-4fa3-b122-f5b2f07c17a5) | Timestamped hook selection, design dimensions, demo, scope and bounded-loop guidance above | Script captured publisher VTT and JSON via public links embedded in page HTML; 2,017 cues, from 00:00:30.990 to 01:30:34.490; alphanumeric token streams agree; audio/demo not independently replayed |
| [Supplement: Claude Code Advanced Patterns](https://anthropic.ondemand.goldcast.io/on-demand/f52c4cc3-7b23-4ab8-93f3-d202706dd62b?t=5.28) | Indexed transcript discusses automatic formatting, tool-call logging, notifications and choosing hooks versus instructions/MCP | Separate webinar, not the requested recording; instructional examples, not a local benchmark |
| [Supplement: What We Shipped](https://anthropic.ondemand.goldcast.io/on-demand/9e373c2d-26b9-4a37-917d-c355df38c6a1) | Indexed discussion cautions about misconfigured or obsolete hooks and excessive injected context | Qualitative counter-evidence; no transferable numerical effect claimed |
| [Claude hook guide](https://code.claude.com/docs/en/hooks-guide), [reference](https://code.claude.com/docs/en/hooks) | Current lifecycle and protocol lookup | Publisher documentation; recheck installed version and native path |
| [Codex hook reference](https://developers.openai.com/codex/hooks/) | Trust, tool coverage and decision differences | Publisher documentation; no implication of host parity |
| [Claude best practices](https://code.claude.com/docs/en/best-practices) | Focused context and verification against concrete outcomes | Publisher advice, not independent performance evidence |
| May 9 research, `topics/build-loop-hooks-and-skills/01-hooks-integration.md` and `04-warn-state.md` in the local research library | Earlier integration proposals and advisory rollout reasoning | Historical proposals; timings and implementation estimates not current measurements |
| [August root investigation](2026-08-22-codex-hook-root-resolution.md), current helper/source links above | Real local failure classes and existing implementation owners | Historical report versus currently inspected source kept separate; installed acceptance untested |

**Resolved contradiction:** the May note says `TaskCompleted` does not exist. The current Claude reference documents it. Preserve the old note as a dated observation/proposal; use the current documented contract and installed-host verification for new work. Do not mechanically retarget every completion check to Stop.

**Resolved scope distinction:** a supplemental webinar's automatic formatter example describes a useful covered event. It does not establish interception of every writer, permission to format unrelated files or equivalence between hosts.

**Resolved intake gap:** the initial browser/text fetch exposed metadata only. A bounded IPv4 HTML fetch recovered embedded public caption links, and a script preserved the page, original VTT/JSON, hashes, validation and a timestamped reading copy in research run `agent-hooks-guide-20261018`. The two caption formats are the same publisher source, not independent corroboration. The full video was not downloaded or watched.

**Evidence limit:** this synthesis has source and documentation checks, not a matched runtime experiment. It claims no measured speedup, failure-rate reduction or improvement in audit quality. Sources from the same publisher are not independent corroboration; local recommendations are engineering synthesis informed by the cited failures.
