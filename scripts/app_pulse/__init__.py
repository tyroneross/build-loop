"""Deprecated alias for the ``rally_point`` package.

This module re-exports the rally_point public surface and emits a
DeprecationWarning on import.  Remove after one release cycle.
"""
import warnings
warnings.warn(
    "app_pulse has been renamed to rally_point. Update your imports.",
    DeprecationWarning,
    stacklevel=2,
)
# Re-export everything (intra-package modules + top-level functions)
from rally_point import *  # noqa: F401,F403
from rally_point import (  # noqa: F401
    changes, channel_paths, checkpoint, inbox, lifecycle,
    mece_gate, post, presence, rally, revision,
)
