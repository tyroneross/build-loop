#!/usr/bin/env bash
# Deprecated alias; use pre-edit-rally-point.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/pre-edit-rally-point.sh" "$@"
