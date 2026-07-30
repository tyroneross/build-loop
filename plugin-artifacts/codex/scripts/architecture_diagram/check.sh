#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# Architecture-diagram freshness + drift gate.
# Wire as a CI step (fail-closed) and/or a git pre-commit hook.
#
#   CI:           bash scripts/architecture_diagram/check.sh
#   git hook:     ln -sf ../../scripts/architecture_diagram/check.sh .git/hooks/pre-commit
#                 (or call it from an existing pre-commit; set BL_ARCH_ADVISORY=1 to warn-not-block locally)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"
rc=0

# Capture each tool's output and surface it ONLY on failure — silent on success (no spam).
drift_out="$("$PY" "$ROOT/scripts/architecture_diagram/drift_lint.py" 2>&1)" || {
  printf '%s\n' "$drift_out" >&2
  echo "✖ architecture drift: the flow references an agent/hook that does not exist. Fix architecture/ARCHITECTURE.md." >&2
  rc=1
}

fresh_out="$("$PY" "$ROOT/scripts/architecture_diagram/generate.py" --check 2>&1)" || {
  printf '%s\n' "$fresh_out" >&2
  echo "✖ architecture diagram stale vs source. Run: python3 scripts/architecture_diagram/generate.py" >&2
  rc=1
}

if [ "$rc" -ne 0 ] && [ "${BL_ARCH_ADVISORY:-0}" = "1" ]; then
  echo "⚠ advisory mode (BL_ARCH_ADVISORY=1) — not blocking the commit." >&2
  exit 0
fi
# Silent on success (no per-commit "all good" spam); only speaks when it blocks.
exit "$rc"
