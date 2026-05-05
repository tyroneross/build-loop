"""Make ``src/`` importable in tests without requiring an editable install.

Tests target ``build_loop.architecture`` directly. We prepend ``src/`` to
``sys.path`` so the package resolves before any system-wide installation.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[2] / "src").resolve()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
