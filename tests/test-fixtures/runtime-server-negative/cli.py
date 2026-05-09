"""CLI-only fixture — no HTTP, no SSE, no embedded UI.

Should produce ``runtimeServer: false`` from the detector.
"""
from __future__ import annotations

import argparse


def main() -> int:
    p = argparse.ArgumentParser(description="Sample CLI for the negative fixture.")
    p.add_argument("--name", default="world")
    args = p.parse_args()
    print(f"hello, {args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
