# build-loop architecture — source of truth for the living diagram

You maintain THIS file. The diagram (`docs/build-loop-flow-mockup.html`) regenerates from it
via `python3 scripts/architecture_diagram/generate.py`. Two layers:

- **Components (auto)** — agents, skills, scripts, hooks are discovered from the repo on every
  regenerate. You never hand-list them; the section below is generated.
- **Flow (authored)** — the phases / sub-steps / gates / dispatch-edges / current-vs-proposed
  wiring lives in the fenced `yaml` block under "## Flow". Edit that block, then regenerate.
  References to components are by name; the generator enriches them (model tier, etc.) from the
  repo and the drift-linter rejects any name that doesn't exist.

Format spec + drift gate: `architecture/README.md`.

## Components (auto-generated — do not edit by hand)

<!-- ARCH_COMPONENTS_START -->
<!-- run: python3 scripts/architecture_diagram/generate.py -->
**28 agents · 47 skills · 358 scripts · 21 hooks** (auto-discovered 95b310bc)

<details><summary>agents</summary>

- `advisor` — fable · _updated 2026-06-24 by Tyrone Ross_ — The Frontier (Fable) standing role that AUTHORS and RE-PLANS the Phase 2 plan synthesis. Generating a plan is harder than evaluating one, so the deepest reasoning pays here. The Advisor frames the goal, decomposes the work, builds the dependency graph, MECE-partitions file ownership, and — on a diagnosed *planning miss* — re-plans and issues CORRECTED INSTRUCTIONS (a diff vs the prior plan + the failure evidence), n…
- `alignment-checker` — sonnet · _updated 2026-06-24 by Tyrone Ross_ — Advisory alignment judge for autonomous-iterate-loop queue items (plan §14.4 A). For each candidate item drained from `.build-loop/ux-queue/` + `.build-loop/issues/` + `.build-loop/proposals/`, reads the build's stated intent (`intent.md`, `goal.md`, canonical build-loop-memory constitution context, optional repo `.build-loop/prd.md`) plus the item body and returns a structured verdict (`aligned | misaligned | uncer…
- `api-assessor` — sonnet · _updated 2026-07-02 by Tyrone Ross_ — Use this agent when the debugging symptom involves API endpoints, REST/GraphQL errors, request/response issues, authentication, rate limiting, or server-side route handlers. Examples - "500 error", "endpoint not found", "auth failed", "CORS error".
- `architecture-scout` — sonnet · _updated 2026-07-07 by Tyrone Ross_ — Read-only architecture analyst. Dispatched by build-loop orchestrator with a task type ('baseline', 'chunk-impact', 'review-rules', 'iterate-subgraph', 'learn-sync'). Decides native engine vs NavGator escalation per task. Returns ≤500-word structured JSON envelope. Owns architecture-related side effects (violation capture, lessons sync).
- `assessment-orchestrator` — opus · _updated 2026-06-24 by Tyrone Ross_ — Use this agent when debugging requires multi-domain analysis, when the symptom is unclear about which domain is affected, or when you need to coordinate parallel assessments across database, frontend, API, and performance domains.
- `build-orchestrator` — opus · _updated 2026-07-12 by Tyrone Ross_ — Coordinates the 5-phase development loop for significant multi-step code changes (Assess → Plan → Execute → Review → Iterate, with optional Learn). Review runs seven ordered sub-steps: Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report; Iterate loops back to Review on failure.
- `database-assessor` — sonnet · _updated 2026-07-06 by Tyrone Ross_ — Use this agent when the debugging symptom involves database issues, queries, migrations, schema problems, Prisma errors, PostgreSQL, connection pooling, vector/retrieval indexes, or data integrity. Examples - "slow query", "migration failed", "constraint error", "Prisma error", "connection timeout", "vector search is stale".
- `design-contract-specialist` — sonnet · _updated 2026-06-24 by Tyrone Ross_ — Build-loop-owned designer and sole writer to `.build-loop/app-contract/{ui.md, data.md, traceability.json}`. In Phase 2 it loads `Skill("build-loop:ui-design")` and chooses UI design direction from the needs of the thing being built: user goal, workflow density, data shape, platform, project tokens, mockups, screenshots, local design artifacts, and `skills/build-loop/references/recent-design-structures.md`. Existing…
- `fact-checker` — opus · _updated 2026-07-12 by Tyrone Ross_ — Validates all rendered data, claims, and metrics before completion. Traces data sources to prevent false or unverifiable information reaching users.
- `fix-critique` — opus · _updated 2026-07-12 by Tyrone Ross_ — Use this agent to pressure-test a proposed fix before declaring a bug resolved. Challenges whether the fix addresses the root cause or just a symptom, checks for potential regressions, and verifies evidence exists for the claimed fix. Run after a fix is implemented but before declaring it done.
- `frontend-assessor` — sonnet · _updated 2026-07-02 by Tyrone Ross_ — Use this agent when the debugging symptom involves React, hooks, rendering, UI components, state management, hydration errors, or client-side performance. Examples - "useEffect infinite loop", "component not rendering", "hydration mismatch", "state not updating".
- `implementer` — sonnet · _updated 2026-06-24 by Tyrone Ross_ — Apply a single ux-fix-plan.md (or per-criterion targeted fix plan) from the build-loop Phase 5 work list. One queue entry per invocation. Returns changed files + status. Designed for parallel fan-out (≤4 in flight per orchestrator pass).
- `independent-auditor` — fable · _updated 2026-07-07 by Tyrone Ross_ — LLM-grade escalation path for the boundary-gated commit auditor. The primary mechanism is the deterministic PreToolUse hook script (`scripts/audit_before_commit.py`); this agent fires only when the orchestrator wants a deeper read on a specific commit (e.g., before squash-merge of a multi-chunk build, or when a chunk's diff is unusually large or crosses an architectural boundary). Gathers the same on-disk context th…
- `mock-scanner` — haiku · _updated 2026-06-24 by Tyrone Ross_ — Fast, lightweight scan for residual mock, placeholder, fake, private, or secret data in production/public code paths.
- `optimize-runner` — sonnet · _updated 2026-06-24 by Tyrone Ross_ — Executes the optimization loop. Generates hypotheses, makes atomic changes within scope, measures metrics, keeps improvements or reverts regressions. Runs autonomously until convergence or budget exhaustion.
- `overfitting-reviewer` — opus · _updated 2026-07-12 by Tyrone Ross_ — Reviews optimization results for overfitting, Goodhart violations, and test-gaming shortcuts. Read-only adversarial review.
- `performance-assessor` — sonnet · _updated 2026-07-02 by Tyrone Ross_ — Use this agent when the debugging symptom involves slowness, latency, timeouts, memory leaks, CPU usage, bottlenecks, or optimization needs. Examples - "app is slow", "memory keeps increasing", "timeout errors", "high CPU usage".
- `plan-critic` — fable · _updated 2026-06-24 by Tyrone Ross_ — Adversarial read-only critique of a Phase 2 plan markdown file for non-deterministic issues that grep cannot catch — alternatives considered, MECE scope quality, marker adequacy, and headline drift across sections. Pair with `scripts/plan_verify.py` (deterministic verifier) — run plan-verify first, feed its JSON output to this agent so it doesn't re-derive what's already been checked.
- `promotion-reviewer` — opus · _updated 2026-07-12 by Tyrone Ross_ — Advisory judge for Phase 6 Learn experimental-artifact promotion. Reads a candidate experimental skill or agent (drafted by `self-improvement-architect`), its A/B track record, and the build-loop constitution, then returns a variance-shaped verdict (`approve | rethink | new_approach`). Never blocks — orchestrator policy still requires async user confirmation on the move from `experimental/` to `active/` per the irre…
- `recurring-pattern-detector` — haiku · _updated 2026-06-24 by Tyrone Ross_ — Scans `.build-loop/state.json.runs[]` for patterns that recur across 3+ runs (same phase failing, same diagnostic command, same file churn, same manual user intervention). Emits a structured JSON proposal list. Pattern-matching only — no authoring, no judgment.
- `retrospective-synthesizer` — sonnet · _updated 2026-07-08 by Tyrone Ross_ — Post-push retrospective synthesizer. Reads the session transcript JSONL + state.json + intent + plan after the Phase 4 Report closing push, and writes a structured 11-section retrospective to `.build-loop/retrospectives/<YYYY-MM-DD>/<run-id>.md` plus a ≤5-line `<run-id>.summary.md` surfaced inline. The 9 core sections plus §10 (plugin & tooling observations) and §11 (deterministic-automation candidates) are computed…
- `root-cause-investigator` — inherit · _updated 2026-06-24 by Tyrone Ross_ — Use this agent when a debugging symptom needs deep causal analysis beyond surface-level diagnosis. Builds a causal tree (not a single chain) to explore multiple potential root causes in parallel. Flags when investigation reaches external/environmental boundaries or when internet research is needed. Examples - "why does this keep failing", "what's the real cause", "dig deeper into this error", "this fix didn't stick".
- `scope-auditor` — opus · _updated 2026-07-12 by Tyrone Ross_ — Read-only Plan→Execute boundary check. For every commit that changes a public function/component/type signature, traces every caller-site outside the commit's owned-files, then either confirms `internal_only: true` or appends the missing caller files to the appropriate commit's owned-files list. Prevents the "fan-out scope-blindness" defect class observed in round-2 of dispatch-pattern testing (example-app 2026-05-0…
- `security-reviewer` — fable · _updated 2026-07-06 by Tyrone Ross_ — Adversarial read-only security review of implementer output against OWASP LLM Top 10, OWASP Agentic Top 10, OWASP Web Top 10 (HTTP boundary only), and starter MITRE ATLAS techniques. Runs in Phase 4 Review sub-step A in parallel with `independent-auditor` at `scope: "build"`, but only when Assess flagged `triggers.riskSurfaceChange: true`.
- `self-improvement-architect` — sonnet · _updated 2026-06-24 by Tyrone Ross_ — Takes a pattern proposal from `recurring-pattern-detector` and drafts a concrete experimental SKILL.md (or agent definition) the build-loop can use immediately. Uses the `plugin-dev:skill-development` or `plugin-dev:agent-development` skill as the authoring reference. Writes output to `.build-loop/skills/experimental/<name>/SKILL.md` — project-local, clearly marked experimental, easy for the user to remove.
- `synthesis-critic` — sonnet · _updated 2026-06-24 by Tyrone Ross_ — Read-only model-based critic for the subjective synthesis dimensions (`copy_tone`, `empty_state`) that `attestation_lint.py` cannot grade deterministically. Runs in Phase 4.5 after the attestation lint, only on commits that touch UI files. WARN-only — never blocks a commit.
- `transcript-pattern-miner` — haiku · _updated 2026-06-24 by Tyrone Ross_ — Scans local Claude Code session transcripts for recurring patterns worth promoting to skills, agents, hooks, or feedback notes. Pure stdlib regex miner — no LLM calls, no network. Five categories: user corrections, repeated tool sequences, cross-project file patterns, manual command rituals, and observed secrets (rotation tracker). Output is markdown report + candidates JSON consumed by self-improvement-architect.
- `ui-validator` — sonnet · _updated 2026-06-24 by Tyrone Ross_ — Run deterministic UI scans against the running dev server: layout collisions, touch-target violations, console errors, hydration stability, computed-style diffs vs prior baseline, and per-route visual SSIM. Used at Phase 3 chunk-close on UI-touching chunks and at Phase 4 Review sub-step B on every build that has `uiTarget != null`. Owns its own browser session so authed routes scan correctly.
</details>
<details><summary>skills</summary>

- `agent-rally-point`
- `agent-rally-watcher`
- `api-registry-bridge`
- `architecture-dead`
- `architecture-impact`
- `architecture-review`
- `architecture-rules`
- `architecture-scan`
- `architecture-trace`
- `attribution-standard`
- `authentication`
- `auto-decision-capture`
- `auto-finding-capture`
- `build-loop`
- `building-with-deepagents`
- `capabilities`
- `data-plane-worktrees`
- `debug-loop`
- `debugging-memory`
- `defenseclaw-bridge`
- `focused-loop-builder`
- `handoff`
- `ibr-bridge`
- `knowledge`
- `logging-tracer`
- `mcp-builder`
- `model-bakeoff`
- `model-tiering`
- `native-ax-driver`
- `optimize`
- `plan-verify`
- `plugin-builder`
- `plugin-tests`
- `prd-bridge`
- `recursive-retrospective`
- `repo-closeout`
- `repo-maintenance`
- `research`
- `root-cause-analysis`
- `runtime-parity-verification`
- `security-methodology`
- `security-scan`
- `self-improve`
- `spec-writing`
- `sync-skills`
- `telemetry`
- `ui-design`
</details>
<details><summary>scripts</summary>

- `scripts/_db_url.py`
- `scripts/_paths.py`
- `scripts/_test_helpers.py`
- `scripts/acceptance_probe.py`
- `scripts/agent_ledger.py`
- `scripts/agent_rally.py`
- `scripts/agent_rally_watcher/__init__.py`
- `scripts/agent_rally_watcher/watch.py`
- `scripts/app_pulse/__init__.py`
- `scripts/app_pulse/_alias.py`
- `scripts/app_pulse/changes.py`
- `scripts/app_pulse/channel_paths.py`
- `scripts/app_pulse/checkpoint.py`
- `scripts/app_pulse/inbox.py`
- `scripts/app_pulse/install_git_hook.py`
- `scripts/app_pulse/lifecycle.py`
- `scripts/app_pulse/mece_gate.py`
- `scripts/app_pulse/post.py`
- `scripts/app_pulse/presence.py`
- `scripts/app_pulse/rally.py`
- `scripts/app_pulse/revision.py`
- `scripts/append_milestone.py`
- `scripts/append_run.py`
- `scripts/apple_sourcekit_triage.py`
- `scripts/architecture_diagram/comments_to_backlog.py`
- `scripts/architecture_diagram/drift_lint.py`
- `scripts/architecture_diagram/generate.py`
- `scripts/architecture_freshness.py`
- `scripts/architecture_snapshot.py`
- `scripts/archive_project_plan.py`
- `scripts/artifact_guard.py`
- `scripts/assess_grounding_score.py`
- `scripts/atomic_io.py`
- `scripts/attestation_lint.py`
- `scripts/attribution_stamp.py`
- `scripts/audit_before_commit.py`
- `scripts/audit_memory_invocation.py`
- `scripts/audit_record_verdict.py`
- `scripts/autonomy_gate.py`
- `scripts/backend_health.py`
- `scripts/backlog.py`
- `scripts/backlog/__init__.py`
- `scripts/backlog/assess.py`
- `scripts/backlog/triage.py`
- `scripts/blm.py`
- `scripts/blm_api.py`
- `scripts/branch_closeout_gate.py`
- `scripts/bridge_lesson_to_harness.py`
- `scripts/brief_mece_validator.py`
- `scripts/budget_check.py`
- `scripts/build_acp.py`
- `scripts/build_capability_index.py`
- `scripts/build_capability_registry.py`
- `scripts/build_codex_plugin_artifact.py`
- `scripts/build_report_lint.py`
- `scripts/candidate_aging.py`
- `scripts/capability_classifier.py`
- `scripts/capability_shortlist.py`
- `scripts/capture_arch_violation.py`
- `scripts/charter.py`
- `scripts/check_cache_sync.py`
- `scripts/check_private_slugs.py`
- `scripts/check_runtime_memory_tracking.py`
- `scripts/classify_action.py`
- `scripts/classify_model_tier.py`
- `scripts/cleanup_legacy_memory_stubs.py`
- `scripts/closeout/__init__.py`
- `scripts/closeout/__main__.py`
- `scripts/closeout/status.py`
- `scripts/codex_preflight.py`
- `scripts/collapse_run.py`
- `scripts/collision_scan.py`
- `scripts/commit_state_check.py`
- `scripts/complexity_detector.py`
- `scripts/consolidate_memory.py`
- `scripts/context_bootstrap.py`
- `scripts/context_snapshot.py`
- `scripts/contextual_prepend.py`
- `scripts/coordination_bootstrap.py`
- `scripts/coordination_rally.py`
- `scripts/coordination_status.py`
- `scripts/coordination_watch.py`
- `scripts/cost_ledger_hook.py`
- `scripts/credential_preflight.py`
- `scripts/data_plane.py`
- `scripts/db.py`
- `scripts/db_substrate_lint.py`
- `scripts/deployment_policy.py`
- `scripts/detect_attribution_layers.py`
- `scripts/detect_decision_rot.py`
- `scripts/detect_runtime_server.py`
- `scripts/detect_self_recursive.py`
- `scripts/dev-tools/experiment_metrics.py`
- `scripts/dispatch_fallback.py`
- `scripts/dispatch_identity.py`
- `scripts/dogfood_reload_checkpoint.py`
- `scripts/embed_backend.py`
- `scripts/embed_daemon.py`
- `scripts/enforce_canonical_memory.py`
- `scripts/enforce_retro_signals.py`
- `scripts/exec_state.py`
- `scripts/extensions_approve.py`
- `scripts/extensions_check.py`
- `scripts/extensions_init.py`
- `scripts/extensions_paths.py`
- `scripts/extensions_pending_count.py`
- `scripts/extensions_route.py`
- `scripts/gate_builder.py`
- `scripts/handoff/__main__.py`
- `scripts/hook_budget_lint.py`
- `scripts/hook_hygiene_lint.py`
- `scripts/hooks/git_command_classifier.py`
- `scripts/hooks/plugin_dir_heal.py`
- `scripts/hooks/session_end_retro_sweep.py`
- `scripts/host_capabilities.py`
- `scripts/ibr_quickpass.py`
- `scripts/import_manifest_lint.py`
- `scripts/infer_risk_surface.py`
- `scripts/inject_dependency_cooldown.py`
- `scripts/install_git_hooks.py`
- `scripts/install_marketplace_shim.py`
- `scripts/install_memory.py`
- `scripts/install_self_review.py`
- `scripts/intent_freshness.py`
- `scripts/judgment_gate.py`
- `scripts/keyword_search.py`
- `scripts/knowledge_review.py`
- `scripts/learning_to_draft.py`
- `scripts/lessons_index/__init__.py`
- `scripts/lessons_index/__main__.py`
- `scripts/lessons_index/ingest.py`
- `scripts/lessons_index/query.py`
- `scripts/lessons_index/schema.py`
- `scripts/list_projects.py`
- `scripts/load_current.py`
- `scripts/log_decision.py`
- `scripts/markdown_graph_parser.py`
- `scripts/marketplace_autoupdate.py`
- `scripts/memory_consolidate/__init__.py`
- `scripts/memory_consolidate/__main__.py`
- `scripts/memory_consolidate/async_runner.py`
- `scripts/memory_consolidate/backlinks/__init__.py`
- `scripts/memory_consolidate/backlinks/backlinks.py`
- `scripts/memory_consolidate/classify.py`
- `scripts/memory_consolidate/distill/__init__.py`
- `scripts/memory_consolidate/distill/distill.py`
- `scripts/memory_consolidate/intake.py`
- `scripts/memory_consolidate/lifecycle/__init__.py`
- `scripts/memory_consolidate/lifecycle/lifecycle.py`
- `scripts/memory_consolidate/place.py`
- `scripts/memory_consolidate/promote/__init__.py`
- `scripts/memory_consolidate/promote/promote.py`
- `scripts/memory_context/__init__.py`
- `scripts/memory_facade/__init__.py`
- `scripts/memory_facade/common.py`
- `scripts/memory_facade/debugger.py`
- `scripts/memory_facade/decisions.py`
- `scripts/memory_facade/lessons.py`
- `scripts/memory_facade/runs.py`
- `scripts/memory_facade/semantic.py`
- `scripts/memory_graph/__init__.py`
- `scripts/memory_index.py`
- `scripts/memory_staleness_check.py`
- `scripts/memory_telemetry.py`
- `scripts/memory_update_ledger.py`
- `scripts/memory_writer.py`
- `scripts/mermaid_render.py`
- `scripts/methodology_drift_lint.py`
- `scripts/metric_runner.py`
- `scripts/migrate_add_chunk_context_column.py`
- `scripts/migrate_add_fts_column.py`
- `scripts/migrate_extend_search_vector_with_context.py`
- `scripts/migrate_feedback_to_decisions.py`
- `scripts/migrate_playbooks_to_procedural.py`
- `scripts/migrate_project_identity.py`
- `scripts/migrate_project_memory.py`
- `scripts/migrate_reembed_to_bgem3.py`
- `scripts/migrate_state_schema.py`
- `scripts/model_availability_store.py`
- `scripts/model_overrides.py`
- `scripts/model_resolver.py`
- `scripts/model_taxonomy.py`
- `scripts/optimize_doe.py`
- `scripts/optimize_loop.py`
- `scripts/optimize_suggest_factors.py`
- `scripts/orchestrator_heartbeat.py`
- `scripts/parallelism.py`
- `scripts/perturbation_spotcheck.py`
- `scripts/plan_verify.py`
- `scripts/prepush_test_gate.py`
- `scripts/prior_art.py`
- `scripts/privacy.py`
- `scripts/procedural_governance.py`
- `scripts/project_registry.py`
- `scripts/project_resolver.py`
- `scripts/promote_violation_to_lesson.py`
- `scripts/promotion_queue.py`
- `scripts/prune_codex_plugin_cache.py`
- `scripts/prune_plugin_cache.py`
- `scripts/push_hold.py`
- `scripts/pytest_collect_gate.py`
- `scripts/question_timeout.py`
- `scripts/rally_merge_gate.py`
- `scripts/rally_point/__init__.py`
- `scripts/rally_point/agent_autoreg.py`
- `scripts/rally_point/binary_fetch.py`
- `scripts/rally_point/boundary.py`
- `scripts/rally_point/build_loop_id.py`
- `scripts/rally_point/capability.py`
- `scripts/rally_point/changes.py`
- `scripts/rally_point/channel_paths.py`
- `scripts/rally_point/checkpoint.py`
- `scripts/rally_point/coordination_policy.py`
- `scripts/rally_point/decay.py`
- `scripts/rally_point/discovery_bridge.py`
- `scripts/rally_point/fact_v1.py`
- `scripts/rally_point/hook_budget.py`
- `scripts/rally_point/hooks.py`
- `scripts/rally_point/inbox.py`
- `scripts/rally_point/inject_readiness.py`
- `scripts/rally_point/install_git_hook.py`
- `scripts/rally_point/leadership.py`
- `scripts/rally_point/lifecycle.py`
- `scripts/rally_point/liveness.py`
- `scripts/rally_point/mece_gate.py`
- `scripts/rally_point/orphan_notice.py`
- `scripts/rally_point/peer_collision.py`
- `scripts/rally_point/post.py`
- `scripts/rally_point/presence.py`
- `scripts/rally_point/producer_metadata.py`
- `scripts/rally_point/rally.py`
- `scripts/rally_point/reaper.py`
- `scripts/rally_point/revision.py`
- `scripts/rally_point/roster.py`
- `scripts/rally_point/session_probe.py`
- `scripts/rally_point/sync_rally.py`
- `scripts/rally_point/task_heartbeat.py`
- `scripts/rally_poll_gate.py`
- `scripts/readme_currency_check.py`
- `scripts/recall.py`
- `scripts/recall_graph.py`
- `scripts/recall_multipliers.py`
- `scripts/reference_activation_audit.py`
- `scripts/reference_capture.py`
- `scripts/reference_capture/__init__.py`
- `scripts/reference_capture/capture.py`
- `scripts/reference_capture/horizons.py`
- `scripts/reference_capture/staleness.py`
- `scripts/reference_graph_orphans.py`
- `scripts/regenerate_knowledge_index.py`
- `scripts/report_lint.py`
- `scripts/rerank.py`
- `scripts/rerank_daemon.py`
- `scripts/research_packet.py`
- `scripts/research_trigger.py`
- `scripts/resolve_agent_model.py`
- `scripts/resume_resolver.py`
- `scripts/retrospective/__init__.py`
- `scripts/retrospective/__main__.py`
- `scripts/retrospective/conftest.py`
- `scripts/retrospective/locate.py`
- `scripts/retrospective/sections.py`
- `scripts/retrospective/synthesize.py`
- `scripts/retrospective/write.py`
- `scripts/review_finding_gate.py`
- `scripts/review_trigger.py`
- `scripts/revoke_decision.py`
- `scripts/route_decision.py`
- `scripts/rrf.py`
- `scripts/run_loc_delta.py`
- `scripts/runtime_smoke.py`
- `scripts/runtime_smoke_adapters/__init__.py`
- `scripts/runtime_smoke_adapters/nextjs.py`
- `scripts/runtime_smoke_adapters/sse_consumer.py`
- `scripts/scan_corrections/__init__.py`
- `scripts/scan_corrections/__main__.py`
- `scripts/scan_corrections/detect.py`
- `scripts/scan_findings/__init__.py`
- `scripts/scan_findings/__main__.py`
- `scripts/scan_findings/detect.py`
- `scripts/scan_transcript_for_decisions.py`
- `scripts/script_relevance.py`
- `scripts/security_scan.py`
- `scripts/self_mod_verify.py`
- `scripts/self_review/__init__.py`
- `scripts/self_review/__main__.py`
- `scripts/self_review/efficiency.py`
- `scripts/self_review/gather.py`
- `scripts/self_review/output.py`
- `scripts/self_review/selfscan.py`
- `scripts/semantic_index/__init__.py`
- `scripts/semantic_index/_bench_hybrid.py`
- `scripts/semantic_index/backfill.py`
- `scripts/semantic_index/hybrid.py`
- `scripts/slice_acp.py`
- `scripts/stale_context_check.py`
- `scripts/state_finalize.py`
- `scripts/status_refresh.py`
- `scripts/stop_closeout.py`
- `scripts/supersede_decision.py`
- `scripts/surface_pending_lessons.py`
- `scripts/sync_agent_model_defaults.py`
- `scripts/sync_db_from_files.py`
- `scripts/sync_navgator_lessons.py`
- `scripts/sync_plugin_cache.py`
- `scripts/sync_skills.py`
- `scripts/systemic_rca_doe.py`
- `scripts/systemic_rca_eval.py`
- `scripts/task_surface.py`
- `scripts/temporal_membership.py`
- `scripts/transcript-pattern-miner.py`
- `scripts/transcript_pattern_miner/__init__.py`
- `scripts/transcript_pattern_miner/__main__.py`
- `scripts/transcript_pattern_miner/accuracy/__init__.py`
- `scripts/transcript_pattern_miner/accuracy/eval_accuracy.py`
- `scripts/transcript_pattern_miner/accuracy/fixtures.py`
- `scripts/transcript_pattern_miner/categories.py`
- `scripts/transcript_pattern_miner/io_cache.py`
- `scripts/transcript_pattern_miner/report.py`
- `scripts/transcript_pattern_miner/secrets_scan.py`
- `scripts/transcript_pattern_miner/session.py`
- `scripts/transcript_pattern_miner/textproc.py`
- `scripts/ux_triage.py`
- `scripts/validate_knowledge.py`
- `scripts/verify_deploy.py`
- `scripts/verify_release_surface.py`
- `scripts/version_advisor.py`
- `scripts/version_drift_warning.py`
- `scripts/wake_scheduler.py`
- `scripts/wiki_client.py`
- `scripts/wiki_local.py`
- `scripts/working_branch_echo.py`
- `scripts/working_state_writer.py`
- `scripts/worktree_guard.py`
- `scripts/worktree_isolation_lint.py`
- `scripts/worktree_reaper/__init__.py`
- `scripts/worktree_reaper/__main__.py`
- `scripts/worktree_reaper/reaper.py`
- `scripts/worktree_reaper/tests/__init__.py`
- `scripts/write_cost_ledger_row.py`
- `scripts/write_decision/__init__.py`
- `scripts/write_decision/__main__.py`
- `scripts/write_decision/cli.py`
- `scripts/write_decision/constants.py`
- `scripts/write_decision/dbwrite.py`
- `scripts/write_decision/frontmatter.py`
- `scripts/write_decision/ids.py`
- `scripts/write_decision/io_ops.py`
- `scripts/write_decision/schema.py`
- `scripts/write_decision/taxonomy.py`
- `scripts/write_decision/writer.py`
- `scripts/write_run_entry/__init__.py`
- `scripts/write_run_entry/__main__.py`
- `scripts/write_run_entry/execstate.py`
- `scripts/write_run_entry/idtime.py`
- `scripts/write_run_entry/iohelpers.py`
- `scripts/write_run_entry/validators.py`
- `scripts/write_subagent_result.py`
</details>
<!-- ARCH_COMPONENTS_END -->

## Flow (authored — edit the yaml below, then regenerate)

<!-- arch:flow -->
```yaml
# Authored architecture manifest — the semantic source of truth for the build-loop flow diagram.
# Phases / sub-steps / gates / dispatch-edges / current-vs-proposed live here (not grep-able from prose).
# The AUTO-DERIVED layer (agent model tiers, hook events) is merged in by scripts/architecture_diagram/generate.py
# from agents/*.md frontmatter, hooks/hooks.json, and scripts/model_overrides.py.
# Drift between this manifest and the real agents/hooks is caught by scripts/architecture_diagram/drift_lint.py.
#
# agent references are [name, tier, by]; tier "" means "fill from agents/*.md frontmatter" (auto-derived).
# by = Orchestrator | "Auditor (independent)".

pipeline:
  in: ["user goal / prompt", "repo + git state", "build-loop memory"]
  out: ["committed, validated change", "## Learn outcome + drafts"]

# ids whose existence is the redesign (not in current build-loop). Rendered with a ⊕ PROPOSED badge.
proposed: [hook, auditor, gate, "p1.risk", "p2.threat", "p3.impl", "p3.audit", "p4.cov", "p6.chart"]

# transition gates rendered as a diamond ON the connector between phases.
gate_after:
  p2: { step: gate,        label: "Plan-review",  tier: "T1 · block",   prop: true }
  p3: { step: "p3.audit",  label: "Commit audit", tier: "T2 · T1 risk", prop: true }
  p4: { step: "p4.b",      label: "Validate",     tier: "T0",           note: "fail → Iterate" }
  pd: { step: "pd.push",   label: "Prod push",    tier: "T1 · human" }
  p6: { step: "p6.signoff",label: "Promote?",     tier: "T1 · human" }

# top-level role cards (orchestrator + co-launched auditor + the launch hook)
roles:
  hook:
    proposed: true
    type: "Input · the one new hook"
    name: "⎇ SessionStart · auditor launch"
    desc: "session-start-auditor.sh — fires once per session, spawns the auditor peer. Framework-owned activation."
  orch:
    type: "Top-level role #1"
    name: "① Orchestrator"
    desc: "The only dispatcher. Owns phases, file-ownership, commits, final call. Does NOT dispatch the auditor — only reads its verdict and blocks."
  auditor:
    proposed: true
    type: "Top-level role #2"
    name: "② Independent Auditor"
    desc: "TODAY (current code): the independent-auditor is DISPATCHED by the orchestrator at Review-A (scope:build) via the auditor ladder, consolidated from commit-auditor in PR #47. PROPOSED (this redesign): co-launch it as a separate peer process via the SessionStart hook so it can't be dispatched or skipped — reviews the plan (the gate), spot-audits, checks coverage, seals the build; verdicts ride the vendored coord channel (no agent-rally-point needed)."

# subagent registry: goal + what it does (rendered when a chip is clicked).
subagents:
  architecture-scout: { goal: "Keep the orchestrator's model of the codebase current.", does: "Read-only architecture baseline / impact / iterate-subgraph / learn-sync scans; escalates to NavGator when needed." }
  design-contract-specialist: { goal: "Keep UI + data contracts truthful.", does: "Writes the app-contract (ui.md / data.md / traceability); reconciles deltas after implementation." }
  advisor: { goal: "Produce the best possible plan on hard work.", does: "Frontier plan synthesis / re-plan: frames the goal, decomposes, builds the dependency graph, MECE-partitions ownership." }
  plan-critic: { goal: "Catch plan defects grep can't.", does: "Adversarial read-only critique of the plan — alternatives, MECE quality, marker adequacy, headline drift." }
  scope-auditor: { goal: "Stop fan-out scope blindness.", does: "Traces every caller of a changed public signature; expands owned-files so nothing downstream is missed." }
  security-reviewer: { goal: "Catch security regressions before they ship.", does: "Adversarial review vs OWASP LLM / Agentic / Web + ATLAS when riskSurfaceChange is set." }
  independent-auditor: { goal: "Be the un-skippable proof the build is real.", does: "Reviews the plan (the gate), runs risk-weighted random spot-audits, verifies coverage, seals chunk/build scope, self-calibrates." }
  implementer: { goal: "Apply one bounded change correctly.", does: "Edits only owned files against a written spec, returns an envelope, never commits. Fan-out scales with the work — PROPOSED: no fixed cap (10/20/50+), bounded only by clean MECE segmentation + per-implementer goal/test-criteria/standards/outcome + a defined merge-and-acceptance plan; TODAY capped at ≤4." }
  synthesis-critic: { goal: "Judge subjective UI synthesis.", does: "WARN-only check that claimed copy_tone / empty_state actually show up in the diff." }
  fact-checker: { goal: "No false or unverifiable data reaches users.", does: "Traces every rendered metric / claim to its real data source." }
  mock-scanner: { goal: "No mock / placeholder data in production paths.", does: "Fast scan for fake / fixture / secret data in public code." }
  ui-validator: { goal: "Prove the UI actually works.", does: "Deterministic scans on the running dev server: collisions, touch targets, console errors, hydration, SSIM." }
  domain-assessors: { goal: "Diagnose a failure in one domain.", does: "Read-only evidence in database / api / frontend / performance; hands the fix to implementer, never ships code." }
  root-cause-investigator: { goal: "Find the real cause when a fix won't hold.", does: "Builds a causal tree across candidate causes; carries WebSearch for upstream-bug research." }
  alignment-checker: { goal: "Keep queued work on-intent.", does: "Advisory aligned / misaligned / uncertain verdict per drained queue item; never blocks." }
  recurring-pattern-detector: { goal: "Surface what's worth automating.", does: "Scans runs[] for patterns recurring across 3+ runs; emits structured proposals." }
  self-improvement-architect: { goal: "Turn a pattern into a usable artifact.", does: "Drafts an experimental SKILL / agent with A/B tracking that the user keeps or removes." }
  promotion-reviewer: { goal: "Gate experimental → active honestly.", does: "Variance-shaped verdict (approve / rethink / new_approach) on a promotion candidate; advisory." }
  retrospective-synthesizer: { goal: "Capture durable run lessons.", does: "Writes the 9-section retrospective + auto-drafts enforce-candidates from anything prompted ≥2×." }
  overfitting-reviewer: { goal: "Stop test-gaming in optimize mode.", does: "Read-only review of optimization results for Goodhart effects and overfitting shortcuts." }

# hook purpose overrides (event is auto-derived from hooks.json; ⚠ purposes inferred from script names).
hook_overrides:
  "session-start-auditor.sh": { proposed: true, purpose: "⊕ PROPOSED — launches the independent auditor peer process once per session." }
  "session-start-retrieval.sh": { purpose: "load memory + retrieval context at session start" }
  "session-start-memory.sh": { purpose: "bootstrap the build-loop memory root" }
  "pre-edit-architecture.sh": { purpose: "architecture impact pre-check before an edit" }
  "pre-edit-rally-point.sh": { purpose: "rally claim / deconflict before a write" }
  "pre_bash_dispatch.sh": { purpose: "guard / classify bash + dispatch actions" }
  "(post) state.json updater": { event: "PostToolUse · Bash", purpose: "track changed files into state.json" }
  "(stop) state_finalize.py": { event: "Stop", purpose: "finalize run state" }
  "(stop) commit_state_check.py": { event: "Stop", purpose: "verify commit / state consistency" }
  "(stop) closeout.sh": { event: "Stop", purpose: "run closeout at stop" }
  "(post) post-push-closeout.sh": { event: "PostToolUse · Bash", purpose: "trigger closeout + retrospective after a push" }

phases:
  - id: p1
    "no": "Phase 1"
    name: Assess
    lane: Orchestrator
    desc: "Reads live repo, memory, docs. Writes intent + goal + acceptance probes + triggers. NEW: emits a per-chunk risk score that feeds the auditor's sampling weight."
    in: ["repo + prompt", "memory", "docs"]
    out: ["intent.md", "goal.md", "probes", "triggers", "risk score"]
    agents: [["architecture-scout", "", "Orchestrator"], ["design-contract-specialist", "", "Orchestrator"]]
    steps:
      - { id: "p1.read", name: "Read live state", kind: process, desc: "The orchestrator reads the live repo, past build-loop memory, and any docs so the plan is grounded in the project's current state.", hooks: ["session-start-retrieval.sh", "session-start-memory.sh"], agents: [] }
      - { id: "p1.intent", name: "Write intent.md", kind: process, desc: "The orchestrator writes intent.md: the goal, what is explicitly out of scope, and which priorities to optimize for.", hooks: [], agents: [] }
      - { id: "p1.goal", name: "Write goal.md + acceptance probes", kind: process, desc: "The orchestrator writes goal.md with three to five pass-or-fail criteria, plus repeatable probes that will later prove each one.", hooks: [], agents: [] }
      - { id: "p1.risk", name: "Emit risk score per chunk", kind: new, desc: "The orchestrator scores each planned chunk for risk (how much it changes, how far its effects reach, its past defect rate) so the auditor can later inspect the riskier chunks more often.", hooks: [], agents: [] }
      - { id: "p1.scout", name: "Dispatch baseline scans", kind: dispatch, desc: "The orchestrator sends architecture-scout to map how the code fits together and design-contract-specialist to record the current UI and data contracts, giving later changes a baseline to check against.", hooks: [], agents: [["architecture-scout", "", "Orchestrator"], ["design-contract-specialist", "", "Orchestrator"]] }

  - id: p2
    "no": "Phase 2"
    name: Plan
    lane: "Orchestrator + Advisor (Frontier)"
    hasGate: true
    desc: "MECE partition + dependency order. Advisor ladder synthesizes at Frontier (Fable) when stakes-gated. NEW: security threat-model shifts left here. Ends with the mandatory Plan-Review gate."
    in: ["intent.md", "goal.md", "architecture baseline"]
    out: ["plan (MECE + dep order)", "PASS → Execute"]
    agents: [["advisor", "", "Orchestrator"], ["plan-critic", "", "Orchestrator"], ["scope-auditor", "", "Orchestrator"], ["independent-auditor", "", "Auditor (independent)"]]
    steps:
      - { id: "p2.mece", name: "MECE partition + dependency order", kind: process, desc: "The orchestrator splits the work so each file has exactly one owning agent (so two agents never edit the same file) and orders the chunks by what depends on what.", hooks: [], agents: [] }
      - { id: "p2.advisor", name: "Advisor plan synthesis", kind: dispatch, desc: "On high-stakes work the orchestrator hands plan-writing to the advisor agent, which runs on the strongest reasoning tier; on routine work the orchestrator writes the plan itself.", hooks: [], agents: [["advisor", "", "Orchestrator"]] }
      - { id: "p2.threat", name: "Security threat-model", kind: new, desc: "When the change touches security, security-reviewer threat-models the design now (trust boundaries and who is allowed to do what), because design flaws are far cheaper to fix before any code is written; the code-level security check still runs later at Review.", hooks: [], agents: [["security-reviewer", "", "Orchestrator"]] }
      - { id: "p2.verify", name: "plan-verify + plan-critic + scope-auditor", kind: dispatch, desc: "A script first checks the plan against fixed rules; then plan-critic reads it for problems a script cannot catch (missing alternatives, vague scope); then scope-auditor confirms the plan already lists every file that callers of the changed code will force you to touch.", hooks: [], agents: [["plan-critic", "", "Orchestrator"], ["scope-auditor", "", "Orchestrator"]] }
      - { id: gate, name: "Plan-Review gate", kind: gate, tier: "T1", desc: "PROPOSED. Auditor (independent peer) reviews the plan and posts the verdict; orchestrator cannot enter Execute until PASS (coordination-protocol block, not a hook). TODAY: there is no mandatory auditor plan-gate — current Plan gating is plan-verify + plan-critic + scope-auditor, and the independent-auditor runs later, at Review-A (build scope).", hooks: [], agents: [["independent-auditor", "", "Auditor (independent)"]], branches: "PASS → Execute\nfail → re-plan" }

  - id: p3
    "no": "Phase 3"
    name: Execute
    lane: "Orchestrator + Auditor (sampled)"
    hasGate: true
    desc: "N implementers on owned files. Single-writer commit protocol. NEW: per-commit audit is sampled. BUG FIX: protocol still names the retired commit-auditor (PR #47) — repoint to independent-auditor."
    in: ["approved plan"]
    out: ["commits", "implementer envelopes"]
    agents: [["implementer ×N", "", "Orchestrator"], ["synthesis-critic", "", "Orchestrator"], ["independent-auditor", "", "Auditor (independent)"]]
    steps:
      - { id: "p3.impl", name: "Dispatch N implementers (parallel)", kind: dispatch, desc: "PROPOSED: no fixed cap on parallel implementers — scale to as many as the work supports (10, 20, 50+) gated by: clean MECE file/chunk segmentation, a per-implementer spec (goal · owned files · test criteria · standards · expected outcome), and a defined merge + acceptance plan. TODAY: capped at ≤4 parallel (Mode A fan-out limit + the standing '4 parallel max' rule). Implementers edit owned files only, return envelopes, never commit.", hooks: ["pre-edit-architecture.sh", "pre-edit-rally-point.sh"], agents: [["implementer ×N", "", "Orchestrator"]] }
      - { id: "p3.commit", name: "Single-writer commit (per chunk)", kind: process, desc: "Only the orchestrator commits to git; the implementer agents hand back their changes for the orchestrator to commit, so two agents never collide on the same commit.", hooks: ["pre_bash_dispatch.sh", "(post) state.json updater"], agents: [] }
      - { id: "p3.verify", name: "verify-scope / verify-landed", kind: gate, tier: "T0", desc: "A deterministic check confirms each implementer's edit actually landed in the files it was assigned and nowhere else.", hooks: [], agents: [] }
      - { id: "p3.synth", name: "synthesis-critic (UI commits)", kind: dispatch, desc: "On UI commits, synthesis-critic checks that the tone and empty-states the implementer claimed actually appear in the change; it warns but never blocks.", hooks: [], agents: [["synthesis-critic", "", "Orchestrator"]] }
      - { id: "p3.audit", name: "Commit audit", kind: gate, tier: "T2", desc: "PROPOSED sampling: 100% on riskSurface, else risk-weighted random draw. TODAY: the independent-auditor runs per-commit as an advisory check with a trivial-change bypass (consolidated from the retired commit-auditor in PR #47) — it is not sampled.", hooks: [], agents: [["independent-auditor", "", "Auditor (independent)"]], branches: "audited → seal\nelse → T0 only" }

  - id: p4
    "no": "Phase 4"
    name: Review
    lane: "Orchestrator + Auditor (seal)"
    hasGate: true
    desc: "Seven sub-steps A–G. Verdict subagents are orchestrator-dispatched children; the auditor seals at build scope and runs the coverage check."
    in: ["commits"]
    out: ["verdicts", "report"]
    agents: [["independent-auditor", "", "Auditor (independent)"], ["security-reviewer", "", "Orchestrator"], ["fact-checker", "", "Orchestrator"], ["mock-scanner", "", "Orchestrator"], ["ui-validator", "", "Orchestrator"]]
    steps:
      - { id: "p4.a", name: "A · Critic (+ coverage check)", kind: dispatch, desc: "CURRENT: independent-auditor at build scope + security-reviewer when riskSurfaceChange (dispatched here via the auditor ladder — this is the auditor's real engagement today). PROPOSED add-on: a coverage check that verifies every required verdict agent actually fired.", hooks: [], agents: [["independent-auditor", "", "Auditor (independent)"], ["security-reviewer", "", "Orchestrator"]] }
      - { id: "p4.b", name: "B · Validate", kind: gate, tier: "T0", desc: "The orchestrator re-runs the Assess probes and smoke-tests the running app to prove each criterion passes, and ui-validator checks the UI; only what these cannot decide is escalated to an LLM judge.", hooks: [], agents: [["ui-validator", "", "Orchestrator"]] }
      - { id: "p4.d", name: "D · Fact-check + mock-scan", kind: dispatch, desc: "fact-checker traces every number and claim the app shows back to a real source, and mock-scanner sweeps for leftover fake or placeholder data, so nothing false reaches users.", hooks: [], agents: [["fact-checker", "", "Orchestrator"], ["mock-scanner", "", "Orchestrator"]] }
      - { id: "p4.fg", name: "F · Auto-resolve → G · Report", kind: process, desc: "The orchestrator auto-resolves the safe leftover issues, then writes the final report: the scorecard, a version-bump recommendation, and a one-line Learn outcome.", hooks: [], agents: [] }

  - id: p5
    "no": "Phase 5"
    name: Iterate
    lane: Orchestrator
    loop: true
    desc: "Phase 5 is a LOOP, not a forward stage: on a Review failure it escalates a stuck cascade by failure count, applies a fix, and loops BACK to Review (up to 5 classic / 25 autonomous). On pass, the run proceeds to Closeout. Current behavior — accurate as drawn."
    in: ["review failures"]
    out: ["fixes → back to Review", "followup/ overflow"]
    agents: [["architecture-scout", "", "Orchestrator"], ["domain-assessors", "", "Orchestrator"], ["root-cause-investigator", "", "Orchestrator"], ["implementer", "", "Orchestrator"]]
    steps:
      - { id: "p5.cascade", name: "Stuck cascade", kind: process, desc: "When a check fails, the orchestrator escalates in steps: first add the missing logging, then re-check past incidents in memory, then send architecture-scout if the failure crosses code layers.", hooks: [], agents: [["architecture-scout", "", "Orchestrator"]] }
      - { id: "p5.domain", name: "2-fail: domain assessors", kind: dispatch, desc: "After two failures on the same criterion, the orchestrator dispatches the matching domain assessors (database, API, frontend, performance) in parallel to diagnose the cause.", hooks: [], agents: [["domain-assessors", "", "Orchestrator"]] }
      - { id: "p5.rca", name: "3-fail: causal-tree investigation", kind: dispatch, desc: "After three failures, root-cause-investigator builds a cause tree and may search the web for known upstream bugs before another attempt is made.", hooks: [], agents: [["root-cause-investigator", "", "Orchestrator"]] }
      - { id: "p5.fix", name: "Apply fix → loop to Review", kind: process, desc: "An implementer applies the fix and the loop returns to Review; on pass the run continues, and if the attempt cap is reached the remaining work is parked as follow-up.", hooks: [], agents: [["implementer", "", "Orchestrator"]] }

  - id: pd
    "no": "Phase D"
    name: Closeout
    lane: Orchestrator
    hasGate: true
    desc: "Runs by default. 9-step closeout; collapses branches to main and reaps the auditor peer."
    in: ["run state"]
    out: ["collapsed branch → main", "archived coord"]
    agents: []
    steps:
      - { id: "pd.reap", name: "Reap presence + collapse branches", kind: process, desc: "The orchestrator shuts down its watchers, releases its coordination lease, stops the auditor peer, and collapses the run's branches back into a single main branch.", hooks: ["(stop) state_finalize.py", "(stop) commit_state_check.py", "(stop) closeout.sh"], agents: [] }
      - { id: "pd.push", name: "Production push?", kind: gate, tier: "T1", desc: "Pushing to production always asks a human first; this gate is never sampled and never auto-approved.", hooks: ["(post) post-push-closeout.sh"], agents: [], branches: "approved → push\nelse → hold" }

  - id: p6
    "no": "Phase 6"
    name: Learn
    lane: "Orchestrator + Fable signoff"
    hasGate: true
    desc: "Always emits a ## Learn line. NEW: feeds the per-phase escape-rate control chart that adapts T2 sampling."
    in: ["run history (runs[])"]
    out: ["## Learn line", "experimental drafts", "control-chart update"]
    agents: [["recurring-pattern-detector", "", "Orchestrator"], ["self-improvement-architect", "", "Orchestrator"], ["promotion-reviewer", "", "Orchestrator"], ["retrospective-synthesizer", "", "Orchestrator"]]
    steps:
      - { id: "p6.detect", name: "Cheap detector + consolidate (always)", kind: process, desc: "recurring-pattern-detector scans recent runs for repeating problems and the orchestrator consolidates the lessons into memory; this step always runs.", hooks: [], agents: [["recurring-pattern-detector", "", "Orchestrator"]] }
      - { id: "p6.draft", name: "Draft experimental artifact", kind: dispatch, desc: "When a pattern recurs often enough, self-improvement-architect drafts a candidate new skill or agent to address it.", hooks: [], agents: [["self-improvement-architect", "", "Orchestrator"]] }
      - { id: "p6.signoff", name: "Promotion signoff", kind: gate, tier: "T1", desc: "promotion-reviewer judges the draft, but turning it on for real always requires your confirmation.", hooks: [], agents: [["promotion-reviewer", "", "Orchestrator"]], branches: "approve → promote\nelse → keep experimental" }
      - { id: "p6.chart", name: "Update escape-rate control chart", kind: new, desc: "build-loop tracks how often defects slip past each phase; when a phase's escape rate climbs too high, the auditor samples that phase more often on future runs.", hooks: [], agents: [] }

# canonical aliases: chip display name -> agents/*.md basename (or "group" for synthetic multi-agent chips)
agent_aliases:
  "implementer ×N": implementer
  "implementer ×≤4": implementer
  "domain-assessors": group        # represents database/api/frontend/performance-assessor

# synthetic group chips -> the real agents/*.md they stand for (used for drift coverage)
agent_groups:
  "domain-assessors": [database-assessor, api-assessor, frontend-assessor, performance-assessor]

# agents intentionally NOT on the main build spine (coordinators, mode-specific, debug-only).
# Excluded from the coverage WARN so the linter only flags genuinely-forgotten agents.
coverage_exempt:
  - build-orchestrator        # the orchestrator role itself
  - assessment-orchestrator   # debug-mode coordinator
  - optimize-runner           # optimize-mode coordinator
  - overfitting-reviewer      # optimize mode only
  - fix-critique              # debug mode only
  - alignment-checker         # autonomous-iterate queue only
  - transcript-pattern-miner  # offline pattern mining
```
