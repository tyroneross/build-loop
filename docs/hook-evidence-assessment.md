# Use hooks to check work at execution boundaries

For the consolidated operating guide, including existing research, host differences, reliability and verification, read [Engineering effective agent hooks](hooks-guide.md). This earlier image assessment remains its source-specific intake record.

Assessment: October 18, 2026. The user supplied the image **Why hooks govern agent work**, labelled “Evidence 18 Oct 2026.” Its original run records were not supplied. Treat its reported successful worker, refusals and approvals as source claims; this assessment does not independently verify that run.

## What helps

The image supplies a useful design rule: define checkable readiness and completion conditions, check actions against the current scope, and retain evidence across handoffs. Hooks can enforce a boundary only when the host calls them and honors their decision. A durable record helps resume work, but its contents still need verification.

| Boundary | Useful check | Build Loop mechanism to reuse |
|---|---|---|
| Before work | Goal, criteria, inputs, dependencies, owner and scope resolve | [Plan verification](../scripts/plan_verify.py) |
| Before an action | The exact requested operation fits its authority and scope | [Bash dispatcher](../scripts/hooks/pre_bash_dispatch.sh), [action classifier](../scripts/classify_action.py) and host permission controls |
| After work | Returned evidence matches the run, inputs, revision and required checks | [Acceptance selection and results](../scripts/acceptance_selector.py), [owed verification](../scripts/owed_verification.py) |
| At completion | Required evidence is present and current; remaining gaps are explicit | [Run-close lint](../scripts/run_close_lint.py), [Stop finalizer](../scripts/hooks/stop_finalize.sh) |
| At a required human decision | Approval refers to the intended output and current evidence | Existing project approval policy; preserve the original response, target and evidence reference |

These are source mappings, not proof that every mechanism ran in this session or implements the image's full contract.

## Where coverage differs

At source revision `21b77e8d3f8c8c29430635120c2cc62ed39c861a`, the repository's [Claude hook manifest](../hooks/hooks.json) registers pre-tool and post-tool hooks. Its [Codex manifest](../.codex/hooks.json) registers SessionStart and Stop only. Other active configuration layers may add hooks; these files alone cannot establish total runtime coverage or host parity.

This session also reported an untrusted registered Codex Stop hook. That report establishes a trust gap for the named hook, not a complete inventory of running hooks. The existing [trust checker](../scripts/codex_hook_trust_check.py) helps inspect configuration, but a direct invocation of a script proves only that script path. Verify a harmless call through the actual host before claiming enforcement. Review current definitions through Codex's `/hooks` interface; retain the user's trust decision.

Official documentation describes the same distinction: [Codex hooks](https://developers.openai.com/codex/hooks/) require review of non-managed definitions and document tool paths that can bypass hook coverage. [Claude Code hooks](https://code.claude.com/docs/en/hooks) expose different decisions at different events. A post-action record cannot prevent an effect that already occurred. Event names and a hook's exit status alone cannot establish a working block.

## Apply the lesson with existing mechanisms

Clean-sheet approach: put consequential operations behind one authorized execution boundary that validates scope and binds results to current inputs. Use hooks to connect host events to that boundary.

Current-repository approach: extend the existing verifier, dispatcher and receipts when an observed failure requires it. Avoid a second readiness schema, lifecycle recorder or broad blocking Stop hook. Keep advisory maintenance non-blocking and honor existing production, deletion and major-decision gates. The image supplies no new execution authority.

For a hook change, check a valid action, a prohibited action and a stale or mismatched result through the real host in an isolated fixture. Inspect resulting side effects as well as returned JSON. Exercise relevant failure paths: missing trust, malformed input, timeout, duplicate delivery, restart and unsupported tool paths. Measure added latency on the affected path before claiming an efficiency improvement.

Reuse passing evidence for unaffected criteria; recheck the criteria whose inputs or outputs changed. If a required human decision no longer covers the changed action, follow the existing approval policy. Preserve valid work and prior decision history.

## Limits and sharing

The image explicitly reports incomplete delivery and limits its Codex and Claude observations. Keep those qualifications when reusing it. Hashes and chronological records support integrity and traceability; they do not authenticate a human or prove substantive correctness.

Shared locally with code-audit as `skills/code-principles/references/hook-boundaries.md`, reachable from its engineering-foundations reference. This change adds assessment and review guidance. No hook was installed, enabled or rewritten by this assessment, and no end-to-end enforcement or performance gain is claimed.
