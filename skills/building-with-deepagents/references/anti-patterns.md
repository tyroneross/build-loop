# DeepAgents Anti-Patterns

Concrete bugs from real projects, mapped to what DeepAgents does or does not cover.

## AP-1: Flat tool list + prompt-injected "focus mode"

**Symptom**: user pins a single agent role (e.g. Planner). Model emits `repo_browser.write_todos` or `mcp.execute` — fabricated namespaces that don't exist in the tool registry. Loop detection fires, turn limit eventually triggers, UI shows a wall of red `tool_error` events for a trivial prompt.

**Root cause**: the main agent had access to all tools (web_search, python_exec, ...). The "focus" was enforced via a `## Single-Agent Mode` system-prompt suffix saying "you are the Planner, use write_todos only." Small models (< 14B) don't reliably obey prompt-level constraints against an in-scope tool list. When the prompt needs no tool call (e.g. "capital of france"), the model invents plausible-looking namespaced tools from its training data.

**Fix**: scope tools at the SubAgent level (or at main-agent level in focus mode). Never rely on the prompt alone to restrict tool access.

**DeepAgents coverage**: ✅ supported via `SubAgent["tools"]`.

---

## AP-2: Re-implementing summarization middleware

**Symptom**: code grows a custom `_truncate_old_messages()` or `_summarize_if_long()` function. Duplicates what DeepAgents already provides.

**Root cause**: contributor didn't read the default middleware stack.

**Fix**: delete the custom summarizer. `create_summarization_middleware` is injected automatically in `graph.py`.

**DeepAgents coverage**: ✅ default stack.

---

## AP-3: `MemorySaver` in production

**Symptom**: user asks "continue our research from yesterday" — all prior context gone after a backend restart.

**Root cause**: `MemorySaver` is in-process; threads evaporate on process exit.

**Fix**: swap to `SqliteSaver.from_conn_string(".app/checkpoints.db")`. LangGraph handles resume automatically.

**DeepAgents coverage**: ✅ any LangGraph `Checkpointer` works.

---

## AP-4: UI looks frozen during LLM reasoning

**Symptom**: user sends "write a 5-page report". UI shows "Thinking…" for 30 seconds, no tokens stream, then a giant block appears at the end.

**Root cause**: stream loop uses `stream_mode="updates"` only. Updates fire on node boundaries (typically after an LLM call finishes). Per-token output is invisible.

**Fix**: either multi-mode `stream_mode=["updates", "messages"]` or switch to `astream_events(version="v2")` and handle `on_chat_model_stream`.

**DeepAgents coverage**: ✅ LangGraph's streaming API is exposed verbatim.

---

## AP-5: Cold-model load looks like a crash

**Symptom**: first query after app launch hangs for 30–60s with no UI signal, then suddenly works. Intermediaries drop the SSE connection during the silence.

**Root cause**: Ollama loads the model into VRAM on first request. DeepAgents doesn't know or care about this — it just hangs in the `ChatOllama.stream()` call.

**Fix**: warm the model explicitly before the first query. POST to `/api/generate` with empty prompt and `keep_alive: "30m"` at backend startup. Emit a "loading" SSE event from your bridge so the UI can show a loading overlay. Add a heartbeat pulse every 15s during silent agent-stream gaps.

**DeepAgents coverage**: ❌ — this is a local-model concern. Build it around DeepAgents, not into it.

---

## AP-6: Tool-name hallucinations still leak after scoping

**Symptom**: even with scoped tools, an 8B model occasionally emits `write_todos.plan` or `filesystem.read_file` — dotted variants of real tools.

**Root cause**: small model pulling patterns from training data. Scoping blocks the worst cases but doesn't eliminate fabrication.

**Fix**: belt-and-braces tool-name validator that rejects any name containing `.` or `/` before it reaches the UI, with a clean `tool_error` event. DeepAgents' `PatchToolCallsMiddleware` only handles *orphaned* tool calls (missing `ToolMessage`), not invalid names.

**DeepAgents coverage**: ⚠️ partial. Keep the validator.

---

## AP-7: `num_ctx` varying per request

**Symptom**: noticeable stall when the system prompt size changes between requests. Ollama logs show KV cache re-allocation.

**Root cause**: every unique `num_ctx` value triggers a full KV cache re-alloc in Ollama.

**Fix**: pin `num_ctx` per role (e.g. 4096 for full agent, 2048 for fast-path). Don't vary it based on prompt size.

**DeepAgents coverage**: ❌ Ollama-specific.

---

## AP-8: No retry on transient Ollama errors

**Symptom**: user query fails with `httpx.ReadError: Server disconnected` a few seconds in. Usually happens right after a model switch.

**Root cause**: Ollama hot-unloads the previous model when a new one is requested, and in-flight requests get dropped. No retry path.

**Fix**: wrap `ChatOllama` in `.with_retry()`:
```python
llm.with_retry(stop_after_attempt=2, wait_exponential_jitter=True,
               retry_if_exception_type=(httpx.TransportError, httpx.TimeoutException))
```

**DeepAgents coverage**: ❌ — LangChain core feature.

---

## AP-9: Stream cancellation doesn't stop the LLM (TAG:TIMEBOUND)

**Symptom**: user clicks Stop. UI updates, but GPU/CPU stays pegged for seconds or minutes. Ollama logs show the model still generating.

**Root cause**: `ThreadingHTTPServer` only detects client disconnect on the next `wfile.write`. And even then, cancelling the Python generator doesn't cancel the underlying `httpx` stream — `langchain-ollama` provides no abort token.

**Fix**: partial — migrate the SSE bridge to ASGI (Starlette + `uvicorn`). `await request.is_disconnected()` inside the generator gives clean cancellation. Then thread that through the `ChatOllama` stream (requires monkey-patching or waiting for langchain-ollama to expose an `abort` hook).

**TIMEBOUND**: the monkey-patch recommendation ages badly once `langchain-ollama` exposes a first-class abort. Re-check upstream before reaching for a patch — track the tracking issues in the langchain-ai/langchain-ollama repo.

**DeepAgents coverage**: ❌ — transport-layer concern.

---

## AP-10: `ChatOllama` default timeout = infinite

**Symptom**: a stuck or slow-loading model never returns. UI hangs forever.

**Root cause**: `ChatOllama` passes `None` as the httpx timeout by default.

**Fix**: always set `client_kwargs={"timeout": httpx.Timeout(connect=5, read=600, write=30, pool=5)}`.

**DeepAgents coverage**: ❌ — LangChain-Ollama config.

---

## AP-11: Subagents with no `tools` field inherit everything

**Symptom**: you define `{"name": "planner", "description": "..."}` without `tools`. The planner somehow calls `web_search`.

**Root cause**: if `tools` is not in the SubAgent spec, it inherits `default_tools` (the main agent's tool set). See `subagents.py:347`.

**Fix**: always set `tools=[]` explicitly for roles that should have no custom tools. An empty list is different from an omitted key.

**DeepAgents coverage**: ⚠️ be explicit — the default is permissive.
