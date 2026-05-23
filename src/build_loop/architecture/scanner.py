# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
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
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import posixpath
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import tomllib

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


@dataclass(frozen=True)
class ServicePattern:
    name: str
    component_type: str
    layer: str
    purpose: str
    patterns: Tuple[re.Pattern[str], ...]


SERVICE_PATTERNS: Tuple[ServicePattern, ...] = (
    ServicePattern(
        "Ollama",
        "llm",
        "external",
        "Ollama local LLM API",
        tuple(re.compile(p) for p in (
            r"\bfrom\s+['\"]ollama['\"]",
            r"\bimport\s+ollama\b",
            r"\bnew\s+Ollama\(",
            r"\bollama\.(chat|generate)\(",
            r"\bOLLAMA_(BASE_URL|MODEL|HOST)\b",
            r"\b(?:localhost|127\.0\.0\.1):11434\b",
            r"\b:11434\b",
        )),
    ),
    ServicePattern(
        "OpenAI",
        "llm",
        "external",
        "OpenAI API",
        tuple(re.compile(p) for p in (
            r"\bfrom\s+['\"]openai['\"]",
            r"\bimport\s+OpenAI\s+from\s+['\"]openai['\"]",
            r"\bnew\s+OpenAI\(",
            r"\bOpenAIApi\(",
            r"\bopenai\.(chat\.completions|completions|embeddings|images|audio)\.",
        )),
    ),
    ServicePattern(
        "Claude (Anthropic)",
        "llm",
        "external",
        "Claude AI API",
        tuple(re.compile(p) for p in (
            r"\bfrom\s+['\"]@anthropic-ai/sdk['\"]",
            r"\bfrom\s+anthropic\s+import\b",
            r"\bnew\s+Anthropic\(",
            r"\banthropic\.(messages|completions)\.create\b",
            r"\banthropic\.beta\.",
        )),
    ),
    ServicePattern(
        "Groq",
        "llm",
        "external",
        "Groq LLM API",
        tuple(re.compile(p) for p in (
            r"\bfrom\s+['\"]groq-sdk['\"]",
            r"\bfrom\s+groq\s+import\b",
            r"\bnew\s+Groq\(",
            r"\bgroq(Client)?\.chat\.completions\.create\b",
        )),
    ),
    ServicePattern(
        "Vercel AI SDK",
        "llm",
        "external",
        "Vercel AI SDK",
        tuple(re.compile(p) for p in (
            r"\bfrom\s+['\"]ai['\"]",
            r"\bfrom\s+['\"]@ai-sdk/",
            (
                r"\bimport\s+\{[^}]*(generateText|streamText|generateObject|useChat|useCompletion)"
                r"[^}]*\}\s+from\s+['\"](?:ai|@ai-sdk/[^'\"]+)['\"]"
            ),
        )),
    ),
    ServicePattern(
        "LangChain",
        "llm",
        "external",
        "LangChain framework",
        tuple(re.compile(p) for p in (
            r"\bfrom\s+['\"]@langchain/",
            r"\bfrom\s+langchain",
            r"\b(ChatOpenAI|ChatAnthropic|ChatGoogleGenerativeAI|ChatGroq)\(",
            r"\b(ChatPromptTemplate|StructuredOutputParser|RunnableSequence)\.",
        )),
    ),
    ServicePattern(
        "Stripe",
        "service",
        "external",
        "Stripe payments",
        tuple(re.compile(p) for p in (
            r"\bfrom\s+['\"]stripe['\"]",
            r"\bimport\s+stripe\b",
            r"\bnew\s+Stripe\(",
            r"\bstripe\.(customers|paymentIntents|subscriptions|invoices|checkout)\.",
        )),
    ),
    ServicePattern(
        "Supabase",
        "database",
        "database",
        "Supabase backend",
        tuple(re.compile(p) for p in (
            r"\bcreateClient\(\s*process\.env\.SUPABASE",
            r"\bsupabase\.(from|auth|storage)\.",
            r"\bfrom\s+['\"]@supabase/",
        )),
    ),
    ServicePattern(
        "Firebase",
        "database",
        "database",
        "Firebase backend",
        tuple(re.compile(p) for p in (
            r"\binitializeApp\(",
            r"\bgetFirestore\(",
            r"\bfirebase\.(firestore|auth)\(",
            r"\bfrom\s+['\"]firebase/",
        )),
    ),
)


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


def _iter_named_files(
    repo_root: Path,
    spec: pathspec.PathSpec,
    names: Set[str],
) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(dirpath, repo_root)
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP_DIRS]
        keep = []
        for d in dirnames:
            rel = os.path.join(rel_dir, d) if rel_dir else d
            if spec.match_file(rel + "/"):
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if fn not in names:
                continue
            rel = os.path.join(rel_dir, fn) if rel_dir else fn
            if spec.match_file(rel):
                continue
            yield Path(repo_root) / rel


def _json_loads_lenient(raw: str) -> Dict[str, object]:
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", raw)
    stripped = re.sub(r"(?m)^\s*//.*$", "", stripped)
    stripped = re.sub(r",\s*([}\]])", r"\1", stripped)
    data = json.loads(stripped)
    return data if isinstance(data, dict) else {}


def _load_ts_path_aliases(repo_root: Path) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for fname in ("tsconfig.json", "jsconfig.json"):
        p = repo_root / fname
        if not p.exists():
            continue
        try:
            data = _json_loads_lenient(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        compiler = data.get("compilerOptions") if isinstance(data, dict) else {}
        if not isinstance(compiler, dict):
            continue
        base_url = str(compiler.get("baseUrl") or ".").replace("\\", "/")
        paths = compiler.get("paths") or {}
        if not isinstance(paths, dict):
            continue
        for alias, targets in paths.items():
            if not isinstance(alias, str) or not isinstance(targets, list) or not targets:
                continue
            target = targets[0]
            if not isinstance(target, str):
                continue
            alias_prefix = alias.replace("*", "")
            target_prefix = target.replace("*", "").replace("\\", "/")
            resolved = posixpath.normpath(posixpath.join(base_url, target_prefix))
            aliases[alias_prefix] = "" if resolved == "." else resolved.strip("/")
        break

    if not aliases:
        next_configs = (
            "next.config.js", "next.config.mjs", "next.config.ts",
            "next.config.cjs",
        )
        if any((repo_root / name).exists() for name in next_configs):
            aliases["@/"] = ""
    return aliases


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


def _runtime_component_id(kind: str, name: str) -> str:
    base = _slug(name)[:32] or "unnamed"
    return f"COMP_{_slug(kind)}_{base}_{_short_hash(f'{kind}:{name}')}"


def _stable_id(rel_path: str) -> str:
    return f"STABLE_component_{rel_path.replace('/', '-')}"


def _runtime_stable_id(kind: str, name: str) -> str:
    return f"STABLE_{_slug(kind)}_{_slug(name) or 'unnamed'}"


def _connection_id(
    from_id: str,
    to_id: str,
    line: int,
    connection_type: str = "imports",
    symbol: str = "",
) -> str:
    if connection_type == "imports" and not symbol:
        seed = f"{from_id}->{to_id}@{line}"
    else:
        seed = f"{connection_type}:{from_id}->{to_id}@{line}:{symbol}"
    digest = _short_hash(seed, 6)
    return f"CONN_{_slug(connection_type)[:24]}_{digest}"


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


def _line_for_offset(source: str, offset: int) -> int:
    return source[:offset].count("\n") + 1


def _read_declared_npm_packages(
    repo_root: Path,
    spec: pathspec.PathSpec,
) -> Dict[str, str]:
    declared: Dict[str, str] = {}
    for manifest in _iter_named_files(repo_root, spec, {"package.json"}):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        rel = str(manifest.relative_to(repo_root)).replace(os.sep, "/")
        for key in (
            "dependencies", "devDependencies", "peerDependencies",
            "optionalDependencies",
        ):
            block = data.get(key) or {}
            if not isinstance(block, dict):
                continue
            for pkg in block:
                if isinstance(pkg, str):
                    declared.setdefault(pkg, rel)
    return declared


_PIP_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _normalize_pip_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _read_declared_pip_packages(
    repo_root: Path,
    spec: pathspec.PathSpec,
) -> Dict[str, Tuple[str, str]]:
    declared: Dict[str, Tuple[str, str]] = {}

    def add_spec(spec_text: str, manifest_rel: str) -> None:
        m = _PIP_REQ_NAME_RE.match(spec_text or "")
        if not m:
            return
        canonical = m.group(1)
        declared.setdefault(_normalize_pip_name(canonical), (canonical, manifest_rel))

    for manifest in _iter_named_files(
        repo_root,
        spec,
        {"pyproject.toml", "requirements.txt", "requirements-dev.txt"},
    ):
        rel = str(manifest.relative_to(repo_root)).replace(os.sep, "/")
        if manifest.name.endswith(".txt"):
            try:
                lines = manifest.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                add_spec(line, rel)
            continue

        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        project = data.get("project") or {}
        for spec_text in project.get("dependencies") or []:
            if isinstance(spec_text, str):
                add_spec(spec_text, rel)
        for group in (project.get("optional-dependencies") or {}).values():
            for spec_text in group or []:
                if isinstance(spec_text, str):
                    add_spec(spec_text, rel)
        for group in (data.get("dependency-groups") or {}).values():
            for spec_text in group or []:
                if isinstance(spec_text, str):
                    add_spec(spec_text, rel)
        uv_block = (data.get("tool") or {}).get("uv") or {}
        for spec_text in uv_block.get("dev-dependencies") or []:
            if isinstance(spec_text, str):
                add_spec(spec_text, rel)

    return declared


def _toplevel_external_npm(spec: str) -> Optional[str]:
    if not spec or spec.startswith((".", "/", "node:", "data:", "http:", "https:", "file:")):
        return None
    if spec.startswith("@"):
        parts = spec.split("/", 2)
        if len(parts) < 2:
            return None
        return f"{parts[0]}/{parts[1]}"
    return spec.split("/", 1)[0]


def _toplevel_external_py(module: str) -> Optional[str]:
    if not module or module.startswith("."):
        return None
    return module.split(".", 1)[0]


def _is_python_stdlib(name: str) -> bool:
    return name in getattr(sys, "stdlib_module_names", set())


_PIP_IMPORT_ALIASES: Dict[str, Set[str]] = {
    "pyyaml": {"yaml"},
    "beautifulsoup4": {"bs4"},
    "pillow": {"pil"},
    "scikit-learn": {"sklearn"},
    "opencv-python": {"cv2"},
    "google-api-python-client": {"googleapiclient", "google"},
}


def _external_package_for_import(
    spec: str,
    ext: str,
    declared_npm: Dict[str, str],
    declared_pip: Dict[str, Tuple[str, str]],
) -> Optional[Tuple[str, str, str]]:
    if ext in TS_EXTS or ext in JS_EXTS:
        pkg = _toplevel_external_npm(spec)
        if pkg and pkg in declared_npm:
            return ("npm", pkg, declared_npm[pkg])
        return None

    if ext in PY_EXTS:
        top = _toplevel_external_py(spec)
        if not top or _is_python_stdlib(top):
            return None
        norm = _normalize_pip_name(top)
        if norm in declared_pip:
            canonical, manifest = declared_pip[norm]
            return ("pip", canonical, manifest)
        for dist, aliases in _PIP_IMPORT_ALIASES.items():
            if norm in aliases and dist in declared_pip:
                canonical, manifest = declared_pip[dist]
                return ("pip", canonical, manifest)
    return None


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
           import\s*\(|
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
    seen = set(out)
    for item in _ts_imports_regex(source):
        if item not in seen:
            out.append(item)
            seen.add(item)
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


def _resolve_ts_path(base: str, repo_files: Set[str]) -> Optional[str]:
    base = base.replace("\\", "/").lstrip("/")
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
        c_norm = c.replace("\\", "/").lstrip("/")
        if c_norm in repo_files:
            return c_norm
    return None


def _resolve_ts_import(
    spec: str,
    from_rel: str,
    repo_files: Set[str],
    path_aliases: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Resolve a TS/JS import specifier to an in-tree file (best effort)."""
    if not spec or spec.startswith(("node:", "data:", "http:", "https:", "file:")):
        return None

    if path_aliases:
        for alias, target in sorted(path_aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if not alias or not spec.startswith(alias):
                continue
            rest = spec[len(alias):]
            base = posixpath.normpath(posixpath.join(target, rest)) if target else rest
            resolved = _resolve_ts_path(base, repo_files)
            if resolved:
                return resolved

    if not spec.startswith("."):
        return None
    from_dir = posixpath.dirname(from_rel.replace("\\", "/"))
    base = posixpath.normpath(posixpath.join(from_dir, spec))
    return _resolve_ts_path(base, repo_files)


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
        connection_counts_by_type: Dict[str, int] = {}
        for conn in self.connections:
            connection_counts_by_type[conn.type] = connection_counts_by_type.get(conn.type, 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "component_count": comp_count,
            "components_count": comp_count,
            "connection_count": conn_count,
            "connections_count": conn_count,
            "connection_counts_by_type": connection_counts_by_type,
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


def _build_package_component(
    manager: str,
    package_name: str,
    manifest_rel: str,
) -> Component:
    now = int(time.time() * 1000)
    return Component(
        component_id=_runtime_component_id(f"{manager}-package", package_name),
        name=package_name,
        type="package",
        role={
            "purpose": f"{manager} package {package_name}",
            "layer": "external",
            "critical": False,
        },
        source={
            "detection_method": "manifest",
            "config_files": [manifest_rel],
            "confidence": 0.9,
        },
        connects_to=[],
        connected_from=[],
        status="active",
        tags=["external", "package", manager],
        metadata={
            "kind": "package",
            "package_manager": manager,
            "package_name": package_name,
        },
        timestamp=now,
        last_updated=now,
        stable_id=_runtime_stable_id(f"{manager}-package", package_name),
    )


def _build_service_component(pattern: ServicePattern, confidence: float = 0.85) -> Component:
    now = int(time.time() * 1000)
    return Component(
        component_id=_runtime_component_id(pattern.component_type, pattern.name),
        name=pattern.name,
        type=pattern.component_type,
        role={
            "purpose": pattern.purpose,
            "layer": pattern.layer,
            "critical": pattern.component_type in {"llm", "database"},
        },
        source={
            "detection_method": "pattern",
            "config_files": [],
            "confidence": confidence,
        },
        connects_to=[],
        connected_from=[],
        status="active",
        tags=[pattern.component_type, pattern.layer, "external"],
        metadata={"kind": "external-service", "service_name": pattern.name},
        timestamp=now,
        last_updated=now,
        stable_id=_runtime_stable_id(pattern.component_type, pattern.name),
    )


def _is_frontend_file(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return (
        normalized.startswith(("app/", "pages/", "components/", "hooks/"))
        or "/app/" in normalized
        or "/pages/" in normalized
        or "/components/" in normalized
        or "/hooks/" in normalized
    )


_FETCH_API_RE = re.compile(
    r"(?:fetch|fetchWith\w+|apiFetch|fetchJSON|fetcher)\s*\(\s*['\"`](/api/[^'\"`\s?)]*)",
)


def _api_fetches(source: str, rel_path: str) -> List[Tuple[str, int]]:
    if not _is_frontend_file(rel_path):
        return []
    out: List[Tuple[str, int]] = []
    seen: Set[str] = set()
    for match in _FETCH_API_RE.finditer(source):
        api_path = match.group(1)
        if "$" in api_path:
            api_path = api_path.split("$", 1)[0].rstrip("/")
        if not api_path.startswith("/api/") or len(api_path) <= len("/api/"):
            continue
        if api_path in seen:
            continue
        seen.add(api_path)
        out.append((api_path, _line_for_offset(source, match.start())))
    return out


def _resolve_api_route(api_path: str, repo_files: Set[str]) -> Optional[str]:
    clean = api_path.split("?", 1)[0].strip("/")
    if not clean.startswith("api/"):
        return None
    candidates: List[str] = []
    for prefix in ("app", "src/app"):
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            candidates.append(f"{prefix}/{clean}/route{ext}")
    for prefix in ("pages", "src/pages"):
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            candidates.append(f"{prefix}/{clean}{ext}")
            candidates.append(f"{prefix}/{clean}/index{ext}")
    for candidate in candidates:
        if candidate in repo_files:
            return candidate
    return None


def _service_matches(source: str) -> List[Tuple[ServicePattern, int, str, str]]:
    found: Dict[str, Tuple[ServicePattern, int, str, str]] = {}
    for i, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "*")):
            continue
        for pattern in SERVICE_PATTERNS:
            if pattern.name in found:
                continue
            for regex in pattern.patterns:
                if regex.search(line):
                    found[pattern.name] = (pattern, i, stripped[:120], regex.pattern)
                    break
    return list(found.values())


def _classification_for_file(rel_path: str) -> str:
    lower = rel_path.lower()
    return "test" if "test" in lower or "__tests__" in lower else "production"


def _refresh_component_links(components: List[Component], connections: List[Connection]) -> None:
    by_id: Dict[str, Component] = {c.component_id: c for c in components}
    for comp in components:
        comp.connects_to = []
        comp.connected_from = []
    for conn in connections:
        f = by_id.get(conn.from_id)
        t = by_id.get(conn.to_id)
        if f and conn.to_id not in f.connects_to:
            f.connects_to.append(conn.to_id)
        if t and conn.from_id not in t.connected_from:
            t.connected_from.append(conn.from_id)


def _prune_unreferenced_runtime_components(
    components: List[Component],
    connections: List[Connection],
) -> List[Component]:
    referenced = {c.from_id for c in connections} | {c.to_id for c in connections}
    return [
        comp for comp in components
        if comp.metadata.get("kind") == "source-file" or comp.component_id in referenced
    ]


def _seed_runtime_components(
    components: List[Component],
) -> Dict[Tuple[str, str], Component]:
    runtime_components: Dict[Tuple[str, str], Component] = {}
    for existing in components:
        kind = existing.metadata.get("kind")
        if kind == "package":
            manager = existing.metadata.get("package_manager", "")
            package_name = existing.metadata.get("package_name", existing.name)
            runtime_components[(f"{manager}-package", package_name)] = existing
        elif kind == "external-service":
            service_name = existing.metadata.get("service_name", existing.name)
            runtime_components[(existing.type, service_name)] = existing
    return runtime_components


def _ensure_package_component(
    components: List[Component],
    by_id: Dict[str, Component],
    runtime_components: Dict[Tuple[str, str], Component],
    manager: str,
    package_name: str,
    manifest_rel: str,
) -> Component:
    key = (f"{manager}-package", package_name)
    existing = runtime_components.get(key)
    if existing:
        return existing
    package_comp = _build_package_component(manager, package_name, manifest_rel)
    runtime_components[key] = package_comp
    components.append(package_comp)
    by_id[package_comp.component_id] = package_comp
    return package_comp


def _ensure_service_component(
    components: List[Component],
    by_id: Dict[str, Component],
    runtime_components: Dict[Tuple[str, str], Component],
    pattern: ServicePattern,
) -> Component:
    key = (pattern.component_type, pattern.name)
    existing = runtime_components.get(key)
    if existing:
        return existing
    service_comp = _build_service_component(pattern)
    runtime_components[key] = service_comp
    components.append(service_comp)
    by_id[service_comp.component_id] = service_comp
    return service_comp


def _append_connection(
    connections: List[Connection],
    seen_connections: Set[Tuple[str, str, str, int, str]],
    from_comp: Component,
    to_comp: Component,
    connection_type: str,
    rel: str,
    line: int,
    symbol: str,
    confidence: float,
    detected_from: str,
    symbol_type: str = "import",
    description: str = "",
) -> None:
    key = (connection_type, from_comp.component_id, to_comp.component_id, line, symbol)
    if key in seen_connections:
        return
    seen_connections.add(key)
    connections.append(Connection(
        connection_id=_connection_id(
            from_comp.component_id,
            to_comp.component_id,
            line,
            connection_type=connection_type,
            symbol=symbol,
        ),
        from_id=from_comp.component_id,
        to_id=to_comp.component_id,
        from_stable=from_comp.stable_id,
        to_stable=to_comp.stable_id,
        type=connection_type,
        file=rel,
        line=line,
        symbol=symbol,
        symbol_type=symbol_type,
        confidence=confidence,
        classification=_classification_for_file(rel),
        detected_from=detected_from,
        description=description,
    ))


def _emit_file_connections(
    *,
    rel: str,
    comp: Component,
    source: str,
    ext: str,
    rel_files_set: Set[str],
    file_map: Dict[str, str],
    by_id: Dict[str, Component],
    components: List[Component],
    connections: List[Connection],
    seen_connections: Set[Tuple[str, str, str, int, str]],
    runtime_components: Dict[Tuple[str, str], Component],
    declared_npm: Dict[str, str],
    declared_pip: Dict[str, Tuple[str, str]],
    path_aliases: Dict[str, str],
) -> None:
    if ext in PY_EXTS:
        imports = _py_imports(source)
        resolver = _resolve_py_import
    elif ext in TS_EXTS or ext in JS_EXTS:
        imports = _ts_imports(source, ext == ".tsx")

        def resolver(spec_str: str, from_rel: str, files: Set[str]) -> Optional[str]:
            return _resolve_ts_import(
                spec_str, from_rel, files, path_aliases=path_aliases
            )
    else:
        return

    for spec_str, line in imports:
        target_rel = resolver(spec_str, rel, rel_files_set)
        if target_rel:
            target_id = file_map.get(target_rel)
            target_comp = by_id.get(target_id or "")
            if target_comp and target_id != comp.component_id:
                _append_connection(
                    connections,
                    seen_connections,
                    comp,
                    target_comp,
                    "imports",
                    rel,
                    line,
                    spec_str,
                    1.0,
                    "build-loop-native-scanner",
                )
            continue

        package = _external_package_for_import(
            spec_str, ext, declared_npm, declared_pip
        )
        if package:
            manager, package_name, manifest_rel = package
            package_comp = _ensure_package_component(
                components, by_id, runtime_components,
                manager, package_name, manifest_rel,
            )
            _append_connection(
                connections,
                seen_connections,
                comp,
                package_comp,
                "uses-package",
                rel,
                line,
                package_name,
                1.0,
                "build-loop-native-scanner (bare-import)",
                description=f"{rel} uses {package_name}",
            )

    if ext in TS_EXTS or ext in JS_EXTS:
        for api_path, line in _api_fetches(source, rel):
            route_rel = _resolve_api_route(api_path, rel_files_set)
            if not route_rel:
                continue
            route_id = file_map.get(route_rel)
            route_comp = by_id.get(route_id or "")
            if not route_comp or route_id == comp.component_id:
                continue
            _append_connection(
                connections,
                seen_connections,
                comp,
                route_comp,
                "frontend-calls-api",
                rel,
                line,
                f"fetch({api_path})",
                0.9,
                "build-loop-native-scanner (fetch)",
                symbol_type="function",
                description=f"{rel} fetches {api_path}",
            )

    for pattern, line, snippet, detected in _service_matches(source):
        service_comp = _ensure_service_component(
            components, by_id, runtime_components, pattern
        )
        _append_connection(
            connections,
            seen_connections,
            comp,
            service_comp,
            "service-call",
            rel,
            line,
            pattern.name,
            0.85,
            f"build-loop-native-scanner pattern: {detected}",
            symbol_type="function",
            description=f"Calls {pattern.name}: {snippet}",
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

    path_aliases = _load_ts_path_aliases(repo_root)
    declared_npm = _read_declared_npm_packages(repo_root, spec)
    declared_pip = _read_declared_pip_packages(repo_root, spec)
    runtime_components = _seed_runtime_components(components)
    by_id: Dict[str, Component] = {c.component_id: c for c in components}
    seen_connections: Set[Tuple[str, str, str, int, str]] = set()

    # Pass 2: parse imports + runtime calls per file, build connections.
    connections: List[Connection] = []
    for comp in components:
        if comp.metadata.get("kind") != "source-file":
            continue
        rel = comp.metadata.get("file", "")
        full = repo_root / rel
        try:
            source = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ext = os.path.splitext(rel)[1].lower()
        _emit_file_connections(
            rel=rel,
            comp=comp,
            source=source,
            ext=ext,
            rel_files_set=rel_files_set,
            file_map=file_map,
            by_id=by_id,
            components=components,
            connections=connections,
            seen_connections=seen_connections,
            runtime_components=runtime_components,
            declared_npm=declared_npm,
            declared_pip=declared_pip,
            path_aliases=path_aliases,
        )

    # Pass 3: backfill connects_to / connected_from on components.
    _refresh_component_links(components, connections)

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
        new_comps = _prune_unreferenced_runtime_components(new_comps, new_conns)
        _refresh_component_links(new_comps, new_conns)
        files_scanned = sum(1 for c in new_comps if c.metadata.get("kind") == "source-file")
        return ScanResult(new_comps, new_conns, new_file_map, new_hashes, files_scanned)

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
    spec = _load_gitignore(repo_root)
    path_aliases = _load_ts_path_aliases(repo_root)
    declared_npm = _read_declared_npm_packages(repo_root, spec)
    declared_pip = _read_declared_pip_packages(repo_root, spec)
    runtime_components = _seed_runtime_components(new_comps)
    by_id: Dict[str, Component] = {c.component_id: c for c in new_comps}
    seen_connections: Set[Tuple[str, str, str, int, str]] = {
        (c.type, c.from_id, c.to_id, c.line, c.symbol) for c in new_conns
    }
    _emit_file_connections(
        rel=rel_path,
        comp=comp,
        source=source,
        ext=ext,
        rel_files_set=rel_files_set,
        file_map=new_file_map,
        by_id=by_id,
        components=new_comps,
        connections=new_conns,
        seen_connections=seen_connections,
        runtime_components=runtime_components,
        declared_npm=declared_npm,
        declared_pip=declared_pip,
        path_aliases=path_aliases,
    )

    # Re-derive connects_to / connected_from.
    new_comps = _prune_unreferenced_runtime_components(new_comps, new_conns)
    _refresh_component_links(new_comps, new_conns)
    files_scanned = sum(1 for c in new_comps if c.metadata.get("kind") == "source-file")

    return ScanResult(new_comps, new_conns, new_file_map, new_hashes, files_scanned)
