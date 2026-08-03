#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
security_checks_api.py — deterministic API + AI-boundary security checks.

The companion module to ``security_scan.py``'s checks A-G. Those cover the
greppable *content* classes (secrets, injection, SSRF). These cover the
*authorization and boundary* classes that dominate real API compromise:

  H. Broken object-level authorization  HIGH/CRITICAL  A01
  I. Missing / fail-open auth guard     HIGH/CRITICAL  A01/A07
  J. Privileged secret in client bundle HIGH/CRITICAL  A07/LLM06
  K. Session + token hygiene            CRITICAL..LOW  A02/A07
  L. CORS misconfiguration              HIGH/MEDIUM    A05
  M. Mass assignment / unvalidated body HIGH/MEDIUM    A03/A04
  N. AI + agent boundary                HIGH/MEDIUM    LLM01/02/04/ASI02/ASI05

Design rule — every check requires TWO positive signals before it emits: a
handler/sink match AND the absence of the corresponding control. A gate that
hard-blocks deploys cannot afford a noisy check, so single-signal heuristics
are deliberately excluded even where they would catch more.

Stdlib only. No LLM. No network.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from security_common import (
    finding,
    first_match_line,
    is_api_path,
    strip_string_literals,
    suppressed,
)

# ---------------------------------------------------------------------------
# Shared route/handler recognition
# ---------------------------------------------------------------------------

# Any HTTP handler, mutating or not. Object-level authorization matters on GET
# too (reading another tenant's record is the canonical BOLA), so this is wider
# than security_scan's _API_HANDLER_RE, which is mutation-only by design.
HANDLER_RE = re.compile(
    r"\bexport\s+(?:const|async\s+function|function|default\s+async\s+function)\s+"
    r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|handler)\b|"
    r"\b(?:router|app|server|fastify)\.(?:get|post|put|patch|delete|all)\s*\(|"
    r"@(?:app|router)\.(?:get|post|put|patch|delete)\s*\(|"
    r"\b(?:publicProcedure|protectedProcedure)\b|"
    r"\bexport\s+async\s+function\s+(?:loader|action)\b",
    re.IGNORECASE,
)

MUTATING_HANDLER_RE = re.compile(
    r"\bexport\s+(?:const|async\s+function|function)\s+(?:POST|PUT|PATCH|DELETE)\b|"
    r"\b(?:router|app|server|fastify)\.(?:post|put|patch|delete)\s*\(|"
    r"@(?:app|router)\.(?:post|put|patch|delete)\s*\(",
    re.IGNORECASE,
)

# Reading a value out of the incoming request. The presence of one of these is
# what makes an identifier "attacker-controlled" for check H.
REQUEST_INPUT_RE = re.compile(
    r"\b(?:req|request|ctx|event)\.(?:params|query|body|nextUrl|url)\b|"
    r"\bparams\s*[.\[]|\bsearchParams\.get\s*\(|"
    r"\bawait\s+(?:req|request)\.json\s*\(|"
    r"\bpathParameters\b|\bqueryStringParameters\b|"
    r"\brequest\.args\b|\brequest\.form\b|\brequest\.json\b",
    re.IGNORECASE,
)

# An authorization predicate: the query is scoped to the caller's principal or
# tenant. Presence anywhere in the handler file clears check H.
OWNER_PREDICATE_RE = re.compile(
    r"\b(?:user_?id|owner_?id|account_?id|tenant_?id|org(?:anization)?_?id|"
    r"workspace_?id|customer_?id|principal_?id|created_?by|belongs_?to)\b|"
    r"\bsession\.user\b|\bauth\.uid\s*\(|\bcurrentUser\b|"
    r"\brls\b|row[\s_\-]?level[\s_\-]?security|"
    r"\bforPrincipal\b|\bscopedTo\b|\bwhereOwner\b",
    re.IGNORECASE,
)

# Evidence that the handler establishes WHO is calling. Required before check H
# can call anything object-level authorization: no principal, no BOLA.
#
# Matches the framework primitives plus the naming conventions codebases
# actually use for their own guards — require*/ensure*/assert* prefixes, the
# *Or401/*Or403/*Or503 suffix idiom, and resolve*Identity. Those conventions are
# near-universal, so this stays portable instead of hard-coding one project's
# helper names.
#
# The camelCase convention alternatives are wrapped in (?-i:...) — case
# SENSITIVE — even though the rest of the pattern is case-insensitive. Under
# IGNORECASE the class [A-Z] also matches lowercase, so `require[A-Z]\w*`
# matched the ordinary English word "required" (as in the error string
# "articleId is required"). That single letter-class turned every route with a
# validation message into a route with an authentication guard. Pinned by
# test_error_message_required_is_not_an_identity_check.
IDENTITY_RESOLUTION_RE = re.compile(
    r"\b(?:getServerSession|getSession|auth\s*\(|currentUser|getCurrentUser|"
    r"getUser\w*|useUser|clerkClient|getAuth|supabase\.auth|"
    r"session\.user|req(?:uest)?\.user|ctx\.user|g\.user|"
    r"user_?id\s*=|userId\s*=|current_user|get_current_user|"
    r"protectedProcedure|login_required|jwt\.verify|jwtVerify|verifyToken)\b"
    r"|(?-i:\b(?:require[A-Z]\w*|ensure[A-Z]\w*|assert[A-Z]\w*|"
    r"resolve\w*Identity\w*|\w+Or(?:401|403|503))\s*\()",
    re.IGNORECASE,
)

# A role/privilege gate rather than an ownership gate. Its presence means the
# route's authorization model is "may this caller act at all", not "does this
# caller own this row" — so the absence of an owner predicate is the design,
# not a defect.
#
# An admin editing a shared catalogue is the ordinary shape of an admin
# endpoint. Demanding `where: { userId }` there asks the query to scope to a
# principal that is explicitly authorized across all of them. Before this
# carve-out, admin routes were the single largest remaining source of CRITICAL
# object-authorization findings on a real app — every one of them correct code.
ROLE_GUARD_RE = re.compile(
    r"\brequire(?:Local)?Admin\b|\brequireRole\b|\brequirePermission\b|"
    r"\bisAdmin\b|\badminOnly\b|\bensureAdmin\b|\bassertAdmin\b|"
    r"\bhasPermission\b|\bhasRole\b|\bcheckPermission\b|\bauthorizeRole\b|"
    r"role\s*(?:===?|==|!=)\s*[\"']admin[\"']|"
    r"\brole\s*:\s*[\"']admin[\"']|"
    r"@admin_required\b|@staff_member_required\b|\bis_staff\b|\bis_superuser\b|"
    r"\bIsAdminUser\b|\bAdminOnly\b",
    re.IGNORECASE,
)

# A database read or write. Broad on purpose: the check only fires when this
# co-occurs with request-derived input AND no owner predicate.
DB_ACCESS_RE = re.compile(
    r"\.(?:findUnique|findFirst|findById|findOne|find_one|get_by_id|"
    r"findMany|update|updateMany|delete|deleteMany|upsert|create)\s*\(|"
    r"\bfrom\s*\(\s*[\"'][\w-]+[\"']\s*\)|"                # supabase.from('t')
    r"\bSELECT\b[\s\S]{0,120}?\bWHERE\b|"
    r"\bUPDATE\s+\w+[\s\S]{0,120}?\bWHERE\b|"
    r"\bDELETE\s+FROM\b|"
    r"\.(?:prepare|execute|query)\s*\(|"
    r"\bgetItem\s*\(|\bGetItemCommand\b|\bUpdateItemCommand\b",
    re.IGNORECASE,
)


def _file_text(lines: list[str]) -> str:
    return "\n".join(lines)


def _first_code_match(
    lines: list[str], pattern: re.Pattern[str]
) -> tuple[int, str] | None:
    """First pattern hit on a line that is not a comment or docstring body.

    Returns None when the pattern only appears in prose — which is the correct
    answer for a check that grades behavior, not documentation.
    """
    for i, line in enumerate(lines, 1):
        if _COMMENT_LINE_RE.match(line):
            continue
        if pattern.search(line):
            return i, line.strip()
    return None


def _emit(
    lines: list[str],
    line_no: int,
    **kw: Any,
) -> list[dict[str, Any]]:
    """Emit one finding unless the anchor line is `nosec:`-suppressed."""
    idx = line_no - 1
    if 0 <= idx < len(lines) and suppressed(lines[idx]):
        return []
    return [finding(line_no=line_no, **kw)]


# ---------------------------------------------------------------------------
# Check H: Broken object-level authorization (BOLA / IDOR)
# ---------------------------------------------------------------------------
# OWASP's highest-ranked API risk. An authenticated caller swaps an object ID
# and reads or mutates another user's record. Route-level auth does not prevent
# it: the query itself must be scoped to the caller's principal.

def check_H_object_authorization(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    if not is_api_path(path):
        return []
    text = _file_text(lines)

    if not HANDLER_RE.search(text):
        return []
    if not DB_ACCESS_RE.search(text):
        return []
    if not REQUEST_INPUT_RE.search(text):
        return []
    # THE THIRD SIGNAL: this route must have a principal.
    #
    # Broken object-level authorization means one caller reaching another
    # caller's object. That presupposes callers are distinguishable — the
    # finding's own wording says "an authenticated caller can substitute another
    # principal's object ID". A handler that never resolves an identity has no
    # principals to confuse: it is a public read of shared data, which is the
    # normal shape of a catalogue, feed, or docs endpoint.
    #
    # Without this signal the check fires on every read of a globally-shared
    # table and buries the real findings. Measured on a 3,386-file Next.js app:
    # 49 CRITICAL, none of them reachable, while four genuinely open endpoints
    # in the same tree went unreported.
    #
    # The trade is a deliberate false negative — an app that authenticates
    # somewhere other than the handler file, and queries by request id without
    # scoping, is missed here. Check I covers the missing-guard half of that,
    # and a gate that cries wolf is worse than one that occasionally stays
    # quiet.
    if not IDENTITY_RESOLUTION_RE.search(text):
        return []
    # Role-based authorization is a complete control for this class. A caller
    # the route has already authorized as an operator is not "another
    # principal" relative to shared records.
    if ROLE_GUARD_RE.search(text):
        return []
    # The control: the query is scoped to the caller. Anywhere in the file is
    # enough — handler files are small, and a false negative here is preferable
    # to blocking a deploy on a correctly-scoped query the regex misread.
    if OWNER_PREDICATE_RE.search(text):
        return []

    line_no, snippet = first_match_line(lines, DB_ACCESS_RE)
    is_mutating = bool(MUTATING_HANDLER_RE.search(text))
    return _emit(
        lines,
        line_no,
        severity="CRITICAL" if is_mutating else "HIGH",
        owasp_ids="A01",
        file_path=path,
        message=(
            "Handler reads a request-supplied identifier and queries the data store "
            "with no owner/tenant predicate — an authenticated caller can substitute "
            "another principal's object ID"
            + (" and mutate it" if is_mutating else "")
        ),
        snippet=snippet,
        fix=(
            "filter by the authenticated principal in the query itself "
            "(get_for_principal(id, tenant_id, principal_id) → 404 on miss), "
            "not by loading the object and checking ownership afterward"
        ),
        check_id="H",
    )


# ---------------------------------------------------------------------------
# Check I: Missing or fail-open auth guard on a mutating route
# ---------------------------------------------------------------------------

AUTH_GUARD_RE = re.compile(
    r"\b(?:getServerSession|getSession|auth\s*\(|requireAuth|requireUser|withAuth|"
    r"verifyToken|verifyJWT|verifyIdToken|jwt\.verify|jwtVerify|"
    r"currentUser|getUser|clerkClient|getAuth|supabase\.auth|"
    r"authenticate|authorize|isAuthenticated|ensureLoggedIn|"
    r"protectedProcedure|login_required|@requires_auth|Depends\s*\(\s*get_current_user)\b|"
    # Project-defined guards. Codebases name their own helpers, and a check that
    # only knows framework primitives reports every one of them as unguarded.
    # These three conventions cover the overwhelming majority:
    #   require*/ensure*/assert*  — requireAdmin, ensureSignedIn, assertOwner
    #   *Or401 / *Or403 / *Or503  — resolveIdentityOr503
    #   resolve*Identity          — resolveIdentity, resolveUserIdentity
    # A codebase that adopted a wrapper for exactly this purpose was previously
    # reported as having "no authentication reference anywhere in the file" on
    # every route that used it — the single largest false-positive source in
    # this check.
    # Case-SENSITIVE: see the note on IDENTITY_RESOLUTION_RE. Under IGNORECASE
    # the [A-Z] class matches lowercase, so "required" in a validation message
    # would register as an auth guard.
    r"(?-i:\b(?:require[A-Z]\w*|ensure[A-Z]\w*|assert[A-Z]\w*|"
    r"\w+Or(?:401|403|503)|resolve\w*Identity\w*|\w*Guard\w*)\s*\()|"
    r"headers\(\)\.get\s*\(\s*[\"']authorization[\"']|"
    r"\breq(?:uest)?\.headers\[?[\.\"']*authorization",
    re.IGNORECASE,
)

# A repo-root request interceptor. Next.js middleware.ts (renamed proxy.ts in
# Next 16), SvelteKit hooks.server.ts, Remix entry.server.tsx, and Django/Rails
# middleware all gate whole path prefixes BEFORE any handler runs, which a
# per-file check cannot see.
#
# Its presence does not prove a given route is gated — the interceptor might
# allow-list that path — so this does not clear the finding. It downgrades the
# severity, because "no guard visible in this file, and the repo has a
# route-prefix gate that may cover it" is a materially weaker claim than "this
# endpoint is open", and only the first is honest from static per-file reading.
_INTERCEPTOR_NAMES = (
    "middleware.ts", "middleware.js", "middleware.tsx",
    "proxy.ts", "proxy.js",
    "hooks.server.ts", "hooks.server.js",
    "entry.server.tsx", "entry.server.ts",
)


def _repo_has_route_interceptor(path: Path) -> bool:
    """Walk up to the repo root looking for a request interceptor."""
    for parent in list(path.parents)[:12]:
        if (parent / ".git").exists() or (parent / "package.json").exists():
            if any((parent / name).exists() for name in _INTERCEPTOR_NAMES):
                return True
            # Keep walking: a monorepo package root is not the repo root.
    return False

# The fail-open bypass: comparing a caller-supplied token to an env var that may
# be undefined. If the env var is unset, `undefined !== undefined` is false and
# the guard passes for everyone. Requires a separate presence assertion to be
# safe.
FAIL_OPEN_CMP_RE = re.compile(
    r"(?:!==?|===?)\s*(?:process\.env\.\w+|process\.env\[[\"']\w+[\"']\]|"
    r"os\.environ\.get\s*\([^)]*\)|os\.getenv\s*\([^)]*\)|Deno\.env\.get\s*\([^)]*\)|"
    r"env\.\w+)"
    r"|(?:process\.env\.\w+|env\.\w+|os\.environ\.get\s*\([^)]*\)|os\.getenv\s*\([^)]*\))"
    r"\s*(?:!==?|===?)",
    re.IGNORECASE,
)

# Deployment-mode and feature-flag variables. Not secrets, so a comparison
# against one is not the fail-open-secret defect. Kept narrow and explicit
# rather than "any name without KEY/SECRET/TOKEN in it", so a genuinely
# secret-bearing variable with an unusual name still gets flagged.
# NOTE: no bare `ENV` alternative. `\bENV\b` matches the `env` inside
# `process.env.WEBHOOK_SECRET`, which silently suppressed every real finding
# this check exists for. Caught by test_real_unset_secret_comparison_still_flagged.
_NON_SECRET_ENV_RE = re.compile(
    r"\b(?:NODE_ENV|ENVIRONMENT|APP_ENV|RAILS_ENV|FLASK_ENV|DJANGO_ENV|"
    r"DEPLOY_ENV|STAGE|VERCEL_ENV|NEXT_PUBLIC_VERCEL_ENV|DEBUG|"
    r"ENABLE_\w+|FEATURE_\w+|FF_\w+|\w+_ENABLED)\b",
    re.IGNORECASE,
)

# An explicit assertion that the secret is configured. Presence clears the
# fail-open finding.
ENV_PRESENCE_GUARD_RE = re.compile(
    r"if\s*\(\s*!\s*(?:process\.env\.\w+|env\.\w+|Deno\.env\.get)|"
    r"\bprocess\.env\.\w+\s*(?:\?\?|\|\|)\s*(?:throw|\(\s*\(\s*\)\s*=>)|"
    r"\brequireEnv\b|\bassertEnv\b|\bgetRequiredEnv\b|"
    r"\bz\.string\(\)\.min\(1\)|"
    r"\bos\.environ\[[\"']\w+[\"']\]|"          # KeyError on missing = fail closed
    r"\braise\s+\w*(?:Error|Exception)\b[\s\S]{0,80}\benv\b",
    re.IGNORECASE,
)


def check_I_auth_guard(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    if not is_api_path(path):
        return []
    text = _file_text(lines)

    if not MUTATING_HANDLER_RE.search(text):
        return []

    out: list[dict[str, Any]] = []
    has_guard = bool(AUTH_GUARD_RE.search(text))

    # I2 — fail-open env comparison. Reported even when another guard exists,
    # because the bypass is in the comparison itself.
    for i, raw in enumerate(lines, 1):
        stripped = strip_string_literals(raw)
        if not FAIL_OPEN_CMP_RE.search(stripped):
            continue
        # Only a comparison that gates control flow is a guard.
        if not re.search(r"\b(?:if|unless|assert|return|throw|raise)\b", stripped):
            continue
        if ENV_PRESENCE_GUARD_RE.search(text):
            continue
        # NODE_ENV and friends are not secrets, and the idiom they appear in is
        # the opposite of fail-open. `if (NODE_ENV !== 'development') return 404`
        # denies whenever the variable is unset, empty, or anything other than
        # the one permitted value — it fails CLOSED, which is the correct shape
        # for a dev-only route. The fail-open defect this check exists for is a
        # comparison against an unset SECRET, where absence makes the comparison
        # succeed. Treating a deployment-mode check as that defect produced a
        # large block of unfixable findings whose recommended remedy
        # ("assert the secret is present at module load") is meaningless for a
        # variable the platform always sets.
        if _NON_SECRET_ENV_RE.search(stripped):
            continue
        out.extend(_emit(
            lines,
            i,
            severity="CRITICAL",
            owasp_ids="A01/A07",
            file_path=path,
            message=(
                "Auth check compares a caller-supplied value against an environment "
                "variable with no assertion that the variable is set — if it is unset "
                "in the deployed environment the comparison passes and the route is open"
            ),
            snippet=raw.strip(),
            fix=(
                "assert the secret is present at module load (throw when unset), then "
                "compare with a constant-time equality check"
            ),
            check_id="I",
        ))

    # I1 — no auth reference of any kind on a mutating route.
    if not has_guard:
        line_no, snippet = first_match_line(lines, MUTATING_HANDLER_RE)
        # A repo-root interceptor gates path prefixes before any handler runs.
        # Static per-file reading cannot tell whether it covers THIS path, so
        # state the weaker, true claim instead of the strong, possibly-false one.
        gated_upstream = _repo_has_route_interceptor(path)
        out.extend(_emit(
            lines,
            line_no,
            severity="MEDIUM" if gated_upstream else "HIGH",
            owasp_ids="A01",
            file_path=path,
            message=(
                "Mutating endpoint has no authentication or authorization reference "
                "in the handler file; the repo has a route-prefix interceptor "
                "(middleware/proxy) that may or may not cover this path — verify "
                "which tier it assigns"
                if gated_upstream else
                "Mutating endpoint has no authentication or authorization reference "
                "anywhere in the handler file"
            ),
            snippet=snippet,
            fix=(
                "resolve the caller's principal before the mutation and reject when "
                "absent; a route-level role check is the floor, object-level scoping "
                "is still required"
            ),
            check_id="I",
        ))

    return out


# ---------------------------------------------------------------------------
# Check J: Privileged secret exposed to the client bundle
# ---------------------------------------------------------------------------
# A browser bundle is inspectable and a mobile binary is decompilable. A build
# system that inlines any variable with a public prefix will happily inline a
# service-role key.

CLIENT_PREFIX_RE = re.compile(
    r"\b(NEXT_PUBLIC_|VITE_|EXPO_PUBLIC_|REACT_APP_|GATSBY_|PUBLIC_|NUXT_PUBLIC_|"
    r"VUE_APP_|PARCEL_|SVELTE_PUBLIC_)([A-Z0-9_]+)\b"
)

# Suffixes that must never reach a client bundle, ordered most-severe first.
_CRITICAL_SUFFIX_RE = re.compile(
    r"SERVICE_ROLE|SERVICE_KEY|SECRET_KEY|PRIVATE_KEY|ADMIN_KEY|ROOT_|MASTER_KEY|"
    r"STRIPE_SECRET|SK_LIVE|DATABASE_URL|DB_PASSWORD|CONNECTION_STRING",
    re.IGNORECASE,
)
_HIGH_SUFFIX_RE = re.compile(
    r"SECRET|PASSWORD|PASSWD|PRIVATE|CREDENTIAL|"
    r"OPENAI|ANTHROPIC|CLAUDE|GEMINI|MISTRAL|GROQ|REPLICATE|HUGGINGFACE|"
    r"AWS_|TWILIO|SENDGRID|RESEND_|POSTMARK|MAILGUN|BREVO|"
    r"GITHUB_TOKEN|SLACK_TOKEN|WEBHOOK_SECRET|SIGNING",
    re.IGNORECASE,
)

# Identifiers that are public by design — a publishable key is an identifier,
# not a credential.
_PUBLISHABLE_RE = re.compile(
    r"PUBLISHABLE|ANON_KEY|CLIENT_ID|MEASUREMENT_ID|SENTRY_DSN|POSTHOG|"
    r"GA_ID|GTM_|PK_LIVE|PK_TEST|SITE_KEY|APP_ID",
    re.IGNORECASE,
)


def check_J_client_exposed_secret(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(lines, 1):
        for m in CLIENT_PREFIX_RE.finditer(raw):
            prefix, suffix = m.group(1), m.group(2)
            if _PUBLISHABLE_RE.search(suffix):
                continue
            if _CRITICAL_SUFFIX_RE.search(suffix):
                sev = "CRITICAL"
            elif _HIGH_SUFFIX_RE.search(suffix):
                sev = "HIGH"
            else:
                continue
            out.extend(_emit(
                lines,
                i,
                severity=sev,
                owasp_ids="A07/LLM06",
                file_path=path,
                message=(
                    f"`{prefix}{suffix}` carries a client-exposed prefix and a "
                    "privileged name — the build inlines it into the shipped bundle "
                    "where anyone can read it"
                ),
                snippet=raw.strip()[:120],
                fix=(
                    "drop the public prefix and read the value server-side only; if a "
                    "client genuinely needs the capability, proxy it through your own "
                    "backend route"
                ),
                check_id="J",
            ))
    return out


# ---------------------------------------------------------------------------
# Check K: Session and token hygiene
# ---------------------------------------------------------------------------

_JWT_NONE_RE = re.compile(
    r"algorithms?\s*[:=]\s*\[?\s*[\"']none[\"']|"
    r"[\"']alg[\"']\s*:\s*[\"']none[\"']",
    re.IGNORECASE,
)
_JWT_DECODE_RE = re.compile(
    r"\bjwt\.decode\s*\(|\bjwtDecode\s*\(|\bdecodeJwt\s*\(|"
    r"\bjose\.decodeJwt\s*\(",
    re.IGNORECASE,
)
_JWT_VERIFY_RE = re.compile(
    r"\bjwt\.verify\s*\(|\bjwtVerify\s*\(|\bverify_jwt\b|\bverifyIdToken\s*\(|"
    r"\bcreateRemoteJWKSet\b",
    re.IGNORECASE,
)
_JWT_PY_UNVERIFIED_RE = re.compile(
    r"verify_signature[\"']?\s*:\s*False|options\s*=\s*\{[^}]*verify[^}]*False",
    re.IGNORECASE,
)

_COOKIE_SET_RE = re.compile(
    r"\bcookies\(\)\.set\s*\(|\bres\.cookie\s*\(|\bresponse\.cookies\.set\s*\(|"
    r"\bsetCookie\s*\(|\bset_cookie\s*\(|[\"']Set-Cookie[\"']",
    re.IGNORECASE,
)
_SESSION_COOKIE_NAME_RE = re.compile(
    r"session|auth|token|jwt|sid\b|refresh|csrf|access",
    re.IGNORECASE,
)
_HTTPONLY_RE = re.compile(r"httpOnly|http_only|HttpOnly", re.IGNORECASE)
_SECURE_FLAG_RE = re.compile(r"\bsecure\s*[:=]|;\s*Secure\b", re.IGNORECASE)
_SAMESITE_RE = re.compile(r"sameSite|same_site|SameSite", re.IGNORECASE)

_WEBSTORAGE_TOKEN_RE = re.compile(
    r"\b(?:localStorage|sessionStorage)\.setItem\s*\(\s*[\"'][^\"']*"
    r"(?:token|jwt|secret|auth|session|credential|api[_-]?key)",
    re.IGNORECASE,
)


def check_K_token_hygiene(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text = _file_text(lines)

    # K1a — the `none` algorithm accepts any unsigned token.
    for i, raw in enumerate(lines, 1):
        if _JWT_NONE_RE.search(raw):
            out.extend(_emit(
                lines, i,
                severity="CRITICAL",
                owasp_ids="A02/A07",
                file_path=path,
                message="JWT verification accepts the `none` algorithm — any attacker-forged unsigned token validates",
                snippet=raw.strip()[:120],
                fix="pin an explicit signing algorithm (RS256/ES256/HS256) and reject everything else",
                check_id="K",
            ))

    # K1b — decoding without verifying. Only a finding when the file never
    # verifies anywhere, so a decode-then-verify flow does not trip it.
    if _JWT_DECODE_RE.search(text) and not _JWT_VERIFY_RE.search(text):
        line_no, snippet = first_match_line(lines, _JWT_DECODE_RE)
        out.extend(_emit(
            lines, line_no,
            severity="HIGH",
            owasp_ids="A02/A07",
            file_path=path,
            message="JWT is decoded but never verified in this file — claims are read from an unauthenticated token",
            snippet=snippet,
            fix="verify signature, issuer, audience, and expiry before trusting any claim",
            check_id="K",
        ))

    for i, raw in enumerate(lines, 1):
        if _JWT_PY_UNVERIFIED_RE.search(raw):
            out.extend(_emit(
                lines, i,
                severity="HIGH",
                owasp_ids="A02/A07",
                file_path=path,
                message="JWT decoded with signature verification explicitly disabled",
                snippet=raw.strip()[:120],
                fix="remove the verify=False option and validate signature, issuer, audience, and expiry",
                check_id="K",
            ))

    # K2 — session cookie flags. Scoped to a window around the set call so a
    # `httpOnly` on an unrelated cookie elsewhere in the file does not clear it.
    for i, raw in enumerate(lines, 1):
        if not _COOKIE_SET_RE.search(raw):
            continue
        window = "\n".join(lines[max(0, i - 1): i + 8])
        if not _SESSION_COOKIE_NAME_RE.search(window):
            continue
        missing = []
        if not _HTTPONLY_RE.search(window):
            missing.append("httpOnly")
        if not _SECURE_FLAG_RE.search(window):
            missing.append("secure")
        if not _SAMESITE_RE.search(window):
            missing.append("sameSite")
        if not missing:
            continue
        out.extend(_emit(
            lines, i,
            severity="HIGH" if "httpOnly" in missing else "MEDIUM",
            owasp_ids="A02/A05",
            file_path=path,
            message=(
                "Session/auth cookie set without " + ", ".join(missing)
                + (" — readable by any script on the page" if "httpOnly" in missing else "")
            ),
            snippet=raw.strip()[:120],
            fix="set httpOnly, secure, and sameSite on every session or auth cookie",
            check_id="K",
        ))

    # K3 — durable credentials in web storage are reachable by any XSS.
    for i, raw in enumerate(lines, 1):
        if _WEBSTORAGE_TOKEN_RE.search(raw):
            out.extend(_emit(
                lines, i,
                severity="MEDIUM",
                owasp_ids="A02/A07",
                file_path=path,
                message="Auth token written to localStorage/sessionStorage — any XSS on the origin can exfiltrate it",
                snippet=raw.strip()[:120],
                fix="hold the session in a Secure/HttpOnly/SameSite cookie; keep only non-sensitive UI state in web storage",
                check_id="K",
            ))

    return out


# ---------------------------------------------------------------------------
# Check L: CORS misconfiguration
# ---------------------------------------------------------------------------
# CORS is not authorization — it constrains cooperating browsers only. But a
# wildcard origin combined with credentials converts every authenticated
# endpoint into a cross-origin one.

_ACAO_WILDCARD_RE = re.compile(
    r"[\"']?Access-Control-Allow-Origin[\"']?\s*[:=,]\s*[\"']\*[\"']|"
    r"\borigin\s*:\s*(?:[\"']\*[\"']|true)\b",
    re.IGNORECASE,
)
_ACAC_TRUE_RE = re.compile(
    r"[\"']?Access-Control-Allow-Credentials[\"']?\s*[:=,]\s*[\"']?true[\"']?|"
    r"\bcredentials\s*:\s*true\b",
    re.IGNORECASE,
)
_ORIGIN_REFLECT_RE = re.compile(
    r"Access-Control-Allow-Origin[\"']?\s*[,:]\s*(?:req|request|event)\.headers"
    r"|setHeader\s*\(\s*[\"']Access-Control-Allow-Origin[\"']\s*,\s*origin\b",
    re.IGNORECASE,
)


def check_L_cors(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text = _file_text(lines)
    creds = bool(_ACAC_TRUE_RE.search(text))

    for i, raw in enumerate(lines, 1):
        if _ACAO_WILDCARD_RE.search(raw):
            out.extend(_emit(
                lines, i,
                severity="HIGH" if creds else "MEDIUM",
                owasp_ids="A05/A01",
                file_path=path,
                message=(
                    "CORS allows any origin"
                    + (" while also allowing credentials — any site can make authenticated "
                       "cross-origin requests as the logged-in user" if creds
                       else " — acceptable only for a genuinely public, unauthenticated endpoint")
                ),
                snippet=raw.strip()[:120],
                fix="replace the wildcard with an explicit origin allowlist checked per request",
                check_id="L",
            ))
        elif _ORIGIN_REFLECT_RE.search(raw) and creds:
            out.extend(_emit(
                lines, i,
                severity="HIGH",
                owasp_ids="A05/A01",
                file_path=path,
                message="CORS reflects the caller's Origin header with credentials enabled — equivalent to a wildcard",
                snippet=raw.strip()[:120],
                fix="validate the incoming Origin against an allowlist before echoing it back",
                check_id="L",
            ))
    return out


# ---------------------------------------------------------------------------
# Check M: Mass assignment and unvalidated request bodies
# ---------------------------------------------------------------------------

_BODY_SPREAD_RE = re.compile(
    r"\.\.\.\s*(?:(?:await\s+)?(?:req|request|ctx|event)\.(?:body|json\s*\(\s*\))|"
    r"\.\.\.\s*)?"
    r"|\.\.\.\s*(?:body|payload|input|data|fields)\b|"
    r"\*\*\s*(?:request\.(?:json|form)|payload|body|data)\b",
    re.IGNORECASE,
)
_PERSIST_CALL_RE = re.compile(
    r"\.(?:update|updateMany|create|createMany|upsert|insert|set|save)\s*\(",
    re.IGNORECASE,
)
_SCHEMA_VALIDATION_RE = re.compile(
    r"\b(?:z|schema|Schema)\.(?:object|string|number|array)\b|"
    r"\.(?:parse|safeParse|parseAsync|validate|validateAsync|cast)\s*\(|"
    r"\bzod\b|\byup\b|\bjoi\b|\bvalibot\b|\bajv\b|\bsuperstruct\b|\bio-ts\b|"
    r"\bpydantic\b|\bBaseModel\b|\bTypeAdapter\b|\bmodel_validate\b|"
    r"\bclass-validator\b|\bValidationPipe\b|\bt\.Object\s*\(",
    re.IGNORECASE,
)
_BODY_READ_RE = re.compile(
    r"await\s+(?:req|request)\.json\s*\(\s*\)|"
    r"\b(?:req|request)\.body\b|"
    r"await\s+request\.formData\s*\(\s*\)|"
    r"\bawait\s+request\.json\s*\(\s*\)",
    re.IGNORECASE,
)


def check_M_mass_assignment(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    if not is_api_path(path):
        return []
    text = _file_text(lines)
    if not MUTATING_HANDLER_RE.search(text):
        return []

    out: list[dict[str, Any]] = []
    validated = bool(_SCHEMA_VALIDATION_RE.search(text))

    # M1 — an unvalidated body spread straight into a persistence call lets the
    # caller set any column, including role, tenant_id, or is_admin.
    if not validated:
        for i, raw in enumerate(lines, 1):
            stripped = raw.strip()
            if not _BODY_SPREAD_RE.search(stripped):
                continue
            window = "\n".join(lines[max(0, i - 4): i + 4])
            if not _PERSIST_CALL_RE.search(window):
                continue
            out.extend(_emit(
                lines, i,
                severity="HIGH",
                owasp_ids="A03/A04",
                file_path=path,
                message=(
                    "Request body is spread into a database write without schema "
                    "validation — the caller controls which columns are set, including "
                    "role, tenant, and ownership fields"
                ),
                snippet=stripped[:120],
                fix="parse the body against an explicit allowed-field schema and write only the parsed result",
                check_id="M",
            ))

    # M2 — mutating handler reads a body but validates nothing.
    if not validated and _BODY_READ_RE.search(text):
        line_no, snippet = first_match_line(lines, _BODY_READ_RE)
        out.extend(_emit(
            lines, line_no,
            severity="MEDIUM",
            owasp_ids="A03",
            file_path=path,
            message="Mutating endpoint reads a request body with no schema validation (allowed fields, types, bounds, lengths)",
            snippet=snippet,
            fix="validate the body against a strict schema before business logic; reject unknown fields",
            check_id="M",
        ))

    return out


# ---------------------------------------------------------------------------
# Check N: AI and agent boundary
# ---------------------------------------------------------------------------
# The model is neither a trusted user nor a policy engine. It may propose an
# action; deterministic code must decide whether it is permitted.

_LLM_CALL_RE = re.compile(
    r"\b(?:openai|anthropic|client|groq|mistral|cohere|bedrock)\s*\.\s*"
    r"(?:chat\s*\.\s*completions\s*\.\s*create|completions\s*\.\s*create|"
    r"messages\s*\.\s*create|messages\s*\.\s*stream|responses\s*\.\s*create)\s*\(|"
    r"\b(?:generateText|streamText|generateObject|streamObject)\s*\(|"
    r"\bChatCompletion\.create\s*\(|\bInvokeModelCommand\b|"
    r"\bollama\.(?:chat|generate)\s*\(",
    re.IGNORECASE,
)
_TOKEN_CAP_RE = re.compile(
    r"\bmax_?(?:output_?)?tokens\b|\bmaxOutputTokens\b|\bmax_completion_tokens\b|"
    r"\bmaxSteps\b|\bmax_steps\b|\bstopSequences\b",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(
    r"\btimeout\b|\bAbortSignal\b|\babortSignal\b|\bsignal\s*:|"
    r"\bmaxRetries\b|\bmax_retries\b|\brequest_timeout\b|\bdeadline\b",
    re.IGNORECASE,
)
_LOOP_RE = re.compile(r"^\s*(?:while|for)\s*[\(:]|\.map\s*\(|\.forEach\s*\(", re.IGNORECASE)

# Model output flowing into an interpreter or renderer. Requires an
# unambiguously model-shaped accessor: a bare `response` variable is far too
# common in ordinary HTTP code to treat as model output.
_MODEL_OUTPUT_VAR_RE = re.compile(
    r"\b\w*(?:completion|llm|model|assistant|generated|gpt|claude)\w*\s*[\.\[]|"
    r"\bchoices\s*\[|\.choices\b|"
    r"\b\w*(?:response|result|answer|output|reply)\w*\s*\.\s*"
    r"(?:content|text|message|choices|output_text|completion)\b|"
    r"\bmessage\s*\.\s*content\b",
    re.IGNORECASE,
)
_DANGEROUS_SINK_RE = re.compile(
    r"\beval\s*\(|\bnew\s+Function\s*\(|\bexec\s*\(|\bexecSync\s*\(|"
    r"\bspawn\s*\(|\bsubprocess\.(?:run|Popen|call)\s*\(|\bos\.system\s*\(|"
    r"\.innerHTML\s*=|dangerouslySetInnerHTML|"
    r"\.(?:query|execute|prepare)\s*\(|\bcursor\.execute\s*\(",
    re.IGNORECASE,
)

# Vector / retrieval calls and the tenant scoping that must accompany them.
_VECTOR_SEARCH_RE = re.compile(
    r"\.(?:similaritySearch|similarity_search|maxMarginalRelevanceSearch|"
    r"asRetriever|as_retriever)\s*\(|"
    r"\bmatch_documents\b|\bmatch_chunks\b|"
    r"\b(?:index|pinecone|weaviate|qdrant|chroma|milvus|vectorStore|vector_store)\s*\.\s*"
    r"(?:query|search|similarity_search)\s*\(",
    re.IGNORECASE,
)
_RETRIEVAL_FILTER_RE = re.compile(
    r"\bfilter\s*[:=]|\bnamespace\s*[:=]|\bwhere\s*[:=]|\bmetadata_filter\b|"
    r"\btenant\w*\b|\buser_?id\b|\borg\w*_?id\b|\bworkspace\w*\b|"
    r"\bacl\b|\bpermission\w*\b|\ballowed_?doc",
    re.IGNORECASE,
)

# A model-proposed tool call being DISPATCHED. Naming `tool_use` is not enough —
# transcript parsers, loggers, and test fixtures all mention it without ever
# executing anything. The invocation itself is the signal: a tool name indexing
# into a registry and being called, or an explicit tool-execution entry point.
_TOOL_DISPATCH_RE = re.compile(
    r"\b(?:TOOLS|TOOL_MAP|toolMap|tool_map|handlers|HANDLERS|registry|"
    r"toolRegistry|tool_registry|TOOL_HANDLERS)\s*\[[^\]\n]{1,60}\]\s*\(|"
    r"\b(?:executeTool|execute_tool|callTool|dispatchTool|dispatch_tool|"
    r"invokeTool|invoke_tool|runTool|run_tool|handleToolCall|handle_tool_call)\s*\(|"
    r"\bgetattr\s*\(\s*\w+\s*,\s*[^,)\n]*(?:tool|fn|func|name)[^,)\n]*\)\s*\(",
    re.IGNORECASE,
)

# The tool-loop context: this file actually processes model-proposed calls.
# Required alongside a dispatch match so a plain plugin registry does not trip.
_TOOL_LOOP_CONTEXT_RE = re.compile(
    r"\btool_?calls\b|\btool_use\b|\btoolUse\b|\bfunction_call\b|\btoolInvocations\b",
    re.IGNORECASE,
)

# Comment and docstring-body lines. A mention inside prose is documentation, not
# behavior, and was the single largest false-positive source for checks N2/N4.
_COMMENT_LINE_RE = re.compile(r"^\s*(?:#|//|\*|/\*|\"\"\"|''')")
_TOOL_POLICY_RE = re.compile(
    r"\bauthorize\w*\s*\(|\bpolicy\b|\bpermission\w*\b|\ballowlist\b|\ballow_?list\b|"
    r"\brequire_?approval\b|\brequiresApproval\b|\bhuman_?in_?the_?loop\b|"
    r"\bcan\s*\(|\bcheckScope\b|\benforce\w+\s*\(|\bTOOL_POLICIES\b|"
    r"\bpermission_tier\b|\bapprovalGate\b",
    re.IGNORECASE,
)


def check_N_ai_boundary(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text = _file_text(lines)

    # N1 — denial-of-wallet. An uncapped call inside a loop is the runaway shape.
    if _LLM_CALL_RE.search(text) and not _TOKEN_CAP_RE.search(text):
        line_no, snippet = first_match_line(lines, _LLM_CALL_RE)
        in_loop = any(_LOOP_RE.search(l) for l in lines[max(0, line_no - 12): line_no + 2])
        no_timeout = not _TIMEOUT_RE.search(text)
        out.extend(_emit(
            lines, line_no,
            severity="HIGH" if (in_loop and no_timeout) else "MEDIUM",
            owasp_ids="LLM04/LLM10",
            file_path=path,
            message=(
                "Model call has no output-token cap"
                + (" and no timeout, inside a loop — one request can fan out into "
                   "unbounded spend" if (in_loop and no_timeout) else
                   " — a large or adversarial input converts one request into a large bill")
            ),
            snippet=snippet,
            fix="set max output tokens, a request timeout, a retry ceiling, and a per-user/per-tenant spend budget",
            check_id="N",
        ))

    # N2 — model output reaching an interpreter or renderer.
    for i, raw in enumerate(lines, 1):
        if _COMMENT_LINE_RE.match(raw):
            continue
        stripped = raw.strip()
        if not _DANGEROUS_SINK_RE.search(stripped):
            continue
        if not _MODEL_OUTPUT_VAR_RE.search(stripped):
            continue
        # Require the file to actually call a model, so a variable coincidentally
        # named `response` in a plain HTTP client does not trip this.
        if not _LLM_CALL_RE.search(text):
            continue
        out.extend(_emit(
            lines, i,
            severity="HIGH",
            owasp_ids="LLM02/ASI05/A03",
            file_path=path,
            message=(
                "Model output flows into an interpreter, shell, SQL statement, or HTML "
                "sink — a prompt-injected response becomes code execution"
            ),
            snippet=stripped[:120],
            fix=(
                "require a typed/schema-validated model output, then act on the validated "
                "fields; never pass generated text to an interpreter"
            ),
            check_id="N",
        ))

    # N3 — retrieval that is not scoped to the caller.
    if _VECTOR_SEARCH_RE.search(text) and not _RETRIEVAL_FILTER_RE.search(text):
        line_no, snippet = first_match_line(lines, _VECTOR_SEARCH_RE)
        out.extend(_emit(
            lines, line_no,
            severity="HIGH",
            owasp_ids="LLM06/ASI06/A01",
            file_path=path,
            message=(
                "Vector retrieval runs with no tenant, namespace, or ACL filter — the "
                "model can be handed documents the caller is not entitled to read"
            ),
            snippet=snippet,
            fix=(
                "filter the corpus by the caller's permissions before retrieval; never "
                "retrieve globally and rely on the model to ignore unauthorized documents"
            ),
            check_id="N",
        ))

    # N4 — a model-proposed tool call dispatched with no deterministic gate.
    dispatch_line = _first_code_match(lines, _TOOL_DISPATCH_RE)
    if (
        dispatch_line is not None
        and _TOOL_LOOP_CONTEXT_RE.search(text)
        and not _TOOL_POLICY_RE.search(text)
    ):
        line_no, snippet = dispatch_line
        out.extend(_emit(
            lines, line_no,
            severity="HIGH",
            owasp_ids="LLM08/ASI02/ASI03",
            file_path=path,
            message=(
                "Model-proposed tool calls are dispatched with no authorization layer — "
                "the model decides what executes instead of proposing it for deterministic approval"
            ),
            snippet=snippet,
            fix=(
                "route every tool call through a policy check: authenticated caller, allowed "
                "tool, parameter schema, resource ownership, budget, approval requirement"
            ),
            check_id="N",
        ))

    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Per-file checks, in report order. security_scan.py drives these.
FILE_CHECKS = (
    check_H_object_authorization,
    check_I_auth_guard,
    check_J_client_exposed_secret,
    check_K_token_hygiene,
    check_L_cors,
    check_M_mass_assignment,
    check_N_ai_boundary,
)

# Checks cheap and precise enough to sweep across UNCHANGED files on every
# deploy without drowning the report. Excludes M and N, whose file-level
# absence-of-control shape is the noisiest on legacy code.
SPOT_CHECK_IDS = frozenset({"H", "I", "J", "K", "L"})


def run_file_checks(path: Path, lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for check in FILE_CHECKS:
        out.extend(check(path, lines))
    return out
