#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""validators.py — input validation and JSON-loader helpers for write_run_entry."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "run_id": str,
    "date": str,
    "goal": str,
    "outcome": str,
    "phases": dict,
    "filesTouched": list,
    "diagnosticCommands": list,
    "manualInterventions": list,
    "active_experimental_artifacts": list,
}
VALID_OUTCOMES = {"pass", "fail", "partial"}
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
# Promotion-reviewer-style verdicts plus the independent-auditor's own vocabulary
# (yay/nay/suggest/look-again — see scripts/audit_record_verdict.py). The two judge
# families write into the same judge_decisions[] list, so both must validate.
VALID_JUDGE_VERDICTS = {"approve", "rethink", "new_approach"}
VALID_AUDITOR_VERDICTS = {
    "yay", "nay", "suggest", "suggest_correction", "look-again", "look_again",
    # Recorded by hand on at least one real run (buildloop-model-roles-20260906).
    # A vocabulary that rejects verdicts the ledger actually contains re-arms a
    # debt that was genuinely discharged.
    "pass", "fail",
}
ALL_JUDGE_VERDICTS = VALID_JUDGE_VERDICTS | VALID_AUDITOR_VERDICTS
# judge_id substring that identifies the independent commit auditor (covers both the
# dispatched "independent-auditor" agent and the "independent-auditor-hook" record).
AUDITOR_JUDGE_MARKER = "independent-auditor"
# judge_id substring that identifies a SECOND-VENDOR (cross-tool) review round — the
# round `scripts/review_trigger.py` demands via `cross_vendor_required`. Canonical
# judge_id is "cross-vendor-audit"; any id containing "cross-vendor" matches, so a
# host-qualified id like "cross-vendor-audit:codex" still counts. Deliberately NOT
# AUDITOR_JUDGE_MARKER: a same-vendor auditor verdict must never discharge the
# cross-vendor debt, which is the whole point of running a second vendor (measured
# on bl-20260912T180923Z-claude_code-selfmodrevert: the skipped cross-vendor round
# later returned 11 findings, 6 Critical, DISJOINT from the same-vendor auditor's).
CROSS_VENDOR_JUDGE_MARKER = "cross-vendor"
VALID_JUDGE_SPEC_ALIGNMENT = {"aligned", "partial", "misaligned", "unverifiable"}
VALID_BUDGET_MODES = {"default", "long", "custom"}
# Advisory oracle-completeness note on a verify verdict: how much of the checked
# surface the oracle actually covered. A "green" gate with a thin oracle is a known
# false-confidence source (arXiv:2606.09863 false-success; the oracle's completeness
# is the reliability ceiling). Additive + optional — never required.
VALID_ORACLE_COVERAGE = {"full", "partial", "thin"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry validation
# ---------------------------------------------------------------------------

def validate_entry(entry: dict) -> None:
    for field, expected in REQUIRED_FIELDS.items():
        if field not in entry:
            raise ValueError(f"missing required field: {field}")
        if not isinstance(entry[field], expected):
            raise ValueError(
                f"field {field!r} must be "
                f"{expected.__name__ if isinstance(expected, type) else expected}, "
                f"got {type(entry[field]).__name__}"
            )
    if entry["outcome"] not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(VALID_OUTCOMES)}, got {entry['outcome']!r}")


# A record that names the auditor but carries no judgement. The deterministic
# hook writes exactly this when it emits an audit packet: the packet is a
# REQUEST for a verdict, and treating it as one is how six `verdict: pending`
# rows certified two runs as reviewed (RossLabs-AI-Assistant, 2026-07-21 and
# 2026-08-03). Membership here means "not yet judged", never "judged badly" --
# a `block` or `request_changes` IS a verdict and must count as present.
NON_VERDICT_VALUES = {"", "pending", "none", "n/a"}
# Statuses that say the round has NOT finished. A status is a claim about the
# round's lifecycle and a verdict is a claim about its result; when the two
# contradict each other -- {"verdict": "yay", "status": "pending"} -- the
# lifecycle wins, because a result reported by a round that says it is still
# running is not a result. Named NON_VERDICT_STATUSES because that is exactly
# what membership means: "no verdict has been rendered yet".
#
# `packet_emitted` is deliberately NOT a member. It predates the verdict
# allowlist and meant "packet emitted, not yet answered", but
# `audit_record_verdict.py` fills the verdict IN PLACE and leaves the status as
# `packet_emitted`, so treating it as non-terminal rejected 103 real rows in
# this repo's own ledger, including three passing code-touching runs whose
# auditor verdict would have silently re-armed. An unanswered packet already
# fails the verdict allowlist, which is the check that actually distinguishes
# answered from not.
NON_VERDICT_STATUSES = {
    "pending",
    "in_progress",
    "in-progress",
    "inprogress",
    "running",
    "queued",
    "dispatched",
    "awaiting_verdict",
    "awaiting-verdict",
    "awaiting_review",
    "awaiting-review",
    "requested",
}
# Statuses that say the round did not complete. An allowed `verdict` riding a
# failed `status` -- {"verdict": "yay", "status": "failed"} -- discharged the
# debt on a record of the review NOT happening.
FAILED_STATUSES = {"failed", "error", "not-run", "not_run", "aborted", "timeout", "cancelled"}


def verdict_rejection(item: object) -> str | None:
    """Why this entry is not a rendered verdict, in field terms, or None.

    Returns the REASON rather than a bare bool so a caller can tell an operator
    which field disqualified an entry they believe they recorded correctly. A
    gate that says only "still owed" about an entry sitting in the ledger sends
    the operator to the waiver, which is the path that reopens the escape.
    """
    if not isinstance(item, dict):
        return f"entry is {type(item).__name__}, not an object"
    verdict = str(item.get("verdict") or "").strip().lower()
    status = str(item.get("status") or "").strip().lower()
    if verdict in NON_VERDICT_VALUES:
        return f"verdict={item.get('verdict')!r} records no judgement"
    if verdict not in ALL_JUDGE_VERDICTS:
        return (
            f"verdict={item.get('verdict')!r} is not one of "
            f"{sorted(ALL_JUDGE_VERDICTS)}"
        )
    # Ordered before FAILED_STATUSES only for message quality; the two sets are
    # disjoint, so the order cannot change any accept/reject outcome.
    if status in NON_VERDICT_STATUSES:
        return (
            f"status={item.get('status')!r} says the round has not finished; a "
            "verdict on an unfinished round is not a rendered verdict"
        )
    if status in FAILED_STATUSES:
        return f"status={item.get('status')!r} says the round did not complete"
    return None


def rendered_verdict(item: object) -> bool:
    """True when a judge_decisions entry carries a real, completed verdict.

    Four conditions, each learned from a record that satisfied the gate without
    a review having happened: the entry must be an object, the verdict must be
    one this repo's judges actually emit (an allowlist -- `verdict: "not-run"`
    passed a blacklist), the status must not say the round is still UNFINISHED
    (`{"verdict": "yay", "status": "pending"}` claims a result from a round that
    says it is still running), and it must not say the round FAILED
    (`{"verdict": "yay", "status": "failed"}` is a record of the review not
    completing).

    The lifecycle checks are unconditional on the verdict: a non-terminal status
    disqualifies the row whatever the verdict says, because the contradiction is
    itself the evidence that the row is not a finished review.
    """
    return verdict_rejection(item) is None


def judge_verdict_present(judge_decisions: object, marker: str) -> bool:
    """True when judge_decisions[] carries a rendered VERDICT from `marker`'s judge.

    A matching judge_id is necessary and NOT sufficient. The verdict checks live
    HERE, in the base predicate, not only in the cross-vendor caller: this
    function is what guards the independent-auditor debt, and a failed-status
    auditor row discharged it while the identical cross-vendor row was rejected.
    """
    ok, _ = judge_verdict_rejections(judge_decisions, marker)
    return ok


def judge_verdict_rejections(
    judge_decisions: object,
    marker: str,
    diff_range: object = None,
    require_range: bool = False,
) -> tuple[bool, list[dict[str, object]]]:
    """(present, per-entry rejection reasons) for `marker`'s judge.

    Same shape as `cross_vendor_rejections` so a caller can report WHY an entry
    the operator believes they recorded was not accepted, rather than only that
    the debt remains. `diff_range` applies the obsolete-diff rule: a verdict
    rendered on a DIFFERENT range is a review of a different diff.

    `require_range` splits ARMING from DISCHARGE, which is what makes the
    historical-compatibility problem tractable.

    With `require_range=False` (arming) an entry carrying NO range is accepted.
    Measured over this repository's own ledger on 2026-09-13, 139 of 143
    rendered auditor verdicts carry no range at all; rejecting them would newly
    ARM the auditor debt on 24 of 64 runs. A debt armed on 38% of history is the
    failure this module's own docstring names -- a flag that is always on is a
    flag nobody reads -- and it would push operators toward the waiver, which is
    the escape the debt exists to close.

    With `require_range=True` (discharging an ALREADY-ARMED debt) an unstamped
    entry is refused, exactly as the cross-vendor half refuses one. Once a debt
    exists, its dispatch command spells `run_id` and `diff_range`, so a round
    run as instructed records both, and an unstamped entry is evidence the
    instruction was not followed. Without this split an older verdict for the
    same run discharged a debt armed over code written after it -- reported
    independently by both review rounds on 2026-09-13.
    """
    rejections: list[dict[str, object]] = []
    if not isinstance(judge_decisions, list):
        return False, rejections
    wanted_range = str(diff_range or "").strip()
    for item in judge_decisions:
        if not isinstance(item, dict):
            continue
        if marker not in str(item.get("judge_id", "")):
            continue
        why = verdict_rejection(item)
        entry_range = str(item.get("diff_range") or "").strip()
        if why is None and wanted_range and wanted_range != "unknown":
            if entry_range and entry_range != wanted_range:
                why = (
                    f"diff_range={entry_range!r} is not the armed range "
                    f"{wanted_range!r}; a review of a different diff is not a "
                    "review of this one"
                )
            elif not entry_range and require_range:
                why = (
                    f"the entry names no diff_range, so it is not evidence about "
                    f"{wanted_range!r}; an armed debt is discharged only by a "
                    "verdict stamped with its own range"
                )
        if why:
            rejections.append({
                "judge_id": item.get("judge_id"),
                "run_id": item.get("run_id"),
                "diff_range": item.get("diff_range"),
                "reason": why,
            })
            continue
        return True, rejections
    return False, rejections


def auditor_present(judge_decisions: object) -> bool:
    """True when judge_decisions[] carries a real independent-auditor VERDICT.

    Matches both the dispatched `independent-auditor` agent and the
    `independent-auditor-hook` record. An empty list, None, or a list of only
    other judges (an inline self-audit substituting for a real dispatch) is False.
    """
    return judge_verdict_present(judge_decisions, AUDITOR_JUDGE_MARKER)


# Which provider each run `host` belongs to. A cross-vendor verdict has to name
# a provider OUTSIDE this set for the run's own host, or the round did not happen.
HOST_PROVIDERS: dict[str, str] = {
    "claude_code": "anthropic",
    "codex": "openai",
    "gemini": "google",
}
# The providers a `vendor` field may name. An allowlist, not a blacklist: a free
# string like "unknown" is not evidence that a different vendor reviewed the
# diff, and treating any non-empty value as proof is the same substitution the
# `judge_id` label already made.
KNOWN_VENDOR_PROVIDERS: dict[str, tuple[str, ...]] = {
    "anthropic": ("anthropic", "claude"),
    "openai": ("openai", "codex", "gpt"),
    "google": ("google", "gemini"),
    "meta": ("meta", "llama"),
    "mistral": ("mistral",),
    "xai": ("xai", "grok"),
    "qwen": ("qwen",),
    "deepseek": ("deepseek",),
    # Local routes are real second vendors relative to a hosted model, and the
    # repo's own model index routes to them.
    "ollama": ("ollama",),
    # Written in POST-SPLIT token form: the splitter turns "llama.cpp" into
    # ['llama','cpp'] and "lm-studio" into ['lm','studio'], so aliases spelled
    # as the joined word matched nothing and a real round stayed owed.
    "local": ("local", "vllm", "lmstudio", "llamacpp", "mlx"),
}


def vendor_provider(vendor: object) -> str | None:
    """The provider a `vendor` string names, or None when it names none.

    Reads the FIRST recognised token rather than matching anywhere in the
    string, because a vendor field records the reviewing provider and may carry
    harness detail after it -- "openai via claude-code peer" is an OpenAI
    review, and substring matching over the whole value called it Anthropic.
    """
    text = str(vendor or "").strip().lower()
    if not text:
        return None
    # Split on ANY non-alphanumeric run. Two str.replace calls left ':', '_',
    # '.' and ',' joined to their neighbours, so "openai:gpt-5" and "codex_cli"
    # resolved to no provider and a legitimate round stayed owed with no reason
    # given.
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", text) if tok]
    # First recognised provider in POSITION order, trying the joined pair at
    # each position before the bare token there. Scanning every joined pair
    # ahead of every token let a later phrase win: "openai via llama.cpp
    # fallback" resolved to the local runtime, so on a codex host a same-vendor
    # OpenAI review counted as a different vendor. The pair-before-token rule
    # still keeps "llama.cpp" from resolving to meta via its bare "llama".
    for index, token in enumerate(tokens):
        candidates = []
        if index + 1 < len(tokens):
            candidates.append(token + tokens[index + 1])
        candidates.append(token)
        for candidate in candidates:
            for provider, aliases in KNOWN_VENDOR_PROVIDERS.items():
                if candidate in aliases:
                    return provider
    return None


# Fields a cross-vendor entry may use to POINT AT the round's own output. The
# `vendor` string is metadata the recorder asserts about itself; these name an
# artifact the round produced, which a recorder that never ran the round has to
# fabricate on disk rather than merely type.
# A session id this process cannot resolve is accepted only when it is long and
# opaque enough to BE an identifier. 16 chars of [A-Za-z0-9_-] covers a ULID
# (26), a UUID (36 with dashes), and a Codex thread id, and excludes the
# two-character placeholder that satisfied the first version of this check.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")
# Suffixes that make a value path-SHAPED when there is no root to resolve against.
_EVIDENCE_SUFFIXES = (".md", ".json", ".txt", ".log", ".jsonl", ".diff", ".patch")
CROSS_VENDOR_EVIDENCE_FIELDS = (
    "evidence",
    "evidence_path",
    "review_path",
    "transcript",
    "transcript_path",
    "dispatch_transcript",
    "session_id",
    "codex_session_id",
)


def cross_vendor_evidence(item: object) -> tuple[str | None, str | None]:
    """The (field, value) this entry uses to point at the round's output.

    Returns (None, None) when the entry names no artifact at all.
    """
    if not isinstance(item, dict):
        return None, None
    for field in CROSS_VENDOR_EVIDENCE_FIELDS:
        value = str(item.get(field) or "").strip()
        if value:
            return field, value
    return None, None


def _evidence_rejection(item: dict, evidence_root: object) -> str | None:
    """Why this entry's evidence reference fails, or None.

    Provider identity is UNAUTHENTICATED metadata: nothing stops an Anthropic-
    hosted process from typing `vendor: "openai/gpt-5-codex"`, so the vendor
    string alone cannot carry the discharge. Requiring a pointer to the round's
    own output does not authenticate the provider either -- that would need a
    signed transcript -- but it moves the claim from "a string anyone types" to
    "a string plus an artifact that must exist", and it makes the discharge
    AUDITABLE: whoever reviews the ledger can open the file and read the round.

    TWO shapes are accepted and every other value is rejected BY NAME:

    1. A path that resolves INSIDE ``evidence_root`` to a non-empty regular
       file. Containment matters: ``/etc/hostname`` exists on every machine, so
       an existence check alone was satisfied by a path the recorder did not
       produce. Emptiness matters: a zero-byte file is a `touch`, not a review.
    2. A ``*session_id`` value matching ``SESSION_ID_RE``. This process cannot
       resolve another host's session store, so the id is not verified -- but a
       16-character opaque token is a specific claim a later audit can chase,
       which ``"zz"`` is not.

    The first version of this check accepted ANY non-empty string whose value
    was not path-shaped, which meant ``evidence: "zz"`` discharged the debt.
    Both review rounds on 2026-09-13 reported that independently: the docstring
    claimed the requirement moved the discharge from "a string anyone types" to
    "a string plus an artifact that must exist", and it had not.
    """
    field, value = cross_vendor_evidence(item)
    if field is None:
        return (
            "no evidence reference: a cross-vendor discharge must name the "
            "round's own output in one of "
            f"{list(CROSS_VENDOR_EVIDENCE_FIELDS)} (a vendor string is "
            "unauthenticated metadata the recorder asserts about itself)"
        )
    if field.endswith("session_id"):
        if not SESSION_ID_RE.match(value):
            return (
                f"{field}={value!r} is not a resolvable session id "
                f"(expected {SESSION_ID_RE.pattern}); an arbitrary short string "
                "is not evidence a round ran"
            )
        return None
    from pathlib import Path as _Path  # local: keeps the module import list flat

    if evidence_root is None:
        # Nothing to resolve against. The value must still LOOK like a path, so
        # a junk token cannot ride in through the unresolvable branch.
        if "/" not in value and not value.endswith(_EVIDENCE_SUFFIXES):
            return (
                f"{field}={value!r} is neither a path nor a session id; a "
                "cross-vendor discharge must name a file the round produced"
            )
        return None
    candidate = _Path(value)
    if not candidate.is_absolute():
        candidate = _Path(str(evidence_root)) / value
    root = _Path(str(evidence_root))
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return (
            f"{field}={value!r} resolves outside the repository ({candidate}); "
            "evidence must be an artifact this run produced, not any file that "
            "happens to exist on the machine"
        )
    if not resolved.is_file():
        return (
            f"{field}={value!r} is not a regular file at {resolved}; the entry "
            "points at a round output that is not on disk"
        )
    try:
        if resolved.stat().st_size == 0:
            return (
                f"{field}={value!r} is an EMPTY file; a zero-byte artifact "
                "records that a path was created, not that a review happened"
            )
    except OSError:
        return f"{field}={value!r} could not be read at {resolved}"
    return None


def cross_vendor_rejections(
    judge_decisions: object,
    host: object = None,
    diff_range: object = None,
    evidence_root: object = None,
) -> tuple[bool, list[dict[str, object]]]:
    """(discharged, per-entry rejection reasons) for the cross-vendor debt.

    The reasons are the point. An entry that LOOKS right to the operator who
    wrote it but fails one field was previously indistinguishable from no entry
    at all, so the only visible exit was the waiver -- the path that reopens the
    escape this debt exists to close. Only entries carrying the cross-vendor
    judge_id are reported; everything else is not an attempt at this discharge.
    """
    rejections: list[dict[str, object]] = []
    if not isinstance(judge_decisions, list):
        return False, rejections
    own_provider = HOST_PROVIDERS.get(str(host or "").strip().lower())
    wanted_range = str(diff_range or "").strip()

    def _reject(item: dict, reason: str) -> None:
        rejections.append({
            "judge_id": item.get("judge_id"),
            "run_id": item.get("run_id"),
            "vendor": item.get("vendor"),
            "diff_range": item.get("diff_range"),
            "reason": reason,
        })

    for item in judge_decisions:
        if not isinstance(item, dict):
            continue
        if CROSS_VENDOR_JUDGE_MARKER not in str(item.get("judge_id", "")):
            continue
        why = verdict_rejection(item)
        if why:
            _reject(item, why)
            continue
        provider = vendor_provider(item.get("vendor"))
        if provider is None:
            # No recognised provider named: the entry asserts a round happened
            # without saying who ran it.
            _reject(item, (
                f"vendor={item.get('vendor')!r} names no recognised provider; "
                f"expected one of {sorted(KNOWN_VENDOR_PROVIDERS)}"
            ))
            continue
        if own_provider is None:
            # The run's host is not one we can map to a provider, so "different
            # vendor" is unanswerable. Fail safe: the debt stays armed.
            _reject(item, (
                f"the run's host={host!r} maps to no provider, so 'a different "
                "vendor' is unanswerable; record host as one of "
                f"{sorted(HOST_PROVIDERS)}"
            ))
            continue
        if provider == own_provider:
            _reject(item, (
                f"vendor={item.get('vendor')!r} resolves to {provider!r}, the "
                "run's OWN host family; a same-vendor review does not discharge "
                "a cross-vendor debt"
            ))
            continue
        if wanted_range and wanted_range != "unknown":
            # A review of an OBSOLETE diff is not a review of this one, and an
            # entry that names NO range is not evidence about this one either:
            # the file is append-only, so accepting an unstamped row let a
            # verdict on the same run's earlier commits discharge a debt armed
            # by later ones. The emitted dispatch command spells `diff_range`,
            # so a round run as instructed records it.
            entry_range = str(item.get("diff_range") or "").strip()
            if entry_range != wanted_range:
                _reject(item, (
                    f"diff_range={entry_range or None!r} is not the armed range "
                    f"{wanted_range!r}; a review of a different diff is not a "
                    "review of this one"
                ))
                continue
        evidence_why = _evidence_rejection(item, evidence_root)
        if evidence_why:
            _reject(item, evidence_why)
            continue
        return True, rejections
    return False, rejections


def cross_vendor_present(
    judge_decisions: object,
    host: object = None,
    diff_range: object = None,
    evidence_root: object = None,
) -> bool:
    """True when judge_decisions[] carries a rendered SECOND-VENDOR verdict.

    Four conditions. The id must contain "cross-vendor" (the
    `independent-auditor` marker does not, so a run cannot satisfy a
    cross-vendor requirement with the auditor it already ran). The entry must
    NAME a vendor that is not the run's own host family. It must be stamped with
    the armed diff range. And it must POINT AT the round's own output.

    The id alone is a label an agent types. The debt exists to force a
    DIFFERENT vendor, so a same-vendor subagent writing
    `judge_id: "cross-vendor-audit"` must not discharge it -- and the emitted
    dispatch text tells an agent to use exactly that id. A missing `vendor`
    field is therefore not a discharge: the convention already exists in
    practice (real entries carry `vendor: "openai/codex-cli 0.154.0"`), and
    treating its absence as a pass is what let the label stand in for the round.

    The vendor field is still only an assertion -- see `_evidence_rejection` for
    why the evidence pointer is required alongside it, and for what that does
    and does not prove.
    """
    ok, _ = cross_vendor_rejections(judge_decisions, host, diff_range, evidence_root)
    return ok


def review_completeness_error(entry: dict, scope: str) -> str | None:
    """Return an error message when a build-scope code-touching pass lacks the auditor.

    The gap this closes (bl-enforce-independent-auditor-dispatch): an orchestrator
    can substitute inline self-reasoning for a real `independent-auditor` dispatch,
    and nothing fails — an empty/inline-only auditor record reaches Report-G on
    shipped code. Rule: a `pass` outcome on a `scope=build` run that touched files
    MUST carry a real independent-auditor verdict. Returns None when satisfied or
    when the rule does not apply (non-build scope, no files, non-pass outcome).
    """
    if scope != "build":
        return None
    if entry.get("outcome") != "pass":
        return None
    if not entry.get("filesTouched"):
        return None
    if not auditor_present(entry.get("judge_decisions")):
        return (
            "review incomplete: a build-scope run that touched code cannot record "
            "outcome=pass without a real independent-auditor verdict in "
            "judge_decisions[] (inline self-audit is not a substitute). Re-dispatch "
            "the independent-auditor at build scope before Report. "
            "See bl-enforce-independent-auditor-dispatch."
        )
    return None


# ---------------------------------------------------------------------------
# JSON source reader (shared by all loaders)
# ---------------------------------------------------------------------------

def _read_source(source: str) -> str | None:
    """Return raw text from path or stdin '-'. Returns None when path missing/empty."""
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _parse_json(raw: str, flag: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{flag} is not valid JSON: {e}") from e


# ---------------------------------------------------------------------------
# Security findings
# ---------------------------------------------------------------------------

def _validate_finding(i: int, item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"security_findings[{i}] must be an object, got {type(item).__name__}")
    if "mapped_risks" not in item or not isinstance(item["mapped_risks"], list):
        raise ValueError(f"security_findings[{i}].mapped_risks must be a list of strings")
    if not all(isinstance(r, str) for r in item["mapped_risks"]):
        raise ValueError(f"security_findings[{i}].mapped_risks must contain only strings")
    sev = item.get("severity")
    if not isinstance(sev, str) or sev not in VALID_SEVERITIES:
        raise ValueError(
            f"security_findings[{i}].severity must be one of {sorted(VALID_SEVERITIES)}, got {sev!r}"
        )


def load_security_findings(source: str) -> list[dict] | None:
    """Read findings JSON from a path or stdin ('-').

    Returns None when the file is missing or empty (caller should not write a 'security_findings'
    key — semantically equivalent to omitting the flag). Returns a list (possibly empty if the
    user explicitly passed `[]`) when the file decoded to a list shape.

    Validates that the decoded value is a list of objects, each with a 'mapped_risks' list of
    strings and a 'severity' string in VALID_SEVERITIES. Other fields pass through unchanged.
    Raises ValueError on malformed input.
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: --security-findings-json path {source} does not exist; treating as no findings (no security_findings key will be written)")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, "--security-findings-json")
    # Accept either a bare list or the reviewer's full envelope ({"findings": [...], ...}).
    if isinstance(data, dict) and "findings" in data:
        data = data["findings"]
    if not isinstance(data, list):
        raise ValueError("--security-findings-json must decode to a list (or an object with a 'findings' list)")
    for i, item in enumerate(data):
        _validate_finding(i, item)
    return data


# ---------------------------------------------------------------------------
# Budget summary
# ---------------------------------------------------------------------------

def _validate_budget_int_fields(data: dict) -> None:
    for field in ("budget_seconds", "used_seconds", "items_closed", "items_deferred", "commits", "pushes"):
        if field not in data:
            raise ValueError(f"budget_summary missing required field: {field}")
        if not isinstance(data[field], int) or isinstance(data[field], bool):
            raise ValueError(f"budget_summary.{field} must be int, got {type(data[field]).__name__}")
        if data[field] < 0:
            raise ValueError(f"budget_summary.{field} must be >= 0, got {data[field]}")


def load_budget_summary(source: str) -> dict | None:
    """Read autonomous-mode budget_summary JSON from a path or stdin ('-').

    Shape per plan §14.4 + §14.5: a single object capturing the run's wall-clock
    + queue-drain summary. All fields required (validate hard) so downstream
    pattern mining doesn't have to defend against partial shapes.

    Required fields:
      mode             one of VALID_BUDGET_MODES (default | long | custom)
      budget_seconds   int >= 0
      used_seconds     int >= 0
      items_closed     int >= 0    # queue items routed through Phase 2→3→4 to completion
      items_deferred   int >= 0    # queue items moved to .build-loop/followup/
      commits          int >= 0
      pushes           int >= 0

    Returns None when source path missing/empty (caller skips the key).
    Raises ValueError on type/shape errors.
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: --budget-summary-json path {source} does not exist; skipping")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, "--budget-summary-json")
    if not isinstance(data, dict):
        raise ValueError("--budget-summary-json must decode to an object")
    mode = data.get("mode")
    if not isinstance(mode, str) or mode not in VALID_BUDGET_MODES:
        raise ValueError(f"budget_summary.mode must be one of {sorted(VALID_BUDGET_MODES)}, got {mode!r}")
    _validate_budget_int_fields(data)
    return data


# ---------------------------------------------------------------------------
# Judge decisions
# ---------------------------------------------------------------------------

def _validate_judge_decision(i: int, item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"judge_decisions[{i}] must be an object, got {type(item).__name__}")
    if not isinstance(item.get("judge_id"), str):
        raise ValueError(f"judge_decisions[{i}].judge_id must be a string")
    verdict = item.get("verdict")
    if not isinstance(verdict, str) or verdict not in ALL_JUDGE_VERDICTS:
        raise ValueError(
            f"judge_decisions[{i}].verdict must be one of {sorted(ALL_JUDGE_VERDICTS)}, got {verdict!r}"
        )
    if "spec_alignment" in item:
        sa = item["spec_alignment"]
        if not isinstance(sa, str) or sa not in VALID_JUDGE_SPEC_ALIGNMENT:
            raise ValueError(
                f"judge_decisions[{i}].spec_alignment must be one of "
                f"{sorted(VALID_JUDGE_SPEC_ALIGNMENT)}, got {sa!r}"
            )
    if "oracle_completeness" in item:
        _validate_oracle_completeness(f"judge_decisions[{i}]", item["oracle_completeness"])


def _validate_oracle_completeness(where: str, oc: Any) -> None:
    """Validate the optional advisory oracle_completeness note on a verify verdict.

    Shape: {"covered": str?, "uncovered": str?, "coverage": "full|partial|thin"?}.
    Every field is optional (a partially-filled note is still useful); only present
    fields are type-checked. Advisory — its purpose is to record WHAT the check
    actually covered so a thin oracle behind a green gate is visible, not to gate.
    """
    if not isinstance(oc, dict):
        raise ValueError(f"{where}.oracle_completeness must be an object, got {type(oc).__name__}")
    for key in ("covered", "uncovered"):
        if key in oc and not isinstance(oc[key], str):
            raise ValueError(f"{where}.oracle_completeness.{key} must be a string")
    if "coverage" in oc:
        cov = oc["coverage"]
        if not isinstance(cov, str) or cov not in VALID_ORACLE_COVERAGE:
            raise ValueError(
                f"{where}.oracle_completeness.coverage must be one of "
                f"{sorted(VALID_ORACLE_COVERAGE)}, got {cov!r}"
            )


def load_judge_decisions(source: str) -> list[dict] | None:
    """Read advisory judge_decisions JSON from a path or stdin ('-').

    Shape per plan §12.5: advisory verdicts that never block execution. Each entry must have
    `judge_id` (str) and `verdict` (one of VALID_JUDGE_VERDICTS). Optional pass-through fields:
    checkpoint_id, confidence, spec_alignment, variances, meta_guidance, policy_refs,
    implementer_response, outcome.

    Returns None when missing/empty (caller skips the key). Returns a list otherwise.
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: --judge-decisions-json path {source} does not exist; skipping")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, "--judge-decisions-json")
    if isinstance(data, dict) and "decisions" in data:
        data = data["decisions"]
    if not isinstance(data, list):
        raise ValueError("--judge-decisions-json must decode to a list (or an object with a 'decisions' list)")
    for i, item in enumerate(data):
        _validate_judge_decision(i, item)
    return data


# ---------------------------------------------------------------------------
# Model + harness config (report at the model+harness level, not model alone)
# ---------------------------------------------------------------------------

def load_config_object(source: str, flag: str) -> dict | None:
    """Read an optional free-form config object (models / harness) from a path or stdin ('-').

    Additive + optional. Reporting a run at the model+harness level — the scaffold,
    tool-set, and context-budget config, not the model id alone — is needed because
    undisclosed harness config confounds model-vs-model comparisons (arXiv:2605.23950)
    and the harness is a first-class reliability lever. The `models` and `harness`
    blocks are pass-through objects (shape owned by the caller / Phase-4 report writer);
    validated only as "must decode to an object" so downstream readers see a dict, never
    a scalar. Returns None when the source is missing/empty (caller skips the key).
    """
    raw = _read_source(source)
    if raw is None:
        log(f"note: {flag} path {source} does not exist; skipping")
        return None
    if not raw.strip():
        return None
    data = _parse_json(raw, flag)
    if not isinstance(data, dict):
        raise ValueError(f"{flag} must decode to an object")
    return data
