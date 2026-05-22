# MCP Security Model — stage-aware controls

Derived from the NSA paper *"MCP: Security Design Considerations"* (May 2026) and the OWASP LLM / Agentic Top 10. This reference is the **rubric** the mcp-builder skill runs as a preflight and the `security-reviewer` agent grades against.

> Source note: the control split and decision-table cells were specified by the build-loop plan that introduced this file, then cross-checked against the NSA paper directly (2026-05-22). The attack-class table and Tier 1/Tier 2 controls are faithful to the paper's documented concerns.

## What the NSA paper documents (attack classes)

| Attack class | What it is |
|---|---|
| Parameter injection | Untrusted/ambiguous input forwarded into a tool's typed parameters, escaping the intended instruction/data boundary |
| Toolchain pivot / naming collision | A malicious or look-alike tool registers a colliding name, so calls intended for a trusted tool route to a parasitic one |
| CVE-2025-49596 (RCE) | A remote-code-execution vulnerability in **MCP-Inspector**, the MCP-server testing toolchain: it accepted unverified inputs, letting a crafted message trigger RCE. Fixed in version 0.14.1. The paper cites it as proof that well-known weaknesses resurface when AI toolchains skip security hygiene. |
| Missing RBAC | No access-control layer; any caller gets any tool at full privilege |
| Opaque approval drift | The set of capabilities a server exposes changes over time without a re-approval workflow, so the user's original consent silently no longer matches reality |

## Output contract

mcp-builder emits a checklist split into two lists:

- **"Mandatory for this build"** — all of Tier 1, plus every Tier 2 cell that resolved to `mandatory-now`.
- **"Plan-now, deferred"** — every Tier 2 cell that resolved to `design-now-implement-later` (interface stubbed, trust boundary documented, implementation deferred).

The checklist is **advisory**. It routes to the build-loop run report (Notes from judges / `state.json`). It is **never** an `AskUserQuestion` and **never** a hard gate — per build-loop's "advisory checks are automated" rule.

---

## Tier 1 — always mandatory

These apply to **every** MCP server regardless of app type or dev stage — including a throwaway consumer prototype. They are cheap, and skipping them is the difference between "small bug" and "RCE".

1. **JSON-schema parameter validation.** Every tool's `inputSchema` is a strict, specific JSON Schema. Validate arguments against it before the tool body runs. Reject on mismatch.
2. **Block parameter forwarding from ambiguous/user-supplied sources.** Never pass a raw user string, prompt fragment, or unvalidated LLM output straight into a tool parameter that reaches a filesystem path, shell, URL, query, or another tool. Re-derive the value from a trusted, typed source.
3. **Insecure-deserialization hygiene.** No `pickle`, no `eval`-backed parsers, no `Function(...)` over inbound data. Deserialize only with schema-bound, format-restricted parsers. (Part of the untrusted-input-reaches-executor class — the failure mode behind CVE-2025-49596.)
4. **Unique / pinned tool identifiers.** Tool names are stable, namespaced, and collision-resistant. Pin the server identity. This is the naming-collision + parasitic-toolchain defense.
5. **Tool-execution sandboxing.** Run tool bodies under least privilege — seccomp / AppArmor / a restricted user / a container — so a compromised tool cannot reach the host.
6. **stderr-only logging.** `stdout` is the JSON-RPC channel. Anything else corrupts the protocol *and* can leak data into the transport.
7. **No network at startup.** Do not validate credentials or call out in the constructor / `initialize`. Lazy-init on first tool call. (Also a load-time-latency fix.)
8. **Treat every tool output as untrusted input to the next stage.** A tool's return value is attacker-influenceable. Validate it before it feeds a prompt, another tool, or a renderer. (Cascading-failure defense.)
9. **Task / data isolation across trust zones — no shared mutable context.** Tools and connected servers must not blend context. A server only sees the data its current task needs; do not pass the whole client context to every server, and do not let one server's output silently become another's task input without origin + scope verification. This is the paper's *unverified task propagation* / cross-server context-bleed risk — demonstrated by the WhatsApp-MCP exploit, where a malicious server connected alongside a trusted one coerced the client into leaking message data. Per-task scoping also limits blast radius when a single server is compromised.

---

## Tier 2 — stage / type-gated decision table

Each control resolves to one of:

- `mandatory-now` — implement in this build.
- `design-now-implement-later` — document the trust boundary and stub the interface now; implement before the next stage.
- `optional` — defense-in-depth; note it, do not require it.

`app_type` ∈ {`consumer`, `enterprise`}. `stage` ∈ {`prototype`, `MVP`, `growth`, `production`}.

| Control | consumer / prototype | consumer / MVP | consumer / growth+prod | enterprise / prototype | enterprise / MVP | enterprise / growth | enterprise / production |
|---|---|---|---|---|---|---|---|
| Access control + RBAC | optional | optional | design-now-implement-later | design-now-implement-later | design-now-implement-later | mandatory-now | mandatory-now |
| Token lifecycle (refresh / revoke / rotate) | optional | design-now-implement-later | mandatory-now | design-now-implement-later | mandatory-now | mandatory-now | mandatory-now |
| Capability-change approval workflow | optional | optional | design-now-implement-later | optional | design-now-implement-later | mandatory-now | mandatory-now |
| Signed + replay-protected messages | optional | optional | optional | optional | design-now-implement-later | design-now-implement-later | mandatory-now |
| Audit-log depth (→ SIEM) | optional | optional | design-now-implement-later | design-now-implement-later | design-now-implement-later | mandatory-now | mandatory-now |
| Egress filtering proxy / DLP | optional | optional | optional | optional | design-now-implement-later | design-now-implement-later | mandatory-now |
| Local-only MCP instance for sensitive data | optional | optional | design-now-implement-later (if `data_sensitivity = high`) | design-now-implement-later | mandatory-now (if `data_sensitivity = high`) | mandatory-now (if `data_sensitivity = high`) | mandatory-now (if `data_sensitivity = high`) |
| Rate limiting / DoS protection | optional | design-now-implement-later | mandatory-now | design-now-implement-later | mandatory-now | mandatory-now | mandatory-now |

**Worked example — RBAC (the user's case).** A consumer-type server resolves RBAC to `optional`. An enterprise server still at MVP resolves it to `design-now-implement-later`: document the trust boundary, stub the RBAC interface, but do not block the MVP on a full implementation. An enterprise server in production resolves it to `mandatory-now`.

When `data_sensitivity = high`, escalate any `optional` cell in that control's row by one step (`optional` → `design-now-implement-later`, `design-now-implement-later` → `mandatory-now`).

---

## Signal inference rules (no config file)

`app_type`, `stage`, and `data_sensitivity` are **inferred from repo signals** — there is no security config file to author or maintain. Run these against the target repo:

### app_type — `enterprise` vs `consumer`

`enterprise` if **either** holds; otherwise `consumer`:

- An auth library is present (`better-auth`, `@clerk/*`, `next-auth`, `@auth/*`, `passport`, `@supabase/auth-*`, `lucia`, …) **AND** multi-tenant signals exist: an `org` / `organization` / `team` / `workspace` / `tenant` table or model, or RBAC scaffolding (a `roles` / `permissions` / `policies` table, a `role`-typed column, a `casl` / `oso` / `permit` dependency).
- Regulated-data keywords appear in the repo: `HIPAA`, `SOC2`, `PCI`, `EHR`, `PHI`, `GDPR` with a DPA, or domain models clearly holding medical / financial / government records.

### stage — `prototype` → `MVP` → `growth` → `production`

Derive from the *highest* tier whose signals are present:

| Stage | Signals |
|---|---|
| `prototype` | No tests, no CI config, no deploy manifest. Often a single dev. |
| `MVP` | A test directory with real tests, OR a CI workflow file (`.github/workflows/*`). |
| `growth` | CI **and** a deploy manifest (`vercel.json`, `railway.json`, `fly.toml`, `Dockerfile` + a deploy step), OR a staging environment. |
| `production` | All of the above **and** a monitoring SDK (`@sentry/*`, `posthog`, `datadog`, OpenTelemetry) **or** a live custom domain referenced in config. |

### data_sensitivity — `high` vs `normal`

`high` if the repo handles PII, EHR/PHI, financial records, or secrets beyond its own service credentials — detected via table/model names (`patients`, `ssn`, `card_number`, `payment_methods`, `medical_*`), regulated-data keywords, or a secrets-management dependency wired to user data. Otherwise `normal`.

---

## How security-reviewer consumes this

When `triggers.riskSurfaceChange` is set **and** the diff introduces a new MCP server, the `security-reviewer` agent grades the diff against:

- **Tier 1** — always, every control. A missing Tier 1 control is at least `HIGH`.
- **Tier 2** — only the cells that the inferred `app_type` / `stage` / `data_sensitivity` resolved to `mandatory-now`. A missing `mandatory-now` control is a finding; a missing `design-now-implement-later` control is `LOW` (note it, do not block) provided the interface stub + boundary doc exist.
