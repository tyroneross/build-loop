# Deprecated Alias Boundary

`app_pulse` was renamed to `rally_point`.

This folder intentionally contains only compatibility shims. Runtime code lives
under `scripts/rally_point/`; new imports, docs, tests, and commands should use
Rally Point names.

The shims route legacy imports such as `scripts.app_pulse.post`,
`app_pulse.post`, and old bare-module imports that put `scripts/app_pulse` on
`sys.path` to the matching `rally_point` module and emit a
`DeprecationWarning`.
