# System access: ask once and check the existing request

The agent must stop creating repeated authentication dialogs for one access need. Read-only probes can display dialogs. Authorization, password entry, a successful command and a successful product check are distinct outcomes.

## Before the first request

1. Prefer source, signing metadata, injected stores and simulator fixtures. Do not read real app Keychain values with an ad hoc Swift/Python interpreter. macOS identifies that interpreter as the requester, creating an avoidable trust prompt.
2. Choose one stable scope for the protected resource and operation, for example `keychain:com.flodoro.identity:read`. All callers and worktrees reuse it; purpose wording, run IDs and helper paths are not scope components. Do not group unrelated credentials as the same authorization.
3. Run the wrapper with `--check-only`, `--scope`, `--purpose` and `--requester`. This reads metadata only and does not run the supplied command. A hold suppresses both another OS attempt and another agent question.
4. If no prior request exists and access is necessary, explain once: what needs access, why, which app the dialog will name, and what can continue without it. Never ask for a password in chat. Route the read-only operation through the wrapper. Privileged mutations remain governed by their existing broker and approval rules; this wrapper cannot authorize them.

## After a request or an unexpected dialog

Retain the request ID, scope, purpose, requester, status and evidence location. Do not persist passwords, tokens or retrieved Keychain contents. `--record-blocked` records an externally observed prompt without running a command. Use it only after verifying that the originating task-owned requester has stopped, and retain that evidence. It cannot replace a pending ledger request. Do not treat the record itself as proof of process termination.

A pending request stays pending. A failed, cancelled, denied or expired result cannot automatically dispatch again. Changing argv or purpose also cannot bypass the scope hold. A successful result is reusable only for the exact request within its freshness window; it grants no permission to another command. A failed-to-start process may be retried because it could not have displayed a dialog.

On timeout or user complaint, identify and stop only requesters owned by this task using PID plus command/path evidence. Do not terminate SecurityAgent, securityd, unrelated apps or the user's existing processes. A system dialog may outlive its requester; do not claim it vanished without observation.

Keep one durable user-facing note:

> Access is already recorded as pending or blocked under request ID X. Check the existing dialog or access settings when convenient. I have paused further attempts. I will retry only after you explicitly ask, and will refer to this request before doing so.

This is a status note, not another approval question. Do not resend it on a timer. If the user reports that they unlocked or signed in, inspect metadata first. If another interactive attempt is needed, explain the changed condition and wait for explicit retry authorization tied to the prior request. A timestamp, timeout, new agent or generic “continue” is not that authorization.

## Retry and limits

The CLI's explicit retry requires the exact prior terminal request ID and a reference to the user's retry instruction. It consumes that reference once. Pending work cannot be superseded this way. A blocked external observation is terminal only because its requester was already verified stopped. The reference records existing authorization; the agent must never invent one. Inspect CLI help for the current retry flags.

The shared ledger makes wrapper dispatch atomic across processes and fails closed on corrupt state. It does not intercept arbitrary commands or prove that an OS dialog appeared. Codex has protocol coverage for indirect app/interpreter access; the Claude command hook covers only recognized invocations. Report that limitation. Do not promise that all macOS prompts are globally eliminated.

## Incident motivating this rule

On 2026-09-07 a TruePace audit ran two Swift probes against the production device-ID item. Attempts to make them noninteractive still waited in Security, and the user saw `swift-frontend` asking for that item. The probes were terminated; two audit-owned app instances were subsequently stopped. The old wrapper keyed requests by command plus purpose and expired terminal deduplication after five minutes. The command registry did not recognize an indirect Swift Keychain read. Those are verified workflow gaps. The exact access-control entry behind each app prompt was not inspected and is not a confirmed signing defect.
