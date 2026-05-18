"""Scanner integration test — fixture repo with .py + .ts files.

Builds a tiny repo on disk, scans it, asserts components + connections + hashes
land correctly, and confirms ``schema_version`` propagates.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from build_loop.architecture.scanner import scan_repo, scan_one_file
from build_loop.architecture.storage import (
    SCHEMA_VERSION as _SV,  # noqa: F401  (sanity import)
    arch_dir,
    write_file_map,
    write_hashes,
    write_index,
)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    # 3 .py files with a chain a -> b -> c.
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from . import b\n")
    _write(tmp_path / "pkg" / "b.py", "from . import c\n")
    _write(tmp_path / "pkg" / "c.py", "X = 1\n")

    # 2 .ts files with d -> e.
    _write(tmp_path / "ts" / "d.ts", "import { e } from './e';\nexport const D = e;\n")
    _write(tmp_path / "ts" / "e.ts", "export const e = 1;\n")

    # Add a .gitignore that excludes a "vendor" dir to prove pathspec works.
    _write(tmp_path / ".gitignore", "vendor/\n")
    _write(tmp_path / "vendor" / "skip.py", "raise RuntimeError('should be skipped')\n")
    return tmp_path


def test_scan_emits_components_and_connections(fixture_repo: Path) -> None:
    result = scan_repo(fixture_repo)

    files = {c.metadata["file"] for c in result.components}
    assert "pkg/a.py" in files
    assert "pkg/b.py" in files
    assert "pkg/c.py" in files
    assert "ts/d.ts" in files
    assert "ts/e.ts" in files
    # Vendor must be skipped via .gitignore.
    assert "vendor/skip.py" not in files

    edges = {(c.from_id, c.to_id) for c in result.connections}
    by_file = {c.metadata["file"]: c.component_id for c in result.components}
    assert (by_file["pkg/a.py"], by_file["pkg/b.py"]) in edges
    assert (by_file["pkg/b.py"], by_file["pkg/c.py"]) in edges
    assert (by_file["ts/d.ts"], by_file["ts/e.ts"]) in edges


def test_scan_persists_artifacts_with_schema_version(fixture_repo: Path) -> None:
    result = scan_repo(fixture_repo)
    write_index(fixture_repo, result.to_index())
    write_hashes(fixture_repo, {"files": result.hashes})
    write_file_map(fixture_repo, {"files": result.file_map})

    idx_path = arch_dir(fixture_repo) / "index.json"
    hashes_path = arch_dir(fixture_repo) / "hashes.json"
    file_map_path = arch_dir(fixture_repo) / "file_map.json"

    for p in (idx_path, hashes_path, file_map_path):
        assert p.exists()
        doc = json.loads(p.read_text())
        assert "schema_version" in doc

    hashes = json.loads(hashes_path.read_text())["files"]
    assert "pkg/a.py" in hashes
    assert hashes["pkg/a.py"]["hash"]  # blake2b digest non-empty


def test_scan_one_file_replaces_in_place(fixture_repo: Path) -> None:
    result = scan_repo(fixture_repo)
    initial_edges = {(c.from_id, c.to_id) for c in result.connections}

    # Modify pkg/a.py to import c directly instead of b.
    (fixture_repo / "pkg" / "a.py").write_text("from . import c\n", encoding="utf-8")

    updated = scan_one_file(fixture_repo, "pkg/a.py", prior_scan=result)
    edges = {(c.from_id, c.to_id) for c in updated.connections}
    by_file = {c.metadata["file"]: c.component_id for c in updated.components}

    assert (by_file["pkg/a.py"], by_file["pkg/c.py"]) in edges
    assert (by_file["pkg/a.py"], by_file["pkg/b.py"]) not in edges
    assert (by_file["pkg/b.py"], by_file["pkg/c.py"]) in edges


def test_scan_emits_gator_style_runtime_edges(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        '{"dependencies": {"react": "^18.0.0"}}',
    )
    _write(
        tmp_path / "tsconfig.json",
        '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}',
    )
    _write(
        tmp_path / "app" / "page.js",
        """
        import React from "react";
        import { helper } from "@/lib/helper";

        export default async function Page() {
          await fetch("/api/investments/research");
          return helper + React.version;
        }
        """,
    )
    _write(
        tmp_path / "app" / "api" / "investments" / "research" / "route.js",
        "export function GET() { return Response.json({ ok: true }); }\n",
    )
    _write(tmp_path / "lib" / "helper.js", "export const helper = 1;\n")
    _write(
        tmp_path / "lib" / "local-llm.js",
        """
        export async function runLocal() {
          const url = process.env.OLLAMA_BASE_URL || "http://localhost:11434";
          return fetch(url);
        }
        """,
    )

    result = scan_repo(tmp_path)
    by_file = {c.metadata.get("file"): c.component_id for c in result.components}
    by_package = {
        c.metadata.get("package_name"): c.component_id
        for c in result.components
        if c.metadata.get("kind") == "package"
    }
    by_service = {
        c.metadata.get("service_name"): c.component_id
        for c in result.components
        if c.metadata.get("kind") == "external-service"
    }

    typed_edges = {
        (c.type, c.from_id, c.to_id, c.symbol)
        for c in result.connections
    }
    assert (
        "imports",
        by_file["app/page.js"],
        by_file["lib/helper.js"],
        "@/lib/helper",
    ) in typed_edges
    assert (
        "uses-package",
        by_file["app/page.js"],
        by_package["react"],
        "react",
    ) in typed_edges
    assert (
        "frontend-calls-api",
        by_file["app/page.js"],
        by_file["app/api/investments/research/route.js"],
        "fetch(/api/investments/research)",
    ) in typed_edges
    assert (
        "service-call",
        by_file["lib/local-llm.js"],
        by_service["Ollama"],
        "Ollama",
    ) in typed_edges


def test_scan_one_file_updates_runtime_edges(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        '{"dependencies": {"react": "^18.0.0", "vue": "^3.0.0"}}',
    )
    _write(
        tmp_path / "app" / "page.js",
        """
        import React from "react";
        export default async function Page() {
          await fetch('/api/ping');
          return React.version;
        }
        """,
    )
    _write(
        tmp_path / "app" / "api" / "ping" / "route.js",
        "export function GET() { return Response.json({ ok: true }); }\n",
    )

    initial = scan_repo(tmp_path)
    _write(
        tmp_path / "app" / "page.js",
        """
        import { createApp } from "vue";
        export default function Page() {
          return createApp;
        }
        """,
    )

    updated = scan_one_file(tmp_path, "app/page.js", prior_scan=initial)
    by_file = {c.metadata.get("file"): c.component_id for c in updated.components}
    packages = {
        c.metadata.get("package_name"): c.component_id
        for c in updated.components
        if c.metadata.get("kind") == "package"
    }
    page_edges = [c for c in updated.connections if c.from_id == by_file["app/page.js"]]

    assert "vue" in packages
    assert "react" not in packages
    assert any(c.type == "uses-package" and c.to_id == packages["vue"] for c in page_edges)
    assert not any(c.type == "frontend-calls-api" for c in page_edges)
