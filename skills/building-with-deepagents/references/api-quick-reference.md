# DeepAgents API Quick Reference

Live source paths and exact signatures. Always verify against the installed version (`pip show deepagents`) — these notes track v0.4.x.

## create_deep_agent signature

File: `deepagents/graph.py:217`

```python
def create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    response_format: ResponseFormat | type | dict | None = None,
    context_schema: type | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    backend: BackendProtocol | BackendFactory | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
) -> CompiledStateGraph: ...
```

Returns a LangGraph `CompiledStateGraph`. Stream/invoke it with standard LangGraph APIs.

## SubAgent TypedDict

File: `deepagents/middleware/subagents.py:22`

```python
class SubAgent(TypedDict):
    name: str                                      # Required
    description: str                               # Required
    system_prompt: str                             # Required

    tools: NotRequired[Sequence[...]]              # Optional — inherits if omitted
    model: NotRequired[str | BaseChatModel]        # Optional — per-agent model
    middleware: NotRequired[list[AgentMiddleware]] # Optional — extra middleware
    interrupt_on: NotRequired[dict[str, ...]]      # Optional — human-in-the-loop per tool
    skills: NotRequired[list[str]]                 # Optional — paths to skill markdown
    permissions: NotRequired[list[FilesystemPermission]]  # Optional
```

**Important**: if you pass `tools=[]` (empty list), subagent still gets middleware-provided tools (`write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`). Only your custom tools are restricted.

## Kwargs worth knowing (often missed)

Not every arg to `create_deep_agent` is obvious at a glance. These four trip up new builders:

- **`backend`** — instance of `BackendProtocol` (usually `FilesystemBackend(root_dir=..., virtual_mode=True)`). Sandboxes the filesystem-middleware tools (`ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`) so they operate inside `root_dir` instead of the whole disk. Always pass one in a local-app context — otherwise the agent can read/write anywhere the process can.

- **`store`** — a LangGraph `BaseStore` (distinct from `checkpointer`). Checkpointer = per-thread conversation state. Store = cross-thread long-term memory (facts, documents, user profile). Pair `SqliteSaver` (checkpointer) with an in-process or file-backed store if you want the agent to remember across threads.

- **`response_format`** — typed final output. Pass a Pydantic model, TypedDict, or JSON schema; the final assistant message is coerced to it and exposed via `agent.invoke(...)["response"]`. **Conflicts with `format: "json"`** at the model layer — don't combine the two. For an 8B local model, Pydantic-schema validation on structured output is flaky; consider using a larger model for that turn only.

- **`interrupt_on`** — dict mapping tool name → bool or config. Causes the graph to pause before the named tool fires; the caller then `Command(resume=...)` after human approval. Requires a checkpointer to save the paused state.

## Async vs sync

The returned graph is dual-API. SSE bridges on ASGI (Starlette, FastAPI) should prefer async:

```python
async for event in agent.astream_events(input, config=config, version="v2"):
    ...
result = await agent.ainvoke(input, config=config)
```

On the subagent side, an `AsyncSubAgent` variant (see `deepagents/async_subagents.py`) exists for purely async subagent bodies. If your runtime mixes — sync HTTP server + async Ollama calls — LangChain's adapters handle the boundary, but you pay a thread-pool hop per LLM call. Match the transport to the agent code.

## Default middleware stack

File: `deepagents/graph.py:192-260` (build order). In order of injection for the main agent. **If this list disagrees with the installed source, trust the source** — this file is a snapshot; the middleware stack is the most volatile surface DeepAgents exposes.

1. `TodoListMiddleware` → exposes `write_todos`
2. `FilesystemMiddleware` (from `backend` arg) → `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
3. `ShellCommandMiddleware` → `execute` (shell command runner)
4. `SkillsMiddleware` (if `skills=` passed) → loads markdown into system prompt
5. `MemoryMiddleware` (if `memory=` passed) → loads `AGENTS.md`
6. `SubAgentMiddleware` (if `subagents=` passed) → the `task` tool
7. `SummarizationMiddleware` → context-window pruning
8. `PatchToolCallsMiddleware` → cleans orphaned tool calls
9. Any `middleware=` arg (custom)
10. `_PermissionMiddleware` (appended last if `permissions=` passed)

Subagents get a subset of this stack — see `subagents.py:660-667` for per-subagent assembly.

## Streaming

DeepAgents returns a LangGraph graph. Stream APIs:

```python
# Node-level (default in most example code)
for chunk in agent.stream(input, config=config, stream_mode="updates"):
    ...

# Token-level (per-token from LLM)
for chunk in agent.stream(input, config=config, stream_mode="messages"):
    ...

# Multi-mode — most useful for SSE bridges
for mode, payload in agent.stream(input, config=config, stream_mode=["updates", "messages"]):
    if mode == "updates": ...
    elif mode == "messages": ...

# Typed events — v2
async for event in agent.astream_events(input, config=config, version="v2"):
    kind = event["event"]
    # on_chat_model_start / on_chat_model_stream / on_chat_model_end
    # on_tool_start / on_tool_end / on_tool_error
    # on_chain_start / on_chain_end
```

`astream_events(include_types=["chat_model","tool"])` drops chain-level noise — ~5× fewer events.

## Checkpointing

```python
from langgraph.checkpoint.memory import MemorySaver       # volatile
from langgraph.checkpoint.sqlite import SqliteSaver       # local persistent
# pip install langgraph-checkpoint-sqlite

checkpointer = SqliteSaver.from_conn_string(".app/checkpoints.db")
agent = create_deep_agent(..., checkpointer=checkpointer)

# Resume a thread
config = {"configurable": {"thread_id": "research_001"}}
result = agent.invoke(input, config=config)
```

Postgres and async variants exist as separate packages (`langgraph-checkpoint-postgres`).

## Interrupts (human-in-the-loop)

```python
subagent = {
    "name": "executor",
    ...,
    "interrupt_on": {"python_exec": True},  # pause before every python_exec call
}
# Or full InterruptOnConfig for per-arg rules.
```

On interrupt, `astream` raises or yields an interrupt event. The UI approves/rejects, then:

```python
from langgraph.types import Command
agent.invoke(Command(resume="approved"), config=config)
```

## Per-agent model routing

`SubAgent["model"]` can be:
- `"openai:gpt-4o-mini"` → parsed as `provider:model-name`
- A `BaseChatModel` instance → used directly (e.g. a pre-configured `ChatOllama`)

If omitted, inherits the main agent's model.

## Common imports

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.subagents import SubAgent, CompiledSubAgent
# Optional:
from deepagents.permissions import FilesystemPermission
```

## Verifying your version

```bash
python3 -c "import deepagents, os; print(deepagents.__version__ if hasattr(deepagents, '__version__') else 'unknown'); print(os.path.dirname(deepagents.__file__))"
```

Then read the actual `graph.py` and `middleware/subagents.py` — this reference may be stale.
