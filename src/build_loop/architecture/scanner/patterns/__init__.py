# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Service-detection data + matchers, plus frontend API-fetch heuristics.

Holds the ``ServicePattern`` table and the per-line matcher that maps source
text to external-service Components (LLM/payment/database SDKs), together with
the Next.js ``fetch('/api/...')`` → route-file resolution used to emit
``frontend-calls-api`` edges. Pure data + regex; no Component construction here
(that lives in ``identity``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


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


def _line_for_offset(source: str, offset: int) -> int:
    return source[:offset].count("\n") + 1


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
