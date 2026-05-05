"""Build-loop native architecture engine (Chunk 1).

Python-native scanner, storage, analysis, and lessons store. NavGator interop is
preserved by mirroring NavGator's component/connection JSON shape verbatim
(see ``schemas.py``). Subsequent chunks add an optional NavGator adapter, ACP
builder, freshness hooks, and lessons sync.
"""

from .schemas import Component, Connection, Index, Manifest, Lesson  # noqa: F401

__all__ = ["Component", "Connection", "Index", "Manifest", "Lesson"]
__version__ = "0.1.0"
