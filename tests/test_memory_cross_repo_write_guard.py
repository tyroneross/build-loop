from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_REF = REPO_ROOT / "skills" / "build-loop" / "references" / "memory.md"


def test_memory_reference_documents_codex_cross_repo_write_guard() -> None:
    text = MEMORY_REF.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    for needle in (
        "Codex cross-repo write guard",
        "path resolution is relative to the active workspace",
        "Prefer `scripts/memory_writer.py`",
        "absolute target paths to `apply_patch`",
        "openable pointer, mirror, or stub at the old path",
        "verify both the canonical destination and every old path",
    ):
        assert needle in normalized
