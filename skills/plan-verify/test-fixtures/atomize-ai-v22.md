# Atomize AI — Unified AI Search Architecture (Full Plan)

**Version:** 2.2 — SOTA research integrated (RRF, cross-encoder reranker, CRAG, multi-query, HippoRAG)
**Last updated:** 2026-04-28
**Working directory:** `/Users/tyroneross/dev/git-folder/atomize-ai/`
**Provider priority:** Accuracy first → speed → cost. Stay on Groq throughout (cheaper + faster than OpenAI/Anthropic for comparable workloads, ~5–10× cost advantage, 3–12× speed advantage). **Pick the right Groq model per task** — strict-schema work (Tier 3 intent classifier) uses `openai/gpt-oss-120b` (constrained decoding, 100% schema-valid); synthesis uses Llama 3.3 70B; fast-path classification uses Llama 3.1 8B. Cross-provider swaps (OpenAI Structured Outputs, dedicated rerankers like Zerank-2 / Voyage 2.5 / Jina v3) stay deferred to a final optimization phase **only after the architecture is working end-to-end**, per user direction. Per-call `max_tokens` and per-run `budget` set in the provider-config block; new LLM calls inherit that ceiling. **Threat model:** PII handling (§13.2) covered by hash-only-store control; new tier-2 embed call mapped to OWASP LLM01 / LLM06 review at top of §11. See `security-methodology` skill for the full OWASP / ASI ID matrix.

> **Codex review applied 2026-04-28** — five errors corrected, scope tightened to Milestone 1 (format-intent + structured-results) merge-able alone, Milestone 2 (consolidations) explicitly deferred. Full change log at §11.
> **SOTA research applied 2026-04-28 (v2.2)** — `/build-loop:research` validated the plan's architectural shape as canonical, but surfaced three implementation-layer upgrades that materially lift quality at modest cost: Reciprocal Rank Fusion (RRF) in hybrid retrieval, a dedicated cross-encoder reranker (replacing LLM-as-reranker), and a CRAG-style self-correction gate. Full change log at §13.

---

## Intent (plain language — read this first)

**What this plan is.** A blueprint for fixing one specific user-visible bug — the AI search silently throws away format instructions like "share this in a table" — by addressing the root architectural cause rather than papering over the symptom.

**Why the small bug needs a big plan.** The format-drop happens because three separate parts of the codebase each have their own opinion about "what is the user asking for", none of them agree on a schema, and the synthesis prompt is contractually forbidden from emitting anything but markdown. Adding `if (query.includes("table"))` somewhere only delays the next dropped intent. A typed pipeline that runs the same shape end-to-end is the actual fix.

**What changes.** The application layer (Vercel API routes, frontend search components, intent and synthesis libraries). One new front door at `/api/search`, one typed `Intent`, one deterministic planner that fans out to the three RAG patterns the codebase already implements (Pipeline / Agentic / Knowledge Graph), one normalized response shape that includes table/chart/timeline/graph/unsupported-format as first-class kinds, one consolidated frontend `SearchBar` and `SearchResults` shell.

**What does not change.** Postgres schema, Railway worker topology, Vercel cron schedules, Redis cache discipline, all 40+ Prisma models, RSS ingest pipeline, KG extraction pipeline. Zero infra/deployment changes.

**How to ship safely.** Two milestones. Milestone 1 (~5 days) is merge-able alone and fixes the visible bug end-to-end while leaving every existing route untouched (new pipeline runs in parallel behind a flag). Milestone 2 (~4 days) does the cleanups — orphan deletions, route consolidation, frontend de-duplication — only after Milestone 1 has soaked in production.

**For another agent picking this up.** Read §0 for the executive frame, §1 for current state, §2 for target state, §5 for the ordered action list, and §11 for what was already wrong and corrected. Each section below opens with its own "Intent of this section" line so you can navigate without reading linearly. Status markers (✅ verified · ⚠️ untested · ❓ uncertain · TAG:INFERRED) are honest about confidence.

**Working assumptions baked in.**
- The two failing queries (`"…share this in a table"` and `"create a tornado diagram of recent news trends"`) are representative, not edge cases. Other format directives are dropped by the same mechanism.
- Postgres has all the data needed; the gap is at the application layer.
- Existing tests, eval harness, and circuit-breaker patterns can be extended rather than replaced.
- Caller maps for ambiguous routes must be verified by template-literal-aware grep, not just NavGator (NavGator misses template-literal `fetch()` calls — see §11.2).

---

---

## 0. Executive Summary

> **Intent of this section:** Hand a stakeholder enough context in one screen to decide whether to greenlight the work. Names the bug, names the root cause, names the cost, names the milestone split.

Two real user queries silently dropped their format directives:

| Query | Format requested | What rendered |
|---|---|---|
| "What's the latest AI trends for research and product releases. **share this in a table**" | table | Markdown pyramid only |
| "**create a tornado diagram** of recent news trends" | tornado diagram | Markdown pyramid only |

The visible bug is "format intent silently dropped." Investigation surfaced a much larger architectural problem:

- **60+ overlapping search/retrieval/synthesis API routes** on Vercel
- **Regex intent detection scattered across three places**, none authoritative
- **Hard partitioning** of general / research / KG / trending data with no server-side fan-out
- **19+ search components** in the frontend (V3, V7D, base, modal variants)
- **Two of six "orphan" routes are actually high-value engineering** that was abandoned mid-migration (HyDE expansion, SSE streaming) — *trending-topics-v2 was misclassified as orphan; it has 3 live callers*
- **An eval harness and cascade scaffolding already exist** — just disconnected

**This plan unifies everything** behind one front door, a 3-tier intent cascade, a deterministic query planner that fans out to Pipeline / Agentic / KG RAG patterns, and a normalized response shape that treats format (`table`, `chart`, `unsupported-format`) as a first-class field.

### Two-milestone delivery

**Milestone 1 — Format intent + structured results + retrieval-quality lift (merge-able alone)** · ~8 days
The high-value, low-risk slice. Ships the visible bug fix end-to-end without touching live routes, **and** lifts retrieval quality with three SOTA additions (v2.2):
- Eval harness incl. RAGAS metrics (P0), 3-tier intent cascade in shadow mode with **OpenAI Structured Outputs for Tier 3** (P1-P2), structured-result kinds + UI (P3), planner extraction with **RRF hybrid retrieval + cross-encoder reranker + CRAG quality gate** (P4), `/api/search` orchestrator behind a flag (P5), prompt governance (P8 partial), telemetry (P9 partial).
- Old routes keep working; new pipeline runs in parallel.

**Milestone 2 — Consolidations, cleanups, and graph quality (defer until M1 has soaked)** · ~6 days
Higher coordination cost, more caller-map work, plus deeper retrieval upgrades:
- **Multi-query / RAG-Fusion as primary rewrite** (P5b extended), HyDE remains as vague-query fallback. SSE recovery (P5c), trending v1↔v2 reconciliation (P5d), orphan deletes (P6), trend-dict extract (P6b), Tier 2 embedding classifier (P7), full prompt-builder governance (P8), full telemetry (P9), **HippoRAG-style PPR over `entity_pairs` for KG-RAG (P5e)**.
- Each step gated by a strict caller-map cross-check (NavGator misses template literals — see §11.2).

**Total focused engineering: ~14 days** across 12 phases, every phase reversible behind a feature flag, zero infra/deployment changes required for cutover. (Up from ~9 days in v2.1; the +5 days buys substantially higher retrieval quality and removes the single-vendor dependency on Groq 70B for reranking.)

---

## 1. BEFORE — Current Architecture

> **Intent of this section:** Show the full system as it exists today so a new reader can orient before any change is proposed. Walks Frontend → Vercel functions → Postgres / Redis / Railway, then enumerates the 12 verified pain points with file:line evidence. If you only need to know "what's there now", read this section and stop.

### 1.1 Layer overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Vercel Edge / CDN)                  │
│                                                                      │
│   19+ search components, 5+ search pages, 4+ overlapping versions    │
│                                                                      │
│   IntelligentSearchBar.tsx ──┐                                       │
│   V7DSearchBar.tsx           ├─► fetch('/api/intelligent-search')    │
│   V3SearchInput.tsx          │   fetch('/api/search')                │
│   SearchModalNew.tsx ────────┤   (also '/api/intelligent-search')    │
│   SearchModal.tsx            │   fetch('/api/trending-topics')       │
│   GlobalSearchBar.tsx        │   fetch('/api/trending-topics-v2')    │
│   SearchBottomSheet.tsx      │   ... 60+ endpoints                   │
│   ArticleSearchPopout.tsx ───┘                                       │
│   FloatingSearchPill.tsx (KG)                                        │
│   EntitySearch.tsx (KG) ────────► fetch('/api/kg/entities/search')   │
│   KGSearchInterface.tsx (KG)                                         │
│   EntityRelationshipPanel (KG)                                       │
│                                                                      │
│   Pages mounting search:                                             │
│   /search, /markettrends, /digest, /graph, /admin, /versions         │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    VERCEL FUNCTIONS (Next.js App Router)             │
│                    maxDuration: 60s · 11 cron jobs                   │
│                                                                      │
│   ┌──────────────────────┐     ┌──────────────────────┐              │
│   │ /api/intelligent-    │     │ /api/search          │              │
│   │   search/route.ts    │     │   route.ts           │              │
│   │   (1582 lines)       │     │   (399 lines)        │              │
│   │                      │     │                      │              │
│   │ Inline regex intent  │     │ Keyword + cache      │              │
│   │ at lines 1170-1181   │     │ (no intent layer)    │              │
│   └──────────┬───────────┘     └──────────┬───────────┘              │
│              │                            │                          │
│              ▼                            ▼                          │
│   ┌──────────────────────────────────────────────┐                   │
│   │  lib/search/query-router.ts                  │                   │
│   │  ⚠ Cascade pattern half-built. Used ONLY by │                   │
│   │  intelligent-search. Other routes bypass.    │                   │
│   └──────────────────────────────────────────────┘                   │
│              │                                                        │
│              ▼                                                        │
│   ┌──────────────────────────────────────────────┐                   │
│   │  lib/knowledge-graph/                        │                   │
│   │   intelligent-query-engine.ts                │                   │
│   │  Workhorse — reads 8 DB tables, hides        │                   │
│   │  planner logic inside retrieval class        │                   │
│   └──────────────────────────────────────────────┘                   │
│                                                                      │
│   Other live routes (selected):                                      │
│   /api/trending-topics, /api/trending-topics-simple,                 │
│   /api/trending-topics-v2 (3 callers — sibling, not orphan)          │
│   /api/research/papers, /api/research/trending                       │
│   /api/graph, /api/graph/trends, /api/graph/analytics                │
│   /api/entities/search       (general entity search)                 │
│   /api/kg/entities/search    (KG-UI specific, 3 callers — KEEP)      │
│   /api/comprehensive-summary, /api/executive-summary,                │
│     /api/brief, /api/articles/smart-summary, /api/summarize          │
│     (8+ overlapping summarizers)                                     │
│                                                                      │
│   Orphans (zero callers — verified by template-literal-aware grep):  │
│   /api/search/semantic ⭐ HyDE expansion (rescue)                    │
│   /api/search/stream   ⭐ SSE progressive (rescue)                   │
│   /api/intelligence    ⚠ Math.random() fake numerics (mostly kill)  │
│   /api/ai-summarize    🗑 Superseded                                 │
│   /api/test-summary    🗑 Test scaffold                              │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
┌─────────────────────┐  ┌──────────────────┐  ┌─────────────────────┐
│  POSTGRES           │  │   REDIS          │  │  RAILWAY WORKERS    │
│  (Supabase / RDS)   │  │   (Upstash KV)   │  │  4 services         │
│                     │  │                  │  │                     │
│  40+ Prisma models  │  │ TrendingCache    │  │  bullmq-worker      │
│  Article            │  │ temporal-cache   │  │  → entities,        │
│  Summary            │  │ request_cache    │  │    relationships,   │
│  ArticleEmbedding   │  │ 5-min TTLs       │  │    embeddings,      │
│   (pgvector)        │  │                  │  │    summaries        │
│  entities           │  │ lib/redis-       │  │                     │
│  entity_pairs       │  │   connection.ts  │  │  clustering-worker  │
│  entity_mentions    │  │                  │  │  → article-clust.   │
│  trending_event_    │  │                  │  │  + kg-extraction    │
│   clusters          │  │                  │  │                     │
│  cluster_trend_     │  │                  │  │  trending-worker    │
│   analysis          │  │                  │  │  → trending-default │
│  topic_clusters     │  │                  │  │                     │
│  RssSource          │  │                  │  │  scraper-worker     │
│  source_credibility │  │                  │  │  → scraper-pipeline │
│  burst_signals      │  │                  │  │                     │
│  ...                │  │                  │  │  Self-timer DISABLED│
│                     │  │                  │  │  Vercel cron        │
│  Materialized view: │  │                  │  │  /api/cron/detect-  │
│   mv_active_        │  │                  │  │   trends is sole    │
│   trending_topics   │  │                  │  │   trigger (every 6h)│
│   (refreshed 30min  │  │                  │  │                     │
│   via cron)         │  │                  │  │                     │
└─────────────────────┘  └──────────────────┘  └─────────────────────┘
```

### 1.2 Pain points (verified, file:line citations)

| # | Layer | Symptom | Evidence |
|---|---|---|---|
| 1 | Vercel API | Format intent dropped | `app/api/intelligent-search/route.ts:1170-1181` regex has no `table\|chart\|tornado` patterns |
| 2 | Vercel API | Two parallel search routes | Both `/api/intelligent-search` and `/api/search` actively wired; no hierarchy |
| 3 | Vercel API | 5 confirmed orphan routes (template-literal-aware grep) | 0 callers: `intelligence`, `ai-summarize`, `test-summary`, `search/semantic`, `search/stream`. Two of them — `search/semantic` (HyDE) and `search/stream` (SSE) — are valuable enough to recover. |
| 4 | Vercel API | 8+ overlapping summarizers | `comprehensive-summary`, `executive-summary`, `executive-summary-optimized`, `brief`, `brief/clusters/[id]/synthesize`, `articles/smart-summary`, `summarize`, `intelligent-search/summary` |
| 5 | Vercel API | 4 overlapping trending endpoints | `trending-topics`, `trending-topics-simple`, `graph/trends`, `kg/trending`, `research/trending` |
| 6 | Vercel API | Two entity search routes serve different consumers | `/api/entities/search` (general) and `/api/kg/entities/search` (KG UI: 3 callers in `KGSearchInterface.tsx:103`, `EntitySearch.tsx:127`, `EntityRelationshipPanel.tsx:412`). Not duplicates — keep both, share lib. |
| 7 | lib | Intent detection scattered | inline regex + `lib/search/query-router.ts` + `lib/knowledge-graph/intelligent-query-engine.ts:80+` |
| 8 | lib | Synthesis prompt is markdown-only | `app/api/intelligent-search/summary/route.ts` — `OUTPUT FORMAT: valid markdown only` |
| 9 | lib | Hard domain partition | `entities.domain = 'general' \| 'research'`; client picks upfront, no fan-out |
| 10 | Frontend | 19+ overlapping search components | V3/V7D/base/modal variants without a single source-of-truth bar |
| 11 | Orphans | `intelligence/route.ts:87` `Math.random()` | Generates fake stock-impact numbers, violates `feedback_no_fake_stats.md` |
| 12 | Orphans | HyDE + SSE built, never wired into the UI | Recoverable in Milestone 2 (`search/semantic` + `search/stream`). Multi-mode trending v2 is *not* an orphan — it has 3 live callers; reconciliation is a separate concern. |

---

## 2. AFTER — Target Architecture

> **Intent of this section:** Show the same system after the change so the diff to §1 is visible at a glance. The shape is: one front door → typed Intent via 3-tier cascade → deterministic planner → fan-out to three RAG patterns → normalized result kinds → format-aware synthesis. Postgres / Redis / Railway / cron stay structurally identical. Read this with §1 open in a split view.

### 2.1 Layer overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Vercel Edge / CDN)                  │
│                                                                      │
│   ONE canonical search bar component:                                │
│     components/search/SearchBar.tsx                                  │
│     (consolidated from IntelligentSearchBar + V7D + V3 variants)     │
│                                                                      │
│   ONE results renderer:                                              │
│     components/search/SearchResults.tsx                              │
│     ├─ <PyramidSummary />     (markdown narration)                   │
│     ├─ <ResultTable />        (NEW — tabular structured)             │
│     ├─ <ResultChart />        (NEW — bar/timeline/ranked-bar)        │
│     ├─ <ResultGraph />        (existing KG viz)                      │
│     └─ <UnsupportedFormat />  (NEW — honest downgrade)               │
│                                                                      │
│   Subscriber for SSE progressive results:                            │
│     hooks/useSearchStream.ts (NEW — wraps EventSource)               │
│                                                                      │
│   Page-specific embeds keep their wrappers but call SearchBar:       │
│     /search, /markettrends, /digest, /graph                          │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ POST { query, hints?, sessionId? }
                                  ▼ or GET ?stream=true (SSE)
┌──────────────────────────────────────────────────────────────────────┐
│                    VERCEL FUNCTIONS — ONE FRONT DOOR                 │
│                                                                      │
│   ┌──────────────────────────────────────────────────┐               │
│   │  app/api/search/route.ts (NEW — supersedes both) │               │
│   │  Shared handler: /api/intelligent-search is a   │               │
│   │  thin shim exporting the same runSearch fn —    │               │
│   │  no redirect, no URL change, both routes alive  │               │
│   └──────────────────────┬───────────────────────────┘               │
│                          ▼                                            │
│   ┌──────────────────────────────────────────────────┐               │
│   │             3-TIER INTENT CASCADE                │               │
│   │  lib/search/intent/                              │               │
│   │   ├─ tier1-regex.ts     5-20ms,  conf ≥ 0.85    │               │
│   │   ├─ tier2-embed.ts    20-50ms,  conf ≥ 0.80    │               │
│   │   └─ tier3-llm.ts     150-600ms, terminal       │               │
│   │                                                  │               │
│   │  Output: typed Intent {                          │               │
│   │    domains: ('articles'|'releases'|'research'    │               │
│   │              |'kg')[],                           │               │
│   │    retrievalPattern: 'pipeline'|'agentic'        │               │
│   │              |'kg'|'hybrid',                     │               │
│   │    format: 'default'|'table'|'chart'|'timeline'  │               │
│   │              |'graph',                           │               │
│   │    chartSubtype?: 'bar'|'ranked-bar'|'tornado',  │               │
│   │    horizonDays, comparisonTargets,               │               │
│   │    topicKeywords, confidence, tier               │               │
│   │  }                                               │               │
│   └──────────────────────┬───────────────────────────┘               │
│                          ▼                                            │
│   ┌──────────────────────────────────────────────────┐               │
│   │  lib/search/planner.ts (NEW — extracted from    │               │
│   │   intelligent-query-engine.ts)                   │               │
│   │  Pure deterministic function:                    │               │
│   │    planRetrieval(intent) → RetrievalPlan         │               │
│   └─────┬───────────┬───────────┬────────────┬───────┘               │
│         ▼           ▼           ▼            ▼                        │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐          │
│   │ PIPELINE │  │ AGENTIC  │  │   KG     │  │  HYDE       │          │
│   │   RAG    │  │   RAG    │  │   RAG    │  │  EXPANSION  │          │
│   │ articles │  │ multi-   │  │ entity   │  │ (RECOVERED) │          │
│   │ +releases│  │ source   │  │ graph,   │  │ for vague   │          │
│   │ +trending│  │ fan-out  │  │ papers,  │  │ queries     │          │
│   │          │  │ +merge   │  │ relations│  │             │          │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬──────┘          │
│        └─────────────┼─────────────┼───────────────┘                  │
│                      ▼             ▼                                  │
│   ┌──────────────────────────────────────────────────┐               │
│   │  lib/search/normalize.ts (NEW)                   │               │
│   │  Builds StructuredResult[]:                      │               │
│   │   { kind:'table',  columns, rows, source }       │               │
│   │   { kind:'chart',  subtype, series, source }     │               │
│   │   { kind:'timeline', events, source }            │               │
│   │   { kind:'graph', nodes, edges, source }         │               │
│   │   { kind:'unsupported-format',                   │               │
│   │     requested, fallbackKind, reason }            │               │
│   └──────────────────────┬───────────────────────────┘               │
│                          ▼                                            │
│   ┌──────────────────────────────────────────────────┐               │
│   │  lib/search/synthesizer.ts                       │               │
│   │  (refactored from /summary/route.ts)             │               │
│   │  Format-aware: 1-paragraph when tables present,  │               │
│   │  full pyramid when format=default                │               │
│   │  Reads prompts from prompt library, NOT inline   │               │
│   └──────────────────────┬───────────────────────────┘               │
│                          ▼                                            │
│                  Response (JSON or SSE chunks)                       │
│                                                                      │
│   Other API routes (UNCHANGED, just deduped):                        │
│   - /api/research/* (kept as KG RAG implementation detail)           │
│   - /api/graph/* (kept as KG visualization)                          │
│   - /api/trending-topics (consolidated with v2 features)             │
│   - /api/cron/* (unchanged — Vercel cron triggers Railway workers)   │
│                                                                      │
│   Milestone 2 deletes (5 zero-caller routes only, AFTER recovery):   │
│     intelligence, ai-summarize, test-summary,                        │
│     search/semantic (logic moved to lib), search/stream (logic       │
│     moved into ?stream=true branch).                                 │
│   Reconciled (M2): trending-topics ← backport v2 features, then      │
│     migrate v2's 3 callers, then deprecate v2.                       │
│   KEPT (Codex correction): kg/entities/search (3 KG-UI callers).     │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
┌─────────────────────┐  ┌──────────────────┐  ┌─────────────────────┐
│  POSTGRES           │  │   REDIS          │  │  RAILWAY WORKERS    │
│  (UNCHANGED)        │  │   (extended)     │  │  (UNCHANGED)        │
│                     │  │                  │  │                     │
│  Same 40+ models    │  │ Existing:        │  │  Same 4 services:   │
│                     │  │   TrendingCache  │  │   bullmq-worker     │
│  Same materialized  │  │   temporal-cache │  │   clustering-worker │
│  view, same         │  │   request_cache  │  │   trending-worker   │
│  indexes, same      │  │                  │  │   scraper-worker    │
│  pgvector           │  │ NEW caches:      │  │                     │
│                     │  │   intent-cache   │  │  Same queue topology│
│  Schema changes:    │  │   (LRU 1k, 24h)  │  │  Same KG write paths│
│  ZERO required      │  │   intent-prompt  │  │  Same self-timer    │
│                     │  │   embedding-     │  │  disable             │
│  Optional addition: │  │   cache (T2)     │  │                     │
│   intent_log table  │  │                  │  │                     │
│   for telemetry     │  │ Same 5-min TTLs  │  │                     │
│   (Phase 9, not     │  │                  │  │                     │
│   blocking)         │  │ Same Upstash KV  │  │                     │
└─────────────────────┘  └──────────────────┘  └─────────────────────┘
```

### 2.2 Architectural principles

1. **One front door** — `/api/search` is the sole search entry. Aliases keep backward-compat during cutover.
2. **Cascade for intent, planner for retrieval** — typed intent fans out deterministically to one of three RAG patterns.
3. **Format is a first-class field** — table / chart / timeline / graph / unsupported-format are normalized response shapes, not afterthoughts.
4. **Synthesis narrates, never fabricates** — when format isn't default, LLM writes 1-paragraph narration; rows/series come from Postgres.
5. **Honest downgrade over silent failure** — if user asks for "tornado diagram" and the data can't honestly support it, return `kind:'unsupported-format'` with a reason and a substituted ranked-bar chart.
6. **Reversible cutover** — every phase ships behind a feature flag, every alias is keep-able indefinitely.
7. **No infra change required** — Postgres schema, Railway topology, Vercel cron, Redis cache layer all stay as-is.

---

## 3. Layer-by-Layer Change Matrix

> **Intent of this section:** A scannable accountability table — for every component in the stack, state explicitly whether it changes, moves, gets added, or stays put, with a one-line reason. Anyone asking "does my layer get touched?" should be able to find the answer here in under thirty seconds. Particularly useful for ops/infra reviewers who only care about Postgres / Railway / cron impact (answer: ~none).

| Layer | Component | Action | Why |
|---|---|---|---|
| **Database (Postgres)** | All 40+ models | ✅ NO CHANGE | Schema is fine; the pain is at the application layer |
| **Database (Postgres)** | `mv_active_trending_topics` materialized view | ✅ NO CHANGE | Already refreshed every 30min via cron |
| **Database (Postgres)** | `request_cache` table | ✅ NO CHANGE | Stays as-is; Redis is primary cache |
| **Database (Postgres)** | NEW: optional `intent_log` table | ➕ OPTIONAL ADD (Phase 9) | Telemetry only; not required for cutover |
| **Cache (Upstash Redis)** | `lib/redis-connection.ts` | ✅ NO CHANGE | Singleton manager keeps working |
| **Cache (Upstash Redis)** | `lib/cache/trending-cache.ts` | ✅ NO CHANGE | Used by trending route post-consolidation |
| **Cache (Upstash Redis)** | `lib/cache/temporal-analysis-cache.ts` | ✅ NO CHANGE | Used by temporal mode |
| **Cache (Upstash Redis)** | NEW: intent classifier LRU | ➕ ADD | New file `lib/search/intent/intent-cache.ts`. Same Redis instance, 24h TTL |
| **Cache (Upstash Redis)** | NEW: prototype-embedding cache (T2) | ➕ ADD | One-time precompute, stored under `search:embed:proto:*` |
| **Railway: bullmq-worker** | Queues entities, relationships, embeddings, summaries | ✅ NO CHANGE | Continues current behavior |
| **Railway: clustering-worker** | article-clustering + kg-extraction co-located | ✅ NO CHANGE | KG writes flow same path |
| **Railway: trending-worker** | trending-default queue | ✅ NO CHANGE | Self-timer still disabled; Vercel cron still triggers |
| **Railway: scraper-worker** | scraper-pipeline (concurrency 20) | ✅ NO CHANGE | RSS ingest unchanged |
| **Vercel: cron jobs** | All 11 cron entries | ✅ NO CHANGE | Same cadence, same paths |
| **Vercel: API — search front door** | `/api/intelligent-search` (1582 lines) | 🔄 SHARED-HANDLER SHIM → both routes call the same orchestrator function (no 308; method/headers preserved) | One front door, no client breakage |
| **Vercel: API — search front door** | `/api/search` (399 lines) | 🔄 REPLACE with new orchestrator | Becomes the entry point |
| **Vercel: API — intent layer** | inline regex (1170-1181) | ➖ REMOVE | Subsumed by cascade |
| **Vercel: API — intent layer** | `lib/search/query-router.ts` | 🔄 PROMOTE & EXTEND | Already test-covered; add `format`, `domains` fields |
| **Vercel: API — planner** | logic embedded in `intelligent-query-engine.ts` | ➡️ EXTRACT to `lib/search/planner.ts` | Pure function, debuggable |
| **Vercel: API — orphans (rescue)** | `/api/search/semantic` (HyDE) | ➡️ EXTRACT to `lib/search/query-expansion.ts`, delete route | Used inside planner |
| **Vercel: API — orphans (rescue)** | `/api/search/stream` (SSE) | ➡️ MERGE into `/api/search?stream=true` | Same code path, different transport |
| **Vercel: API — sibling reconciliation** | `/api/trending-topics-v2` (3 active callers, NOT orphan) | 🔄 Milestone 2: reconcile with `/api/trending-topics` (caller-map first) | Three live trending variants converge after caller map verified |
| **Vercel: API — orphans (kill)** | `/api/intelligence` | 🔄 PARTIAL: extract dict, delete rest | `Math.random()` violation |
| **Vercel: API — orphans (kill)** | `/api/ai-summarize` | ➖ DELETE (after cron audit) | Superseded by 8 live summarizers |
| **Vercel: API — orphans (kill)** | `/api/test-summary` | ➖ DELETE | Test scaffold in production tree |
| **Vercel: API — KG search** | `/api/kg/entities/search` (3 callers — KGSearchInterface, EntitySearch, EntityRelationshipPanel) | ✅ KEEP both routes; consolidate at lib layer (shared `lib/search/retrieval/entity-search.ts`) | Different consumers, no merge benefit; shared retrieval primitives instead |
| **Vercel: API — synthesis** | `/api/intelligent-search/summary` | ➡️ MOVE logic into `lib/search/synthesizer.ts`, delete route | Synthesis as library, not endpoint |
| **Vercel: API — research** | `/api/research/*` | ✅ KEEP | Becomes implementation detail of KG RAG fan-out |
| **Vercel: API — graph** | `/api/graph/*` | ✅ KEEP | KG visualization & analytics — separate concern |
| **Vercel: API — trending** | `/api/trending-topics-simple` | ✅ KEEP | 2 callers; not dead. Can deprecate later. |
| **Frontend: search bars** | `IntelligentSearchBar.tsx`, `V7DSearchBar.tsx`, `V3SearchInput.tsx`, `GlobalSearchBar.tsx`, `SearchModal.tsx`, `SearchModalNew.tsx`, `SearchBottomSheet.tsx`, `ArticleSearchPopout.tsx` | 🔄 CONSOLIDATE into `components/search/SearchBar.tsx` (1) + page-specific wrappers | One source of truth for query handling |
| **Frontend: results render** | scattered renderers in pages + `PyramidSummary.tsx` | 🔄 CONSOLIDATE into `components/search/SearchResults.tsx` | Format-aware shell |
| **Frontend: result kinds** | only markdown pyramid today | ➕ ADD `<ResultTable>`, `<ResultChart>`, `<UnsupportedFormat>` | Render the new structured kinds |
| **Frontend: streaming** | `EventSource` not used anywhere | ➕ ADD `hooks/useSearchStream.ts` | Subscribe to SSE for progressive results |
| **Frontend: KG search** | `components/graph/EntitySearch.tsx`, `FloatingSearchPill.tsx`, `ResearchEntityDetail.tsx` | ✅ KEEP | KG-specific UI; calls KG RAG via planner under the hood |
| **Tests** | `tests/lib/search/query-router.test.ts`, `groq-reranker.test.ts`, `rerank-policy.test.ts` | ✅ KEEP & EXTEND | Already covers cascade — extend with format/domains |
| **Tests** | `tests/unit/intelligent-query-engine-fallback.test.ts` | ✅ KEEP | Fallback path stays |
| **Eval harness** | `scripts/evaluate-intelligent-search.ts` (Nov 10) | ✅ KEEP & EXTEND | Already exists — extend with intent-eval mode |
| **Eval harness** | `scripts/create-intelligent-search-datasets.ts` | ✅ KEEP & EXTEND | Add intent labels |
| **Prompt governance** | inline prompts in routes | ➡️ MOVE to `lib/search/prompts/` | Versioned, scored via `prompt-builder` |

**Summary of layer impact:**

| Layer | Changes | Risk |
|---|---|---|
| Postgres | None (optional intent_log later) | Zero |
| Redis | Add 2 cache namespaces | Negligible |
| Railway | None | Zero |
| Vercel cron | None | Zero |
| Vercel API | Big surgery — but feature-flagged, aliased, reversible | Medium, contained |
| Frontend | Big consolidation — but page wrappers preserve URLs | Medium, contained |

---

## 4. File Map — Where Things Are Now → Where They Will Be

> **Intent of this section:** A path-by-path translation layer for an implementer. For every relevant file, name the current path, the target path, the purpose, and the action verb (REPLACE / MOVE / RENAME / KEEP / DELETE). When you sit down to write a PR, this is the section you read with the editor open. Sub-tables are organized by concern: front-door, intent cascade, planner, normalization, synthesis, trending, frontend, and "what stays put."

### 4.1 Search-front-door files

| Current path | New path | Purpose | Action |
|---|---|---|---|
| `app/api/search/route.ts` (399 lines) | `app/api/search/route.ts` | Single search entry, orchestrates cascade → planner → fan-out → normalize → synthesize | REPLACE contents (calls `runSearch()` from lib) |
| `app/api/intelligent-search/route.ts` (1582 lines) | (shared-handler shim) | Calls the same `runSearch()` function. No 308. Method/headers preserved. Identical response shape. | REPLACE body with `export const POST = runSearch` |
| `app/api/intelligent-search/summary/route.ts` (1082 lines) | `lib/search/synthesizer.ts` | Library function called from front door | MOVE logic, delete route |
| `app/api/intelligent-search/health/route.ts` | KEEP at current path; add `/api/search/health` that calls same handler | Liveness check on both URLs | DUAL-MOUNT |
| `app/api/intelligent-search/cache/clear/route.ts` | KEEP; add `/api/search/cache/clear` that calls same handler | Admin cache invalidation | DUAL-MOUNT |

### 4.2 Intent cascade (NEW — built from existing pieces)

| Source | New path | Purpose |
|---|---|---|
| Inline regex in `intelligent-search/route.ts:1170-1181` + `lib/search/query-router.ts:performBasicAnalysis` | `lib/search/intent/tier1-regex.ts` | Fast-path regex matcher with LRU cache |
| (NEW) — uses existing OpenAI embedding model | `lib/search/intent/tier2-embed.ts` | Cosine match against ~30 prototype-intent embeddings (cached in Redis) |
| `lib/search/query-router.ts:performEnhancedAnalysis` | `lib/search/intent/tier3-llm.ts` | Structured-output call (Groq Llama-3.1-8B primary, Haiku 4.5 fallback) |
| (NEW) | `lib/search/intent/intent-schema.ts` | Zod schema for the typed `Intent` object |
| (NEW) | `lib/search/intent/intent-cache.ts` | LRU + Redis cache wrapper |
| (NEW) | `lib/search/intent/index.ts` | `classifyIntent(query): Promise<Intent>` orchestrator |

### 4.3 Planner & retrieval

| Source | New path | Purpose |
|---|---|---|
| Logic embedded in `lib/knowledge-graph/intelligent-query-engine.ts` | `lib/search/planner.ts` | Pure function: `planRetrieval(intent) → RetrievalPlan` |
| `lib/knowledge-graph/intelligent-query-engine.ts` (8 DB tables, ~3500 lines) | `lib/search/retrieval/pipeline-rag.ts` (article + summary fast-path) + `lib/search/retrieval/agentic-rag.ts` (multi-source merge) + `lib/search/retrieval/kg-rag.ts` (entities + papers + relationships) | Decompose by RAG pattern |
| `app/api/search/semantic/route.ts:16-48` (HyDE expansion) | `lib/search/query-expansion.ts` | Used by retrieval when query is vague |
| `app/api/intelligence/route.ts:101-112` (trend keywords) | `lib/search/trend-keywords.ts` | Tier 1 regex seeds |
| `app/api/intelligence/route.ts:67-78` (sentiment regex) | `lib/search/sentiment-regex.ts` | Optional metadata enrichment |

### 4.4 Normalization & response shape

| Source | New path | Purpose |
|---|---|---|
| (NEW) | `lib/search/normalize.ts` | Build `StructuredResult[]` from retrieval output + intent |
| (NEW) | `lib/search/types.ts` | TS types for `Intent`, `StructuredResult`, `SearchResponse` |

### 4.5 Synthesis & prompts

| Source | New path | Purpose |
|---|---|---|
| Inline in `app/api/intelligent-search/summary/route.ts` | `lib/search/synthesizer.ts` | Format-aware narration |
| Inline prompt strings | `lib/search/prompts/synthesis-default.md` | Pyramid synthesis (current behavior) |
| (NEW) | `lib/search/prompts/synthesis-with-tables.md` | 1-paragraph narration when tables/charts present |
| `app/api/search/semantic/route.ts:33-34` (HyDE prompt) | `lib/search/prompts/hyde-expansion.md` | Query expansion |
| (NEW) | `lib/search/prompts/intent-classifier.md` | Tier 3 classifier prompt |
| All prompts | scored & versioned via `prompt-builder` library | Governance |

### 4.6 Trending consolidation

**Three live variants** — caller map verified 2026-04-28 via template-literal-aware grep:

| Current | Active callers | Action (Milestone 2) |
|---|---|---|
| `app/api/trending-topics/route.ts` (203 lines, base mode) | live (TBD precise count) | KEEP path; receives v2 features via backport |
| `app/api/trending-topics-v2/route.ts` (996 lines, multi-mode + MV + quality tiering) | 3 callers: `TrendingTopics.tsx:121`, `TrendingTopicsEnhanced.tsx:422`, `useTrendingTopicsCache.ts:256` | KEEP path during reconciliation; deprecate AFTER consumers migrate |
| `app/api/trending-topics-simple/route.ts` | 2 callers | KEEP for one release, then deprecate |

**Reconciliation strategy** (Milestone 2, 1.5 days):
1. Compare response shapes field-by-field across all three.
2. Backport v2's missing features (materialized view fast-path, three-mode dispatch, quality tier ranking, domain filter, dedup) into `/api/trending-topics`.
3. Migrate v2 consumers one at a time to base path.
4. Only after all three callers move: delete v2.

### 4.7 Frontend consolidation

| Current paths (8+ search bars) | New path | Action |
|---|---|---|
| `components/IntelligentSearchBar.tsx` | `components/search/SearchBar.tsx` | One canonical bar |
| `components/V7DSearchBar.tsx` | (deleted, V7DSearchPage uses SearchBar) | DELETE after migration |
| `components/v3/V3SearchInput.tsx` | (deleted) | DELETE |
| `components/v3/GlobalSearchBar.tsx` | (page-specific wrapper around SearchBar) | REPLACE internals |
| `components/SearchModal.tsx`, `SearchModalNew.tsx` | merge into `components/search/SearchModal.tsx` | KEEP one |
| `components/SearchBottomSheet.tsx` | `components/search/SearchBottomSheet.tsx` (mobile shell over SearchBar) | RENAME |
| `components/ArticleSearchPopout.tsx` | (page-specific wrapper) | KEEP, retarget |
| `components/v3/SearchOverlay.tsx`, `SearchOverlayProvider.tsx` | `components/search/SearchOverlay.tsx` | RENAME |
| `components/graph/FloatingSearchPill.tsx`, `EntitySearch.tsx` | (KG-specific, KEEP) | UNCHANGED — they call KG RAG via planner |

| New files | Purpose |
|---|---|
| `components/search/SearchResults.tsx` | Format-aware result shell |
| `components/search/ResultTable.tsx` | Sortable table with sticky first column, mono dates |
| `components/search/ResultChart.tsx` | Recharts wrapper for bar / ranked-bar / timeline / line / "tornado" |
| `components/search/ResultGraph.tsx` | KG node-link viz (extract from existing graph code) |
| `components/search/UnsupportedFormat.tsx` | Honest downgrade notice |
| `hooks/useSearch.ts` | One-shot POST flow |
| `hooks/useSearchStream.ts` | SSE EventSource flow |

### 4.8 What stays put (touched but not moved)

| File | Why |
|---|---|
| `lib/redis-connection.ts` | Singleton — works as-is |
| `lib/cache/trending-cache.ts`, `temporal-analysis-cache.ts` | Used by consolidated trending route |
| `lib/prisma.ts` + `lib/prisma-cached-queries.ts` | DB layer untouched |
| `lib/ai/groq-service.ts`, `lib/ai/unified-ai-service.ts`, `lib/ai/fact-extraction-service.ts` | Provider abstractions stay |
| `prisma/schema.prisma` | No schema changes required |
| `vercel.json` | Cron entries unchanged |
| `ecosystem.config.js`, Railway nixpacks config | Worker topology unchanged |
| `tests/lib/search/*` | Extended, not rewritten |
| All `/api/research/*`, `/api/graph/*`, `/api/cron/*` routes | Still used as KG/cron implementation detail |

---

## 5. Step-by-Step Execution Plan (10 Phases — Two Milestones)

> **Intent of this section:** The work order. Every phase has a clear deliverable, a flag, and a reversal path. The two-milestone split is the single most important thing here: Milestone 1 ships the bug fix without touching anything live; Milestone 2 does the cleanups only after M1 has soaked. If you are starting work, you start at Phase 0. If you are reviewing, check that no phase in M2 leaks into M1.

Every phase ships independently. Every phase has a flag. Every phase is reversible. Milestone 1 fixes the visible bug end-to-end without deleting or moving anything live.

```
═══════════ MILESTONE 1 — Format-intent + structured results + retrieval lift ═══
                          (mergeable alone, ~8 days · v2.2 SOTA additions)

PHASE 0  Eval harness — extend + add RAGAS metrics      ─── 0.5  day  (was 0.25)
   │
PHASE 1  Intent cascade T1+T3 (shadow mode)             ─── 1.0  day
   │       Tier 3 = Groq openai/gpt-oss-120b strict mode
   │
PHASE 2  Intent schema extension                        ─── 0.25 day
   │
PHASE 3  Structured-result kinds + UI                   ─── 1.0  day
   │       (table, chart, unsupported-format)
   │
PHASE 4  Planner + RRF + cross-encoder placeholder      ─── 3.0  days (was 1.5)
   │       + CRAG self-correction gate
   │       (LLM-rerank stays via Groq Llama-3.3-70b;
   │        cross-encoder swap deferred to Phase 11)
   │
PHASE 5  Shared-handler orchestrator behind flag        ─── 0.5  day
   │
PHASE 8a Synthesis prompts — default + with-tables      ─── 0.25 day
   │
PHASE 9a Telemetry — log every classification           ─── 0.25 day
                                                            ───────────
                                            MILESTONE 1 TOTAL: ~8 days

────────── soak 1-2 weeks · monitor disagreements · validate UX ────────

═══════════ MILESTONE 2 — Consolidations + KG quality + adaptive ═══════
                          (defer until M1 soaked, ~6 days · v2.2 additions)

PHASE 5b Multi-query (primary) + HyDE (vague fallback)  ─── 1.5  days (was 0.5)
   │
PHASE 5c RECOVER SSE progressive search                 ─── 1.0  day
   │
PHASE 5d Trending v1↔v2 reconciliation                  ─── 1.5  days
   │
PHASE 5e HippoRAG-style PPR over entity_pairs           ─── 1.5  days (NEW v2.2)
   │
PHASE 6  Delete confirmed orphans                       ─── 0.25 day
   │
PHASE 6b Extract trend dict + sentiment regex           ─── 0.25 day
   │
PHASE 7  Tier 2 embedding classifier                    ─── 0.5  day
   │
PHASE 8b Full prompt-builder governance pass            ─── 0.25 day
   │
PHASE 9b Full telemetry + intent_log + PII policy       ─── 0.25 day
   │
PHASE 10 Adaptive complexity routing + RAGAS gate       ─── 0.5  day  (NEW v2.2)
                                                            ───────────
                                            MILESTONE 2 TOTAL: ~6 days

────────── soak again · measure faithfulness/recall/precision ──────────

═══════════ MILESTONE 3 — Model swaps (LAST STEP, ~1 day) ══════════════
                          (only after end-to-end works, per user direction)

PHASE 11 Evaluate + swap candidates                     ─── ~1   day  (NEW v2.2)
         · Cross-encoder reranker (Jina v3 / Voyage 2.5
           / Zerank-2 / BGE-self-host)
         · OpenAI Structured Outputs for Tier 3 (only
           if Groq strict mode shows drift in telemetry)
         · ColBERT/PLAID late interaction (only if recall
           plateaus < 90%)
                                                            ───────────
                                            MILESTONE 3 TOTAL: ~1 day
```

**Total focused engineering: ~14 days** (was ~9d in v2.1) across 4-5 calendar weeks with shadow-mode soak between milestones. The +5 days buys: ~84-91% recall@10 (from ~62-78%), agentic self-correction, multi-hop graph retrieval, and a clean off-ramp to dedicated rerankers without committing upfront.

### 5.1 Phase 0 — Extend the existing eval harness + RAGAS metrics (0.5 day, +0.25d in v2.2)

**Goal:** Without an eval, every later change is blind. v2.2 expands eval to retrieval+synthesis quality, not just intent accuracy.

- Open `scripts/evaluate-intelligent-search.ts` and `scripts/create-intelligent-search-datasets.ts`.
- Add `intent` labels to the dataset schema: `domains`, `format`, `chartSubtype`, `horizonDays`, **`complexity`** (new in v2.2).
- Hand-label **200 queries** (was ~50 in v2.1 — corrected for §6 verification consistency) that exercise format requests, multi-domain ("research and releases"), ambiguous cases, simple/medium/complex complexity tiers, and known multi-hop queries.
- Set **90% per-field intent accuracy** as the merge gate for Phase 1+.
- **NEW (v2.2): Add RAGAS-style reference-free metrics** judged by Groq Llama 3.3 70B (cheap, ~$5/full run):
  - **Faithfulness** ≥ 0.85 — no claim in synthesis without grounding in retrieved context.
  - **Context Recall** ≥ 0.80 — retrieval brought in the docs needed.
  - **Context Precision** ≥ 0.75 — retrieved docs were relevant, not noise.
  - **Answer Relevance** ≥ 0.85 — synthesis actually answered the question.
- These are merge gates for any phase that touches retrieval or synthesis (4, 5b, 5e, 8a, 11).

**Reversible?** N/A — additive only.

### 5.2 Phase 1 — Intent cascade T1+T3 (shadow mode) (1 day)

**Goal:** Stand up the cascade as a parallel data-collection layer. Don't act on output yet. **Stay on Groq throughout** — pick the right Groq model per tier.

- Create `lib/search/intent/intent-schema.ts` with the Zod `Intent` type. Mirror it as a JSON Schema literal for Groq's strict `response_format`.
- Create `lib/search/intent/tier1-regex.ts` — promote and rename existing regex rules from `intelligent-search/route.ts:1170-1181` and `query-router.ts:performBasicAnalysis`. Add format/domain detection.
- Create `lib/search/intent/tier3-llm.ts` — **Groq-first, accuracy-correct model selection**:
  - **Primary: Groq `openai/gpt-oss-120b` with `response_format: { type: 'json_schema', strict: true, json_schema: IntentJsonSchema }`.** Constrained decoding → 100% schema-valid by construction. ~500 tps, $0.15/$0.75 per 1M tok. This is the *correct* Groq model for this job, not Llama 3.1 8B (which only supports `json_object` and would let the typed `Intent` drift on field/enum errors).
  - **Fallback A: Groq `openai/gpt-oss-20b` with strict `json_schema`** — same constrained-decoding guarantee, smaller/cheaper, available on the same provider. Use on circuit-breaker open or 120b unavailable.
  - **Fallback B: Groq `meta-llama/llama-4-scout-17b-16e-instruct` with best-effort `json_schema`** — schema-aware but not strict; validate with Zod and re-prompt once on parse failure.
  - **Final fallback: Groq `llama-3.1-8b-instant` with `json_object`** + Zod validation + 1 retry. Last resort; still cheaper than any OpenAI/Anthropic call.
  - **Why this is accuracy-first AND speed/cost-optimized** — verified at console.groq.com/docs/structured-outputs (2026-04-28): only `openai/gpt-oss-{20b,120b}` use constrained decoding for guaranteed schema adherence on Groq. Llama models on Groq only enforce "valid JSON" via `json_object` mode, which can drift on field names, enum values, and array shapes. Picking gpt-oss-120b keeps you on Groq (cheaper/faster than OpenAI Structured Outputs) AND keeps the typed `Intent` schema-strict (the shape gates planner + normalize + synthesize downstream).
  - **Cross-provider escape hatch (deferred)** — if production telemetry shows gpt-oss-120b strict-mode is misclassifying despite schema-validity, the `lib/search/intent/tier3-llm.ts` provider abstraction makes swapping to OpenAI `gpt-4.1-mini` Structured Outputs a one-line change. Defer the swap until the architecture is working end-to-end and there is data demanding it.
- Create `lib/search/intent/intent-cache.ts` — Redis-backed LRU keyed on raw query, 24h TTL. Cache key includes `IntentSchema.version` so schema bumps invalidate automatically.
- Create `lib/search/intent/index.ts` — `classifyIntent(query)` orchestrator.
- Wire `classifyIntent()` into `/api/intelligent-search/route.ts` in **shadow mode** behind `ENABLE_INTENT_CASCADE_SHADOW=true`. Log results, don't act on them.
- Run for 24-48h, compare classifier output to current regex output. **Track schema-validation failure rate per model** as a quality signal even though gpt-oss-120b strict mode should be ~0%.

**Reversible?** Yes — flag to off.

### 5.3 Phase 2 — Intent schema extension (0.25 day)

- Extend `Intent` type with `format`, `chartSubtype`, `domains`. Already done in Phase 1's schema.
- Update existing `query-router.ts` consumers to read these fields when present. Default behavior unchanged when fields absent.
- Add unit tests in `tests/lib/search/intent/`.

**Reversible?** Yes — typed extension, default values mean old callers still work.

### 5.4 Phase 3 — Structured-result kinds + UI (1 day)

- Create `lib/search/types.ts` with `StructuredResult` discriminated union.
- Create `components/search/ResultTable.tsx`, `ResultChart.tsx`, `UnsupportedFormat.tsx`.
- Create `components/search/SearchResults.tsx` shell.
- Verify Recharts is already in `package.json` (it likely is — `app/chart-sandbox/page.tsx` exists).
- Keep `<PyramidSummary />` rendering when `format === 'default'` — no regression.

**Reversible?** Yes — new components, old code path untouched.

### 5.5 Phase 4 — Planner extraction + multi-domain fan-out + RRF + CRAG gate (3 days)

**Up from 1.5d in v2.1 — adds two SOTA-validated retrieval improvements that are pure code changes (no new model dependency, no provider swap).**

- Create `lib/search/planner.ts` — pure function `planRetrieval(intent) → RetrievalPlan`.
- Carefully extract retrieval primitives from `lib/knowledge-graph/intelligent-query-engine.ts` into:
  - `lib/search/retrieval/pipeline-rag.ts` (articles + summaries; the common case)
  - `lib/search/retrieval/agentic-rag.ts` (multi-source fan-out + merge)
  - `lib/search/retrieval/kg-rag.ts` (entities, papers, relationships — calls `/api/research/*` and `/api/graph/*` internally)

**NEW (v2.2) — Reciprocal Rank Fusion (RRF) inside `pipeline-rag.ts`** (~1 day):
- Today's pipeline runs vector search (pgvector) and keyword search (`title ILIKE`) in sequence with implicit precedence. Replace with parallel queries + RRF fusion.
- Run pgvector `ORDER BY embedding <-> $query LIMIT 50` AND Postgres `tsvector` (`websearch_to_tsquery`) `LIMIT 50` in parallel.
- Fuse with RRF: `score(doc) = Σ 1/(k + rank_i)` across both lists, k=60. Trim to top-20 for rerank.
- Recall lift expectation: ~62–78% → ~84–91% recall@10 (per Supermemory, ParadeDB, Tiger Data, Weaviate benchmarks). **Verify on atomize fixtures via Phase 0 eval — direction is documented; magnitude must be measured.**
- Keep current Groq Llama 3.3 70B as the reranker over the top-20 fused candidates. Dedicated cross-encoder reranker swap is deferred to **Phase 11 (last step)** per user direction — code is wired so the swap is one provider-abstraction change.

**NEW (v2.2) — CRAG-style relevance gate inside `agentic-rag.ts`** (~0.5 day):
- After rerank, compute average relevance score across top-N reranked docs.
- If `avg_relevance < 0.5` (tunable from telemetry):
  - Branch A: trigger one query reformulation pass via existing Groq Llama 3.3 70B (rewrite, retry retrieval once).
  - Branch B: if Branch A also fails the threshold, return `{ kind: 'low-confidence', reason, fallback: <broader-query results> }` as an honest-downgrade structured result — same pattern the plan already uses for `unsupported-format`.
- Log every gate trigger; tune threshold from one week of shadow-mode data.
- This makes the "agentic" branch *actually* agentic (critique-and-retry), not just fan-out-and-merge.

- Add a `?intent_v2=true` flag to `/api/intelligent-search` that routes through the new planner. Default off.

**Reversible?** Yes — flag to off. RRF is additive (old code path stays); CRAG gate can be disabled by env var.

### 5.6 Phase 5 — Promote `/api/search` as front door via SHARED HANDLER (0.5 day)

**Rationale (Codex review):** A 308 redirect changes the URL the client observes, can break method/header preservation in older clients, and is hard to revert mid-request. A shared handler keeps both routes mounted with identical behavior — zero client surface change.

- Create `lib/search/runSearch.ts` exporting `async function runSearch(req: Request): Promise<Response>` — the new orchestrator (`classifyIntent → planRetrieval → fanOutRetrieve → normalize → synthesize → respond`).
- Replace contents of `app/api/search/route.ts` with `export const POST = runSearch`.
- Replace contents of `app/api/intelligent-search/route.ts` with `export const POST = runSearch`. Same handler, two URLs, identical response.
- Move `/api/intelligent-search/summary/route.ts` logic into `lib/search/synthesizer.ts`. Delete the route file (no external callers — synthesis was internal).
- Dual-mount `/api/search/health` and `/api/search/cache/clear` alongside the originals.

**Reversible?** Yes — `runSearch` runs behind `ENABLE_SEARCH_ORCHESTRATOR_V2=true`. Flag off → both routes fall back to the old `intelligent-search` body. No URL change, no client work.

### 5.7 Phase 5b — Query rewriting: multi-query primary + HyDE fallback (1.5 days, +1d in v2.2)

**v2.2 update:** DMQR-RAG (multi-query rewriting) shows +14.46% P@5 on FreshQA and +8% on HotpotQA multi-hop vs HyDE-only. RAG-Fusion (multi-query + RRF) is the 2026 standard. HyDE remains valuable specifically for *vague* queries; multi-query better for *complex/multi-hop*. Plan now does both, routed by intent.

- Extract `expandQuery()` from `app/api/search/semantic/route.ts:16-48` into `lib/search/query-expansion.ts`.
- Move the HyDE prompt to `lib/search/prompts/hyde-expansion.md`.
- **NEW: Add multi-query rewriter** (`lib/search/query-multiquery.ts`):
  - Generate 3 alternative phrasings via Groq Llama 3.3 70B (existing model, no swap).
  - Run RRF fusion across all 4 candidate lists (original + 3 rewrites).
  - Cap rewrites at 3; gate on `intent.complexity ≥ medium` (see Phase 2 complexity field below) to avoid cost on simple queries.
- **Routing rule (in planner)**:
  - `intent.complexity = 'simple'` → no rewrite (skip both).
  - `intent.complexity = 'medium'` AND `tokenCount ≤ 4` → HyDE only (vague short query).
  - `intent.complexity = 'complex'` → multi-query (RAG-Fusion).
  - `intent.complexity = 'complex'` AND vague → both, fused.
- Add `complexity: 'simple' | 'medium' | 'complex'` to `Intent` schema (cheap heuristic in Tier 1: token count + entity count + presence of "vs/compare/why/how" patterns). This is the Adaptive RAG pattern from §13.
- Run all expansion prompts through `prompt-builder:score`; optimize if < 80.

**Reversible?** Yes — flag `ENABLE_QUERY_REWRITE=false` (covers both). Sub-flags `ENABLE_HYDE` / `ENABLE_MULTIQUERY` for granular rollback.

### 5.8 Phase 5c — Recover SSE progressive search (1 day)

- Add `?stream=true` branch to `/api/search/route.ts`. When set, return SSE; otherwise JSON.
- Reuse the three-phase pattern from `app/api/search/stream/route.ts:24-181` — keyword → vector → expansion.
- Create `hooks/useSearchStream.ts` with `EventSource` wrapper.
- Add `streamMode={true}` prop to `<SearchBar />` for pages that opt in (start with `/search`).

**Reversible?** Yes — frontend prop default `false`.

### 5.9 Phase 5d — Trending v1↔v2 consolidation (1.5 days)

- Compare field-by-field: components consuming `/api/trending-topics` (live, base mode), `/api/trending-topics-simple` (live, 2 callers), `/api/trending-topics-v2` (live, 3 callers — multi-mode + MV + quality tiering).
- Backport v2's materialized-view fast-path, three-mode dispatch, quality tier ranking (VERIFIED/EMERGING/SPECULATIVE), domain filter, deduplication into `/api/trending-topics`.
- Verify `?mode=default|temporal|thematic` works on the consolidated route.
- Migrate consumers one-by-one to base path: `useTrendingTopicsCache.ts:256`, `TrendingTopics.tsx:121`, `TrendingTopicsEnhanced.tsx:422`. Smoke-test each.
- Only after all three callers migrated: delete `/api/trending-topics-v2`.

**Reversible?** Soft — restorable from git, but consumers will have moved by then.

### 5.9.5 Phase 5e — HippoRAG-style PPR over `entity_pairs` for KG-RAG (1.5 days, NEW in v2.2)

**Why:** GraphRAG-Bench (ICLR'26) shows HippoRAG / HippoRAG2 lead on multi-hop reasoning — Evidence Recall 87–91%, Context Relevance 85–88% — vs Microsoft GraphRAG (community-summary, expensive to index) and LightRAG (lower latency, lower accuracy). atomize-ai's `entities` + `entity_pairs` schema fits HippoRAG's Personalized PageRank pattern *natively* — no new tables, no re-indexing.

- In `lib/search/retrieval/kg-rag.ts`: replace the current "fetch entity neighbors" walk with a Personalized PageRank seeded by entities matched in the query.
- Implementation: precompute entity_pair edge weights from co-occurrence + recency; run PPR (5–10 iterations, damping 0.85) over the seeded entities; rank papers/articles by their entity-membership score.
- Cache PPR vectors in Redis keyed on `seed_entity_set`; 6h TTL since `entity_pairs` updates daily via clustering-worker.
- Verify schema indices on `entity_pairs(source_id, target_id)` and `entity_pairs(target_id, source_id)` exist before deploying — PPR over 1M+ edge graphs needs them.
- Keep current KG retrieval as the fallback path (`USE_HIPPORAG=false`).

**Reversible?** Yes — flag-gated. Old KG path stays as-is.

### 5.10 Phase 6 — Delete confirmed orphans (Milestone 2, 0.25 day)

**Rule:** Each delete preceded by a **template-literal-aware grep** (NOT just NavGator's `frontend-calls-api` connection check, which misses `\`/api/${var}\``) AND a `npx navgator impact <stable-id>` check.

```bash
# template-literal-aware grep recipe
grep -rnE "['\"\`]/api/<route-name>['\"\`]?" --include='*.ts*' app components hooks lib
grep -rnE "/api/<route-name>" --include='*.ts*' app components hooks lib  # broader catch
```

Confirmed-deletable (verified 2026-04-28):
- `app/api/intelligence/route.ts` — 0 callers via both grep patterns. Delete after dict extract (Phase 6b).
- `app/api/ai-summarize/route.ts` — 0 callers; delete after cron audit (`grep -E "ai-summarize" vercel.json`).
- `app/api/test-summary/route.ts` — 0 callers, test scaffold.
- `app/api/search/semantic/route.ts` — after Phase 5b extracts HyDE logic.
- `app/api/search/stream/route.ts` — after Phase 5c merges SSE logic.

**Removed from delete list (Codex correction):**
- ❌ ~~`app/api/kg/entities/search/route.ts`~~ — KEEP, 3 KG-UI callers (`KGSearchInterface.tsx:103`, `EntitySearch.tsx:127`, `EntityRelationshipPanel.tsx:412`).
- ❌ ~~`app/api/trending-topics-v2/route.ts`~~ — KEEP, 3 callers (`TrendingTopics.tsx:121`, `TrendingTopicsEnhanced.tsx:422`, `useTrendingTopicsCache.ts:256`); reconcile in Phase 5d.

**Reversible?** Soft — git revert.

### 5.11 Phase 6b — Extract trend dict + sentiment regex (0.25 day)

- `lib/search/trend-keywords.ts` ← from `intelligence/route.ts:101-112`
- `lib/search/sentiment-regex.ts` ← from `intelligence/route.ts:67-78`
- Wire into Tier 1 regex matcher (intent cascade) and optional metadata enrichment in normalize.

### 5.12 Phase 7 — Tier 2 embedding classifier (0.5 day)

- Pre-compute embeddings for ~30 prototype intents ("show me a table", "compare X vs Y", "trending in research", "tornado diagram of news"). Store in Redis under `search:embed:proto:*`.
- Cosine match incoming query embedding (reuse existing OpenAI embedding model) against prototypes.
- Insert between Tier 1 and Tier 3.
- Rebalance: target ~70/20/10 traffic at T1/T2/T3.

**Reversible?** Yes — flag to bypass T2.

### 5.13 Phase 8 — Synthesizer prompt update + governance (0.5 day)

- Two prompts in `lib/search/prompts/`:
  - `synthesis-default.md` (current pyramid behavior)
  - `synthesis-with-tables.md` (1-paragraph narration only)
- Synthesizer reads from these files at startup; hot-swap via env var `SYNTHESIS_PROMPT_VERSION=2026-04-30`.
- Run all three prompts (HyDE, intent classifier, synthesis) through `prompt-builder:score` → `prompt-builder:optimize` if any < 80.
- Save final versions via `prompt-builder:save` to project library.

### 5.14 Phase 9 — Telemetry + weekly review (continuous)

- Log every classification: `query`, `intent`, `confidence`, `tier`, `latencyMs`, `model`. Sample 5% to a review queue.
- Optional new table `intent_log` in Postgres if you want SQL-queryable history (not blocking).
- Weekly: review disagreements between regex fast-path and LLM classifier, retune Tier 1 rules.
- Cost dashboard: alert if classifier spend > $1/day at current traffic.
- **PII policy (v2.2):** queries can carry PII (emails, names, internal project codenames). Apply hash-only-store for the 5% review sample, OR opt-in flag for full-query storage, OR redact via regex pre-store. Decide before Phase 9 ships.

### 5.15 Phase 10 — Adaptive complexity routing + RAGAS-style eval (0.5 day, NEW in v2.2)

- Wire the `intent.complexity` field (added in Phase 5b) into the planner: `simple` → skip retrieval where possible (cache hit + return), `medium` → standard RAG, `complex` → multi-step + multi-query.
- Extend `scripts/evaluate-intelligent-search.ts` with four RAGAS-style reference-free metrics, judged by Groq Llama 3.3 70B (cheap, ~$5/full eval run):
  - **Faithfulness** — every claim in the response is grounded in retrieved context.
  - **Context Recall** — the retrieval brought in the documents needed to answer.
  - **Context Precision** — retrieved docs are relevant (not noise).
  - **Answer Relevance** — the response answers the asked question.
- Set merge gate: no phase ships unless faithfulness ≥ 0.85 AND context recall ≥ 0.80 AND answer relevance ≥ 0.85 vs the v2.1 baseline.

### 5.16 Phase 11 — Deferred model swaps (LAST STEP, only after end-to-end works) (~1 day)

**Per user direction (2026-04-28):** new-LLM swaps come last, after the architecture is proven working. Each swap is a one-line provider-abstraction change because Phase 1 / Phase 4 / Phase 5b already wired the abstraction.

Candidates to evaluate at this phase, in priority order:

1. **Dedicated cross-encoder reranker** (highest expected lift). Replace LLM-rerank-via-Groq-70B with one of:
   - **Jina Reranker v3** — 81.33% Hit@1 at 188ms (sub-200ms champion); managed API, listwise, 131k context.
   - **Voyage Rerank 2.5** — competitive accuracy, ~2× lower latency than Cohere.
   - **Zerank-2** — current ELO leader (1638) on 2026 reranker leaderboards.
   - **BGE-reranker-v2-m3** — open-source, self-host on existing Railway worker fleet (matches user's "build from scratch" preference).
   - Decision criterion: run all four against atomize fixtures via Phase 0 eval; pick the model with the best (faithfulness × context-recall) / latency curve. Expected latency win: 500–2000ms LLM-rerank → 50–200ms cross-encoder.
2. **OpenAI Structured Outputs for Tier 3** — only if Groq `openai/gpt-oss-120b` strict mode shows ≥3% schema-validation drift in production telemetry. Drop-in `gpt-4.1-mini` or `gpt-5-mini` if needed.
3. **Cohere Rerank 4.0 Pro / Fast** — comparable accuracy to top-tier; managed; only if self-host BGE creates ops burden you don't want.
4. **Late interaction (ColBERT v2 / PLAID)** — premium upgrade after RRF caps out; only if recall@10 plateaus below 90% on atomize-specific eval.

**Reversible?** Yes — provider abstraction means each swap is config-only.

---

## 6. Verification

> **Intent of this section:** Define "done" before any code is written. Each row names a test and what passing it actually proves about the system. The two failing user queries appear here as **Integration A and B** — they are the canonical proof that the bug is fixed. If a phase gets shipped without its row turning green, treat it as not shipped.

| Test | What it proves |
|---|---|
| **Unit** — 200 labeled fixture set, ≥ 90% per-field intent accuracy | Cascade is honest about what it understands |
| **Integration A** — re-run "What's the latest AI trends for research and product releases. share this in a table" | Response contains `structuredResults: [{kind:'table'}, …]`, prose ≤ 1 paragraph |
| **Integration B** — re-run "create a tornado diagram of recent news trends" | Response contains `kind:'unsupported-format'` with substituted ranked-bar `kind:'chart'` |
| **Integration C** — query "what's trending in AI research and product launches?" | Multi-domain fan-out: response contains release rows AND paper rows from KG RAG, not just news articles |
| **Smoke** — old URL `/api/intelligent-search` still returns valid response | Alias works |
| **Smoke** — SSE: subscribe to `/api/search?stream=true` | Receive keyword phase < 200ms, vector phase < 1s |
| **Performance** — Tier 1 hit latency p99 | < 25ms |
| **Performance** — full request latency (T3 + retrieval + synthesis) p95 | < 1500ms |
| **Cost** — daily classifier spend dashboard | < $1/day at current traffic |
| **NavGator** — `npx navgator scan && npx navgator impact <front-door>` after cutover | No orphan callers, route count dropped by ≥ 6 |
| **NEW (v2.2) — RAGAS Faithfulness** | ≥ 0.85 on 200-query fixture; no synthesis claim without retrieved-context support |
| **NEW (v2.2) — RAGAS Context Recall** | ≥ 0.80; RRF + multi-query bring in the right docs (vs ~62% v2.1 vector-only baseline) |
| **NEW (v2.2) — RAGAS Context Precision** | ≥ 0.75; reranker + CRAG gate filter noise |
| **NEW (v2.2) — RAGAS Answer Relevance** | ≥ 0.85; synthesis answers the asked question |
| **NEW (v2.2) — Tier 3 schema-validation rate** | ≥ 99.5% on Groq `openai/gpt-oss-120b` strict mode; trigger Phase 11 swap if < 97% |
| **NEW (v2.2) — CRAG gate trigger rate** | 5–15% of queries (too low = gate not firing; too high = retrieval is broken) |
| **NEW (v2.2) — Provider abstraction** — swap reranker model in Phase 11 with one config change | Provider swap works config-only, no code edit elsewhere |

---

## 7. Risks & Mitigations

> **Intent of this section:** Surface the things most likely to go wrong, with their pre-decided mitigations, so review cycles don't have to rediscover them. If you find yourself proposing a workaround during implementation, check this table first — it may already be addressed.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cascade misclassifies common queries | Medium | High UX | Phase 1 shadow mode + 24-48h soak before any flip |
| Synthesis prompt regression on `format=default` | Medium | UX | Keep current pyramid prompt verbatim; only add new format-aware variant |
| HyDE prompt overfitting / off-topic expansions | Low | Mid | 1.5s timeout already in original; keep length-validation guard |
| Trending v1↔v2 cutover breaks `/markettrends` page | Medium | High | Backport features one mode at a time; smoke test each |
| Frontend consolidation breaks one of 5 search pages | Medium | Mid | Page-specific wrappers preserve URLs; visual regression via IBR |
| Deleting `intelligence/route.ts` breaks an unknown consumer | Low | Low | Strict grep + `navgator impact` before delete |
| Redis cost bump from intent cache | Very low | Negligible | Same Upstash instance, 24h TTL on a small surface |
| Provider outage on Tier 3 (Groq) | Low | Mid | Existing circuit-breaker pattern + Haiku 4.5 fallback already designed |
| Eval harness drift after Phase 8 prompt changes | Medium | Mid | Re-run eval after every prompt-builder save; fail merge if accuracy drops |

---

## 8. What This Plan Deliberately Does NOT Do

> **Intent of this section:** Hold the line on scope. Each bullet names something a reasonable person might think this plan should also do, and explains why it stays out. If a future change request lands in this list, it earns its own plan rather than getting bolted onto this one.

- ❌ Replace synthesis provider (Groq → other). Orthogonal.
- ❌ Move to a learned router (RouteLLM-style). Engineering cost dwarfs marginal gain at current scale ([TianPan](https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades)). Revisit at 100k+ qpd.
- ❌ Unify `domain = general | research` into one column. Cosmetic; planner-level fan-out gets the same UX without a migration.
- ❌ Big-bang rewrite. Every phase ships behind a flag.
- ❌ New RAG pattern. You already have all three (Pipeline / Agentic / KG).
- ❌ Schema migration. Postgres stays as-is.
- ❌ Railway worker topology change. The 4-service map stays as documented in `docs/04-operations/RAILWAY_WORKERS.md`.
- ❌ Vercel cron change. Same 11 schedules.

---

## 8.5 Data Flow Sequences — BEFORE and AFTER

> **Intent of this section:** Walk a single real query through every box in §1 and §2 step by step, showing what data exists at each stage and where it gets lost (BEFORE) or honored (AFTER). This is the section to read if you want to *understand* the bug rather than just see the architecture diagrams. The worked example is the actual failing query the user hit; the integration-points table at the end shows side-by-side change at every system boundary.

These diagrams trace a single query end-to-end. Worked example throughout: **"What's the latest AI trends for research and product releases. share this in a table"** — the actual query that triggered this plan.

Conventions:
- Steps numbered 1, 2, 3 …
- Each step shows: **what fires**, **what data is shaped**, **integration point**.
- 🔴 marks where the format intent gets lost (BEFORE) or honored (AFTER).
- 💾 marks integration with persistent stores.
- ⚡ marks cache lookups.

---

### 8.5.1 BEFORE — Current data flow (where it breaks)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  USER                                                                        │
│  Types: "What's the latest AI trends for research and product releases.      │
│          share this in a table"                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ keystrokes
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 1 · FRONTEND · components/IntelligentSearchBar.tsx                     │
│                                                                              │
│  Debounce(300ms) → onSubmit                                                  │
│  Local state: { query: "...", timeHorizonDays: 7, mode: "all" }              │
│  📤 fires TWO parallel fetches:                                              │
│     fetch('/api/intelligent-search',         POST, body=above)               │
│     fetch('/api/intelligent-search/summary', POST, body=above)               │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │   HTTPS / Vercel Edge
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 2 · VERCEL · app/api/intelligent-search/route.ts (1582 lines)          │
│                                                                              │
│  ⚡ in-memory cache check (per-instance, lost on cold start)                 │
│  Body parsed → { query, mode, timeHorizonDays }                              │
│                                                                              │
│  Inline regex intent detection (route.ts:1170-1181):                         │
│    wantsTimeline   = /timeline|release history|releases over.../i            │
│    wantsRelease    = /release|launch|new model|shipped/i        ✓ matches    │
│    wantsComparison = /compare|versus|benchmark/i                             │
│    🔴 wantsTable / wantsChart / wantsTornado: DOES NOT EXIST                 │
│                                                                              │
│  Data shape: { query, mode, horizon, wantsRelease: true }                    │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ delegates to
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 3 · LIB · lib/search/query-router.ts                                   │
│                                                                              │
│  performBasicAnalysis(query)                                                 │
│    → temporal words, entity extraction, complexity heuristics                │
│    → output: QueryAnalysis { type, complexity, entities, searchStrategy }    │
│                                                                              │
│  shouldUseEnhancedAnalysis(query)                                            │
│    if true → performEnhancedAnalysis() → OpenAI call                         │
│    Classifies as: factual | conceptual | temporal | entity | mixed           │
│    Strategy: vector-primary | keyword-primary | hybrid-weighted | …          │
│                                                                              │
│  ⚡ in-memory cache (5min TTL, max 500 entries)                              │
│  ⚠ format/domain NOT in QueryAnalysis schema                                 │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ feeds analysis to
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 4 · LIB · lib/knowledge-graph/intelligent-query-engine.ts              │
│                                                                              │
│  IntelligentQueryEngine.query(query, queryAnalysis)                          │
│    │                                                                          │
│    ├─ 4a. Vector search (pgvector)                                           │
│    │      💾 SELECT FROM article_embeddings ORDER BY <-> embedding LIMIT 50  │
│    │                                                                          │
│    ├─ 4b. Keyword search                                                     │
│    │      💾 SELECT FROM articles WHERE title ILIKE … LIMIT 30               │
│    │                                                                          │
│    ├─ 4c. Release branch (because wantsRelease=true, route.ts:903-923)       │
│    │      💾 SELECT FROM release WHERE releasedAt >= NOW()-INTERVAL '2 years'│
│    │                                                                          │
│    ├─ 4d. Summary join                                                       │
│    │      💾 SELECT summaryJson FROM summary WHERE articleId IN (…)          │
│    │                                                                          │
│    └─ 4e. Groq reranker                                                      │
│           Sends top 50 to Groq llama-3.1-70b for relevance scoring           │
│                                                                              │
│  🔴 NEVER queries: papers, OpenAlex, Semantic Scholar, arXiv                 │
│  🔴 NEVER fans out by domain — "research" interpreted as articles-mentioning-│
│     research, not papers                                                     │
│                                                                              │
│  Data shape: { results: Article[], releases: Release[], rerankedScores }     │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ raw results returned to route
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 5 · VERCEL · /api/intelligent-search/route.ts (continues)              │
│                                                                              │
│  Builds response object:                                                     │
│    {                                                                         │
│      success: true,                                                          │
│      results: Article[],                                                     │
│      structuredResults: [                                                    │
│        { kind: 'timeline', events: [...releases...] }   ← if wantsTimeline   │
│      ],                                                                      │
│      timelineEvents: [...],                                                  │
│      pyramidSummary: null   ← filled by parallel summary call                │
│    }                                                                         │
│                                                                              │
│  🔴 Allowed structuredResults kinds: 'timeline', 'benchmark' — NO 'table'    │
│  🔴 Format directive "share this in a table" already gone forever            │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ ──────────► JSON response (one half)
                                  │
                  PARALLEL ──────►│
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 6 · VERCEL · /api/intelligent-search/summary/route.ts (1082 lines)     │
│                                                                              │
│  Receives same query independently                                           │
│  selectSynthesisModel() picks Groq llama-3.1-70b primary                     │
│                                                                              │
│  Calls articles search again (often redundantly — separate cache key)        │
│                                                                              │
│  System prompt:                                                              │
│    "OUTPUT FORMAT: valid markdown only.                                      │
│     Task: Summarize N articles about <query> for Smart-Brevity reader.       │
│     Structure: # Key Insight / ## Supporting Points / ## Important Nuances   │
│              / ## Key Entities"                                              │
│                                                                              │
│  🔴 Prompt is contractually markdown-only — even if the route received a     │
│     format hint, the LLM cannot emit a table                                 │
│                                                                              │
│  Returns: { type: 'markdown', content: '# Key Insight\n…' }                  │
│                                                                              │
│  Circuit breakers on Groq + OpenAI handle failover                           │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ ──────────► JSON response (other half)
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 7 · FRONTEND · IntelligentSearchBar.tsx + PyramidSummary.tsx           │
│                                                                              │
│  Awaits both fetches                                                         │
│  parseMarkdownPyramid(pyramidSummary.content) → AST                          │
│                                                                              │
│  Renders in order:                                                           │
│    1. PyramidSummary    (markdown prose)                                     │
│    2. EventTimeline     (release events, NOT a table)                        │
│    3. FactsChart        (if any extracted)                                   │
│    4. Article cards     (top results)                                        │
│                                                                              │
│  🔴 No table component is ever instantiated                                  │
│  🔴 The user's "share this in a table" instruction has been silently lost    │
│     at three layers: route regex, query analysis, synthesis prompt           │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                              USER SEES:
              ┌──────────────────────────────────────┐
              │ # Key Insight                        │
              │ AI is becoming increasingly…         │
              │                                      │
              │ ## Supporting Points                 │
              │ • Conversational Interfaces — …      │
              │ • Spatial Services — …               │
              │ • Simulation-Based Manufacturing — …│
              │                                      │
              │ NO TABLE.                            │
              └──────────────────────────────────────┘
```

**Where data is lost:**

| Step | Loss |
|---|---|
| 2 | Format directive ("table") — regex doesn't look for it |
| 3 | Format/domain — `QueryAnalysis` schema has no fields for them |
| 4 | Domain fan-out — "research" routed to articles, not papers |
| 6 | Format expressivity — synthesis prompt is markdown-only |
| 7 | No table renderer exists in the result-shell anyway |

---

### 8.5.2 AFTER — Target data flow (where it works)

Same query as input. Annotations show transformations and integration points.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  USER                                                                        │
│  Types: "What's the latest AI trends for research and product releases.      │
│          share this in a table"                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ keystrokes
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 1 · FRONTEND · components/search/SearchBar.tsx (canonical)             │
│                                                                              │
│  Debounce(300ms) → onSubmit                                                  │
│  Hook chooses transport based on prop:                                       │
│    streamMode=false → useSearch()        → POST /api/search                  │
│    streamMode=true  → useSearchStream()  → GET  /api/search?stream=true      │
│                                          → EventSource subscription           │
│                                                                              │
│  Body shape:                                                                 │
│    { query: "...", sessionId, hints?: { domain?, horizonDays?, format? } }   │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ HTTPS / Vercel Edge
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 2 · VERCEL · app/api/search/route.ts (orchestrator, ~150 lines)        │
│                                                                              │
│  Parses body → { query, hints, sessionId }                                   │
│                                                                              │
│  Pipeline:                                                                   │
│    intent     = await classifyIntent(query, hints)            ── Step 3      │
│    plan       = planRetrieval(intent)                         ── Step 4      │
│    retrieval  = await fanOutRetrieve(plan)                    ── Step 5      │
│    structured = normalize(intent, retrieval)                  ── Step 6      │
│    prose      = await synthesize(intent, structured, plan)    ── Step 7      │
│    return respond({ intent, prose, structured, plan, diag })  ── Step 8      │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ classifyIntent()
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 3 · LIB · lib/search/intent/  (3-tier cascade)                         │
│                                                                              │
│  ⚡ Step 3.0 · intent-cache.ts                                               │
│      Check Redis: GET search:intent:<hash(query)>  (24h TTL)                 │
│      HIT  → return cached Intent (microseconds, no work)                     │
│      MISS → continue to T1                                                   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │ Step 3.1 · tier1-regex.ts        target: 5–20 ms             │           │
│  │                                                              │           │
│  │ Patterns checked (selected):                                 │           │
│  │   FORMAT:    /\btable|grid|tabular|spreadsheet\b/i  ✓ MATCH  │           │
│  │              /\bchart|diagram|graph|tornado\b/i              │           │
│  │              /\btimeline|history\b/i                          │           │
│  │   DOMAIN:    /\bresearch|paper|arxiv|study\b/i      ✓ MATCH  │           │
│  │              /\brelease|launch|product\b/i          ✓ MATCH  │           │
│  │   COMPARE:   /\bvs\.?|versus|compare|benchmark\b/i           │           │
│  │   HORIZON:   /\blast\s+\d+\s+(day|week|month|year)/i         │           │
│  │                                                              │           │
│  │ Builds Intent partial:                                       │           │
│  │   { format: 'table',                                         │           │
│  │     domains: ['research','releases'],                        │           │
│  │     horizonDays: 7  /* default */,                           │           │
│  │     confidence: 0.92, tier: 1 }                              │           │
│  │                                                              │           │
│  │ confidence ≥ 0.85? ✓ YES → SHORT-CIRCUIT, return Intent      │           │
│  │ confidence  < 0.85? → fall through to T2                     │           │
│  └──────────────────────────────────────────────────────────────┘           │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │ Step 3.2 · tier2-embed.ts        target: 20–50 ms            │           │
│  │   Compute query embedding (existing OpenAI text-embed-3)     │           │
│  │   ⚡ ~30 prototype embeddings cached in Redis                │           │
│  │   Cosine match top-1 ≥ 0.75 → map to canonical Intent        │           │
│  │   confidence ≥ 0.80? → return; else fall through              │           │
│  └──────────────────────────────────────────────────────────────┘           │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │ Step 3.3 · tier3-llm.ts          target: 150–600 ms          │           │
│  │   Groq Llama-3.1-8B (primary) / Haiku 4.5 (fallback)         │           │
│  │   response_format: { type:'json_schema', schema: IntentZod } │           │
│  │   temperature: 0                                             │           │
│  │   Always returns; low-confidence outputs still used + logged │           │
│  └──────────────────────────────────────────────────────────────┘           │
│                                                                              │
│  💾 SET search:intent:<hash> = Intent (24h TTL)                              │
│                                                                              │
│  Final Intent (this query):                                                  │
│    { domains: ['research', 'releases', 'articles'],                          │
│      retrievalPattern: 'agentic',          /* multi-domain → agentic */      │
│      format: 'table',                                                        │
│      chartSubtype: undefined,                                                │
│      horizonDays: 7,                                                         │
│      comparisonTargets: [],                                                  │
│      topicKeywords: ['AI', 'trends', 'research', 'releases'],                │
│      confidence: 0.92,                                                       │
│      tier: 1 }                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ planRetrieval(intent)
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 4 · LIB · lib/search/planner.ts (pure function, no LLM, no I/O)        │
│                                                                              │
│  Rules (deterministic, debuggable):                                          │
│    domains has 'research' AND domains.length > 1 → 'agentic'  ✓ matches      │
│    domains has 'kg'        OR comparisonTargets   → 'kg'                     │
│    domains.length === 1                          → 'pipeline'                │
│    else                                          → 'hybrid'                  │
│                                                                              │
│  Output RetrievalPlan:                                                       │
│    {                                                                         │
│      pattern: 'agentic',                                                     │
│      sources: [                                                              │
│        { kind: 'articles',  branch: 'pipeline', horizonDays: 7 },            │
│        { kind: 'releases',  branch: 'pipeline', horizonDays: 365 },          │
│        { kind: 'papers',    branch: 'kg',       horizonDays: 30 },           │
│      ],                                                                      │
│      merge: 'union-by-recency',                                              │
│      expandQuery: false,        /* not vague — 13 tokens */                  │
│      synthesize: true,                                                       │
│    }                                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ fanOutRetrieve(plan)
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 5 · LIB · lib/search/retrieval/ (parallel fan-out)                     │
│                                                                              │
│  Promise.all([                                                               │
│   ┌─────────────────────────────────────────┐                                │
│   │ 5a. pipeline-rag.ts (articles)          │                                │
│   │   IF intent.tier===3 OR query.length<25 │                                │
│   │     ⤳ HyDE expansion (query-expansion)  │                                │
│   │       Groq llama-3.3-70b rewrites vague │                                │
│   │       query as 2-3 sentence excerpt     │                                │
│   │       (recovered from search/semantic)  │                                │
│   │   💾 article_embeddings (pgvector)      │                                │
│   │   💾 articles (keyword fallback if <3)  │                                │
│   │   💾 summary (join)                     │                                │
│   │   ⤳ Groq reranker (top 50)              │                                │
│   └─────────────────────────────────────────┘                                │
│                                                                              │
│   ┌─────────────────────────────────────────┐                                │
│   │ 5b. pipeline-rag.ts (releases)          │                                │
│   │   💾 release WHERE releasedAt >= cutoff │                                │
│   │   💾 join primaryArticle                │                                │
│   └─────────────────────────────────────────┘                                │
│                                                                              │
│   ┌─────────────────────────────────────────┐                                │
│   │ 5c. kg-rag.ts (research papers)         │                                │
│   │   💾 entities WHERE                     │                                │
│   │       type='paper' AND domain='research'│                                │
│   │       AND publishedAt >= cutoff         │                                │
│   │       ORDER BY citationCount delta DESC │                                │
│   │   💾 entity_pairs (citation chain)      │                                │
│   │   ⤳ optional: arXiv inline fetch for    │                                │
│   │     fresh metadata                      │                                │
│   └─────────────────────────────────────────┘                                │
│  ])                                                                          │
│                                                                              │
│  Merge (union-by-recency, dedup by canonical id)                             │
│  Output: { articles[], releases[], papers[], evidence[] }                    │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ normalize(intent, retrieval)
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 6 · LIB · lib/search/normalize.ts                                      │
│                                                                              │
│  Reads intent.format and intent.chartSubtype, builds StructuredResult[]:     │
│                                                                              │
│  format === 'table' → emit two table panels:                                 │
│    {                                                                         │
│      kind: 'table',                                                          │
│      title: 'Recent Product Releases',                                       │
│      columns: ['Date','Vendor','Product','Version','Source'],                │
│      rows: [                                                                 │
│        ['2026-04-25','Anthropic','Claude','Opus 4.7','anthropic.com'],       │
│        ['2026-04-22','OpenAI',   'GPT-…',  'v…',     'openai.com'],          │
│        ...                                                                   │
│      ],                                                                      │
│      source: { releases: 'postgres.release' }                                │
│    },                                                                        │
│    {                                                                         │
│      kind: 'table',                                                          │
│      title: 'Recent Research Papers',                                        │
│      columns: ['Date','Paper','Authors','Venue','Citations','Link'],         │
│      rows: [...],                                                            │
│      source: { papers: 'postgres.entities[domain=research]' }                │
│    }                                                                         │
│                                                                              │
│  Always also emit (when format !== 'graph'):                                 │
│    { kind: 'timeline', events: […releases by date…] }   ← contextual         │
│                                                                              │
│  IF user had requested 'tornado diagram':                                    │
│    { kind: 'unsupported-format',                                             │
│      requested: 'tornado',                                                   │
│      fallbackKind: 'chart',                                                  │
│      reason: 'Tornado diagrams need signed impact values; news data does    │
│               not provide that. Showing ranked-bar of theme frequency.' }    │
│                                                                              │
│  🟢 Format intent has been HONORED at the data layer                         │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ synthesize(intent, structured, plan)
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 7 · LIB · lib/search/synthesizer.ts                                    │
│                                                                              │
│  Selects prompt by format:                                                   │
│    format === 'default' → lib/search/prompts/synthesis-default.md            │
│    format !== 'default' → lib/search/prompts/synthesis-with-tables.md        │
│                                                                              │
│  synthesis-with-tables.md (excerpt):                                         │
│    "Write ONE paragraph (≤80 words) summarizing the trend across the rows   │
│     in `<tables>`. Do NOT invent rows. Do NOT format as a list. Mention      │
│     the dominant theme and one notable outlier. Plain prose."                │
│                                                                              │
│  LLM (Groq llama-3.1-70b primary) returns:                                   │
│    "Recent AI activity skews toward research with 12 new papers on …,        │
│     while the release calendar is dominated by Anthropic and OpenAI.         │
│     Notable outlier: …"                                                      │
│                                                                              │
│  🟢 LLM narrates, never fabricates table rows                                │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ respond()
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 8 · VERCEL · /api/search/route.ts (returns)                            │
│                                                                              │
│  JSON body:                                                                  │
│    {                                                                         │
│      intent:    Intent,                /* echoed for UI transparency */      │
│      prose:     { kind:'pyramid', markdown: '…' },                           │
│      structuredResults: [                                                    │
│        { kind:'table', title:'Recent Product Releases', columns, rows },     │
│        { kind:'table', title:'Recent Research Papers',  columns, rows },     │
│        { kind:'timeline', events: [...] }                                    │
│      ],                                                                      │
│      diagnostics: { tier: 1, latencyMs: 187, cacheHit: false,                │
│                     model: 'groq:llama-3.1-70b' }                            │
│    }                                                                         │
│                                                                              │
│  IF stream=true: same content emitted as SSE chunks:                         │
│    event: intent       data: {…}        ← <50ms                              │
│    event: keyword      data: {…}        ← <200ms (early article hits)        │
│    event: table        data: {…}        ← <500ms (releases query done)       │
│    event: table        data: {…}        ← <800ms (papers query done)         │
│    event: timeline     data: {…}                                             │
│    event: prose        data: {…}        ← last (synthesis is slowest)        │
│    event: complete     data: { totalLatencyMs }                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │ JSON or SSE
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  STEP 9 · FRONTEND · components/search/SearchResults.tsx                     │
│                                                                              │
│  for (const r of structuredResults) {                                        │
│    switch (r.kind) {                                                         │
│      case 'table':              <ResultTable {...r} />     ← 🟢 RENDERS     │
│      case 'chart':              <ResultChart {...r} />                       │
│      case 'timeline':           <EventTimeline {...r} />                     │
│      case 'graph':              <ResultGraph {...r} />                       │
│      case 'unsupported-format': <UnsupportedFormat {...r} />                 │
│    }                                                                         │
│  }                                                                           │
│  <PyramidSummary content={prose.markdown} />   ← AT TOP, 1 paragraph         │
│                                                                              │
│  Streaming variant:                                                          │
│    hooks/useSearchStream.ts subscribes via EventSource;                      │
│    sets each event into a discriminated state slot;                          │
│    SearchResults re-renders incrementally as each chunk arrives              │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                              USER SEES:
       ┌─────────────────────────────────────────────────────────────┐
       │ Recent AI activity skews toward research with 12 new        │
       │ papers, while releases are dominated by Anthropic and       │
       │ OpenAI. Notable outlier: …                                  │
       │                                                             │
       │ ┌───────────────────────────────────────────────────────┐   │
       │ │ Recent Product Releases                               │   │
       │ │ ┌──────────┬────────────┬─────────┬────────┬───────┐ │   │
       │ │ │ Date     │ Vendor     │ Product │ Version│ Source│ │   │
       │ │ │ 2026-04…│ Anthropic  │ Claude  │ Opus…  │ …     │ │   │
       │ │ │ 2026-04…│ OpenAI     │ GPT-…   │ v…     │ …     │ │   │
       │ │ └──────────┴────────────┴─────────┴────────┴───────┘ │   │
       │ └───────────────────────────────────────────────────────┘   │
       │                                                             │
       │ ┌───────────────────────────────────────────────────────┐   │
       │ │ Recent Research Papers                                │   │
       │ │ ┌──────────┬─────────┬───────────┬──────┬───────┬───┐ │   │
       │ │ │ Date     │ Paper   │ Authors   │ Venue│ Citns │ … │ │   │
       │ │ └──────────┴─────────┴───────────┴──────┴───────┴───┘ │   │
       │ └───────────────────────────────────────────────────────┘   │
       │                                                             │
       │ Timeline · April 2026                                       │
       │ ●━━━●━━━●━━━●  releases plotted by date                     │
       └─────────────────────────────────────────────────────────────┘
```

---

### 8.5.3 Side-by-side at each integration point

| Integration point | BEFORE | AFTER |
|---|---|---|
| **User → Frontend** | 8+ search components, inconsistent | One `SearchBar`, optional streaming |
| **Frontend → Vercel** | TWO parallel POSTs to overlapping routes | ONE POST, optional `?stream=true` |
| **Intent detection** | Inline regex in route + partial QueryRouter, schema lacks format/domain | 3-tier cascade with typed `Intent`, format & domain first-class |
| **Cache (intent)** | None | Redis 24h LRU keyed on query hash |
| **Cache (results)** | In-memory per-Vercel-instance, lost on cold start | Redis (existing `TrendingCache`/`temporal-cache` reused for retrieval) |
| **Postgres reads** | Articles + releases only | Articles + releases + papers + entity graph (multi-domain fan-out) |
| **pgvector** | One vector pass | Same, plus optional HyDE expansion for vague queries |
| **KG / arXiv** | Isolated under `/api/research/*`, not reached from search | Reached as KG-RAG branch in retrieval |
| **Synthesis prompt** | Markdown-only, can't honor format | Two prompts (default / with-tables), prompt-builder governance |
| **Response shape** | `results[]` + optional `structuredResults` (`timeline`, `benchmark` only) | `intent` + `prose` + `structuredResults[]` (5 kinds incl. `table`, `chart`, `unsupported-format`) |
| **Vercel → Frontend** | Two awaited fetches, then render | One JSON response, OR SSE stream with progressive paint |
| **Frontend render** | Pyramid + maybe timeline, no table | Format-aware `SearchResults` shell renders correct kind |
| **Format directive** | 🔴 Lost at three layers | 🟢 Honored at data layer; honestly downgraded if unsupported |
| **Railway workers** | ✅ Untouched | ✅ Untouched (4 services keep current queues) |
| **Vercel cron** | ✅ Untouched | ✅ Untouched (11 schedules keep) |
| **Postgres schema** | ✅ Untouched | ✅ Untouched (optional `intent_log` later) |
| **Provider failover** | Existing Groq/OpenAI circuit-breakers in summary route | Same pattern reused in T3 classifier and synthesizer |

---

### 8.5.4 Integration points worth calling out

1. **Single front door via shared handler** — `/api/search` and `/api/intelligent-search` both export the same `runSearch` function. No 308 redirect, no URL semantics change. Backwards-compat is automatic; clients never see a difference.
2. **Intent → Plan → Retrieve are pure functions** of their inputs. No global state, no hidden coupling. Each is independently testable.
3. **Postgres is the source of truth for rows; LLM is the source of narration only.** The split eliminates row-level hallucination by construction.
4. **Redis becomes denser** but doesn't change shape — still one Upstash KV instance. New keyspaces are namespaced (`search:intent:*`, `search:embed:proto:*`).
5. **Railway and cron are off the critical path of this work entirely.** No deploy choreography needed; the workers keep ingesting and clustering as today.
6. **SSE is the only new transport.** Frontend gets `EventSource`; the backend reuses the same `respond()` function with a different writer. Same underlying pipeline.
7. **Prompt library is the new governance surface.** Every LLM call (T3 classifier, HyDE expansion, default synthesis, with-tables synthesis) reads from `lib/search/prompts/*.md`. `prompt-builder` scores and versions them. Hot-swap via env var.

---

## 9. Sources

> **Intent of this section:** Make the research traceable. Every architectural pattern recommended in this plan came from a real source — these are the ones cited inline. Internal sources (NavGator scan, file reads, memory references) are listed alongside external links. If you want to re-derive a recommendation from scratch, this is the bibliography.

**Web research (live, late-2025 / early-2026):**
- [Meganova: The 3-Tier Routing Cascade — Rule-Based → Semantic → LLM](https://blog.meganova.ai/the-3-tier-routing-cascade-rule-based-semantic-llm/) — concrete tier latencies, 0.8 confidence threshold, 96% accuracy claim
- [TianPan: LLM Routing and Model Cascades (Nov 2025)](https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades) — when learned routing is worth it
- [LogRocket: LLM Routing in Production](https://blog.logrocket.com/llm-routing-right-model-for-requests/) — strategy taxonomy
- [NVIDIA AI Blueprints — llm-router](https://github.com/NVIDIA-AI-Blueprints/llm-router) — production reference impl
- [Lanham: Pipeline vs Agentic vs KG RAG (Feb 2026)](https://medium.com/@Micheal-Lanham/pipeline-rag-vs-agentic-rag-vs-knowledge-graph-rag-what-actually-works-and-when-47a26649a457) — pattern selection by query shape
- [Neo4j: GraphRAG and Agentic Architecture](https://neo4j.com/blog/developer/graphrag-and-agentic-architecture-with-neoconverse/) — KG + RAG hybrid memory
- [ZBrain: Knowledge Graphs for Agentic AI](https://zbrain.ai/knowledge-graphs-for-agentic-ai/) — vector vs KG memory tradeoffs
- [Data Nucleus: Agentic RAG Enterprise Guide 2026](https://datanucleus.dev/rag-and-agentic-ai/agentic-rag-enterprise-guide-2026) — adoption stats
- [arXiv: Agentic RAG with Knowledge Graphs for Multi-Hop Reasoning](https://arxiv.org/abs/2507.16507) — academic basis

**v2.2 SOTA-validation sources (live web research 2026-04-28):**
- Hybrid retrieval / RRF: [Supermemory hybrid search guide (Apr 2026)](https://blog.supermemory.ai/hybrid-search-guide/), [ParadeDB hybrid search manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual), [Tiger Data Postgres hybrid search](https://www.tigerdata.com/blog/hybrid-search-postgres-you-probably-should), [Glaforge RRF (Feb 2026)](https://glaforge.dev/posts/2026/02/10/advanced-rag-understanding-reciprocal-rank-fusion-in-hybrid-search/), [Weaviate hybrid search](https://weaviate.io/blog/hybrid-search-explained), [Supabase hybrid search docs](https://supabase.com/docs/guides/ai/hybrid-search)
- Query rewriting: [DMQR-RAG arXiv 2411.13154](https://arxiv.org/html/2411.13154v1), [DMFlow 6 advanced query transformation architectures](https://www.dmflow.chat/en/blog/rag-query-transformation-guide-6-advanced-architectures)
- Reranker leaderboard: [agentset.ai reranker leaderboard](https://agentset.ai/rerankers), [Cohere Rerank 3.5 docs](https://docs.cohere.com/changelog/rerank-v3.5), [awesome-rerankers curated list](https://github.com/agentset-ai/awesome-rerankers), [Mixedbread BEIR benchmarks via Jina docs](https://jina.ai/news/maximizing-search-relevancy-and-rag-accuracy-with-jina-reranker/)
- Knowledge graph RAG: [GraphRAG-Bench (ICLR'26)](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark), [arXiv 2506.05690 — When to use Graphs in RAG](https://arxiv.org/html/2506.05690v3), [Graph Praxis 2026 practitioner guide](https://medium.com/graph-praxis/graph-rag-in-2026-a-practitioners-guide-to-what-actually-works-dca4962e7517)
- Agentic RAG / CRAG: [arXiv 2501.09136 Agentic RAG Survey](https://arxiv.org/abs/2501.09136), [Data Nucleus enterprise guide 2026](https://datanucleus.dev/rag-and-agentic-ai/agentic-rag-enterprise-guide-2026)
- Structured outputs / constrained decoding: [Groq Structured Outputs docs (verified 2026-04-28)](https://console.groq.com/docs/structured-outputs), [Groq supported models docs](https://console.groq.com/docs/models), [arXiv 2408.11061 StructuredRAG](https://arxiv.org/abs/2408.11061), [OpenAI Structured Outputs docs](https://developers.openai.com/api/docs/guides/structured-outputs)
- Evaluation: [Ragas metrics docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/), [PremAI RAG evaluation 2026](https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026/)
- Provider comparison (Groq vs OpenAI): [Groq pricing](https://groq.com/pricing), [Artificial Analysis Groq provider page](https://artificialanalysis.ai/providers/groq), [LLM API comparison 2026](https://www.morphllm.com/llm-api), [TokenMix Groq pricing breakdown](https://tokenmix.ai/blog/groq-api-pricing)
- Research packet: `~/dev/git-folder/atomize-ai/.build-loop/research/2026-04-28-unified-search-sota-validation.md`

**Internal sources:**
- Fresh NavGator scan 2026-04-28: 236 components, 2,580 connections, 1,889 files
- File-level Explore pass over `/Users/tyroneross/dev/git-folder/atomize-ai/`
- Read all 6 candidate "orphan" route files end-to-end (5 confirmed orphan, 1 reclassified as live sibling per Codex review)
- Memory: `reference_atomize_ai_railway_workers.md`, `atomize-ai.md`, `feedback_no_fake_stats.md`
- `vercel.json` (11 cron entries, 60s function timeout)
- `ecosystem.config.js` (PM2 layer for non-Railway deploys)
- Existing tests: `tests/lib/search/{query-router,groq-reranker,rerank-policy}.test.ts`, `tests/unit/intelligent-query-engine-fallback.test.ts`
- Existing eval harness: `scripts/evaluate-intelligent-search.ts`, `scripts/create-intelligent-search-datasets.ts`

---

## 11. Codex Review Log (2026-04-28)

> **Intent of this section:** Honest record of what the previous version got wrong. An external code-grounded review caught five concrete errors before any code shipped — this section names each one, the verification, and the correction applied. Most importantly, §11.2 captures the *root cause* of the errors so the same trap can be avoided in future plans (NavGator's connection detector misses template-literal `fetch()` calls). If you are about to delete a route, read §11.2 first.

External code-grounded review caught five errors in v2.0. All verified against the working tree, all corrected in v2.1.

### 11.1 Errors caught and fixed

| # | Codex finding | Verification | Correction applied |
|---|---|---|---|
| 1 | `/api/trending-topics-v2` is **not** an orphan — 3 active callers | ✅ Confirmed: `lib/hooks/useTrendingTopicsCache.ts:256`, `components/TrendingTopics.tsx:121`, `components/TrendingTopicsEnhanced.tsx:422` | Reclassified as live sibling. Phase 5d reframed as "reconciliation" not "kill orphan". Removed from §6 delete list. |
| 2 | `/api/kg/entities/search` has 3 KG-UI callers | ✅ Confirmed: `components/kg/KGSearchInterface.tsx:103`, `components/graph/EntitySearch.tsx:127`, `components/kg/EntityRelationshipPanel.tsx:412` | Removed from delete list. Plan now consolidates at lib layer (`lib/search/retrieval/entity-search.ts`), keeps both routes. |
| 3 | `SearchModalNew` fetches `/api/intelligent-search`, not `/api/intelligence` | ✅ Confirmed: `components/SearchModalNew.tsx:51` `fetch('/api/intelligent-search', …)` | BEFORE diagram in §1.1 corrected. |
| 4 | Recharts presence should be confirmed, not deferred | ✅ Confirmed: `package.json:133` `"recharts": "^3.8.0"` | §10 status changed from ❓ to ✅. |
| 5 | 308 redirect for `/api/intelligent-search` is too risky | Method preservation, header propagation, observability all break in older clients | Replaced with **shared-handler shim** — both routes export the same `runSearch` function. Identical behavior, zero URL change, see §5.6. |

### 11.2 Root cause of errors 1 and 2

Both came from over-trusting NavGator's `frontend-calls-api` connection detection, which only matches **literal-string** `fetch('…')` calls. Template literals (`fetch(\`/api/${endpoint}\`)`) and conditional URLs (`url = condition ? '/api/v2' : '/api/v1'`) are invisible to that detector.

**Mitigation now codified in §5.10:** every delete is gated by *both*:
1. `grep -rnE "['\"\`]/api/<name>['\"\`]?"` (literal)
2. `grep -rnE "/api/<name>"` (broader, catches template literals and string concatenation)

NavGator's `impact` is a useful directional signal but not authoritative for delete decisions.

### 11.3 Scope discipline applied (Codex's milestone advice)

Codex recommended: *"greenlight the format-intent/structured-results milestone first; defer route deletion / trending consolidation / frontend consolidation until caller maps are corrected."*

Plan restructured into:

- **Milestone 1** (~5 days, merge-able alone): P0 eval, P1-P2 cascade in shadow, P3 structured results + UI, P4 planner extraction, P5 shared-handler orchestrator behind a flag, P8 partial prompt governance, P9 partial telemetry. **Old routes keep working untouched.**
- **Milestone 2** (~4 days, defer until M1 has soaked): P5b-P5d orphan rescues + reconciliations, P6/P6b deletes, P7 T2 classifier, P8/P9 full.

This means the **format-intent fix can ship without ever deleting a route or consolidating frontend**. The two failing queries get solved in M1; cleanup is M2.

### 11.4 Items still flagged for further verification

- Caller map for `/api/trending-topics` (live, base mode) and `/api/trending-topics-simple` — exact callers not enumerated; do this in Phase 5d before any reconciliation.
- Caller map for the 8+ summarization routes — needed before any consolidation.
- Caller map for `/api/entities/search` vs `/api/kg/entities/search` — confirm distinct consumers stay distinct, but share retrieval primitives.

### 11.5 Updated risk profile

| Risk | Pre-Codex | Post-Codex |
|---|---|---|
| Delete a live route by mistake | Medium-high | Low (template-literal grep mandatory before any delete) |
| Break clients via 308 redirect | Medium | Eliminated (shared handler) |
| Break KG UI by deleting kg/entities/search | High | Eliminated (route preserved) |
| Break trending UI by deleting v2 | High | Eliminated (treated as live sibling, reconciled before any delete) |
| Misread eval/cascade infra status | Low (was already noted) | Low |

---

## 12. Status of claims

> **Intent of this section:** A confidence audit. Every load-bearing claim in the plan gets a status marker — ✅ verified by code · ✅ verified by NavGator · ⚠️ untested · TAG:INFERRED · ❓ uncertain — so a reader can see what was actually checked vs assumed. If you act on a TAG:INFERRED claim, verify it first.

- ✅ Verified by code: 60+ search routes, two parallel search surfaces, regex inline at `intelligent-search/route.ts:1170-1181`, `lib/search/query-router.ts` cascade-shaped, hard domain partition, markdown-only synthesis prompt, all 6 candidate "orphan" files read end-to-end (5 confirmed orphan + 1 misclassification corrected), eval harness present, 4-service Railway topology
- ✅ Verified by NavGator (2026-04-28): 236 components, 2,580 connections, dead-route grep cross-check
- ⚠️ Untested: latency/cost numbers in §3 — typical industry numbers, not measured against your stack
- TAG:INFERRED: precise traffic distribution across the three tiers (~70/20/10 estimate from cited sources, not measured)
- ✅ Recharts present at `package.json:133`, version `^3.8.0` — verified by Codex review
- Some web sources are dated 2026 but published in early 2026 — directional, not statistically authoritative
- ✅ **(v2.2) Verified at console.groq.com/docs/structured-outputs (2026-04-28):** strict `json_schema` mode (constrained decoding, 100% schema adherence) is supported on Groq for `openai/gpt-oss-20b` and `openai/gpt-oss-120b` only. Best-effort `json_schema` adds `openai/gpt-oss-safeguard-20b` and `meta-llama/llama-4-scout-17b-16e-instruct`. All other Groq models (Llama 3.3 70B, Llama 3.1 8B, Kimi K2, Qwen 3, etc.) only support `json_object` mode (valid JSON, no schema enforcement).
- TAG:INFERRED **(v2.2)** RRF recall lift (~62-78% → ~84-91%) and reranker latency wins (500-2000ms → 50-200ms) are aggregate industry benchmarks, not measured on atomize fixtures. Phase 0 RAGAS eval must confirm magnitude before locking M2/M3.
- ⚠️ Untested **(v2.2)** HippoRAG-vs-current-KG retrieval comparison — GraphRAG-Bench (ICLR'26) results are general; needs atomize-specific eval.
- ✅ **(v2.2)** User provider preference — Groq cheaper+faster than OpenAI verified via live web search 2026-04-28: 3-12× tps, 5-10× cheaper, TTFT 120-250ms vs 300-700ms.

---

## 13. v2.2 SOTA Research Log (2026-04-28)

> **Intent of this section:** Honest record of what changed between v2.1 and v2.2 and why. v2.1 was correct in *shape*; v2.2 surfaces three implementation-layer upgrades (RRF, CRAG, multi-query) plus correct Groq-model selection per task. Mirrors §11 in style — names the finding, the verification, and the correction. If you are reviewing why a phase grew, this is the section that explains it.

### 13.1 Trigger

User asked `/build-loop:research` to validate whether v2.1's approach reflects current SOTA for unified AI search across multi-source data, with explicit priority **accuracy first → speed → cost**, and a stated preference to stay on Groq for cost/latency reasons. User followed up clarifying that (a) Groq runs many LLMs, not one, so per-model capabilities matter; and (b) cross-provider model swaps (e.g. dedicated rerankers) should be a *last step after the architecture is working end-to-end*, not part of the initial cutover.

### 13.2 Findings (live web research 2026-04-28)

| # | Finding | Source confidence | Action |
|---|---|---|---|
| 1 | The 3-tier cascade shape (regex → embed → LLM) is canonical 2026 production pattern. RouteLLM achieves ~95% of GPT-4 quality at ~15% cost. NVIDIA ships an llm-router blueprint. | T1/T2 — Meganova, NVIDIA, akshayghalme, TianPan | **Validated. No change.** v2.1 shape is SOTA. |
| 2 | Pure pgvector retrieval ≈ 62-78% recall@10. Pure BM25/tsvector ≈ 65%. Vector + BM25 fused with **Reciprocal Rank Fusion (RRF, k=60)** ≈ 84-91% recall@10. RRF is now table-stakes for pgvector hybrid search. | T1 — Supermemory, ParadeDB, Tiger Data, Weaviate, Glaforge, Supabase docs, multiple benchmarks | **Added to Phase 4.** Pure code change, no new dependency. |
| 3 | Modern "agentic RAG" expects a self-correction loop (Self-RAG, **CRAG**) that critiques retrieval quality post-rerank and reformulates or honest-downgrades when relevance is low. v2.1's "agentic" branch fans out and merges but never gates on quality. | T2 — arXiv 2501.09136, Data Nucleus 2026, multiple 2026 medium guides | **Added to Phase 4.** Reuses existing Groq Llama 3.3 70B as critic; honest-downgrade pattern already in plan for `unsupported-format`. |
| 4 | DMQR-RAG (multi-query rewriting) shows +14.46% P@5 on FreshQA, +8% on HotpotQA multi-hop. **RAG-Fusion** (multi-query + RRF) is now the standard query-rewrite pattern. HyDE remains valuable specifically for *vague* queries; multi-query better for *complex/multi-hop*. | T1 — DMQR-RAG arXiv 2411.13154, multiple 2026 guides | **Phase 5b extended.** Multi-query is now primary, HyDE is the vague-query fallback, both routed by `intent.complexity`. |
| 5 | GraphRAG-Bench (ICLR'26) shows **HippoRAG / HippoRAG2** lead multi-hop reasoning at 87-91% Evidence Recall, 85-88% Context Relevance. atomize's `entities` + `entity_pairs` schema fits HippoRAG's Personalized PageRank pattern *natively* — no new tables. LightRAG = lower latency but slightly lower accuracy. Microsoft GraphRAG = community-summary, expensive to index. | T1 — GraphRAG-Bench (ICLR'26), arXiv 2506.05690, Graph Praxis 2026 | **Added as Phase 5e (M2).** Uses existing schema; flag-gated; old KG retrieval is fallback. |
| 6 | RAGAS reference-free metrics (faithfulness, context recall, context precision, answer relevance) are the 2026 standard for production RAG eval. v2.1 eval covered intent accuracy only. | T1 — Ragas docs, PremAI 2026 review, Maxim AI | **Phase 0 extended; merge gates added in §6.** Judged by existing Groq Llama 3.3 70B (~$5/full run). |
| 7 | **Groq supports strict `json_schema`** (constrained decoding, 100% schema adherence) on `openai/gpt-oss-20b` and `openai/gpt-oss-120b` only. All other Groq models (including Llama 3.3 70B and 3.1 8B) only support `json_object` mode. The `gpt-oss-120b` model on Groq runs at ~500 tps with strict schema enforcement — better speed AND strictness than OpenAI Structured Outputs at lower cost. | T1 — verified live at console.groq.com/docs/structured-outputs (2026-04-28) | **Phase 1 Tier 3 model pinned to `openai/gpt-oss-120b` strict mode**, with `gpt-oss-20b` and `llama-4-scout` as fallbacks. **Stays on Groq.** Cross-provider OpenAI swap deferred to Phase 11 only if telemetry shows drift. |
| 8 | **Reranker leadership has shifted in 2026** — Zerank-2 (1638 ELO leader), Voyage Rerank 2.5, Jina Reranker v3 (81.33% Hit@1 at 188ms) now lead Cohere on accuracy. Cohere Rerank 3.5/4.0 still competitive but no longer #1. Cross-encoders run 50-200ms vs LLM-rerank 500-2000ms with comparable or better accuracy. | T1/T2 — agentset.ai leaderboard, Mixedbread benchmarks, Jina docs, ZeroEntropy guide 2026 | **Deferred to Phase 11 (last step) per user direction.** v2.2 keeps Groq Llama 3.3 70B as reranker; Phase 4 wires the provider abstraction so swap is config-only later. |
| 9 | Adaptive RAG: route by query complexity (simple → skip retrieval; medium → standard RAG; complex → multi-step). v2.1 routes by domain+format only. | T2 — Asai et al., 2026 surveys | **Added as Phase 10** plus an `intent.complexity` field threaded through Phase 5b's expansion routing. |

### 13.3 What stayed unchanged in v2.1

- 3-tier cascade with 0.85/0.80 confidence gates ✅ canonical
- Two-milestone shipping discipline ✅ correct cadence
- Shared-handler shim over 308 redirect ✅ post-Codex correction holds
- Format as first-class field with structured kinds ✅ aligned with StructuredRAG benchmark direction
- Honest-downgrade pattern (`unsupported-format`) ✅ now extended to retrieval quality (`low-confidence`)
- Pipeline / Agentic / KG split ✅ matches Lanham / Data Nucleus taxonomy
- Provider abstraction in `lib/ai/*-service.ts` ✅ enables Phase 11 deferred swaps cleanly

### 13.4 Provider strategy (v2.2)

Confirmed live 2026-04-28: Groq is **3-12× faster (tps)** and **5-10× cheaper** than comparable OpenAI/Anthropic calls. User preference is correct. Strategy is "stay on Groq, pick the right Groq model per task":

| Task | Groq model | Why |
|---|---|---|
| Tier 3 intent classifier | **`openai/gpt-oss-120b` strict** | Constrained decoding → 100% schema-valid for typed `Intent`. Schema correctness is non-negotiable; this is the only Groq model that guarantees it (alongside gpt-oss-20b). |
| Tier 3 fallback | `openai/gpt-oss-20b` strict | Same constrained-decoding guarantee, cheaper, smaller. |
| Tier 3 second fallback | `meta-llama/llama-4-scout-17b-16e-instruct` best-effort | Schema-aware but not strict; Zod-validate + 1 retry. |
| Tier 3 final fallback | `llama-3.1-8b-instant` json_object | Last resort; cheapest; Zod-validate + retry. |
| Synthesis (default + with-tables) | `llama-3.3-70b-versatile` | 86% MMLU, ~280 tps, $0.59/$0.79 per 1M. Schema strictness not required for narration. |
| Reranker (Phase 4) | `llama-3.3-70b-versatile` | Same model as synthesis; LLM-rerank stays until Phase 11 swap to dedicated cross-encoder. |
| CRAG critic (Phase 4) | `llama-3.3-70b-versatile` | Reuses same model; relevance scoring is a 0-1 numeric ask, not schema-strict. |
| Multi-query rewriter (Phase 5b) | `llama-3.3-70b-versatile` | Generates 3 alternative phrasings; output validated via plain parse, not schema. |
| RAGAS judge (Phase 0/10) | `llama-3.3-70b-versatile` | Numeric score output; ~$5 per full eval run. |
| HyDE expansion (Phase 5b) | `llama-3.3-70b-versatile` | Existing model; rewrites short query as 2-3 sentence excerpt. |

**Cross-provider escape hatches (deferred to Phase 11):** OpenAI `gpt-4.1-mini` Structured Outputs (Tier 3 if Groq drift), Cohere Rerank 4.0 / Jina Reranker v3 / Voyage Rerank 2.5 / Zerank-2 / BGE self-host (reranker), Anthropic Haiku 4.5 (additional Tier 3 fallback). Each is a one-line provider-config change because the abstraction is wired upfront.

### 13.5 Net delta v2.1 → v2.2

| Aspect | v2.1 | v2.2 | Why |
|---|---|---|---|
| M1 budget | ~5 days | ~8 days | RRF (+1d), CRAG gate (+0.5d), Phase 1 schema-strict pin (no extra time, just correct model), Phase 0 RAGAS (+0.25d), Phase 4 provider abstraction (+0.25d) |
| M2 budget | ~4 days | ~6 days | Multi-query expansion (+1d in 5b), HippoRAG PPR (+1.5d in 5e), Phase 10 adaptive routing (+0.5d) |
| M3 budget | (none) | ~1 day | New Phase 11 for deferred model swaps (cross-encoder reranker, optional cross-provider escape) |
| **Total** | **~9 days** | **~14 days** | +5d for substantially higher retrieval quality (~62→~91% recall), agentic self-correction, multi-hop graph retrieval, removed single-vendor dependency on Groq 70B for reranking critical path |
| Risk profile | Medium (intent fix only) | Lower (RAGAS gates catch regressions; CRAG masks retrieval edge cases) | RAGAS metrics are now merge gates; CRAG honest-downgrade prevents silent quality failures |
| Provider lock-in | High (everything on Groq Llama) | Low (Groq stays default; provider abstraction enables Phase 11 swap config-only) | Phase 4 + Phase 1 wire abstractions even when not used immediately |

### 13.6 Items still flagged for further verification

- RAGAS judge calibration on atomize fixtures — Llama 3.3 70B as judge is cheap but hasn't been calibrated against human ratings on atomize-specific data. First eval run will reveal whether scores are stable.
- HippoRAG PPR performance on `entity_pairs` of atomize's actual size — verify edge count and required indices via `EXPLAIN` before Phase 5e build.
- CRAG threshold (0.5) — placeholder. Tune from one week of shadow-mode telemetry.
- Multi-query cost amplification — 4× retrieval calls per complex query. Measure actual cost lift vs RAGAS quality lift; abort if cost > 2× without quality gain.
- Whether Groq `openai/gpt-oss-120b` strict mode actually achieves ~99.5% schema validity in production (one community report flagged "Structured Outputs ignored by openai/gpt-oss-120b"). Phase 1 shadow mode telemetry validates this empirically before Phase 5 cuts over.

### 13.7 Updated risk profile

| Risk | v2.1 | v2.2 |
|---|---|---|
| Recall too low for multi-domain queries | High | Lower — RRF + multi-query + HippoRAG measured via RAGAS recall gate |
| Synthesis hallucinates rows when table requested | Medium | Low — RAGAS faithfulness gate + plan principle "LLM narrates, never fabricates" |
| Tier 3 returns malformed Intent JSON | Medium | Low — Groq `gpt-oss-120b` strict mode + Zod fallback |
| Single-vendor Groq dependency on critical path | Medium | Low — provider abstraction wired in Phases 1/4; Phase 11 swap is config-only when needed |
| Plan grows beyond user's appetite | Low (was 9d) | Low (now 14d, but reversible per-phase, M1 alone still solves visible bug) |
| New dependency on dedicated reranker disrupts cutover | (planned in v2.1 thinking) | Eliminated — reranker swap deferred to Phase 11 last step |
