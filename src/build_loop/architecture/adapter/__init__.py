"""NavGator adapter — optional capability provider for build-loop.

Native engine (Chunk 1) handles ``scan``/``impact``/``trace``/``connections``/
``rules``/``dead``. NavGator handles capabilities not yet ported:
``llm_map``, ``schema``, ``diagram``.

The adapter is a transport layer, not a translator: NavGator's JSON output is
passed through verbatim. NavGator is OPTIONAL — ``Adapter(mode='auto')`` is the
safe default; if NavGator is absent, escalation-only methods return
``{"available": False, "reason": "..."}`` rather than raising.

Public surface:

    from build_loop.architecture.adapter import (
        Adapter,
        AdapterMode,
        is_navgator_available,
        AdapterError,
        CapabilityNotAvailable,
        NavGatorNotAvailable,
    )
"""

from .navgator_adapter import (
    Adapter,
    AdapterError,
    AdapterMode,
    CapabilityNotAvailable,
    NavGatorNotAvailable,
    is_navgator_available,
)

__all__ = [
    "Adapter",
    "AdapterError",
    "AdapterMode",
    "CapabilityNotAvailable",
    "NavGatorNotAvailable",
    "is_navgator_available",
]
