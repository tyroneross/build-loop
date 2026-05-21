#!/usr/bin/env bash
# Deprecated alias; use test_rally_point_hooks.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/test_rally_point_hooks.sh" "$@"
