#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""db_substrate_lint.py — ADVISORY, WARN-only substrate-boundary lint.

Scans a TARGET consumer repo for two clear, grep-able anti-patterns from
`references/database-agent-constitution.md`. It is **advisory** — it never
gates a build, it only surfaces findings. Each finding cites the constitution
rule it implements plus the real seeding evidence (sample-app, evidence
sample 1) that motivated the check.

It deliberately covers ONLY two deterministic, low-false-positive patterns:

  (a) Embedding / retrieval / vector tables (and retrieval/cache key builders)
      written WITHOUT any version component
      (embedding_model / embedding_version / index_version / permission_version).
      Constitution "Retrieval And Metadata Rules" #4 and #5.

  (b) AI-visible artifact tables (vector-row models, or file/object ingestion
      records) WITHOUT an accompanying metadata field
      (checksum / version / permissions / status).
      Constitution "File And Artifact Rule".

The SEMANTIC items ("a vector store treated as source of truth", "a derived
summary treated as authoritative") are NOT detected here — they stay an
assessor-LLM lens per the constitution, because a grep would false-positive
and rot. Heuristics here are conservative: a table/model block must look
clearly embedding/artifact-shaped before it is flagged.

CLI::

    python3 scripts/db_substrate_lint.py --workdir <repo> --json

Exit code is ALWAYS 0 (advisory). The JSON payload carries the findings.

Output JSON::

    {
      "tool": "db_substrate_lint",
      "advisory": true,
      "workdir": "<abs path>",
      "scanned_files": <int>,
      "findings": [
        {
          "check": "missing_version_component" | "artifact_without_metadata",
          "severity": "WARN",
          "file": "<rel path>",
          "line": <int>,
          "block": "<table/model name>",
          "message": "<what + why>",
          "constitution_rule": "<rule citation>",
          "seed_evidence": "<observed failure that motivated the check>"
        }
      ],
      "finding_count": <int>
    }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Files worth scanning. Schema/migration/model definition surfaces only —
# keeps the scan cheap and the false-positive rate low.
SCAN_SUFFIXES = (".prisma", ".sql", ".ts", ".py")
SCAN_NAME_HINTS = ("schema", "migration", "model", "embed", "vector",
                   "ingest", "artifact", "retrieval", "cache", "repository", "repo")

# Directories we never descend into.
SKIP_DIRS = {
    ".git", ".claude", "node_modules", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".build-loop", "coverage", ".turbo", "worktrees",
}

# Identifier tokenizer: split snake_case / camelCase / kebab into lowercase
# segments so `ingested_documents` -> {ingested, documents} and
# `ArticleEmbedding` -> {article, embedding}. Lets us match shape tokens that
# live INSIDE a compound identifier without the false positives of a loose
# substring search (e.g. "documentation" stays distinct from "document").
_SEG_SPLIT_RE = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])")


def _segments(identifier: str) -> set[str]:
    return {seg.lower() for seg in _SEG_SPLIT_RE.split(identifier) if seg}


# Shape token sets. A block matches when its identifier segments intersect one
# of these sets (singular forms — trailing 's' is stripped before the check).
_EMBED_TOKENS = {"embedding", "vector", "pgvector"}
_ARTIFACT_TOKENS = {
    "artifact", "document", "file", "object", "ingestion", "ingested",
    "blob", "upload", "embedding", "vector",
}


def _has_token(identifier: str, tokens: set[str]) -> bool:
    segs = _segments(identifier)
    # Match singular or plural (strip a trailing 's').
    norm = {s[:-1] if s.endswith("s") and len(s) > 3 else s for s in segs}
    return bool((segs | norm) & tokens)


# Embedding/vector-shaped block detection from the body. Conservative: requires
# a vector TYPE (`vector(...)`, `Unsupported("vector")`, pgvector) or an
# embedding/vector COLUMN declaration — NOT a bare word, which would fire on
# comments (e.g. `-- 'reranking' | 'embeddings'` in an unrelated cost table).
EMBED_BODY_RE = re.compile(
    r"(vector\s*\(|Unsupported\(\s*[\"']vector|pgvector|"
    r"\b(embedding|embeddings|embedding_vector|centroid_vector)\b\s+"
    r"(vector|Unsupported|bytea|float|real|double|json))",
    re.IGNORECASE,
)


def _strip_comments(text: str) -> str:
    """Remove SQL line comments (-- ...) and // line comments so a token inside
    a comment never triggers a body match. Block comments are left alone — they
    are rare in schema/migration files and stripping them risks corrupting
    multiline definitions."""
    out = []
    for line in text.splitlines():
        # Cut at the first -- or // that isn't inside a quoted string (cheap
        # heuristic: schema/migration comments are not inside strings here).
        for marker in ("--", "//"):
            idx = line.find(marker)
            if idx != -1:
                line = line[:idx]
        out.append(line)
    return "\n".join(out)
# Version components that satisfy check (a). Any one present clears the block.
VERSION_TOKEN_RE = re.compile(
    r"\b(embedding_model|embeddingModel|embedding_version|embeddingVersion|"
    r"index_version|indexVersion|permission_version|permissionVersion|"
    r"model_version|modelVersion|chunking_version|chunkingVersion|"
    r"source_version|sourceVersion)\b",
    re.IGNORECASE,
)
# Cache/retrieval key builders. Conservative: a string-concat key build that
# mentions cache/retrieval/embedding context.
CACHE_KEY_RE = re.compile(
    r"\b(cacheKey|cache_key|retrievalKey|retrieval_key|redisKey|embeddingKey)\b",
    re.IGNORECASE,
)

# Metadata fields that satisfy check (b). Any one present clears the block.
METADATA_TOKEN_RE = re.compile(
    r"\b(checksum|content_hash|contentHash|sha256|md5|version|"
    r"permissions|permission|acl|status|classification|"
    r"retention_policy|retentionPolicy|owner|tenant)\b",
    re.IGNORECASE,
)

# Detect the start of a table/model definition block and capture its name.
# Prisma:  model ArticleEmbedding {
# SQL:     CREATE TABLE article_embeddings (
PRISMA_MODEL_RE = re.compile(r"^\s*model\s+(\w+)\s*\{")
SQL_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`']?(\w+)[\"`']?",
    re.IGNORECASE,
)

RULE_A = 'Retrieval And Metadata Rules #4/#5 (track embedding/index versions; version cache keys)'
RULE_B = 'File And Artifact Rule (every AI-visible artifact needs a DB metadata record)'
SEED_A = (
    'sample-app (evidence sample 1): article_embeddings rows store content_hash '
    '(content staleness) but carry no embedding_model/embedding_version/index_version '
    '-> a model swap yields undetectable stale vectors.'
)
SEED_B = (
    'Constitution "File And Artifact Rule" (modeled, not a specific observed '
    'sample-app row — sample embedding rows carry content_hash, which clears '
    'this check): AI-visible vector/ingestion rows without an accompanying '
    'checksum/version/permissions/status record cannot express ownership, '
    'freshness, or retrieval status. Check (b) guards the artifact class the '
    'constitution names; the live evidence-sample-1 failure surfaced via '
    'check (a).'
)


def _is_scannable(path: Path) -> bool:
    if path.suffix.lower() not in SCAN_SUFFIXES:
        return False
    # .prisma and .sql are always schema-shaped. For .ts/.py require a name hint
    # so we don't scan the whole app.
    if path.suffix.lower() in (".prisma", ".sql"):
        return True
    lower = str(path).lower()
    return any(h in lower for h in SCAN_NAME_HINTS)


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if _is_scannable(p):
            yield p


def _split_blocks(lines: list[str]):
    """Yield (block_name, start_line_1based, body_text) for each model/table
    definition. Brace-balanced for Prisma; paren-balanced for SQL CREATE TABLE.
    Conservative — only blocks we can clearly delimit are yielded."""
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = PRISMA_MODEL_RE.match(line)
        if m:
            name = m.group(1)
            depth = line.count("{") - line.count("}")
            body = [line]
            j = i + 1
            while j < n and depth > 0:
                body.append(lines[j])
                depth += lines[j].count("{") - lines[j].count("}")
                j += 1
            yield name, i + 1, "\n".join(body)
            i = j
            continue
        m = SQL_TABLE_RE.match(line)
        if m:
            name = m.group(1)
            depth = line.count("(") - line.count(")")
            body = [line]
            j = i + 1
            # If the opening paren wasn't on the CREATE line yet, keep reading.
            while j < n and (depth > 0 or "(" not in "".join(body)):
                body.append(lines[j])
                depth += lines[j].count("(") - lines[j].count(")")
                j += 1
                if depth <= 0 and "(" in "".join(body):
                    break
            yield name, i + 1, "\n".join(body)
            i = j
            continue
        i += 1


def scan_repo(workdir: Path) -> dict:
    findings: list[dict] = []
    scanned = 0
    for path in _iter_files(workdir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        rel = str(path.relative_to(workdir))
        lines = text.splitlines()

        for name, start, body in _split_blocks(lines):
            # Check (a): embedding/vector block missing a version component.
            body_nc = _strip_comments(body)
            if _has_token(name, _EMBED_TOKENS) or EMBED_BODY_RE.search(body_nc):
                if not VERSION_TOKEN_RE.search(body):
                    findings.append({
                        "check": "missing_version_component",
                        "severity": "WARN",
                        "file": rel,
                        "line": start,
                        "block": name,
                        "message": (
                            f"Embedding/vector definition '{name}' carries no version "
                            f"component (embedding_model/embedding_version/index_version); "
                            f"stale vectors after a model/index swap would be undetectable."
                        ),
                        "constitution_rule": RULE_A,
                        "seed_evidence": SEED_A,
                    })
            # Check (b): AI-visible artifact block missing a metadata record.
            if _has_token(name, _ARTIFACT_TOKENS):
                if not METADATA_TOKEN_RE.search(body):
                    findings.append({
                        "check": "artifact_without_metadata",
                        "severity": "WARN",
                        "file": rel,
                        "line": start,
                        "block": name,
                        "message": (
                            f"AI-visible artifact definition '{name}' has no metadata "
                            f"field (checksum/version/permissions/status); ownership, "
                            f"freshness, and retrieval status cannot be expressed."
                        ),
                        "constitution_rule": RULE_B,
                        "seed_evidence": SEED_B,
                    })

        # Check (a) extension: cache/retrieval key builders without a version key.
        for idx, line in enumerate(lines, start=1):
            if CACHE_KEY_RE.search(line) and not VERSION_TOKEN_RE.search(line):
                # Only flag clear key-construction lines (assignment or concat),
                # to keep false positives low.
                if "=" in line and ("`" in line or "+" in line or "${" in line or "join" in line.lower() or "f'" in line or 'f"' in line):
                    findings.append({
                        "check": "missing_version_component",
                        "severity": "WARN",
                        "file": rel,
                        "line": idx,
                        "block": "cache/retrieval key",
                        "message": (
                            "Retrieval/cache key built without a version component "
                            "(index_version/permission_version); stale or "
                            "unauthorized context can leak."
                        ),
                        "constitution_rule": RULE_A,
                        "seed_evidence": SEED_A,
                    })

    return {
        "tool": "db_substrate_lint",
        "advisory": True,
        "workdir": str(workdir),
        "scanned_files": scanned,
        "findings": findings,
        "finding_count": len(findings),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Advisory, WARN-only substrate-boundary lint for a target repo."
    )
    ap.add_argument("--workdir", required=True, help="Target consumer repo to scan.")
    ap.add_argument("--json", action="store_true", help="Emit JSON (default human).")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    if not workdir.is_dir():
        result = {"tool": "db_substrate_lint", "advisory": True,
                  "error": f"workdir not a directory: {workdir}",
                  "findings": [], "finding_count": 0}
        print(json.dumps(result) if args.json else result["error"])
        return 0  # advisory: never hard-fail

    result = scan_repo(workdir)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"db_substrate_lint (ADVISORY) — scanned {result['scanned_files']} files, "
              f"{result['finding_count']} finding(s)")
        for f in result["findings"]:
            print(f"  [WARN] {f['file']}:{f['line']} ({f['block']}) — {f['message']}")
            print(f"         rule: {f['constitution_rule']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
