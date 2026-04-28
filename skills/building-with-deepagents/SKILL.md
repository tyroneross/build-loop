---
name: building-with-deepagents
description: Use when building or refactoring an agent that imports OSS `deepagents` (`from deepagents import create_deep_agent`). Covers SubAgent API, middleware, tool scoping, streaming, checkpointing. Not for `.claude/agents/*.md`.
---

# Building With DeepAgents (OSS)

This skill is for agents running on the open-source `deepagents` package (`pip install deepagents`) on top of LangChain / LangGraph, typically paired with a local model via `langchain-ollama`. It is **not** for the hosted LangChain/DeepAgents cloud product — if you find yourself reaching for LangSmith Platform or hosted subagents, stop: those features don't apply here. Prefer Arize Phoenix (self-hosted, OpenInference instrumentation) for observability in OSS deployments.

Read this skill **before** writing or modifying code that calls `create_deep_agent`, defines agent "roles", or streams LangGraph events.

## When this applies

Triggers (check any):
- Project imports `deepagents` (grep `from deepagents`)
- Project has a multi-role agent concept (planner / researcher / writer / etc.)
- Work touches tool binding, system prompts, focus/single-agent mode, or streaming of an existing DeepAgents app
- Pain report mentions: tool-call hallucinations, "silent thinking" gaps, focus-mode prompt injection, per-agent model routing

## Authoritative sources

Read these in order when you need more detail than this skill:

1. `references/api-quick-reference.md` — the `SubAgent` TypedDict, `create_deep_agent()` signature, middleware stack, cached on disk
2. `references/anti-patterns.md` — concrete bugs we've hit and what DeepAgents does / doesn't prevent
3. **Live source** — the installed package is always ground truth. Find it with:
   ```bash
   python3 -c "import deepagents; print(deepagents.__file__)"
   ```
   Read `graph.py` (the assembly logic) and `middleware/subagents.py` (the `SubAgent` TypedDict + dispatch).
4. **GitHub**: `https://github.com/langchain-ai/deepagents` — CHANGELOG for recent middleware additions
5. **LangGraph docs**: `https://langchain-ai.github.io/langgraph/` — streaming modes, checkpointing, interrupts. DeepAgents is a thin wrapper over LangGraph.
6. **Context7 MCP**: `mcp__plugin_context7_context7__resolve-library-id("deepagents")` for on-demand doc lookups during build.

## Core principles

### Principle 1 — Per-subagent tool scoping is the hallucination fix

Small local models (< 14B) emit fabricated tool-call namespaces when given a large flat tool set for a prompt that needs none (e.g. `repo_browser.write_todos` when only `write_todos` was requested). The canonical fix is **not** validation after the fact — it's restricting each subagent's tool surface so the model has fewer options to invent against.

**Do**: pass `subagents=[{"name": "planner", "tools": [], ...}]` so the Planner only sees DeepAgents' built-in middleware tools.
**Don't**: pass a single flat `tools=[...]` list and inject "You are the X agent" in the system prompt.

Evidence: `middleware/subagents.py:65` — the `SubAgent["tools"]` field; `middleware/subagents.py:347` — per-subagent tool resolution falling back to `default_tools` if unspecified.

### Principle 2 — The middleware stack is always-on; treat it as free

DeepAgents injects by default (see `graph.py:192-260`):
- `TodoListMiddleware` → provides `write_todos`
- `FilesystemMiddleware` → `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- `SummarizationMiddleware` → automatic context-window pruning
- `PatchToolCallsMiddleware` → cleans up orphaned tool calls (missing `ToolMessage`)
- `SubAgentMiddleware` → the `task` tool for main-agent delegation

You do NOT need to re-implement any of these. If you wrote your own summarization, thread-storage loader, or orphan-tool-call cleanup before finding this skill — delete it.

### Principle 3 — Focus mode = scoped main agent, NOT prompt injection

"Single-agent" or "focus" mode (user pins one role, skips delegation) should be implemented by:

```python
# Focus mode: main agent IS the role.
agent = create_deep_agent(
    model=role_model,
    tools=scoped_tools,           # only the role's allow-list
    system_prompt=role_prompt,     # the role's system_focus verbatim
    subagents=[],                  # no delegation — no `task` tool
)
```

Not by appending `"\n\n## Single-Agent Mode\n\nYou are the X agent..."` to a shared system prompt. The prompt-injection approach leaves the full flat tool surface accessible, which is exactly the hallucination failure mode Principle 1 addresses.

### Principle 4 — Keep local optimizations that DeepAgents doesn't cover

DeepAgents is agnostic to:
- Local-model cold-load (no Ollama awareness). Keep your warmup + heartbeat code.
- SSE transport to a UI. DeepAgents emits LangGraph events; you still wire the HTTP.
- Fast-path routing for trivial prompts. As of v0.4.x there is no "skip the graph" concept; future `middleware` additions could change this — re-check the changelog before removing a fast-path layer.
- Tool-name validation as a defensive layer. DeepAgents' `PatchToolCallsMiddleware` only handles orphaned `ToolMessage`, not invalid tool names.

Don't delete these when migrating to DeepAgents' native features. They solve different problems.

## The `SubAgent` spec — quick reference

```python
from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent  # TypedDict

subagent: SubAgent = {
    "name": "researcher",                    # required
    "description": "Gathers web + file info",# required — main agent reads this to decide when to delegate
    "system_prompt": "You are RESEARCHER...",# required
    "tools": [web_search, scrape_url, ...],  # optional — if omitted, inherits main agent's tools
    "model": "provider:model-name",          # optional — per-agent model routing
    "skills": ["/path/to/skills/"],          # optional — markdown skills injection
    "interrupt_on": {"dangerous_tool": True},# optional — human-in-the-loop
    "permissions": [...],                    # optional — FilesystemPermission rules
}

agent = create_deep_agent(
    model=main_model,
    tools=[...],              # main agent's tools (subagents have their own)
    system_prompt="...",
    subagents=[subagent],     # ONE or more; ordering matters for main-agent selection
    checkpointer=...,         # pass a real Checkpointer, not MemorySaver, for durable threads
)
```

### Recommended checkpointer

`MemorySaver` is volatile — threads vanish on restart. Use `SqliteSaver` for local apps:

```python
from langgraph.checkpoint.sqlite import SqliteSaver
checkpointer = SqliteSaver.from_conn_string(".app/checkpoints.db")
```

Durable threads + resume-from-checkpoint come for free. You do not need a custom `threads.py` loader.

## Streaming — match the UI's needs

DeepAgents exposes LangGraph's stream API verbatim. Pick the mode based on what the UI consumes:

| Mode | Emits | Good for |
|---|---|---|
| `"updates"` | Node outputs (messages list) on each step | Tool-event-level UI (our current bridge) |
| `"messages"` | Token-by-token from the LLM node | Chat UI with per-token streaming |
| `["updates", "messages"]` | Both | The usual answer — tool events **and** tokens |
| `astream_events(version="v2")` | Typed `on_tool_start`, `on_chat_model_stream`, etc. | Clean disambiguation without message-sniffing |

If your current SSE loop does `hasattr(msg, "tool_calls")` or `msg.type == "tool"` branching, consider `astream_events` instead — cleaner event types, forward-compatible.

## Local model gotchas

When paired with `langchain-ollama`:

1. **Always set a read timeout** — default is infinite (`None`). A hung model blocks the UI forever:
   ```python
   ChatOllama(model=..., client_kwargs={"timeout": httpx.Timeout(connect=5, read=600, write=30, pool=5)})
   ```
2. **Add `.with_retry()`** for transient `httpx.TransportError` — common when a model is hot-unloaded mid-turn:
   ```python
   llm.with_retry(stop_after_attempt=2, wait_exponential_jitter=True,
                  retry_if_exception_type=(httpx.TransportError, httpx.TimeoutException))
   ```
3. **`num_ctx` is sticky** — changing it between calls triggers full KV-cache re-allocation in Ollama. Pick one per role, don't vary.
4. **Parallel tool calls are serialized** by Ollama's OpenAI-compat endpoint even when the flag is set. Don't prompt the model to "call tools in parallel."
5. **Structured output (`format: json`) conflicts with tool calling** — use one or the other per turn.

## Anti-patterns we've encountered

- **Flat tool list + prompt-injected focus mode** → tool-name hallucinations. See Principle 1.
- **Re-implementing summarization** → `SummarizationMiddleware` is already in the default stack.
- **Using `MemorySaver` in production** → threads vanish on restart. Swap to `SqliteSaver`.
- **Relying on `agent.stream("updates")` alone** → tokens are invisible until a tool boundary. UI appears frozen. Use multi-mode or events.
- **Per-call `num_ctx` tuning** → forces KV cache re-alloc. Fix `num_ctx` per role.
- **No `.with_retry()`** → a single `httpx.ReadError` kills the turn.

## Before you code

Run these checks:

1. **Read the installed source**. `cat $(python3 -c 'import deepagents, os; print(os.path.dirname(deepagents.__file__))')/middleware/subagents.py | head -100`
2. **Check the version**. `pip show deepagents` — know what API you have.
3. **Grep the repo for anti-patterns** before adding new code:
   ```bash
   grep -rn "agent_focus_prompt\|## Single-Agent Mode\|flat tool" src/
   grep -rn "MemorySaver" src/   # durable threads?
   grep -rn "ChatOllama(" src/ | grep -v client_kwargs  # missing timeout?
   ```

## Output (what "done" looks like)

A DeepAgents-based agent is well-built when:

- [ ] Each role that exists conceptually has its own `SubAgent` spec with a scoped `tools` list
- [ ] Focus/single-agent mode scopes the MAIN agent's tools — no prompt-level "You are the X agent" override
- [ ] `ChatOllama` instances have `client_kwargs={"timeout": ...}` and `.with_retry()`
- [ ] `checkpointer` is durable (SQLite/Postgres), not `MemorySaver`
- [ ] `astream_events(version="v2")` or multi-mode streaming is used if the UI needs per-token updates
- [ ] Local-only optimizations (warmup, heartbeat, fast-path, tool validator) are preserved — DeepAgents doesn't cover them
- [ ] The tool-name validator is still present as a belt-and-braces guard against small-model fabrication

## Related skills

- `claude-code-debugger:debug-loop` — for investigating agent behavior (tool hallucinations, stuck streams)
- `prompt-builder:prompt-builder` — for tightening per-role `system_prompt` text
- `calm-precision` — if you're also building the UI side of the agent

---

*Version 1.0 — 2026-04-13. Based on `deepagents` v0.4.x. Re-read the installed package source when the CHANGELOG mentions middleware or SubAgent changes.*
