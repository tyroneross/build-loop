#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Explicit setup/reconciliation for build-loop Git hooks (idempotent).
# Installs: pre-push (deploy-HOLD gate + deterministic pre-push TEST gate, composed
# in one hook body — the test gate added per the 2026-06-29 RCA so new sessions get
# the prevent-before-merge control automatically) + the artifact-guard pre-commit segment
# (regenerate-and-restage checked-in artifacts; block undeclared third-party
# imports — chained so it coexists with the Rally Point hygiene and independent
# commit-audit segment).
# Skips silently when: not inside a git repo, Python unavailable, hook
# source not found, or a foreign (non-build-loop) pre-push is already
# installed (requires --force to overwrite; we never force on auto-install).
# This explicit maintenance command is not registered as a lifecycle hook.
. "$(dirname "$0")/_bounded_hook.sh"
bl_bound_hook "${BUILD_LOOP_GIT_HOOKS_DEADLINE_MS:-15000}" 15000 text "$BASH" "$0" "$@"

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}"
cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
python3 "${PLUGIN_ROOT}/scripts/install_git_hooks.py" --install >/dev/null 2>&1 || true
python3 "${PLUGIN_ROOT}/scripts/artifact_guard.py" --install-hook >/dev/null 2>&1 || true
python3 "${PLUGIN_ROOT}/scripts/rally_point/install_git_hook.py" --repo "$PWD" >/dev/null 2>&1 || true
exit 0
