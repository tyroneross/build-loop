#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
exposure_policy.py — THE definition of what a SKILL.md exposes to the user.

One rule, one place. Every script that answers "is this skill user-facing?" reads
it from here: `surface_policy.py` (report/enforce), `skill_index.py` (generated
routing index), `stamp_skill_frontmatter.py` (the authoring/promotion stamper),
and `test_agent_surface_policy.py` (the repo gate). A second copy of this rule is
a second thing to get wrong, and it already happened twice:

  * the first draft of `skill_index.py` classified a MISSING `user-invocable`
    field as hidden — the exact inverse of what the harness does — and was only
    caught by eyeballing a peer script;
  * `stamp_skill_frontmatter.py` matched the flag CASE-SENSITIVELY while the two
    other consumers lowercased it, so `user-invocable: False` was a `violation`
    to one tool and `hidden` to the other two. Same file, three answers.

Whatever imports this module cannot make either mistake in isolation. The
cross-tool agreement matrix in `test_exposure_policy.py` is the regression
artifact: it feeds one value set through EVERY consumer and asserts one answer.

THE FAIL-OPEN DEFAULT (why this rule is not obvious)
----------------------------------------------------
The harness resolves a skill's visibility as ``userInvocable ?? true``. A SKILL.md
with NO ``user-invocable`` field is therefore **PUBLIC**, not hidden. Skills are
born exposed. An author who simply never writes the field has shipped a public
skill without ever deciding to.

THE FOUR CLASSES (mutually exclusive, exhaustive)
-------------------------------------------------
    hidden              `user-invocable: false`                — not user-facing
    public_justified    `user-invocable: true` + a non-empty `public-justification:`
    public_unjustified  a field that does not spell the opt-out and states no
                        reason: `true` with no justification, OR any value this
                        repo does not recognize (`yes`, `1`, `maybe`, empty) with
                        or without one — an unrecognized flag is not an opt-in,
                        so it never buys the justified class
    default_public      no `user-invocable` field at all       — PUBLIC BY DEFAULT

`default_public` and `public_unjustified` are the two UNDECLARED classes: not
provably hidden, with nothing in the file saying that was intended. They are what
a gate rejects.

MATCHING IS CASE-INSENSITIVE, AND THAT IS EVIDENCE, NOT TASTE
--------------------------------------------------------------
`false`, `False`, and `FALSE` are ONE answer — hidden — everywhere in this repo.
The binding authority is the harness's own parser, not a YAML spec argument, so
the rule was settled by decoding it out of the shipped Claude Code binary
(`~/.local/share/claude/versions/2.1.220`, read 2026-07-30). Frontmatter is parsed
with `Bun.YAML.parse`, then both the skill loader and the plugin-command loader
resolve the flag through the same two helpers:

    userInvocable = raw === undefined ? true : coerce(raw)
    coerce(v)  = classify(v) ?? false          // unrecognized  -> FALSE
    classify(v): boolean            -> itself
                 not string/number  -> undefined          (YAML null lands here)
                 else s = String(v).toLowerCase().trim()
                      s in {1, true,  yes, on}  -> true
                      s in {0, false, no,  off} -> false
                      otherwise                 -> undefined

The lowercasing in that last branch is what makes the case question decidable
WITHOUT pinning down which YAML version Bun implements. Under YAML 1.2 core,
`False` parses to the boolean `false` and is hidden. Under YAML 1.1, or if it
arrived as the plain string `"False"`, it is lowercased to `false` and is hidden.
Both branches converge, so `False` is hidden by the harness either way, and a tool
that called it a violation was reporting a file the harness had already hidden.

Two corrections this evidence forces on the older reading of the rule:

  * an unrecognized value (`maybe`, `TRUE-ish`) is read by the harness as
    HIDDEN, not truthy — `coerce` falls back to `false`, not to the `?? true`
    default, which applies only to an ABSENT key;
  * an EMPTY value (`user-invocable:` = YAML null) is likewise HIDDEN, because
    null is not `undefined` and never reaches the fail-open default.

This repo still classifies both as `public_unjustified`. That is a DELIBERATE
over-report, not a disagreement: the repo demands the canonical literal so a
reader never has to replay the coercion table to know what a file exposes. The
safety direction is the point — over-reporting costs one edit, under-reporting
ships a public skill nobody decided to publish. The invariant that keeps it safe:
**this rule is never more permissive than the harness.** The only inputs it calls
`hidden` normalize to the literal `false`, and the harness hides every one of them.

Known limit: `y` / `n` are the one shape where the two YAML versions do NOT
converge (1.1 reads them as booleans; 1.2 as strings the coercion table rejects).
Nothing in this repo uses them, and both readings land in an UNDECLARED class
here, so the gate reports them either way.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not read files and it does not parse YAML. Callers extract the two field
values however suits them — `surface_policy.py` uses MULTILINE regexes,
`skill_index.py` uses a small scalar parser that also needs `name`/`description` —
then hand the raw strings here. The POLICY is shared; the parsing is not, because
the two callers genuinely need different amounts of the frontmatter.

Callers may also render a different class VOCABULARY at their own output boundary
(`skill_index.py` collapses the two undeclared classes into one `public-undeclared`
column). That mapping stays local to the caller; the DETERMINATION is here.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Field names
# ---------------------------------------------------------------------------

USER_INVOCABLE_FIELD = "user-invocable"
JUSTIFICATION_FIELD = "public-justification"

#: The one literal that hides a skill. Compared after unquoting AND lowercasing,
#: so `false` / `False` / `FALSE` / `"false"` are the same answer — see the
#: case-insensitivity section above for the harness evidence behind that.
HIDDEN_VALUE = "false"
#: The one literal that counts as a deliberate opt-in to exposure. Same
#: normalization, so `true` / `True` / `TRUE` are one answer.
EXPOSED_VALUE = "true"

# ---------------------------------------------------------------------------
# The four classes
# ---------------------------------------------------------------------------

HIDDEN = "hidden"
PUBLIC_JUSTIFIED = "public_justified"
PUBLIC_UNJUSTIFIED = "public_unjustified"
DEFAULT_PUBLIC = "default_public"

#: Report order: loudest first. `surface_policy` renders in this order.
EXPOSURE_CLASSES: tuple[str, ...] = (
    DEFAULT_PUBLIC,
    PUBLIC_UNJUSTIFIED,
    PUBLIC_JUSTIFIED,
    HIDDEN,
)

#: Publicly reachable with no stated reason — what an enforcing gate rejects.
UNDECLARED_CLASSES: tuple[str, ...] = (DEFAULT_PUBLIC, PUBLIC_UNJUSTIFIED)

HARNESS_DEFAULT_NOTE = (
    "no `user-invocable` field; the harness reads `userInvocable ?? true`, "
    "so an ABSENT field means PUBLIC"
)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def unquote(value: str) -> str:
    """Strip surrounding whitespace and quote characters from a YAML scalar."""
    return value.strip().strip('"').strip("'").strip()


def normalize_flag(raw: str | None) -> str | None:
    """Normalize a raw `user-invocable` scalar for comparison.

    Returns the unquoted, LOWERCASED value, or None when the field is absent.
    Idempotent, so it is safe to call on an already-normalized value.

    The lowercasing is the case rule, and every consumer gets it by calling this
    instead of comparing a raw string. `stamp_skill_frontmatter.py` used to
    compare case-sensitively and reported `False` as a violation while its peers
    read the same file as hidden.
    """
    if raw is None:
        return None
    return unquote(raw).lower()


def has_justification(raw: str | None) -> bool:
    """True when `public-justification:` carries actual text."""
    return bool(raw) and bool(unquote(raw))


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def classify(user_invocable: str | None, public_justification: str | None = None) -> str:
    """Map one skill's two frontmatter fields onto exactly one exposure class.

    *user_invocable* is the raw scalar (or None when the field is ABSENT — which
    is NOT the same as an empty value, and is the fail-open case). Normalization
    happens here, so callers may pass raw or normalized text.
    """
    flag = normalize_flag(user_invocable)

    if flag is None:
        # No field. `userInvocable ?? true` — public, and nobody decided that.
        return DEFAULT_PUBLIC
    if flag == HIDDEN_VALUE:
        return HIDDEN
    if flag == EXPOSED_VALUE and has_justification(public_justification):
        return PUBLIC_JUSTIFIED
    # `true` with no reason, or a value this repo does not recognize (`yes`, `1`,
    # `maybe`, empty). The harness hides the unrecognized ones; this repo reports
    # them anyway so nobody has to replay its coercion table to read a file. The
    # over-report is one edit; the opposite error ships an undecided public skill.
    return PUBLIC_UNJUSTIFIED


def is_public(exposure_class: str) -> bool:
    """Is the user able to reach this skill directly?"""
    return exposure_class != HIDDEN


def is_undeclared(exposure_class: str) -> bool:
    """Is it public with no stated reason — i.e. does a gate reject it?"""
    return exposure_class in UNDECLARED_CLASSES


# ---------------------------------------------------------------------------
# Worktree exclusion — shared because every consumer walks the same tree
# ---------------------------------------------------------------------------

#: A worktree copy of the plugin carries a full duplicate `skills/` tree, so a
#: walk that crosses one double-counts every skill and reports another agent's
#: checkout as this plugin's surface. Matched on whole path SEGMENTS, never
#: substrings, so a legitimately named skill like `data-plane-worktrees` survives.
EXCLUDED_PATH_SEGMENTS: tuple[tuple[str, ...], ...] = (
    (".build-loop", "worktrees"),
    (".claude", "worktrees"),
    ("node_modules",),
    ("plugin-artifacts",),
)


def is_excluded_path(rel_parts: tuple[str, ...]) -> bool:
    """True when *rel_parts* (a path relative to the plugin root) crosses a
    worktree, vendored tree, or generated artifact copy."""
    for segments in EXCLUDED_PATH_SEGMENTS:
        window = len(segments)
        for start in range(len(rel_parts) - window + 1):
            if rel_parts[start : start + window] == segments:
                return True
    return False
