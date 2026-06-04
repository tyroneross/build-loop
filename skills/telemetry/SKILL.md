---
name: telemetry
description: Use when adding observability/telemetry to an app, instrumenting traces/metrics/logs, wiring LLM/agent tracing, choosing a monitoring vendor, or auditing an app for missing telemetry. Triggers — "add telemetry", "add observability", "instrument this", "tracing", "OpenTelemetry/OTel", "LLM observability", "LangChain tracing", "Phoenix/Langfuse/LangSmith", "Crashlytics/Sentry/mobile telemetry", "why is this slow in prod", "no visibility into". OpenTelemetry-first, vendor-neutral.
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Telemetry — OpenTelemetry-first, vendor-neutral

**The rule:** default to **OpenTelemetry (OTel)**. Instrument with OTel SDKs + **GenAI semantic conventions**; export OTLP to whatever backend fits. Never lock telemetry to a single proprietary vendor, and **never roll your own tracing schema** — OTel conventions exist. This codifies the user's already-decided stack (`prefer-opentelemetry`); it is a default, not dogma — document an exception when a closed tool genuinely wins.

Canonical working exemplar in-tree: **`local-smartz/src/localsmartz/observability.py`** (OTLP/HTTP → Phoenix, env-gated `LOCALSMARTZ_OBSERVE=1`, fails silent). Copy this pattern; don't reinvent it.

## Decision tree — pick the stack by app type

| App type | Instrument with | Dev backend | Prod backend | Error tracking |
|---|---|---|---|---|
| **LLM / agent** (LangChain, LangGraph, raw SDK) | `openinference-instrumentation-langchain` **or** `opentelemetry-instrumentation-langchain` (OpenLLMetry) + OTel SDK | **Arize Phoenix** (Apache-2.0, OTLP-native, local eval UI) | **Langfuse** (self-host, OTLP `/api/public/otel`, evals + replay) | Sentry |
| **Web / server** (Node, Python) | `@opentelemetry/sdk-node` (JS SDK 2.0, stable) / `opentelemetry-sdk` (Python) + auto-instrumentations | OTel Collector → any | Grafana/SigNoz/Honeycomb/Datadog via OTLP | **Sentry** (the record for deployed web apps) |
| **Mobile / iOS** (SwiftUI) | **Embrace Apple SDK** (Apache-2.0, built on `opentelemetry-swift`; adds crash/ANR/session/UI that bare OTel-Swift lacks) | — | any OTLP backend (Grafana/SigNoz/Honeycomb) | Embrace or Sentry-cocoa (MIT) |

**LLM tracing — three paths, in preference order:** (A) **OpenLLMetry** `opentelemetry-instrumentation-langchain` — `LangchainInstrumentor().instrument()`, vendor-neutral OTLP; (B) **OpenInference** `openinference-instrumentation-langchain` — same monkey-patch model, tight Phoenix integration; (C) **LangSmith** — opt-in only (proprietary backend; has OTLP export but canonical storage stays LangSmith). LangChain does **not** emit OTel natively as of 2026, so an instrumentation lib is required. LangSmith stays behind a flag — "no paid posture."

**Mobile — the Firebase tradeoff (stated plainly):** Firebase's iOS SDK is Apache-2.0, but **Crashlytics/Performance emit no OTLP** — data is trapped in the Firebase console (or BigQuery export). For OTel portability Firebase Performance is a dead end; Crashlytics is fine for triage only. Prefer Embrace (OTel-compatible) when portability/reliability matter. Note: CocoaPods support for firebase-ios-sdk ends Oct 2026 (SPM only).

## Minimal wire-up (Python LLM app)

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(
    OTLPSpanExporter(endpoint="http://otel-collector:4317")))  # swap endpoint = swap backend
LangchainInstrumentor().instrument(tracer_provider=provider)
```

Use GenAI semconv attributes (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`) so the data is backend-agnostic. Gate observability behind an env flag, default-off on cheap tiers (OTel + batch processor adds ~10–50 ms/call). Probe the backend at startup and **warn without blocking** if it's down (fail-silent, like the local-smartz exemplar).

## Status (2026, cite before relying — these move)

- OTel **GenAI client spans stable** (early 2026); agent/framework spans experimental-but-stable-in-practice. JS SDK 2.0 stable; Python SDK stable.
- Phoenix, Langfuse, OpenLLMetry, OpenInference all OTLP-compatible and OSS.
- Embrace Kotlin SDK donated to OTel (CNCF, Mar 2026, KMP — Android-first); Embrace **Apple** SDK stays in Embrace's repo (Apache-2.0) but is built on OTel primitives.

## Instrumentation gaps in this user's repos (audit 2026-05-31)

Already instrumented (reference these): **local-smartz** (OTel+Phoenix+OpenInference, full), **atomize/atomize-ai** (OTel full + Sentry), **infisical** (OTel metrics).

**Gaps worth closing first** — LLM/agent apps with zero telemetry:
1. **market-research-platform** — LangChain + LangGraph, nothing. Drop in the local-smartz pattern (~10 lines) for instant span/token visibility.
2. **stratagem** — has LangSmith dep but no OTel plumbing; unify with the OTel-first stack.
3. **agent-builder** — LangGraph, no telemetry.

Partial (error-only, no traces): ProductPilot, decision-doctor-cc, prompt-test-lab (Sentry only). **SpeakSavvy-iOS** — no telemetry; candidate for Embrace Apple SDK.

## When build-loop fires this skill

Phase 1 Assess: if the build touches a server/LLM/mobile app with no telemetry, flag the gap. Phase 2 Plan: if the build adds a new service, LLM call path, or user-facing mobile flow, include an OTel instrumentation step (default-off env flag). Phase 4 Review: a new LLM call path shipped without span coverage is an observability gap to surface, not a blocker.

## Sources

OTel GenAI semconv: opentelemetry.io/docs/specs/semconv/gen-ai/ · JS SDK 2.0: opentelemetry.io/blog/2025/otel-js-sdk-2-0/ · OpenLLMetry: github.com/traceloop/openllmetry · OpenInference: github.com/Arize-ai/openinference · Phoenix: arize.com/docs/phoenix · Langfuse OTel: langfuse.com/integrations/native/opentelemetry · Embrace Apple: github.com/embrace-io/embrace-apple-sdk · opentelemetry-swift: github.com/open-telemetry/opentelemetry-swift · Sentry OTLP: docs.sentry.io/concepts/otlp/ · Firebase: firebase.google.com/docs/crashlytics, /docs/perf-mon. User's prior decision: ~/dev/research/inbox/2026-04-23-stratagem-local-first-multi-agent-architecture.md.
