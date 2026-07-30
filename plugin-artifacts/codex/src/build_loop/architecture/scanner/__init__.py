# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Repo scanner — walks files, parses imports, emits Components + Connections.

Design:
    * Walks ``repo_root`` respecting ``.gitignore`` via ``pathspec``.
    * Python (.py): stdlib ``ast`` — no third-party parser needed.
    * JS/TS (.js/.jsx/.ts/.tsx): ``tree_sitter`` + ``tree_sitter_typescript``,
      lazy-imported so a Python-only repo doesn't pay the import cost.
    * Hashes file content via ``hashlib.blake2b`` (faster than sha256).
    * One Component per file/module; one Connection per import edge.
    * ``scan_one_file`` exposes a single-file rescan path for incremental
      updates (Chunk 4 will wire SessionStart hooks against this).

This package was split out of the former single ``scanner.py`` module by
responsibility — ``manifests`` (filesystem walk + declared deps + path
aliases), ``imports`` (Python/JS-TS import extraction), ``resolve`` (specifier
→ in-tree-file / external-package), ``patterns`` (service detection + frontend
API fetches), ``identity`` (deterministic IDs + Component/Connection builders),
and ``core`` (scan orchestration). The full public API is re-exported here so
every existing ``from build_loop.architecture.scanner import ...`` keeps working
unchanged. No scan/resolve/ID logic changed in the split.
"""

from __future__ import annotations

# Filesystem walk + declared-dependency + path-alias readers.
from .manifests import (
    ALWAYS_SKIP_DIRS,
    JS_EXTS,
    PY_EXTS,
    SUPPORTED_EXTS,
    TS_EXTS,
    _add_pip_spec,
    _iter_files,
    _iter_named_files,
    _json_loads_lenient,
    _load_gitignore,
    _load_ts_path_aliases,
    _parse_pyproject_deps,
    _parse_requirements_txt,
    _read_declared_npm_packages,
    _read_declared_pip_packages,
)

# Import extraction (Python ast + JS/TS tree-sitter/regex).
from .imports import (
    _TS_IMPORT_RE,
    _py_imports,
    _ts_imports,
    _ts_imports_regex,
    _ts_parsers,
)

# Specifier resolution + external-package classification.
from .resolve import (
    _PIP_IMPORT_ALIASES,
    _PIP_REQ_NAME_RE,
    _external_package_for_import,
    _is_python_stdlib,
    _normalize_pip_name,
    _resolve_py_absolute,
    _resolve_py_import,
    _resolve_py_relative,
    _resolve_ts_import,
    _resolve_ts_path,
    _toplevel_external_npm,
    _toplevel_external_py,
)

# Service-detection data + matchers + frontend API-fetch heuristics.
from .patterns import (
    SERVICE_PATTERNS,
    ServicePattern,
    _api_fetches,
    _FETCH_API_RE,
    _is_frontend_file,
    _line_for_offset,
    _resolve_api_route,
    _service_matches,
)

# Deterministic identity + Component/Connection construction.
from .identity import (
    _append_connection,
    _build_component,
    _build_package_component,
    _build_service_component,
    _classification_for_file,
    _component_id,
    _connection_id,
    _ensure_package_component,
    _ensure_service_component,
    _layer_for_path,
    _prune_unreferenced_runtime_components,
    _refresh_component_links,
    _runtime_component_id,
    _runtime_stable_id,
    _seed_runtime_components,
    _short_hash,
    _slug,
    _SLUG_RE,
    _stable_id,
)

# Scan orchestration — the headline public API.
from .core import (
    ScanResult,
    _emit_api_fetch_edges,
    _emit_file_connections,
    _emit_import_edges,
    _emit_service_edges,
    _hash_file,
    scan_one_file,
    scan_repo,
)

from ..schemas import SCHEMA_VERSION

__all__ = [
    # Headline public API
    "scan_repo",
    "scan_one_file",
    "ScanResult",
    # Extension sets + skip dirs
    "PY_EXTS",
    "JS_EXTS",
    "TS_EXTS",
    "SUPPORTED_EXTS",
    "ALWAYS_SKIP_DIRS",
    # Service patterns
    "ServicePattern",
    "SERVICE_PATTERNS",
    # Identity helpers (deterministic IDs — downstream dedup keys)
    "_slug",
    "_short_hash",
    "_component_id",
    "_runtime_component_id",
    "_stable_id",
    "_runtime_stable_id",
    "_connection_id",
    "_layer_for_path",
    "_classification_for_file",
    # Import extraction
    "_py_imports",
    "_ts_imports",
    "_ts_imports_regex",
    "_ts_parsers",
    # Resolution
    "_resolve_py_import",
    "_resolve_ts_import",
    "_resolve_ts_path",
    "_external_package_for_import",
    "_normalize_pip_name",
    # Schema version passthrough
    "SCHEMA_VERSION",
]
