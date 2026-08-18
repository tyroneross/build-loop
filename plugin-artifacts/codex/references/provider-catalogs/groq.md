# Groq provider catalog and workload guide

Snapshot date: **2026-08-18**

Review by: **2026-09-17**
Machine-readable companion: `references/provider-catalogs/groq-models.json`

Use this catalog for downstream workloads that call the Groq API. It does not make a Groq model reachable as a Build Loop host agent. Add a model to the host-agent taxonomy only after its adapter works and it clears the role-specific Build Loop benchmark.

## Decision summary

- Default to `openai/gpt-oss-20b` for production text and reasoning: it shares the documented context, maximum completion, strict-schema, reasoning-control, cache, and tool features of 120B while Groq advertises twice the throughput and half the output price.
- Evaluate `openai/gpt-oss-120b` only when representative workload tests demonstrate a quality gain worth its higher price and lower advertised throughput. Groq's capability pages do not establish that quality difference.
- Use `groq/compound` or `groq/compound-mini` when Groq-managed server-side web/code/tool orchestration is the product requirement. They accept JSON mode but not caller-supplied local/remote tools, cap output at 8,192 tokens, and pass through underlying model/tool charges.
- Use `whisper-large-v3` for error-sensitive multilingual transcription or English translation. Use `whisper-large-v3-turbo` for cheaper, faster transcription when translation is unnecessary.
- `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` are gone. Groq shut both down on **2026-08-16** for Free and Developer plans, and the 2026-08-18 runtime model listing no longer returns either ID. Requests to them now fail. Migrate to `openai/gpt-oss-20b` and `openai/gpt-oss-120b` or `qwen/qwen3.6-27b` respectively.
- Groq currently has no stable production vision, text-to-speech, or standalone safety-model default in this catalog. The documented choices are preview models.

## Current catalog

### Production text models and systems

| ID | Lifecycle | Context / max output | Advertised speed | List price input/output | Developer reference limits | Best fit |
|---|---|---:|---:|---:|---:|---|
| `openai/gpt-oss-120b` | production | 131,072 / 65,536 | 500 t/s | $0.15 / $0.60 per 1M tokens | 250K TPM, 1K RPM | benchmark candidate for harder cases; quality gain unverified |
| `openai/gpt-oss-20b` | production | 131,072 / 65,536 | 1,000 t/s | $0.075 / $0.30 per 1M tokens | 250K TPM, 1K RPM | default production text/reasoning candidate |
| `groq/compound` | production system | 131,072 / 8,192 | 450 t/s | pass-through model/tool charges | 200K TPM, 200 RPM | server-side multi-tool orchestration |
| `groq/compound-mini` | production system | 131,072 / 8,192 | 450 t/s | pass-through model/tool charges | 200K TPM, 200 RPM | lighter server-side tool orchestration |

### Retired

| ID | Shut down | Replacement | Migration note |
|---|---|---|---|
| `llama-3.1-8b-instant` | 2026-08-16 | `openai/gpt-oss-20b` | costs more: $0.075/$0.30 against Llama's former $0.05/$0.08, so 1.5x input and 3.75x output. Re-budget output-heavy jobs |
| `llama-3.3-70b-versatile` | 2026-08-16 | `openai/gpt-oss-120b` or `qwen/qwen3.6-27b` | costs less: $0.15/$0.60 against Llama's former $0.59/$0.79, so 75% off input and 24% off output, and max completion doubles from 32,768 to 65,536 |

The 2026-08-07 snapshot carried both as production-but-deprecated because Groq's models page still badged them production while the deprecation page scheduled the shutdown. That conflict is resolved: the shutdown executed, the models page dropped both rows, the rate-limits page dropped both rows, and a 2026-08-18 call to `GET /models` returned neither ID. Groq's notice carved out Enterprise committed-spend contracts, so those accounts may retain access; confirm with your Groq account team rather than with this catalog. Groq's tool-use page still lists both models in its capability matrix — that page is stale, and the deprecation schedule outranks it.

### Production speech-to-text

| ID | Price | Developer reference limits | Groq-advertised benchmark | Use when |
|---|---:|---:|---:|---|
| `whisper-large-v3` | $0.111/audio hour | 200K audio seconds/hour, 300 RPM | RTF 189; WER 10.3% | multilingual accuracy or translation to English matters |
| `whisper-large-v3-turbo` | $0.04/audio hour | 400K audio seconds/hour, 400 RPM | RTF 216; WER 12% | transcription price and speed matter; no translation |

Groq documents a 100 MB Developer-plan upload limit, a 25 MB Free-plan limit, and a ten-second minimum billed duration. Files above 25 MB should use a URL. Treat RTF and WER as vendor benchmarks, not workload guarantees.

### Preview and Enterprise-preview entries

| ID | Type | Key limits or price | Constraint |
|---|---|---|---|
| `qwen/qwen3.6-27b` | reasoning + vision | 131,072 context; 16,384 output; $0.60/$3.00; 5 images; 20 MB/image | only documented vision model; preview |
| `openai/gpt-oss-safeguard-20b` | safety reasoning | 131,072 context; $0.075/$0.30 | best-effort schemas and prompt cache; preview |
| `minimaxai/minimax-m2.7` | reasoning | 196,608 context; 131,072 output; contact sales | Enterprise preview |
| `meta-llama/llama-prompt-guard-2-22m` | prompt attack classifier | 512 context; $0.03/$0.03 | preview |
| `meta-llama/llama-prompt-guard-2-86m` | prompt attack classifier | 512 context; $0.04/$0.04 | preview |
| `canopylabs/orpheus-v1-english` | text to speech | $22 per 1M characters | preview |
| `canopylabs/orpheus-arabic-saudi` | text to speech | $40 per 1M characters | preview |

Preview models can change or disappear on short notice. Require a fallback, contract test, and rollback path before a production experiment.

## Capability rules

### API surface

Groq exposes OpenAI-compatible endpoints under `https://api.groq.com/openai/v1`. Use Chat Completions for the broadest model surface. The Responses API supports text and image inputs, tools, structured output, and reasoning, but Groq labels it **beta** and documents unsupported OpenAI features. Require contract tests and a Chat Completions fallback before production use. Each request must still obey the selected model's capability constraints below. Query `GET /models` at runtime when an API key is available; the documentation snapshot alone cannot prove account-specific access.

### Structured outputs

- Strict `json_schema` constrained decoding is limited to `openai/gpt-oss-20b` and `openai/gpt-oss-120b` in the captured docs.
- Best-effort schema mode also includes `openai/gpt-oss-safeguard-20b`.
- Other chat models should use JSON Object mode and application-side validation.
- Structured Outputs cannot be combined with streaming or tool use. Choose the schema or tool path per request.

### Tools

- GPT OSS 20B/120B support local and remote tools plus Groq built-in tools, but not parallel tool calls.
- Qwen 3.6 and MiniMax M2.7 support local/remote and parallel tool calls, but not built-in tools.
- Compound systems provide built-in server-side tools. They do not accept caller-supplied local or remote tools.
- Tool prices may be additional pass-through charges. Recheck the tool-specific page before estimating cost.

### Reasoning

- Reasoning is documented for GPT OSS 20B/120B, GPT OSS Safeguard 20B, Qwen 3.6, and MiniMax M2.7.
- GPT OSS accepts `reasoning_effort` values `low`, `medium`, and `high`. Qwen 3.6 documents `none` and `default`.
- GPT OSS 20B/120B do **not** support `reasoning_format`; use `include_reasoning` to include or suppress their dedicated reasoning field.
- Non-GPT-OSS reasoning models use `reasoning_format` values `parsed`, `raw`, and `hidden`. Raw reasoning cannot be combined with JSON mode or tool use and produces HTTP 400. Where both controls are accepted, `include_reasoning` and `reasoning_format` are mutually exclusive.

### Vision and audio

- `qwen/qwen3.6-27b` is the only model listed in Groq's vision guide. It is preview, accepts up to five images, and caps an image URL at 20 MB.
- Whisper v3 and Whisper v3 Turbo are the stable speech-to-text choices. Turbo does not translate.
- Orpheus English and Arabic Saudi are preview text-to-speech models; keep a production fallback outside this catalog.

## Performance and production guidance

Groq's tokens-per-second figures describe advertised generation throughput, not end-to-end latency or an SLA. Measure the real workload:

1. Record time to first token, total server latency, input/output tokens, tokens per second, network time, error rate, retry rate, and cost.
2. Load-test with production-like prompt sizes, concurrency, tool calls, and streaming behavior. Compare percentiles, not only averages.
3. Use explicit timeouts, bounded exponential backoff with jitter, streaming-error handling, graceful degradation, and a rollback path.
4. Read rate-limit response headers and honor `retry-after` on HTTP 429. Groq applies limits at organization level; the console is the authority for the actual account.
5. Use automatic prompt caching for repeated exact prefixes on GPT OSS 20B/120B or Safeguard. Groq documents a two-hour inactivity expiry and a 50% cached-input discount. Put static content first.
6. Use Flex processing for paid, capacity-tolerant traffic that benefits from up to 10× on-demand limits at the same model price. Handle HTTP 498 `capacity_exceeded` with jittered retry or a fallback.
7. Use Batch for non-interactive work that can complete in 24 hours to seven days. Groq advertises a 50% batch discount and separate limits; the discount does not stack with prompt caching.

## Freshness protocol

Recheck this catalog by 2026-09-17. Nothing is currently scheduled for shutdown: the deprecation page's newest entry is the 2026-08-16 Llama retirement, which has already executed, so the seven-day pending-deprecation interval below does not apply and the 30-day stable interval does. Seven of the thirteen live models are preview, and preview models can be withdrawn without notice, so do not stretch the interval past 30 days. Refresh immediately, ahead of that date, if Groq emails a new deprecation notice or if a workload starts depending on a preview model. Each refresh:

1. Fetch the official models, deprecations, rate-limit, pricing, and capability-specific pages.
2. If `GROQ_API_KEY` is available, compare model IDs with `GET https://api.groq.com/openai/v1/models`; never store the key or response headers containing secrets.
3. Diff all fields listed in `dynamic_fields` in `groq-models.json`.
4. Run `python3 -m pytest tests/test_groq_provider_catalog.py -q`.
5. Regenerate and verify the Codex artifact.

Use a 30-day review interval for a stable catalog, a seven-day interval while a deprecation or preview promotion is pending, and an immediate refresh before billing decisions or production capacity changes.

## Provenance

All sources were re-fetched on 2026-08-18 and are Groq first-party documentation (T1 primary, single-vendor). The runtime endpoint `GET https://api.groq.com/openai/v1/models` was also queried once on 2026-08-18 with a Developer-plan key to confirm the retirements; it is a first-party runtime check, not a documentation source, and no key or response header was recorded.

- [Models](https://console.groq.com/docs/models)
- [Deprecations](https://console.groq.com/docs/deprecations)
- [Structured Outputs](https://console.groq.com/docs/structured-outputs)
- [Tool use](https://console.groq.com/docs/tool-use/overview)
- [Reasoning](https://console.groq.com/docs/reasoning)
- [Responses API](https://console.groq.com/docs/responses-api)
- [Vision](https://console.groq.com/docs/vision)
- [Speech to text](https://console.groq.com/docs/speech-to-text)
- [Text to speech](https://console.groq.com/docs/text-to-speech)
- [Rate limits](https://console.groq.com/docs/rate-limits)
- [Prompt caching](https://console.groq.com/docs/prompt-caching)
- [Flex processing](https://console.groq.com/docs/flex-processing)
- [Batch processing](https://console.groq.com/docs/batch)
- [Optimizing latency](https://console.groq.com/docs/production-readiness/optimizing-latency)
- [Production-ready checklist](https://console.groq.com/docs/production-readiness/production-ready-checklist)

## Changelog

- 2026-08-07: Added the current production, preview, and compound-system catalog; capability matrices; commercial metadata; production guidance; and a freshness contract.
- 2026-08-07: Overrode the Llama production badges with the newer Free/Developer deprecation schedule and recorded replacements.
- 2026-08-07: Kept Groq workload guidance separate from Build Loop host-agent resolution pending adapter and benchmark evidence.
- 2026-08-18: Confirmed the 2026-08-16 shutdown executed. Moved `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` out of the production table into a Retired section and changed their catalog lifecycle from `production_deprecated` to `retired`.
- 2026-08-18: Re-verified every remaining model's context window, max completion tokens, price, advertised throughput, and Developer-plan limits against the models page. No value changed, and Groq added no model.
- 2026-08-18: Recorded `allam-2-7b` as an undocumented runtime ID that is deliberately excluded from the catalog, and recorded the tool-use page as stale on the retired Llama models.
