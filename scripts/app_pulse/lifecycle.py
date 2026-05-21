"""Deprecated alias for ``rally_point.lifecycle``."""
try:
    from ._alias import route_module
except ImportError:
    from _alias import route_module  # type: ignore

route_module(__name__, "lifecycle")
