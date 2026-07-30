<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Design: rally `inject` fresh-laptop portability

**Author:** claude_code · **Date:** 2026-07-02 · **Status:** design spec · grounded in source + live probe.
**Corrects** an earlier machine-specific claim ("ptyd already ships, zero cost") — true only because this is a dev box with Easy Terminal installed.

## Headline
On a **fresh laptop** (no tmux, no ptyd, no cmux), `rally inject` **effectively does not work** — neither rally nor build-loop provisions any pane backend. The portable path that works with zero backend is **ledger handoffs (`rally say handoff`)**. The fix is to make backend-absence **detectable** and **gracefully degrade to handoffs** — not to add an always-on daemon.

## Provisioning reality (why fresh-laptop inject fails)
ptyd is **not bundled in rally, not fetched by build-loop, not built lazily** — it is a separate **Easy Terminal** companion binary, present here only because this is an ET/dev box.
- rally has **zero** ptyd dependency by architectural lint H4 (`rally-cli/Cargo.toml:26-30`); it shells out to an external `ptyd` (`backends.rs:595-664`, attach path opens the pane "in EasyTerminal").
- No ptyd crate in ARP; releases publish `rally-<triple>` only (`binary_fetch.py:18-48`). build-loop fetches **only** rally (`ensure_binary`, `binary_fetch.py:157-206`) — no ptyd fetch anywhere in the bridge.
- ARP autostart resolves an **existing** binary and errors if absent: `ptyd_binary()` = `$RALLY_PTYD_BIN` or `ptyd` on PATH (`daemon_client.rs:611-624`); `autostart_daemon` spawns `ptyd server` detached but returns `Err "no ptyd binary was found"` when missing (`daemon_client.rs:552-565`). Lazy-*spawn*, not lazy-*provision*.

**Fresh-laptop truth table** (no tmux/ptyd/cmux):

| Command | Result |
|---|---|
| `rally run --backend auto` | ptyd socket dead → falls to tmux → **exec fails (tmux not found)** |
| `rally run --backend ptyd` | socket dead → autostart → no ptyd binary → **Err** |
| `rally run --backend cmux` | cmux exec fails (heavy GUI app, not installed) |
| `rally inject <managed>` | no managed session exists → target-resolution error |
| `rally say handoff …` | **works — zero backend needed** ✅ |

## How agents should know whether to try inject (detection)
Today: partial only. `rally whoami --json .host_runtime` reports ptyd sockets, **not** tmux, and there is **no** `inject_available`/`recommended_backend` field. `rally doctor` is maintenance-scoped, not backend-readiness.

**Minimal deterministic check (cheap, fail-open) an agent can run now:**
```
inject_available = which tmux
                || (whoami .host_runtime.under_ptyd == true || ptyd socket live)
                || which ptyd
# all false ⇒ no pane backend ⇒ use `rally say handoff`, not `rally inject`
```
**Proposed (ARP, small):** a `capabilities` block in `whoami --json` (or `rally doctor --backends`): `{tmux, ptyd_socket_live, ptyd_bin, cmux, inject_available, recommended_backend}` — one authoritative signal so agents/plugins stop re-deriving it.

## How the plugin ensures requirements
- **(a) Detect at session start — build-loop bridge.** `capability.py` levels describe the rally *binary*, not the pane *backend* (a machine can be `full` yet have no inject backend). Add the pane-backend probe above to the session-start preflight; stamp result next to `capability_level`. Fail-open.
- **(b) Provision ptyd on-demand?** Only viable once ARP/Easy-Terminal **publishes a pinned, host-triple ptyd release asset + `.sha256`** (it doesn't today). Then build-loop adds a `ptyd_fetch.py` sibling of `binary_fetch.py` (SHA-verified, pinned, cached). Idle cost stays zero (rally lazy-starts the daemon only on demand). **Blocked on the ARP release; not build-loop-only.**
- **(c) Graceful degradation — split.** ARP: `--backend auto` should detect "no backend" and return a clear coordination message ("use `rally say handoff`") instead of a raw tmux "command not found". build-loop: when the probe reports no backend, route cross-agent signals through `rally say handoff`/`post.py` (backend-free) and mark inject unavailable.

## Ranked recommendation (lightweight lens: on-demand > always-on)
1. **Detection probe + graceful-degrade to ledger handoffs** — build-loop bridge (+ small ARP message fix). Zero new processes, zero idle. Makes the fresh-laptop story *correct*. **Do first.** *(build-loop-side is independently shippable now.)*
2. **`inject_available` field in `whoami`/`doctor`** — ARP, small. One authoritative readiness signal. *(→ Codex.)*
3. **Document tmux as optional `brew install tmux`** — docs-only. (tmux is a persistent daemon once started — slightly less aligned with no-idle than ptyd's lazy-start.)
4. **Pin+fetch ptyd parallel to rally** — the clean *portable* managed-pane inject answer, idle cost zero (lazy autostart). **Blocked** until ARP publishes a ptyd release asset. *(ARP release → then build-loop `ptyd_fetch`.)*
5. **Bundle ptyd / always-on daemon** — reject (violates no-idle + H4 lint). cmux never auto.

**Bottom line:** fresh-install fix = #1 + #2 (detectable absence + graceful degrade to handoffs, no daemon added). Reserve #4 as the durable portable path once a ptyd asset exists.
