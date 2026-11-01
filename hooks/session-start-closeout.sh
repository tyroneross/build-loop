#!/usr/bin/env bash
# Explicit closeout reconciliation. Never registered on generic session startup.
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
exec python3 "$ROOT/scripts/closeout_baton.py" --workdir "${CLAUDE_PROJECT_DIR:-$PWD}"
