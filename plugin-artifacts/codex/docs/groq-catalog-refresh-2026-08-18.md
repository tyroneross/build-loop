<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Groq catalog refresh — 2026-08-18

`references/provider-catalogs/groq-models.json` expired on 2026-08-14 and
`tests/test_groq_provider_catalog.py::test_review_deadline_has_not_passed`
started failing. This records what the live Groq documentation said on
2026-08-18, what changed in the snapshot, what could not be established, and
why the next review date is 2026-09-17.

## Headline

The 2026-08-16 shutdown executed. `llama-3.1-8b-instant` and
`llama-3.3-70b-versatile` are gone from Groq's models page, its rate-limits
page, and the runtime models endpoint. Nothing else moved: every remaining
model kept its context window, max completion tokens, price, advertised
throughput, and Developer-plan limits, and Groq added no model that belongs in
this catalog.

## Per-model changes

| Model | Field | Previous | Now | Evidence |
|---|---|---|---|---|
| `llama-3.1-8b-instant` | `lifecycle` | `production_deprecated` | `retired` | absent from [models](https://console.groq.com/docs/models), absent from [rate limits](https://console.groq.com/docs/rate-limits), absent from `GET /models`; shutdown date 08/16/26 on [deprecations](https://console.groq.com/docs/deprecations) |
| `llama-3.1-8b-instant` | `values_as_of` | — | `2026-08-07` (new field) | its pricing/limits/capabilities are now historical |
| `llama-3.1-8b-instant` | `deprecation.shutdown_confirmed_at` | — | `2026-08-18` (new field) | runtime listing on 2026-08-18 |
| `llama-3.3-70b-versatile` | `lifecycle` | `production_deprecated` | `retired` | same three sources |
| `llama-3.3-70b-versatile` | `values_as_of` | — | `2026-08-07` (new field) | as above |
| `llama-3.3-70b-versatile` | `deprecation.shutdown_confirmed_at` | — | `2026-08-18` (new field) | as above |
| `openai/gpt-oss-120b` | all | — | unchanged | 500 t/s, $0.15/$0.60, 250K TPM / 1K RPM, 131,072 / 65,536 on the models page |
| `openai/gpt-oss-20b` | all | — | unchanged | 1000 t/s, $0.075/$0.30, 250K TPM / 1K RPM, 131,072 / 65,536 |
| `whisper-large-v3` | all | — | unchanged | $0.111/hr, 200K ASH, 300 RPM, 100 MB; RTF 189 / WER 10.3% on [speech to text](https://console.groq.com/docs/speech-to-text) |
| `whisper-large-v3-turbo` | all | — | unchanged | $0.04/hr, 400K ASH, 400 RPM; RTF 216 / WER 12%; 100 MB dev-tier cap |
| `groq/compound` | all | — | unchanged | 450 t/s, 200K TPM / 200 RPM, 131,072 / 8,192 |
| `groq/compound-mini` | all | — | unchanged | as above |
| `canopylabs/orpheus-arabic-saudi` | all | — | unchanged | $40 per 1M characters, 50K TPM / 250 RPM, 4,000 / 50,000 |
| `canopylabs/orpheus-v1-english` | all | — | unchanged | $22 per 1M characters, same limits |
| `meta-llama/llama-prompt-guard-2-22m` | all | — | unchanged | $0.03/$0.03, 30K TPM / 100 RPM, 512 / 512 |
| `meta-llama/llama-prompt-guard-2-86m` | all | — | unchanged | $0.04/$0.04, same limits |
| `minimaxai/minimax-m2.7` | all | — | unchanged | Enterprise badge, 260 t/s, contact sales, 196,608 / 131,072 |
| `openai/gpt-oss-safeguard-20b` | all | — | unchanged | 1000 t/s, $0.075/$0.30, 150K TPM / 1K RPM, 131,072 / 65,536 |
| `qwen/qwen3.6-27b` | all | — | unchanged | 500 t/s, $0.60/$3.00, 250K TPM / 1K RPM, 131,072 / 16,384, 20 MB |

Catalog-level changes: `captured_at` 2026-08-07 → 2026-08-18, `review_after`
2026-08-14 → 2026-09-17, all fifteen `sources[].captured_at` → 2026-08-18, and
four new `global_notes` keys (`retired`, `undocumented_runtime_models`,
`stale_capability_pages`, plus a rewritten `active_models_api`).

Capability facts re-read and unchanged: strict `json_schema` still limited to
GPT-OSS 20B/120B with Safeguard 20B on best-effort; `reasoning_format`
unsupported on GPT-OSS 20B/120B with `include_reasoning` as their control;
`reasoning_effort` `none`/`default` on Qwen 3.6 and `low`/`medium`/`high` on
GPT-OSS; Qwen 3.6 the only vision model at 5 images and 20 MB; prompt caching
on the three GPT-OSS models at a 50% cached-input discount with 2-hour
expiry; Responses API still labelled beta; Flex still 10x limits at on-demand
price with HTTP 498 on capacity; Batch still 50% off over a 24-hour to 7-day
window and still non-stacking with cache discounts.

## Two things the previous snapshot could not have known

**Groq's tool-use page is stale.** It still lists
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` in its capability matrix
two days after they stopped serving. The catalog's existing
`source_precedence` (`deprecations` > `capability_specific_docs` > `models`)
already resolves this correctly, so no schema change was needed — but the
conflict is now recorded in `global_notes.stale_capability_pages` so the next
reader does not re-litigate it.

**`allam-2-7b` is served but undocumented.** The runtime listing returned it
(owner SDAIA, 4,096 context, `json_object` only, no pricing block). No page
under `console.groq.com/docs` describes it — not models, not deprecations, not
the changelog. It is excluded from `models[]` and recorded in
`global_notes.undocumented_runtime_models` instead, because adding it would
mean publishing a lifecycle and price this refresh cannot source.

## Sources consulted

Documentation, all fetched 2026-08-18:

- https://console.groq.com/docs/models
- https://console.groq.com/docs/deprecations
- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/tool-use/overview
- https://console.groq.com/docs/reasoning
- https://console.groq.com/docs/responses-api
- https://console.groq.com/docs/vision
- https://console.groq.com/docs/speech-to-text
- https://console.groq.com/docs/text-to-speech
- https://console.groq.com/docs/rate-limits
- https://console.groq.com/docs/prompt-caching
- https://console.groq.com/docs/flex-processing
- https://console.groq.com/docs/batch
- https://console.groq.com/docs/changelog
- https://console.groq.com/docs/content-moderation
- https://groq.com/pricing

Runtime check, once, 2026-08-18: `GET https://api.groq.com/openai/v1/models`
with a Developer-plan key, per step 2 of the guide's own freshness protocol.
Returned 13 active IDs. No key and no response header was recorded. Its
pricing and context fields matched the models page exactly for all twelve
documented IDs it returned, which is the strongest cross-check available here:
two independent Groq surfaces agreeing.

## Not verified

**`https://groq.com/pricing` renders client-side.** The fetched HTML contains
no model names or prices, so the pricing cross-check came from the models page
and the runtime endpoint rather than the marketing pricing page. Both are
first-party and they agree, so the pricing values are verified — just not from
the page you would expect.

**Enterprise-only availability.** The runtime key is Developer-plan, so it
cannot see Enterprise models. `minimaxai/minimax-m2.7` did not appear in the
listing; that is expected for a non-Enterprise key and is *not* evidence of
removal — the models page still shows it with an Enterprise badge. By the same
limit, whether the retired Llama models remain reachable for Enterprise
committed-spend contracts (which Groq's notice explicitly carves out) could not
be tested and is marked as such in the guide.

**Two Enterprise models announced in April 2026 have no current status.** The
changelog's 2026-04-18 entry added `minimaxai/minimax-m2.5` and
`qwen/qwen3-vl-32b-instruct` for Enterprise customers. Neither appears on the
models page today, neither appears on the deprecations page, and the changelog
has no entry after 2026-04-18 — it is itself stale, since it never records the
Qwen 3.6 or MiniMax M2.7 additions that the models page shows. Whether the
April models were superseded or merely delisted from public docs is
undetermined. They are not added to the catalog.

**`allam-2-7b` lifecycle and price.** Runtime-visible, documentation-silent;
see above.

## Why 2026-09-17

The guide's own rule sets the interval: 30 days for a stable catalog, 7 days
while a deprecation or preview promotion is pending, immediate before a
billing or capacity decision. Applying it:

- Nothing is pending. The deprecation page's newest entry is the 2026-08-16
  retirement, which has now executed. The previous 7-day window existed only
  because that shutdown was in flight; that reason is spent.
- The stable 30-day interval therefore applies: 2026-08-18 + 30 days =
  **2026-09-17**.
- Do not stretch it further. Seven of the thirteen live models are preview, and
  Groq documents that preview models may be discontinued at short notice. The
  observed swap rate supports that caution — MiniMax M2.5 and Qwen3-VL-32B were
  announced in April and are already off the models page, replaced by MiniMax
  M2.7 and Qwen 3.6 27B.

The date is a ceiling, not a schedule. A new deprecation email, or a workload
taking a dependency on a preview model, should trigger a refresh immediately.

## Test conflict this refresh exposes

Two assertions in `tests/test_groq_provider_catalog.py` pin the *contents* of
the 2026-08-07 capture rather than an invariant, so any truthful refresh
breaks them. They were left untouched — weakening or rewriting a test to match
new data is not this refresh's call to make. See the report accompanying this
document for the exact failures and the proposed replacements.

- `test_catalog_has_dated_primary_source_provenance` asserts
  `captured_at == "2026-08-07"`. A refresh must advance `captured_at`, so this
  assertion can never survive one. The invariant it was presumably written to
  protect — provenance exists, is dated, is T1, and is single-vendor — is
  better expressed as "`captured_at` parses as a date and is not in the
  future."
- `test_deprecation_schedule_overrides_models_page_badge` asserts
  `lifecycle == "production_deprecated"` for both Llama models. That was the
  right assertion while the models page and the deprecation page disagreed. The
  disagreement is over: both pages now agree the models are gone. The assertion
  should become `lifecycle == "retired"` with the `shutdown_at`, `plans`, and
  `replacements` checks kept as-is, since the migration record is what still
  earns those entries their place in the catalog.
