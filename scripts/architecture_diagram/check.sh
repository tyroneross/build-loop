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

if ! "$PY" "$ROOT/scripts/architecture_diagram/drift_lint.py"; then
  echo "✖ architecture drift: flow.yaml references an agent/hook that does not exist. Fix architecture/flow.yaml." >&2
  rc=1
fi

if ! "$PY" "$ROOT/scripts/architecture_diagram/generate.py" --check; then
  echo "✖ architecture diagram stale vs source. Run: python3 scripts/architecture_diagram/generate.py" >&2
  rc=1
fi

if [ "$rc" -ne 0 ] && [ "${BL_ARCH_ADVISORY:-0}" = "1" ]; then
  echo "⚠ advisory mode (BL_ARCH_ADVISORY=1) — not blocking the commit." >&2
  exit 0
fi
# Silent on success (no per-commit "all good" spam); only speaks when it blocks.
exit "$rc"
