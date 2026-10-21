<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# ACP agents and launch commands

Generated from the ACP registry (`registry.json`, registry format `1.0.0`,
`https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json`) fetched
**2026-10-21**. Versions go stale within days; refresh before pinning anything.
The registry lists only agents that support authentication and carries no
release dates, so the date on every row is the fetch date.

## Agents build-loop routes

| Agent | Protocol | Launch | Registry version (2026-10-21) | Notes |
|---|---|---|---|---|
| Claude | ACP | `npx @agentclientprotocol/claude-agent-acp@0.94.0` | 0.94.0 | Old `@zed-industries/claude-code-acp` is dead at 0.16.2. Pins drift fast: one local host pinned 0.63.0 on the fetch date. |
| Codex | app-server (primary) | `codex app-server` | — (ships with `@openai/codex`) | OpenAI labels app-server experimental. Exposes `turn/steer`, `thread/fork`, `review/start`, which codex-acp does not forward. |
| Codex | ACP (parity / uniform-renderer option) | `npx @agentclientprotocol/codex-acp@2.2.2` | 2.2.2 | Spawns `codex app-server` and translates; `CODEX_PATH` overrides the bundled codex. Replaces `@zed-industries/codex-acp` (frozen 0.16.0). |
| Cursor | ACP | `cursor-agent acp` | 2026.10.19 | Blocking `cursor/ask_question`, `cursor/create_plan`: the client must reply or the agent hangs. Auth `cursor_login` or `CURSOR_API_KEY`. |
| Gemini CLI | ACP | `gemini --acp` (registry: `npx @google/gemini-cli@0.65.0 --acp`) | 0.65.0 | Past stdout-pollution bug (#25345, closed): drop non-JSON lines. |
| OpenCode | ACP | `opencode acp` | 1.19.3 | Native. |
| Muse | MSP, not ACP | `muse serve` | — | Its own protocol. Do not route through ACP. |
| Google Antigravity | ACP (separate binary) | `agy_acp_server.par` | 1.4.1 | ❓ No `agy --acp` flag found; unverified end to end. |
| Kiro CLI | ACP | `kiro-cli acp [--agent X]` | not in registry | Native since 1.25 per Kiro docs. |

## Full registry snapshot (42 agents)

| Registry id | Agent | Version | Launch | Env set by registry |
|---|---|---|---|---|
| agoragentic-acp | Agoragentic | 1.3.0 | `npx agoragentic-mcp@1.3.0 --acp` | — |
| amp-acp | Amp | 0.10.0 | binary `amp-acp` | — |
| antigravity-acp | Google Antigravity | 1.4.1 | binary `agy_acp_server.par` | — |
| auggie | Auggie CLI | 0.36.0 | `npx @augmentcode/auggie@0.36.0 --acp` | AUGMENT_DISABLE_AUTO_UPDATE |
| autohand | Autohand Code | 0.2.1 | `npx @autohandai/autohand-acp@0.2.1` | — |
| bob | Bob | 2.0.8 | `npx @ibm/bob@2.0.8 acp` | — |
| claude-acp | Claude Agent | 0.94.0 | `npx @agentclientprotocol/claude-agent-acp@0.94.0` | — |
| cline | Cline | 3.0.73 | `npx cline@3.0.73 --acp` | — |
| codebuddy-code | Codebuddy Code | 2.171.0 | `npx @tencent-ai/codebuddy-code@2.171.0 --acp` | — |
| codex-acp | Codex | 2.2.2 | `npx @agentclientprotocol/codex-acp@2.2.2` | — |
| cortex-code | Cortex Code | 1.0.73 | binary `cortex acp serve` | — |
| corust-agent | Corust Agent | 0.6.0 | binary `corust-agent-acp` | — |
| crow-cli | crow-cli | 0.1.24 | binary `crow-cli acp` | — |
| cursor | Cursor | 2026.10.19 | binary `cursor-agent acp` | — |
| deepagents | DeepAgents | 0.1.7 | `npx deepagents-acp@0.1.7` | — |
| devin | Devin | 3000.11.92 | binary `devin acp` | — |
| dimcode | DimCode | 0.5.21 | `npx dimcode@0.5.21 acp` | — |
| dirac | Dirac | 0.5.22 | `npx dirac-cli@0.5.22 --acp` | — |
| factory-droid | Factory Droid | 0.245.0 | `npx droid@0.245.0 exec --output-format acp-daemon` | DROID_DISABLE_AUTO_UPDATE, FACTORY_DROID_AUTO_UPDATE_ENABLED |
| fast-agent | fast-agent | 0.10.1 | `uvx fast-agent-acp==0.10.1 -x` | FAST_AGENT_MODEL |
| gemini | Gemini CLI | 0.65.0 | `npx @google/gemini-cli@0.65.0 --acp` | — |
| github-copilot-cli | Copilot | 1.0.101 | `npx @github/copilot@1.0.101 --acp` | — |
| glm-acp-agent | GLM Agent | 1.15.0 | `npx glm-acp-agent@1.15.0` | — |
| goose | goose | 1.55.0 | binary `goose acp` | — |
| grok-build | Grok Build | 1.0.66 | `npx @xai-official/grok@1.0.66 agent stdio` | — |
| harn | Harn | 0.10.171 | binary `harn serve acp` | — |
| junie | Junie | 3659.7.0 | binary `junie --acp=true` | — |
| kilo | Kilo | 7.8.15 | binary `kilo acp` or `npx @kilocode/cli@7.8.15 acp` | — |
| kimchi | Kimchi | 1.17.0 | binary `kimchi --mode acp` | — |
| kimi | Kimi CLI | 1.52.0 | binary `kimi acp` | — |
| minimax-code | MiniMax Code | 0.2.7 | `npx @minimax-ai/code@0.2.7 acp` | — |
| minion-code | Minion Code | 0.1.44 | `uvx minion-code@0.1.44 acp` | — |
| mistral-vibe | Mistral Vibe | 2.26.1 | binary `vibe-acp` | — |
| nova | Nova | 1.1.59 | `npx @compass-ai/nova@1.1.59 acp` | — |
| opencode | OpenCode | 1.19.3 | binary `opencode acp` | — |
| pi-acp | pi ACP | 0.0.34 | `npx pi-acp@0.0.34` | — |
| poolside | Poolside | 1.0.16 | binary `pool-darwin-arm64 acp` | — |
| qoder | Qoder CLI | 0.2.14 | `npx @qoder-ai/qodercli@0.2.14 --acp` | — |
| qwen-code | Qwen Code | 0.25.3 | `npx @qwen-code/qwen-code@0.25.3 --acp --experimental-skills` | — |
| sigit | siGit Code | 1.7.3 | binary `sigit` or `npx @smbcloud/sigit@1.7.3` | — |
| stakpak | Stakpak | 0.3.88 | binary `stakpak acp` | — |
| vtcode | VT Code | 0.96.14 | binary `vtcode acp` | VT_ACP_ENABLED, VT_ACP_ZED_ENABLED |

## Refresh

Regenerate the snapshot table from the live registry (stdlib only):

```bash
curl -fsSL https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json -o /tmp/acp-registry.json
python3 - /tmp/acp-registry.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("| Registry id | Agent | Version | Launch | Env set by registry |")
print("|---|---|---|---|---|")
for a in sorted(d["agents"], key=lambda a: a["id"]):
    launches, envs = [], set()
    for k, v in a["distribution"].items():
        if k in ("npx", "uvx"):
            args = " ".join(v.get("args", []))
            launches.append(f"`{k} {v['package']}{' ' + args if args else ''}`")
            envs |= set(v.get("env", {}))
        elif k == "binary":
            p = next(iter(v.values()))
            args = " ".join(p.get("args", []))
            launches.append(f"binary `{p.get('cmd', '').split('/')[-1]}{' ' + args if args else ''}`")
            envs |= set(p.get("env", {}))
    print(f"| {a['id']} | {a['name']} | {a['version']} | {' or '.join(launches)} | {', '.join(sorted(envs)) or '—'} |")
PY
```

Then update the fetch date at the top, re-check the "Agents build-loop routes"
rows by hand, and compare each local pin with the new registry version. If
installed, `rosslabs-lsp acp doctor` reports the same drift per agent.
