#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Continuous adaptive-cadence sensor loop printing coordination state transitions.
#   application: coordination
#   status: active
"""Compatibility wrapper for the embedded agent-rally-watcher namespace."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from agent_rally_watcher.watch import (  # noqa: E402
    _change_revisions,
    _signature,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
