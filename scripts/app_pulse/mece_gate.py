"""Deprecated alias for ``rally_point.mece_gate``."""
try:
    from ._alias import route_module
except ImportError:
    from _alias import route_module  # type: ignore

route_module(__name__, "mece_gate")
