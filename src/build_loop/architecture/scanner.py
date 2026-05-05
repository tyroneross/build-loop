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
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pathspec

from .schemas import Component, Connection, SCHEMA_VERSION

# Extensions we currently understand.
PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXTS = {".ts", ".tsx"}
SUPPORTED_EXTS = PY_EXTS | JS_EXTS | TS_EXTS

# Always-skip directories (in addition to .gitignore matches).
ALWAYS_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "dist", "build", "out",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".navgator", ".build-loop", ".bookmark", ".ibr",
    ".next", ".turbo", ".cache", "coverage",
}


# ---------------------------------------------------------------------------
# Tree-sitter lazy bootstrap
# ---------------------------------------------------------------------------

_TS_PARSER = None
_TSX_PARSER = None


def _ts_parsers() -> Tuple[object, object]:
    """Lazy-load tree-sitter TS/TSX parsers. Returns ``(ts, tsx)``."""
    global _TS_PARSER, _TSX_PARSER
    if _TS_PARSER is not None and _TSX_PARSER is not None:
        return _TS_PARSER, _TSX_PARSER

    try:
        from tree_sitter import Language, Parser
        import tree_sitter_typescript as tsts
    except Exception as e:  # pragma: no cover — import-time
        raise RuntimeError(
            "tree_sitter / tree_sitter_typescript not installed. "
            "Run `uv pip install tree-sitter tree-sitter-typescript`."
        ) from e

    ts_lang = Language(tsts.language_typescript())
    tsx_lang = Language(tsts.language_tsx())

    p_ts = Parser(ts_lang)
    p_tsx = Parser(tsx_lang)
    _TS_PARSER, _TSX_PARSER = p_ts, p_tsx
    return p_ts, p_tsx


# ---------------------------------------------------------------------------
# .gitignore support
# ---------------------------------------------------------------------------

def _load_gitignore(repo_root: Path) -> pathspec.PathSpec:
    patterns: List[str] = []
    for fname in (".gitignore", ".git/info/exclude"):
        p = repo_root / fname
        if p.exists():
            try:
                patterns.extend(p.read_text(encoding="utf-8").splitlines())
            except OSError:
                pass
    # Also seed with our always-skip directory globs so the spec is the only check.
    patterns.extend([f"{d}/" for d in ALWAYS_SKIP_DIRS])
    # "gitignore" pattern style is the modern equivalent of the deprecated
    # "gitwildmatch"; semantics are equivalent for our use.
    try:
        return pathspec.PathSpec.from_lines("gitignore", patterns)
    except (ValueError, KeyError):
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


# ---------------------------------------------------------------------------
# Component / connection identity helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slug(s: str) -> str:
    return _SLUG_RE.sub("_", s).strip("_").lower()


def _short_hash(s: str, n: int = 4) -> str:
    return hashlib.blake2b(s.encode("utf-8"), digest_size=8).hexdigest()[:n]


def _component_id(rel_path: str) -> str:
    base = _slug(rel_path)[:32]
    return f"COMP_component_{base}_{_short_hash(rel_path)}"


def _stable_id(rel_path: str) -> str:
    return f"STABLE_component_{rel_path.replace('/', '-')}"


def _connection_id(from_id: str, to_id: str, line: int) -> str:
    digest = _short_hash(f"{from_id}->{to_id}@{line}", 6)
    return f"CONN_imports_{digest}"


def _layer_for_path(rel_path: str) -> str:
    parts = rel_path.split("/")
    p0 = parts[0] if parts else ""
    if p0 in {"src", "lib", "core", "engine", "build_loop"}:
        return "backend"
    if p0 in {"web", "frontend", "ui", "app", "pages", "components"}:
        return "frontend"
    if p0 in {"scripts", "cli", "bin"}:
        return "tooling"
    if p0 in {"tests", "test", "__tests__"}:
        return "test"
    if p0 in {"docs", "doc"}:
        return "docs"
    return "unknown"


# ---------------------------------------------------------------------------
# Python imports via ast
# ---------------------------------------------------------------------------

def _py_imports(source: str) -> List[Tuple[str, int]]:
    """Return list of (module_string, line_number) for top-level imports.

    For ``from X import a, b`` we emit BOTH the package-level edge (``X``) AND
    one candidate edge per name (``X.a``, ``X.b``). The resolver picks the most
    specific in-tree match, so ``from . import b`` resolves to ``pkg/b.py``
    rather than ``pkg/__init__.py``.
    """
    out: List[Tuple[str, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, getattr(node, "lineno", 1)))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            prefix = "." * (node.level or 0)
            # Emit one edge per imported name as a candidate submodule first.
            for alias in node.names:
                if alias.name == "*":
                    continue
                if base:
                    full = prefix + base + "." + alias.name
                else:
                    full = prefix + alias.name
                out.append((full, getattr(node, "lineno", 1)))
            # Also emit the package-level edge so star-imports + bare
            # ``from X import name`` where ``name`` is a symbol (not module)
            # still get attributed.
            pkg = prefix + base if base else prefix
            if pkg:
                out.append((pkg, getattr(node, "lineno", 1)))
    return out


# ---------------------------------------------------------------------------
# JS/TS imports via tree-sitter (with regex fallback)
# ---------------------------------------------------------------------------

_TS_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*
        (?:import\s+[^'"\n;]+\s+from\s+|
           import\s+|
           export\s+\*\s+from\s+|
           export\s+\{[^}]*\}\s+from\s+|
           require\s*\()
        ['"]([^'"\n]+)['"]
    """,
    re.VERBOSE,
)


def _ts_imports_regex(source: str) -> List[Tuple[str, int]]:
    """Cheap fallback when tree-sitter isn't available."""
    out: List[Tuple[str, int]] = []
    for m in _TS_IMPORT_RE.finditer(source):
        spec = m.group(1)
        line = source[: m.start()].count("\n") + 1
        out.append((spec, line))
    return out


def _ts_imports(source: str, is_tsx: bool) -> List[Tuple[str, int]]:
    try:
        p_ts, p_tsx = _ts_parsers()
        parser = p_tsx if is_tsx else p_ts
    except RuntimeError:
        return _ts_imports_regex(source)
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return _ts_imports_regex(source)

    out: List[Tuple[str, int]] = []

    def walk(node):
        # import ... from "X"  ->  import_statement with string child.
        # require("X")         ->  call_expression w/ identifier 'require'.
        if node.type in ("import_statement", "export_statement"):
            for child in node.children:
                if child.type == "string":
                    spec = child.text.decode("utf-8").strip("'\"`")
                    out.append((spec, child.start_point[0] + 1))
        elif node.type == "call_expression" and node.child_count >= 2:
            fn = node.child(0)
            args = node.child(1)
            if fn.type == "identifier" and fn.text == b"require":
                for arg in args.children:
                    if arg.type == "string":
                        spec = arg.text.decode("utf-8").strip("'\"`")
                        out.append((spec, arg.start_point[0] + 1))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return out


# ---------------------------------------------------------------------------
# Module path resolution (best-effort)
# ---------------------------------------------------------------------------

def _resolve_py_import(module: str, from_rel: str, repo_files: Set[str]) -> Optional[str]:
    """Map a Python import string to a repo-relative file, if it lives in-tree."""
    if not module:
        return None
    if module.startswith("."):
        # Relative — resolve against from_rel.
        from_dir = os.path.dirname(from_rel)
        ups = len(module) - len(module.lstrip("."))
        rest = module.lstrip(".")
        base = from_dir
        for _ in range(ups - 1):
            base = os.path.dirname(base) if base else ""
        candidate_stem = os.path.join(base, rest.replace(".", "/")) if rest else base
        for ext in (".py",):
            cand = (candidate_stem + ext).lstrip("/")
            if cand in repo_files:
                return cand
        cand = os.path.join(candidate_stem, "__init__.py").lstrip("/")
        if cand in repo_files:
            return cand
        return None

    # Absolute. Try to find a matching file under any top-level package.
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        rel = "/".join(parts[:i]) + ".py"
        if rel in repo_files:
            return rel
        rel_init = "/".join(parts[:i]) + "/__init__.py"
        if rel_init in repo_files:
            return rel_init
        # also try src/<module>/...
        for prefix in ("src/", ""):
            rel2 = prefix + "/".join(parts[:i]) + ".py"
            if rel2 in repo_files:
                return rel2
    return None


def _resolve_ts_import(spec: str, from_rel: str, repo_files: Set[str]) -> Optional[str]:
    """Resolve a TS/JS import specifier to an in-tree file (best effort)."""
    if not spec or spec.startswith(("@", "node:")):
        # Bare specifier — likely external package; not in-tree.
        if not spec.startswith("."):
            return None
    if not spec.startswith("."):
        return None
    from_dir = os.path.dirname(from_rel)
    base = os.path.normpath(os.path.join(from_dir, spec))
    candidates = [base]
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if base.endswith(ext):
            continue
        candidates.append(base + ext)
    candidates += [
        base + "/index.ts",
        base + "/index.tsx",
        base + "/index.js",
        base + "/index.jsx",
    ]
    # Strip explicit ".js" suffixes commonly seen in TS for ESM (./foo.js -> ./foo.ts).
    if base.endswith(".js"):
        candidates.append(base[:-3] + ".ts")
        candidates.append(base[:-3] + ".tsx")
    for c in candidates:
        c_norm = c.lstrip("/")
        if c_norm in repo_files:
            return c_norm
    return None


# ---------------------------------------------------------------------------
# Top-level scan
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    components: List[Component]
    connections: List[Connection]
    file_map: Dict[str, str]            # rel_path -> component_id
    hashes: Dict[str, Dict[str, str]]   # rel_path -> {hash, mtime, size}
    files_scanned: int

    def to_index(self) -> Dict[str, object]:
        # Schema-key parity: NavGator and the orchestrator state.json field
        # convention both use the plural form ("components_count",
        # "connections_count") and "last_scan". Build-loop's native engine
        # historically emitted the singular forms ("component_count",
        # "connection_count") plus "generated_at". Both are written so any
        # consumer (orchestrator state read, NavGator-shape adapter,
        # downstream tools) sees what it expects. Treat all six as a single
        # contract; tests in test_schema_parity.py lock the invariant.
        now_ms = int(time.time() * 1000)
        comp_count = len(self.components)
        conn_count = len(self.connections)
        return {
            "schema_version": SCHEMA_VERSION,
            "component_count": comp_count,
            "components_count": comp_count,
            "connection_count": conn_count,
            "connections_count": conn_count,
            "components": [c.to_dict() for c in self.components],
            "connections": [c.to_dict() for c in self.connections],
            "generated_at": now_ms,
            "last_scan": now_ms,
        }


def _iter_files(repo_root: Path, spec: pathspec.PathSpec) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(dirpath, repo_root)
        if rel_dir == ".":
            rel_dir = ""
        # Prune always-skip dirs for speed (still defer to .gitignore for the rest).
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP_DIRS]
        # Apply pathspec to remaining dirs.
        keep = []
        for d in dirnames:
            rel = os.path.join(rel_dir, d) if rel_dir else d
            if spec.match_file(rel + "/"):
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            rel = os.path.join(rel_dir, fn) if rel_dir else fn
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue
            if spec.match_file(rel):
                continue
            yield Path(repo_root) / rel


def _hash_file(path: Path) -> Tuple[str, int, int]:
    h = hashlib.blake2b(digest_size=16)
    size = 0
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
    except OSError:
        return ("", 0, 0)
    try:
        mtime = int(path.stat().st_mtime * 1000)
    except OSError:
        mtime = 0
    return (h.hexdigest(), size, mtime)


def _build_component(rel_path: str) -> Component:
    cid = _component_id(rel_path)
    name = rel_path.rsplit(".", 1)[0].replace("\\", "/")
    return Component(
        component_id=cid,
        name=name,
        type="component",
        role={
            "purpose": f"Internal module at {rel_path}",
            "layer": _layer_for_path(rel_path),
            "critical": False,
        },
        source={
            "detection_method": "auto",
            "config_files": [rel_path],
            "confidence": 0.95,
        },
        connects_to=[],
        connected_from=[],
        status="active",
        tags=["internal", "module"],
        metadata={"file": rel_path, "kind": "source-file"},
        timestamp=int(time.time() * 1000),
        last_updated=int(time.time() * 1000),
        stable_id=_stable_id(rel_path),
    )


def scan_repo(repo_root: Path | str) -> ScanResult:
    """Full scan. Returns a ScanResult; caller persists via storage."""
    repo_root = Path(repo_root).resolve()
    spec = _load_gitignore(repo_root)

    # Pass 1: enumerate files, build component map.
    rel_files: List[str] = []
    for p in _iter_files(repo_root, spec):
        rel_files.append(str(p.relative_to(repo_root)).replace(os.sep, "/"))

    rel_files_set = set(rel_files)
    components: List[Component] = []
    file_map: Dict[str, str] = {}
    hashes: Dict[str, Dict[str, str]] = {}

    for rel in rel_files:
        comp = _build_component(rel)
        components.append(comp)
        file_map[rel] = comp.component_id
        h, size, mtime = _hash_file(repo_root / rel)
        hashes[rel] = {"hash": h, "size": size, "mtime": mtime}

    # Pass 2: parse imports per file, build connections.
    connections: List[Connection] = []
    for comp in components:
        rel = comp.metadata.get("file", "")
        full = repo_root / rel
        try:
            source = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ext = os.path.splitext(rel)[1].lower()

        if ext in PY_EXTS:
            imports = _py_imports(source)
            resolver = _resolve_py_import
        elif ext in TS_EXTS or ext in JS_EXTS:
            is_tsx = ext == ".tsx"
            imports = _ts_imports(source, is_tsx)
            resolver = _resolve_ts_import
        else:
            continue

        for spec_str, line in imports:
            target_rel = resolver(spec_str, rel, rel_files_set)
            if not target_rel:
                continue
            target_id = file_map.get(target_rel)
            if not target_id or target_id == comp.component_id:
                continue
            cid = _connection_id(comp.component_id, target_id, line)
            conn = Connection(
                connection_id=cid,
                from_id=comp.component_id,
                to_id=target_id,
                from_stable=comp.stable_id,
                to_stable=_stable_id(target_rel),
                type="imports",
                file=rel,
                line=line,
                symbol=spec_str,
                confidence=1.0,
                classification="test" if "test" in rel.lower() else "production",
            )
            connections.append(conn)

    # Pass 3: backfill connects_to / connected_from on components.
    by_id: Dict[str, Component] = {c.component_id: c for c in components}
    for conn in connections:
        f = by_id.get(conn.from_id)
        t = by_id.get(conn.to_id)
        if f and conn.to_id not in f.connects_to:
            f.connects_to.append(conn.to_id)
        if t and conn.from_id not in t.connected_from:
            t.connected_from.append(conn.from_id)

    return ScanResult(
        components=components,
        connections=connections,
        file_map=file_map,
        hashes=hashes,
        files_scanned=len(rel_files),
    )


def scan_one_file(
    repo_root: Path | str,
    rel_path: str,
    prior_scan: Optional[ScanResult] = None,
) -> ScanResult:
    """Single-file rescan path for incremental updates.

    Re-parses ``rel_path`` only and merges the new component + outgoing
    connections back into ``prior_scan`` (if provided). This is the seam
    Chunk 4's freshness hooks will pull on.
    """
    repo_root = Path(repo_root).resolve()
    rel_path = rel_path.replace(os.sep, "/")
    if prior_scan is None:
        # No prior context — fall back to a small full scan.
        return scan_repo(repo_root)

    rel_files_set = set(prior_scan.file_map.keys()) | {rel_path}
    full = repo_root / rel_path
    if not full.exists():
        # File was deleted — drop it.
        new_comps = [c for c in prior_scan.components if c.metadata.get("file") != rel_path]
        new_file_map = {k: v for k, v in prior_scan.file_map.items() if k != rel_path}
        new_conns = [c for c in prior_scan.connections if c.file != rel_path]
        new_hashes = {k: v for k, v in prior_scan.hashes.items() if k != rel_path}
        return ScanResult(new_comps, new_conns, new_file_map, new_hashes, len(new_comps))

    # Re-build component for rel_path.
    comp = _build_component(rel_path)
    h, size, mtime = _hash_file(full)
    new_hashes = dict(prior_scan.hashes)
    new_hashes[rel_path] = {"hash": h, "size": size, "mtime": mtime}

    new_comps = [c for c in prior_scan.components if c.metadata.get("file") != rel_path]
    new_comps.append(comp)
    new_file_map = dict(prior_scan.file_map)
    new_file_map[rel_path] = comp.component_id

    # Drop old outgoing connections from this file, re-emit.
    new_conns = [c for c in prior_scan.connections if c.file != rel_path]
    try:
        source = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in PY_EXTS:
        imports = _py_imports(source); resolver = _resolve_py_import
    elif ext in TS_EXTS or ext in JS_EXTS:
        imports = _ts_imports(source, ext == ".tsx"); resolver = _resolve_ts_import
    else:
        imports, resolver = [], None

    for spec_str, line in imports:
        target_rel = resolver(spec_str, rel_path, rel_files_set) if resolver else None
        if not target_rel:
            continue
        target_id = new_file_map.get(target_rel)
        if not target_id or target_id == comp.component_id:
            continue
        cid = _connection_id(comp.component_id, target_id, line)
        new_conns.append(Connection(
            connection_id=cid,
            from_id=comp.component_id,
            to_id=target_id,
            from_stable=comp.stable_id,
            to_stable=_stable_id(target_rel),
            type="imports",
            file=rel_path,
            line=line,
            symbol=spec_str,
            confidence=1.0,
            classification="test" if "test" in rel_path.lower() else "production",
        ))

    # Re-derive connects_to / connected_from.
    by_id = {c.component_id: c for c in new_comps}
    for c in new_comps:
        c.connects_to = []
        c.connected_from = []
    for conn in new_conns:
        f = by_id.get(conn.from_id)
        t = by_id.get(conn.to_id)
        if f and conn.to_id not in f.connects_to:
            f.connects_to.append(conn.to_id)
        if t and conn.from_id not in t.connected_from:
            t.connected_from.append(conn.from_id)

    return ScanResult(new_comps, new_conns, new_file_map, new_hashes, len(new_comps))
