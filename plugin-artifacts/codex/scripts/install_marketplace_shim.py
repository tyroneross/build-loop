#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Install the host marketplace-autoupdate hook as a pure-exec shim.

The canonical script is version-controlled at
``scripts/marketplace_autoupdate.py``. The host hook at
``~/.claude/scripts/hooks/marketplace-autoupdate.py`` should be a thin shim
that runpy-execs the canonical (loose wrappers desync silently — hooks-hygiene
lesson — so the shim is a bare exec, not a copy).

This installer writes that shim. It is idempotent and fail-safe:
  - refuses to overwrite a host file that is NOT already a shim, unless
    ``--force`` is passed (so a hand-edited host copy is never silently lost);
  - ``--print`` dry-runs the shim body to stdout without touching disk;
  - ``--canonical PATH`` overrides the embedded repo-relative canonical path.

Exit 0 on success / already-installed / dry-run; exit 1 on a refused clobber;
exit 2 on a write error.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HOST_HOOK = Path.home() / ".claude" / "scripts" / "hooks" / "marketplace-autoupdate.py"
# Repo-relative canonical, resolved at install time to an absolute path.
_REPO_CANONICAL = Path(__file__).resolve().parent / "marketplace_autoupdate.py"

SHIM_MARKER = "runpy.run_path"


def shim_body(canonical: Path) -> str:
    # Store the canonical as an absolute path so the shim works regardless of
    # the host hook's invocation cwd.
    return (
        "#!/usr/bin/env python3\n"
        '"""Pure-exec shim -> repo canonical scripts/marketplace_autoupdate.py.\n'
        "\n"
        "Loose wrappers desync silently (hooks-hygiene lesson), so this is a bare\n"
        "exec, not a copy. Edit the canonical in the build-loop repo; this shim\n"
        "picks it up. Re-generate with scripts/install_marketplace_shim.py.\n"
        '"""\n'
        "import os\n"
        "import runpy\n"
        "import sys\n"
        "\n"
        f"_CANONICAL = {str(canonical)!r}\n"
        "if not os.path.isfile(_CANONICAL):\n"
        "    sys.exit(0)  # fail-open: never break the session if the repo is absent\n"
        "sys.argv[0] = _CANONICAL\n"
        "runpy.run_path(_CANONICAL, run_name=\"__main__\")\n"
    )


def is_shim(text: str) -> bool:
    return SHIM_MARKER in text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", dest="dry",
                    help="print the shim body to stdout; do not write")
    ap.add_argument("--force", action="store_true",
                    help="overwrite even a non-shim host file")
    ap.add_argument("--canonical", default=str(_REPO_CANONICAL),
                    help="path to the canonical script (default: repo copy)")
    ap.add_argument("--host", default=str(HOST_HOOK),
                    help="path to the host hook to write (default: ~/.claude/...)")
    args = ap.parse_args(argv)

    canonical = Path(args.canonical).expanduser().resolve()
    host = Path(args.host).expanduser()
    body = shim_body(canonical)

    if args.dry:
        sys.stdout.write(body)
        return 0

    if host.exists():
        existing = host.read_text(encoding="utf-8", errors="replace")
        if is_shim(existing):
            if existing == body:
                print(f"[install_marketplace_shim] already a current shim: {host}")
                return 0
            # Shim exists but points elsewhere / is an older revision — refresh.
        elif not args.force:
            print(
                f"[install_marketplace_shim] REFUSED: {host} is not a shim "
                f"({len(existing.splitlines())} lines). Re-run with --force to "
                "replace the host copy with the exec shim.",
                file=sys.stderr,
            )
            return 1

    try:
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_text(body, encoding="utf-8")
        os.chmod(host, 0o755)
    except OSError as exc:
        print(f"[install_marketplace_shim] write failed: {exc!r}", file=sys.stderr)
        return 2

    print(f"[install_marketplace_shim] installed shim -> {canonical}\n  at {host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
