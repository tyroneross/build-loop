# Handoff: Fundamentals + Harness verified-remaining improvements

Implementer pointers. Read the plan for full context; this file is the per-feature build order.

- **When implementing F-02** (build-loop verify hardening), read ADR-01 and satisfy T-02: add an advisory `oracle_completeness` field to each verify verdict and a perturbation spot-check helper (rename identifiers / reorder inputs) for high-risk outcome gates. WARN-only, never blocks. Colocate `test_<name>.py` — a gamed-outcome fixture must fail the perturbation check; a thin-oracle fixture must record lower completeness. Reads-from: build-loop verify verdict object + run-state (verified).

- **When implementing F-05** (NavGator depth metric), satisfy T-05: add a module-depth / interface-width metric (public-symbol-count ÷ internal-LOC proxy) + `shallow-cluster` flag to `navgator/src/architecture-insights.ts`, surface via `navgator review` (`mcp/tools.ts`). Reads-from: `ArchitectureComponent`/`CodeLocation`/`ArchitectureIndex` in `types.ts` (verified). Ship ADVISORY — it feeds P4 guidance, it is not a gate. Unit-test both a many-tiny-modules fixture (flag true) and a deep-module fixture (flag false).

- **When implementing F-06** (harness report line), satisfy T-06: add a `harness:{}` config line (tool-set, context-budget) to the Phase-4 run report beside the existing `models:{}` line. Snapshot-test it.

- **When implementing F-07** (policy/doc edits P1/P4/P5/P7): each edit cites the verified commit/file and links `build-loop-memory/research/2026-07-06-ai-coding-fundamentals-and-harness-claims.md`. Frame module/quality guidance as a COST lever, not an accuracy gate (per the minimal-pair evidence).

- **V-00** (verify-only, do first): re-test qwen2.5-coder:32b on /api/chat now that `dialect.rs` exists; append a dated row to `harness-gaps.md`. No code.

Do NOT rebuild the harness tool-call parser or structural validators — both shipped (`dialect.rs` `8f3e525`; `eval/validator.rs`). Confirm BOTH before assuming otherwise: `cargo test -p provider dialect::` (parser) AND `cargo test -p eval` (structural validators — empty-reply/phantom-completion/malformed findings). If either is red/absent, the corresponding already-done claim is stale — re-open it.
