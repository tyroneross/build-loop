#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
# Deprecated alias; use session-start-rally-point.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/session-start-rally-point.sh" "$@"
