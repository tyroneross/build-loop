#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Cache roots recognised by evidence: CACHEDIR.TAG and Xcode derived data.

Backlog BUIL-WORKTREE-ARCHIVE-m6b569web6dc1xj04mphf (2026-11-01): 57 Easy
Terminal worktree removals wrote 5.08 GB of archives, mostly compiled output in
`build-rust/` (cargo writes a CACHEDIR.TAG) and `build/` (xcodebuild
-derivedDataPath). Neither name was in CACHE_ROOTS, so both were archived.

Run: python3 scripts/hooks/test_worktree_archive_cache_evidence.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_worktree_removal_guard_review import Base, git, wa  # noqa: E402

XCODE_PLIST = ("<?xml version=\"1.0\"?>\n<plist version=\"1.0\"><dict>"
               "<key>LastAccessedDate</key><date>2026-11-01T00:00:00Z</date>"
               "<key>WorkspacePath</key><string>/x/App.xcodeproj</string></dict></plist>\n")
def git_add_commit(wt: Path, *paths: str) -> None:
    git(wt, "add", "-f", *paths)
    git(wt, "commit", "-q", "-m", "track")


SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55\n# This file is a cache directory tag.\n"


class CacheEvidence(Base):
    def paths(self, receipt: dict) -> set[str]:
        return {f["path"] for f in receipt["files"]}

    def cargo_dir(self, rel: str, tag: str | bytes | None = SIGNATURE) -> Path:
        root = self.wt / rel
        (root / "release" / "deps").mkdir(parents=True)
        (root / "release" / "deps" / "libx.rlib").write_bytes(b"\0" * 64)
        if isinstance(tag, bytes):
            (root / "CACHEDIR.TAG").write_bytes(tag)
        elif tag is not None:
            (root / "CACHEDIR.TAG").write_text(tag)
        return root

    def xcode_dir(self, rel: str, variant: str = "modulecache") -> Path:
        root = self.wt / rel
        (root / "Build" / "Products" / "Debug").mkdir(parents=True)
        (root / "Build" / "Products" / "Debug" / "App").write_bytes(b"\0" * 64)
        (root / "info.plist").write_text(XCODE_PLIST)
        if variant == "modulecache":
            (root / "ModuleCache.noindex" / "X").mkdir(parents=True)
            (root / "ModuleCache.noindex" / "X" / "m.pcm").write_bytes(b"\0")
        elif variant == "intermediates":
            (root / "Build" / "Intermediates.noindex").mkdir()
        return root

    def test_valid_cachedir_tag_dir_is_listed_not_archived(self) -> None:
        self.cargo_dir("build-rust")
        receipt = wa.archive(self.wt)
        self.assertNotIn("build-rust/release/deps/libx.rlib", self.paths(receipt))
        self.assertNotIn("build-rust/CACHEDIR.TAG", self.paths(receipt))
        self.assertIn("build-rust/", receipt["caches_listed_not_archived"])

    def test_tagged_dir_nested_inside_ordinary_dir_is_listed(self) -> None:
        self.cargo_dir("apps/gpui/out")
        (self.wt / "apps" / "gpui" / "notes.md").write_text("handwritten\n")
        receipt = wa.archive(self.wt)
        self.assertIn("apps/gpui/notes.md", self.paths(receipt))
        self.assertNotIn("apps/gpui/out/release/deps/libx.rlib", self.paths(receipt))
        self.assertIn("apps/gpui/out/", receipt["caches_listed_not_archived"])

    def test_ignored_child_of_tagged_dir_is_not_archived(self) -> None:
        # git reports `build-rust/scratch/` (ignored) apart from `build-rust/`
        # (untracked); the ignored entry must still count as inside the cache.
        root = self.cargo_dir("build-rust")
        (root / "scratch").mkdir()
        (root / "scratch" / "obj.o").write_bytes(b"\0")
        receipt = wa.archive(self.wt)
        self.assertNotIn("build-rust/scratch/obj.o", self.paths(receipt))
        self.assertIn("build-rust/", receipt["caches_listed_not_archived"])

    def test_empty_or_wrong_signature_tag_is_not_a_cache(self) -> None:
        cases = {"empty": "", "wrong": "Signature: 0000000000000000000000000000000\n",
                 "short": "Signature: 8a477f597d28d172789f0688680",
                 "leading-space": " " + SIGNATURE, "lowercase": SIGNATURE.lower()}
        for name, tag in cases.items():
            self.cargo_dir(f"out-{name}", tag)
        receipt = wa.archive(self.wt)
        for name in cases:
            self.assertIn(f"out-{name}/release/deps/libx.rlib", self.paths(receipt), name)
            self.assertNotIn(f"out-{name}/", receipt["caches_listed_not_archived"], name)

    def test_cachedir_tag_symlink_is_not_trusted(self) -> None:
        root = self.cargo_dir("out-link", None)
        real = self.tmp / "real-tag"
        real.write_text(SIGNATURE)
        (root / "CACHEDIR.TAG").symlink_to(real)
        self.assertFalse(wa.is_cache_root(root))

    def test_xcode_derived_data_layouts_are_listed_not_archived(self) -> None:
        self.xcode_dir("build-mac", "modulecache")
        self.xcode_dir("build-ios", "intermediates")
        receipt = wa.archive(self.wt)
        for rel in ("build-mac", "build-ios"):
            self.assertNotIn(f"{rel}/Build/Products/Debug/App", self.paths(receipt), rel)
            self.assertNotIn(f"{rel}/info.plist", self.paths(receipt), rel)
            self.assertIn(f"{rel}/", receipt["caches_listed_not_archived"], rel)

    def test_partial_xcode_layout_is_archived(self) -> None:
        # info.plist + Build/ alone is a plausible hand-made folder, not proof.
        self.xcode_dir("build-partial", "none")
        receipt = wa.archive(self.wt)
        self.assertIn("build-partial/info.plist", self.paths(receipt))
        self.assertIn("build-partial/Build/Products/Debug/App", self.paths(receipt))

    def test_plain_build_dir_with_source_is_archived(self) -> None:
        (self.wt / "build" / "src").mkdir(parents=True)
        (self.wt / "build" / "src" / "main.swift").write_text("print(1)\n")
        (self.wt / "build" / "README.md").write_text("handwritten\n")
        receipt = wa.archive(self.wt)
        self.assertIn("build/src/main.swift", self.paths(receipt))
        self.assertIn("build/README.md", self.paths(receipt))
        self.assertNotIn("build/", receipt["caches_listed_not_archived"])

    def test_credential_shaped_file_inside_tagged_dir_is_archived(self) -> None:
        root = self.cargo_dir("build-rust")
        (root / ".env").write_text("K=v\n")
        (root / "release" / "app.sqlite").write_bytes(b"SQLite format 3\0")
        receipt = wa.archive(self.wt)
        self.assertIn("build-rust/.env", self.paths(receipt))
        self.assertIn("build-rust/release/app.sqlite", self.paths(receipt))
        self.assertNotIn("build-rust/release/deps/libx.rlib", self.paths(receipt))

    def test_app_info_plist_is_not_derived_data(self) -> None:
        # An app target folder: Info.plist (no WorkspacePath) beside build output.
        root = self.xcode_dir("AppTarget")
        (root / "info.plist").write_text("<plist><dict><key>CFBundleName</key></dict></plist>\n")
        self.assertFalse(wa.is_cache_root(root))

    def test_untracked_file_in_tracked_tagged_folder_is_archived(self) -> None:
        # Audit F1 repro 1: tracked docs/ with a committed CACHEDIR.TAG.
        (self.wt / "docs").mkdir()
        (self.wt / "docs" / "a.md").write_text("tracked\n")
        (self.wt / "docs" / "CACHEDIR.TAG").write_text(SIGNATURE)
        git_add_commit(self.wt, "docs")
        (self.wt / "docs" / "notes.md").write_text("new work\n")
        receipt = wa.archive(self.wt)
        self.assertIn("docs/notes.md", self.paths(receipt))
        self.assertNotIn("docs/", receipt["caches_listed_not_archived"])

    def test_untracked_file_in_tracked_xcode_shaped_folder_is_archived(self) -> None:
        # Audit F1 repro 2: tracked App/ that also matches the derived-data layout.
        root = self.xcode_dir("App")
        (root / "Main.swift").write_text("print(1)\n")
        git_add_commit(self.wt, "App/info.plist", "App/Main.swift")
        (root / "NewView.swift").write_text("struct V {}\n")
        receipt = wa.archive(self.wt)
        self.assertIn("App/NewView.swift", self.paths(receipt))
        self.assertNotIn("App/", receipt["caches_listed_not_archived"])

    def test_guard_searches_tagged_folders_for_worktrees(self) -> None:
        # Audit F2: a tagged or Xcode-shaped folder can still hold worktrees.
        tagged = self.tmp / "cache-home"
        nested = self.add_wt(tagged / "tool" / "inner")
        (tagged / "CACHEDIR.TAG").write_text(SIGNATURE)
        (tagged / "tool" / "CACHEDIR.TAG").write_text(SIGNATURE)
        self.assertTrue(nested.exists())
        self.denied(self.run_guard(f"rm -rf {tagged}"))
        self.denied(self.run_guard(f"rm -rf {tagged / 'tool'}"))

    def test_credential_shaped_file_inside_xcode_dir_is_archived(self) -> None:
        root = self.xcode_dir("build-mac")
        (root / ".env").write_text("K=v\n")
        receipt = wa.archive(self.wt)
        self.assertIn("build-mac/.env", self.paths(receipt))

    def test_checkout_root_is_never_a_cache_even_when_tagged(self) -> None:
        (self.wt / "CACHEDIR.TAG").write_text(SIGNATURE)
        self.assertFalse(wa.is_cache_root(self.wt))
        nested = self.add_wt(self.wt / "tagged-parent" / "inner")
        (nested / "CACHEDIR.TAG").write_text(SIGNATURE)
        self.assertFalse(wa.is_cache_root(nested))
        # A tagged directory holding a worktree is still searched by the guard.
        (self.wt / "tagged-parent" / "CACHEDIR.TAG").write_text(SIGNATURE)
        self.denied(self.run_guard(f"rm -rf {nested}"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
