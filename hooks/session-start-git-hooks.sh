#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Auto-install build-loop git hooks on session start (idempotent).
# Installs: pre-push (push-HOLD gate).
# Skips silently when: not inside a git repo, Python unavailable, hook
# source not found, or a foreign (non-build-loop) pre-push is already
# installed (requires --force to overwrite; we never force on auto-install).
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}"
cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
python3 "${PLUGIN_ROOT}/scripts/install_git_hooks.py" --install >/dev/null 2>&1 || true
exit 0
