#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Compare model + effort treatments from local Codex session history.

This is a preselection tool, not a benchmark scorer. It reports observational
runtime proxies and privacy-safe repeat-task candidates for the controlled
``model-bakeoff`` harness. Prompt text is hashed in memory and never emitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "build-loop/model-run-history/v1"
VERIFY_RE = re.compile(
    r"\b("
    r"pytest|unittest|npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|"
    r"yarn\s+(?:run\s+)?test|cargo\s+test|go\s+test|swift\s+test|"
    r"xcodebuild|self_mod_verify|test_[A-Za-z0-9_]+\.py"
    r")\b",
    re.IGNORECASE,
)
TASK_RULES = (
    ("code_review", re.compile(r"\b(review|audit|inspect|assess)\b", re.IGNORECASE)),
    ("diagnosis", re.compile(r"\b(debug|diagnos|root[- ]cause|failure|broken)\b", re.IGNORECASE)),
    ("code_change", re.compile(r"\b(build|implement|fix|refactor|migrat|change|update code)\b", re.IGNORECASE)),
    ("research", re.compile(r"\b(research|look up|search|compare|benchmark)\b", re.IGNORECASE)),
    ("planning", re.compile(r"\b(plan|design|architect|proposal|prd)\b", re.IGNORECASE)),
    ("communication", re.compile(r"\b(email|message|reply|draft|slack)\b", re.IGNORECASE)),
    ("scheduling", re.compile(r"\b(calendar|schedule|meeting|availability)\b", re.IGNORECASE)),
    ("document", re.compile(r"\b(document|report|spreadsheet|slides|pdf)\b", re.IGNORECASE)),
)


@dataclass
class Turn:
    turn_id: str
    session: str
    model: str | None = None
    effort: str | None = None
    workspace_hash: str | None = None
    prompt_hash: str | None = None
    task_class: str = "unknown"
    started_at: int | None = None
    completed_at: int | None = None
    duration_ms: int | None = None
    time_to_first_token_ms: int | None = None
    completed: bool = False
    tool_calls: int = 0
    verification_signals: int = 0
    tokens: dict[str, int] = field(default_factory=dict)

    @property
    def treatment(self) -> tuple[str | None, str | None]:
        return self.model, self.effort


def normalize_prompt(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split())


def prompt_hash(text: str) -> str | None:
    normalized = normalize_prompt(text)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def opaque_hash(prefix: str, value: str, length: int = 16) -> str:
    return hashlib.sha256(f"{prefix}:{value}".encode("utf-8")).hexdigest()[:length]


def classify_task(text: str) -> str:
    normalized = normalize_prompt(text)
    for name, pattern in TASK_RULES:
        if pattern.search(normalized):
            return name
    return "other" if normalized else "unknown"


def message_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("content", []):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            chunks.append(item["text"])
    return "\n".join(chunks)


def payload_turn_id(payload: dict[str, Any], active_turn_id: str | None) -> str | None:
    direct = payload.get("turn_id")
    if isinstance(direct, str):
        return direct
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, dict) and isinstance(metadata.get("turn_id"), str):
        return metadata["turn_id"]
    return active_turn_id


def parse_jsonl(path: Path, sessions_root: Path) -> list[Turn]:
    session = str(path.relative_to(sessions_root))
    turns: dict[str, Turn] = {}
    active_turn_id: str | None = None

    def get_turn(turn_id: str) -> Turn:
        return turns.setdefault(turn_id, Turn(turn_id=turn_id, session=session))

    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_type = record.get("type")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue

            if record_type == "event_msg":
                event_type = payload.get("type")
                if event_type == "task_started" and isinstance(payload.get("turn_id"), str):
                    active_turn_id = payload["turn_id"]
                    turn = get_turn(active_turn_id)
                    turn.started_at = _as_int(payload.get("started_at"))
                elif event_type == "token_count" and active_turn_id:
                    info = payload.get("info")
                    usage = info.get("last_token_usage") if isinstance(info, dict) else None
                    if not isinstance(usage, dict) and isinstance(info, dict):
                        # Legacy records may lack the per-call delta. Use the
                        # cumulative value once rather than dropping the turn.
                        usage = info.get("total_token_usage")
                    if isinstance(usage, dict):
                        turn = get_turn(active_turn_id)
                        for key, raw in usage.items():
                            value = _as_int(raw)
                            if value is not None:
                                turn.tokens[key] = turn.tokens.get(key, 0) + value
                elif event_type == "task_complete" and isinstance(payload.get("turn_id"), str):
                    turn_id = payload["turn_id"]
                    turn = get_turn(turn_id)
                    turn.completed = True
                    turn.started_at = _as_int(payload.get("started_at")) or turn.started_at
                    turn.completed_at = _as_int(payload.get("completed_at"))
                    turn.duration_ms = _as_int(payload.get("duration_ms"))
                    turn.time_to_first_token_ms = _as_int(payload.get("time_to_first_token_ms"))
                    if active_turn_id == turn_id:
                        active_turn_id = None
                continue

            if record_type == "turn_context":
                turn_id = payload_turn_id(payload, active_turn_id)
                if not turn_id:
                    continue
                turn = get_turn(turn_id)
                model = payload.get("model")
                if isinstance(model, str):
                    turn.model = model
                effort = payload.get("effort")
                if not isinstance(effort, str):
                    collaboration = payload.get("collaboration_mode")
                    settings = collaboration.get("settings") if isinstance(collaboration, dict) else None
                    effort = settings.get("reasoning_effort") if isinstance(settings, dict) else None
                if isinstance(effort, str):
                    turn.effort = effort
                cwd = payload.get("cwd")
                if isinstance(cwd, str):
                    turn.workspace_hash = opaque_hash("workspace", cwd, length=12)
                continue

            if record_type != "response_item":
                continue
            turn_id = payload_turn_id(payload, active_turn_id)
            if not turn_id:
                continue
            turn = get_turn(turn_id)
            item_type = payload.get("type")
            if item_type == "message" and payload.get("role") == "user":
                text = message_text(payload)
                turn.prompt_hash = prompt_hash(text)
                turn.task_class = classify_task(text)
            elif item_type in {"custom_tool_call", "function_call"}:
                turn.tool_calls += 1
                tool_input = payload.get("input", "")
                if not isinstance(tool_input, str):
                    tool_input = json.dumps(tool_input, sort_keys=True)
                if VERIFY_RE.search(tool_input):
                    turn.verification_signals += 1

    return list(turns.values())


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def parse_arm(value: str) -> tuple[str, tuple[str, str]]:
    if "=" not in value or ":" not in value:
        raise argparse.ArgumentTypeError("arm must use LABEL=MODEL:EFFORT")
    label, treatment = value.split("=", 1)
    model, effort = treatment.rsplit(":", 1)
    if not label or not model or not effort:
        raise argparse.ArgumentTypeError("arm must use non-empty LABEL=MODEL:EFFORT")
    return label, (model, effort)


def median(values: Iterable[int | None]) -> int | float | None:
    present = [value for value in values if value is not None]
    return statistics.median(present) if present else None


def summarize_turns(turns: list[Turn]) -> dict[str, Any]:
    completed = sum(turn.completed for turn in turns)
    token_keys = (
        "total_tokens",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    return {
        "turns": len(turns),
        "completed_turns": completed,
        "completion_rate": round(completed / len(turns), 4) if turns else None,
        "turns_with_verification_signal": sum(turn.verification_signals > 0 for turn in turns),
        "median_duration_ms": median(turn.duration_ms for turn in turns),
        "median_time_to_first_token_ms": median(turn.time_to_first_token_ms for turn in turns),
        "median_tool_calls": median(turn.tool_calls for turn in turns),
        **{
            f"median_{key}": median(turn.tokens.get(key) for turn in turns)
            for key in token_keys
        },
    }


def deduplicate_turns(turns: list[Turn]) -> list[Turn]:
    """Collapse copied rollout lineage while preserving treatment identity."""
    unique: dict[tuple[str, str | None, str | None], Turn] = {}

    def information_score(turn: Turn) -> tuple[int, int, int, int, int, int]:
        return (
            int(turn.completed),
            int(turn.duration_ms is not None),
            turn.tokens.get("total_tokens", 0),
            turn.tool_calls,
            turn.verification_signals,
            int(turn.prompt_hash is not None),
        )

    for turn in turns:
        key = (turn.turn_id, turn.model, turn.effort)
        current = unique.get(key)
        if current is None or information_score(turn) > information_score(current):
            unique[key] = turn
    return list(unique.values())


def exact_repeat_candidates(
    arm_turns: dict[str, list[Turn]],
    max_candidates: int,
) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[str, Turn]]] = {}
    for label, turns in arm_turns.items():
        for turn in turns:
            if turn.prompt_hash:
                groups.setdefault(turn.prompt_hash, []).append((label, turn))

    candidates: list[dict[str, Any]] = []
    for fingerprint, observations in groups.items():
        labels = {label for label, _turn in observations}
        if len(labels) < 2:
            continue
        candidates.append(
            {
                "candidate_id": opaque_hash("candidate", fingerprint),
                "task_class": observations[0][1].task_class,
                "arms": {
                    label: sum(observed_label == label for observed_label, _turn in observations)
                    for label in sorted(labels)
                },
                "source_refs": [
                    {
                        "arm": label,
                        "session": turn.session,
                        "turn_id": turn.turn_id,
                    }
                    for label, turn in sorted(observations, key=lambda item: (item[0], item[1].session))
                ],
            }
        )
    candidates.sort(key=lambda item: (-sum(item["arms"].values()), item["candidate_id"]))
    return candidates[:max_candidates]


def directional_cohorts(arm_turns: dict[str, list[Turn]]) -> list[dict[str, Any]]:
    cohorts: dict[tuple[str, str], dict[str, list[Turn]]] = {}
    for label, turns in arm_turns.items():
        for turn in turns:
            if turn.workspace_hash:
                cohorts.setdefault((turn.workspace_hash, turn.task_class), {}).setdefault(label, []).append(turn)

    output: list[dict[str, Any]] = []
    for (workspace_hash, task_class), by_arm in cohorts.items():
        if len(by_arm) < 2:
            continue
        output.append(
            {
                "workspace_id": workspace_hash,
                "task_class": task_class,
                "evidence_level": "directional_observational",
                "arms": {
                    label: summarize_turns(turns)
                    for label, turns in sorted(by_arm.items())
                },
            }
        )
    output.sort(
        key=lambda item: (
            -sum(arm["turns"] for arm in item["arms"].values()),
            item["workspace_id"],
            item["task_class"],
        )
    )
    return output


def build_report(
    turns: list[Turn],
    arms: list[tuple[str, tuple[str, str]]],
    max_candidates: int,
) -> dict[str, Any]:
    input_record_count = len(turns)
    turns = deduplicate_turns(turns)
    arm_turns = {
        label: [turn for turn in turns if turn.treatment == treatment]
        for label, treatment in arms
    }
    candidates = exact_repeat_candidates(arm_turns, max_candidates=max_candidates)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            "level": "observational",
            "quality_verdict": "unsupported",
            "input_turn_records": input_record_count,
            "unique_treatment_turns": len(turns),
            "warning": (
                "History is not a controlled benchmark. Task mix, workspace, prompt, "
                "tooling, and run conditions may differ."
            ),
        },
        "privacy": {
            "prompt_text_emitted": False,
            "workspace_names_emitted": False,
            "identifiers": "opaque hashes plus local session references",
        },
        "arms": {
            label: {
                "model": treatment[0],
                "effort": treatment[1],
                "metrics": summarize_turns(arm_turns[label]),
            }
            for label, treatment in arms
        },
        "exact_repeat_candidates": candidates,
        "exact_repeat_candidate_count": len(candidates),
        "directional_cohorts": directional_cohorts(arm_turns),
        "next_step": {
            "harness": "model-bakeoff",
            "artifact_schema": "abc-comparison/v2",
            "instruction": (
                "Rerun selected exact-repeat candidates on the same base SHA, in "
                "isolated worktrees, with identical prompts, fixed scoring, and at "
                "least three samples per arm."
            ),
        },
    }


def parse_date(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD or ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def iter_session_files(root: Path) -> Iterable[Path]:
    return sorted(root.rglob("*.jsonl"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions-root",
        default=str(Path.home() / ".codex" / "sessions"),
        help="Root containing Codex rollout JSONL files.",
    )
    parser.add_argument(
        "--arm",
        action="append",
        type=parse_arm,
        required=True,
        help="Comparison arm as LABEL=MODEL:EFFORT; repeat for each arm.",
    )
    parser.add_argument("--since", help="Include turns starting on/after this ISO date.")
    parser.add_argument("--until", help="Include turns starting before this ISO date.")
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--output", help="Write JSON to this file instead of stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.sessions_root).expanduser().resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"sessions root does not exist: {root}"}))
        return 2
    if len(args.arm) < 2:
        print(json.dumps({"error": "at least two --arm values are required"}))
        return 2
    labels = [label for label, _treatment in args.arm]
    if len(labels) != len(set(labels)):
        print(json.dumps({"error": "arm labels must be unique"}))
        return 2

    since = parse_date(args.since)
    until = parse_date(args.until)
    turns: list[Turn] = []
    for path in iter_session_files(root):
        turns.extend(parse_jsonl(path, root))
    turns = [
        turn
        for turn in turns
        if (since is None or (turn.started_at is not None and turn.started_at >= since))
        and (until is None or (turn.started_at is not None and turn.started_at < until))
    ]
    report = build_report(turns, args.arm, max_candidates=max(0, args.max_candidates))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
