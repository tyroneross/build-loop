#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# _resolve_python.sh — source (don't exec) to set `$_py` to a usable python3
# without depending on a populated PATH. Hooks run under a minimal PATH
# (/usr/bin:/bin), so resolve via `command -v` then absolute fallbacks. Sets
# `_py=""` when none is found.
#
#   . "$(dirname "$0")/_resolve_python.sh"
#   [ -n "$_py" ] || exit 0
#
# Single source of truth for the resolver shared by closeout.sh,
# session-start-closeout.sh, and post-push-closeout.sh. See memory
# `reference_hooks_minimal_path_failopen`.

_py=""
for _candidate in python3 python; do
    if command -v "$_candidate" >/dev/null 2>&1; then
        _py="$_candidate"
        break
    fi
done
for _fallback in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if [ -z "$_py" ] && [ -x "$_fallback" ]; then
        _py="$_fallback"
    fi
done
