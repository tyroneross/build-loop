"""Fixture: a module-level helper with exactly one call site in scope, small
body, no decorator, not public (not in __all__) — extracted "just in case".

SEED:needless_indirection@_format_one — only called from render_all, trivial.
"""

__all__ = ["render_all"]


def _format_one(item):
    # SEED:needless_indirection@_format_one  (single call site, tiny, private)
    return f"[{item}]"


def render_all(items):
    return ", ".join(_format_one(i) for i in items)
