# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/classify_action.py — MECE classification.

Coverage matrix:
  - read-only short-circuit (highest user-pain reduction)
  - irreversible × {production, non-production}
  - production deploy command, non-irreversible
  - DECISION states: pickable, low_confidence, malformed
  - RISKY file globs (defaults + custom)
  - SAFE default
  - precedence: ensure the priority order in classify() is respected
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import classify_action


@pytest.fixture()
def empty_workdir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def configured_workdir(tmp_path: Path) -> Path:
    (tmp_path / ".build-loop").mkdir()
    (tmp_path / ".build-loop" / "config.json").write_text(
        json.dumps({"classifyAction": {"riskyGlobs": ["custom-risky/**"]}})
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Read-only short-circuit (priority 4) — the user-pain fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat README.md",
        "grep -r foo src/",
        "git status",
        "git log --oneline",
        "git diff HEAD",
        "vercel curl --deployment preview-abc123.vercel.app /",
        "vercel logs deployment-id",
        "vercel inspect deployment-id",
        "gh pr view 123",
        "gh issue list",
        "kubectl get pods",
        "docker ps -a",
        "npm list --depth=0",
        "uv pip list",
        "curl https://api.example.com/health",
        "ps aux",
    ],
)
def test_read_only_commands_are_safe(empty_workdir: Path, command: str) -> None:
    result = classify_action.classify(empty_workdir, command=command)
    assert result["classification"] == "SAFE", (
        f"expected SAFE for read-only {command!r}, got {result}"
    )
    assert result["matched_rule"] == "read_only"


def test_vercel_logs_prod_is_safe_not_production(empty_workdir: Path) -> None:
    """vercel logs --prod inspects prod but doesn't deploy → SAFE, not PRODUCTION."""
    result = classify_action.classify(empty_workdir, command="vercel logs --prod")
    assert result["classification"] == "SAFE"


# ---------------------------------------------------------------------------
# PRODUCTION (priority 1, 3) — irreversible+prod OR production deploy
# ---------------------------------------------------------------------------


PRODUCTION_DEPLOYS = [
    "git push origin main",
    "git push --force origin main",
    "vercel deploy --prod",
    "netlify deploy --prod",
    "firebase deploy",
]


@pytest.mark.parametrize("command", PRODUCTION_DEPLOYS)
def test_production_deploys(empty_workdir: Path, command: str) -> None:
    result = classify_action.classify(empty_workdir, command=command)
    assert result["classification"] == "PRODUCTION", (
        f"{command!r} → {result}"
    )


def test_drop_database_is_production_irreversible(empty_workdir: Path) -> None:
    # Not a deploy command, but irreversible. Without an environment marker,
    # deployment_policy returns "n/a"/"unknown", so this falls through to
    # priority 2 (irreversible+non-production) → RISKY, isolating to a branch.
    # That's intentional: a DROP TABLE in an unmerged branch is recoverable
    # by deleting the branch.
    result = classify_action.classify(empty_workdir, command="DROP TABLE users;")
    assert result["classification"] == "RISKY"
    assert "irreversible" in result["reason"]


# ---------------------------------------------------------------------------
# RISKY (priority 2, 6) — irreversible non-prod, or broad-blast files
# ---------------------------------------------------------------------------


def test_force_push_to_feature_branch_is_risky(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir, command="git push --force origin feature/abc"
    )
    assert result["classification"] == "RISKY"


def test_npm_publish_is_production(empty_workdir: Path) -> None:
    # `npm publish` is both irreversible (IRREVERSIBLE_PATTERNS) and recognized
    # as production by deployment_policy. Priority 1 wins → PRODUCTION.
    result = classify_action.classify(empty_workdir, command="npm publish")
    assert result["classification"] == "PRODUCTION"


@pytest.mark.parametrize(
    "files",
    [
        ["migrations/0042_add_user.sql"],
        [".github/workflows/deploy.yml"],
        ["Dockerfile"],
        ["package.json"],
        ["prisma/schema.prisma"],
        ["uv.lock"],
    ],
)
def test_risky_files(empty_workdir: Path, files: list[str]) -> None:
    result = classify_action.classify(empty_workdir, files_touched=files)
    assert result["classification"] == "RISKY"
    assert result["delegated_to"] == "risky_globs"


def test_custom_risky_globs(configured_workdir: Path) -> None:
    result = classify_action.classify(
        configured_workdir, files_touched=["custom-risky/foo.py"]
    )
    assert result["classification"] == "RISKY"


def test_override_replaces_defaults(configured_workdir: Path) -> None:
    """Override list replaces defaults — Dockerfile no longer triggers."""
    result = classify_action.classify(
        configured_workdir, files_touched=["Dockerfile"]
    )
    assert result["classification"] == "SAFE"


# ---------------------------------------------------------------------------
# DECISION (priority 5) — implementer envelope
# ---------------------------------------------------------------------------


def test_decision_pickable(empty_workdir: Path) -> None:
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}, {"id": "B"}],
            }
        ]
    }
    result = classify_action.classify(empty_workdir, envelope=envelope)
    assert result["classification"] == "DECISION"
    assert result["decision_state"] == "pickable"


def test_decision_low_confidence(empty_workdir: Path) -> None:
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "low",
                "options": [{"id": "A"}],
            }
        ]
    }
    result = classify_action.classify(empty_workdir, envelope=envelope)
    assert result["classification"] == "DECISION"
    assert result["decision_state"] == "low_confidence"


def test_decision_malformed(empty_workdir: Path) -> None:
    envelope = {
        "novel_decisions": [{"decision_id": "d1", "confidence": "high"}]
    }
    result = classify_action.classify(empty_workdir, envelope=envelope)
    assert result["classification"] == "DECISION"
    assert result["decision_state"] == "malformed"


# ---------------------------------------------------------------------------
# SAFE (priority 7) — default
# ---------------------------------------------------------------------------


def test_safe_empty(empty_workdir: Path) -> None:
    result = classify_action.classify(empty_workdir)
    assert result["classification"] == "SAFE"


def test_safe_innocuous_files(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir, files_touched=["src/foo.py", "README.md"]
    )
    assert result["classification"] == "SAFE"


def test_safe_feature_branch_push(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir, command="git push origin feature/foo"
    )
    assert result["classification"] == "SAFE"


# ---------------------------------------------------------------------------
# Precedence — ensure higher-priority rules win
# ---------------------------------------------------------------------------


def test_production_beats_risky_files(empty_workdir: Path) -> None:
    """Production deploy beats a touched risky file."""
    result = classify_action.classify(
        empty_workdir,
        command="git push origin main",
        files_touched=["migrations/0001.sql"],
    )
    assert result["classification"] == "PRODUCTION"


def test_read_only_beats_decision(empty_workdir: Path) -> None:
    """Read-only inspection wins even if envelope has a decision."""
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}, {"id": "B"}],
            }
        ]
    }
    result = classify_action.classify(
        empty_workdir, command="git status", envelope=envelope
    )
    assert result["classification"] == "SAFE"


def test_risky_file_beats_decision(empty_workdir: Path) -> None:
    """A migrations/ touch wins over a DECISION envelope (priority 6 > 5? no, 5 > 6).

    Actually DECISION (5) wins over risky files (6) per the priority list.
    Test that ordering is honored.
    """
    envelope = {
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}],
            }
        ]
    }
    result = classify_action.classify(
        empty_workdir, files_touched=["migrations/0001.sql"], envelope=envelope
    )
    # DECISION fires before risky files in priority order.
    assert result["classification"] == "DECISION"


def test_irreversible_non_prod_beats_files(empty_workdir: Path) -> None:
    result = classify_action.classify(
        empty_workdir,
        command="git push --force origin feature/abc",
        files_touched=["src/foo.py"],
    )
    assert result["classification"] == "RISKY"
    assert result["matched_rule"] == "irreversible+non-production"


# ---------------------------------------------------------------------------
# MECE proof — for a representative sample, exactly one classification fires.
# ---------------------------------------------------------------------------


def test_mece_each_label_terminates(empty_workdir: Path) -> None:
    """Every classification fires for distinct inputs; none overlap."""
    cases = [
        ({"command": "ls"}, "SAFE"),
        ({"command": "vercel logs"}, "SAFE"),
        ({"command": "git push origin main"}, "PRODUCTION"),
        ({"command": "git push --force origin feat"}, "RISKY"),
        ({"files_touched": ["migrations/x.sql"]}, "RISKY"),
        ({
            "envelope": {"novel_decisions": [{
                "decision_id": "d1", "recommended_default": "A",
                "confidence": "high",
            }]},
        }, "DECISION"),
        ({}, "SAFE"),
    ]
    seen = set()
    for kwargs, expected in cases:
        result = classify_action.classify(empty_workdir, **kwargs)
        assert result["classification"] == expected, f"{kwargs} → {result}"
        seen.add(expected)
    assert seen == {"SAFE", "RISKY", "PRODUCTION", "DECISION"}


# ---------------------------------------------------------------------------
# CLI / exit codes
# ---------------------------------------------------------------------------


def test_cli_safe_exit_zero(
    empty_workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = classify_action.main(["--workdir", str(empty_workdir), "--command", "ls"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["classification"] == "SAFE"


def test_cli_risky_exit_one(
    empty_workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = classify_action.main(
        ["--workdir", str(empty_workdir), "--files-touched", "migrations/0042.sql"]
    )
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["classification"] == "RISKY"


def test_cli_production_exit_two(
    empty_workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = classify_action.main(
        ["--workdir", str(empty_workdir), "--command", "git push origin main"]
    )
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["classification"] == "PRODUCTION"


def test_cli_decision_exit_three(
    empty_workdir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps({
        "novel_decisions": [
            {
                "decision_id": "d1",
                "recommended_default": "A",
                "confidence": "high",
                "options": [{"id": "A"}, {"id": "B"}],
            }
        ]
    }))
    rc = classify_action.main(
        ["--workdir", str(empty_workdir), "--envelope-json", str(env_file)]
    )
    assert rc == 3
    assert json.loads(capsys.readouterr().out)["classification"] == "DECISION"


# ---------------------------------------------------------------------------
# Compound commands — a command is rated by its most severe part
# ---------------------------------------------------------------------------
#
# Defect (confirmed on main 2026-11-09): `_is_read_only` looked at the first
# word only, so `ls && rm -rf ~/x` was "read-only command" and
# `echo hi && npm publish` dropped from PRODUCTION to RISKY. The property test
# below derives its command list from the rule tables themselves, so a new
# rule is covered without anyone remembering to add it here.

import fnmatch as _fnmatch

try:  # Python 3.11+
    import re._parser as _sre_parse  # type: ignore[import-not-found]
    from re import _constants as _sre_c  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - older interpreters
    import sre_constants as _sre_c  # type: ignore[no-redef]
    import sre_parse as _sre_parse  # type: ignore[no-redef]

import autonomy_gate as _gate

_CATEGORY_SAMPLE = {
    _sre_c.CATEGORY_SPACE: " ",
    _sre_c.CATEGORY_DIGIT: "0",
    _sre_c.CATEGORY_WORD: "a",
}


def _regex_examples(parsed) -> list[str]:
    """Every string the simple rule regexes accept along each alternative."""
    out = [""]
    for op, av in parsed:
        if op is _sre_c.LITERAL:
            opts = [chr(av)]
        elif op is _sre_c.AT:
            opts = [""]
        elif op in (_sre_c.ANY, _sre_c.NOT_LITERAL):
            opts = ["x"]
        elif op is _sre_c.IN:
            first = av[0]
            if first[0] is _sre_c.LITERAL:
                opts = [chr(first[1])]
            elif first[0] is _sre_c.CATEGORY:
                opts = [_CATEGORY_SAMPLE.get(first[1], "a")]
            elif first[0] is _sre_c.RANGE:
                opts = [chr(first[1][0])]
            else:
                opts = ["x"]
        elif op is _sre_c.SUBPATTERN:
            opts = _regex_examples(av[-1])
        elif op is _sre_c.BRANCH:
            opts = [s for branch in av[1] for s in _regex_examples(branch)]
        elif op in (_sre_c.MAX_REPEAT, _sre_c.MIN_REPEAT):
            lo, hi, item = av
            inner = _regex_examples(item)
            if lo == 0 and hi == 1:
                opts = [""] + inner
            elif lo == 0:
                opts = [""]
            else:
                opts = [s * lo for s in inner]
        else:
            opts = ["x"]
        out = [a + b for a in out for b in opts]
    return out


def _glob_example(glob: str) -> str | None:
    for fill in ("", "x", " x "):
        candidate = " ".join(glob.replace("*", fill).split())
        if candidate and _fnmatch.fnmatch(candidate, glob):
            return candidate
    return None


def derived_commands() -> list[str]:
    """Commands generated from the rule tables, not picked by hand.

    Sources: classify_action.IRREVERSIBLE_PATTERNS (regex → example strings),
    autonomy_gate.DEFAULT_CONFIRM_FOR / DEFAULT_BLOCK_FOR (glob → example), and
    this file's PRODUCTION_DEPLOYS table. Callers filter to the ones their own
    gate rates above its lowest level.
    """
    seen: dict[str, None] = {}
    for pattern in classify_action.IRREVERSIBLE_PATTERNS:
        for example in _regex_examples(_sre_parse.parse(pattern.pattern, pattern.flags)):
            example = example.strip()
            if example and pattern.search(example):
                seen.setdefault(example, None)
    for glob in list(_gate.DEFAULT_CONFIRM_FOR) + list(_gate.DEFAULT_BLOCK_FOR):
        example = _glob_example(glob)
        if example:
            seen.setdefault(example, None)
    for command in PRODUCTION_DEPLOYS:
        seen.setdefault(command, None)
    return list(seen)


SEVERITY = {"SAFE": 0, "RISKY": 1, "PRODUCTION": 2, "DECISION": 3}

READ_ONLY_PREFIXES = ("ls && {}", "echo hi; {}", "cat x | {}", "true || {}", "echo $( {} )")


def _above_safe(workdir: Path) -> list[tuple[str, str]]:
    out = []
    for command in derived_commands():
        label = classify_action.classify(workdir, command=command)["classification"]
        if SEVERITY[label] > SEVERITY["SAFE"]:
            out.append((command, label))
    return out


def test_derived_list_is_substantial(empty_workdir: Path) -> None:
    rated = dict(_above_safe(empty_workdir))
    # Guards the derivation itself: an empty list would make the property vacuous.
    assert len(rated) >= 15, rated
    assert rated.get("npm publish") == "PRODUCTION"
    assert "gh release create" in rated


def test_no_read_only_prefix_lowers_any_label(empty_workdir: Path) -> None:
    failures = []
    for command, alone in _above_safe(empty_workdir):
        for shape in READ_ONLY_PREFIXES:
            compound = shape.format(command)
            got = classify_action.classify(empty_workdir, command=compound)
            if SEVERITY[got["classification"]] < SEVERITY[alone]:
                failures.append(f"{compound!r}: {got['classification']} < {alone} (alone)")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize(
    "command",
    [
        "ls && rm -rf ~/Documents/example",
        "ls; git reset --hard HEAD~5",
        "cat x && git branch -D feature",
        "echo hi && npm publish",
    ],
)
def test_confirmed_compound_defects_are_not_read_only(empty_workdir: Path, command: str) -> None:
    result = classify_action.classify(empty_workdir, command=command)
    assert result["matched_rule"] != "read_only", result
    assert result["reason"] != "read-only command", result
    assert not classify_action._is_read_only(command)


def test_echo_then_publish_is_production(empty_workdir: Path) -> None:
    assert classify_action.classify(empty_workdir, command="echo hi && npm publish")["classification"] == "PRODUCTION"


@pytest.mark.parametrize(
    "command",
    [
        "git branch -D feature",
        "git branch -d feature",
        "git branch --delete feature",
        "git branch -m old new",
        "git branch -M old new",
        "git branch -c old new",
        "git branch -f feature HEAD~1",
        "git branch --set-upstream-to=origin/x",
        "git branch --unset-upstream",
        "git tag -d v1.0",
        "git tag --delete v1.0",
        "git tag -f v1.0",
        "git tag -a v1.0 -m msg",
        "git config user.name Someone",
        "git config --unset user.name",
        "git config --add x.y z",
        "git config --remove-section x",
        "git config set user.name Someone",
        "git remote add origin url",
        "git remote remove origin",
        "git remote rm origin",
        "git remote set-url origin url",
        "git remote rename a b",
        "git remote prune origin",
        "git reflog expire --expire=now --all",
        "git reflog delete HEAD@{1}",
    ],
)
def test_git_writing_forms_are_not_read_only(empty_workdir: Path, command: str) -> None:
    assert not classify_action._is_read_only(command), command
    assert classify_action.classify(empty_workdir, command=command)["matched_rule"] != "read_only"


@pytest.mark.parametrize(
    "command",
    [
        "ls && pwd",
        "git status; git log --oneline -3",
        "cat a | grep b",
        'echo "a && b"',
        "echo 'x; rm -rf ~/y'",
        "git branch",
        "git branch -a -v",
        "git branch --list 'feat*'",
        "git tag",
        "git tag -l 'v*'",
        "git config --get user.name",
        "git config user.name",
        "git config --list",
        "git remote -v",
        "git remote show origin",
        "git reflog",
        "git log --oneline 2>&1 | head -3",
        "ls &> /dev/null",
        "echo $(git rev-parse HEAD)",
        "if git diff --quiet; then echo clean; fi",
    ],
)
def test_genuinely_read_only_compounds_stay_safe(empty_workdir: Path, command: str) -> None:
    result = classify_action.classify(empty_workdir, command=command)
    assert result["classification"] == "SAFE", result
    assert result["matched_rule"] == "read_only", result


# Splitter -------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, parts",
    [
        ("ls", ["ls"]),
        ("ls && pwd", ["ls", "pwd"]),
        ("a || b; c | d & e", ["a", "b", "c", "d", "e"]),
        ("a\nb\r\nc", ["a", "b", "c"]),
        ('echo "a && b"', ['echo "a && b"']),
        ("echo 'a; b | c'", ["echo 'a; b | c'"]),
        (r"echo a \&\& b", [r"echo a \&\& b"]),
        (r"find . -exec rm {} \;", [r"find . -exec rm {} \;"]),
        ("cmd 2>&1 | tee log", ["cmd 2>&1", "tee log"]),
        ("cmd &> out", ["cmd &> out"]),
        ("(cd /tmp && ls) && echo done", ["cd /tmp", "ls", "echo done"]),
        ("echo $(npm publish)", ["npm publish", "echo $(npm publish)"]),
        ("echo $(a $(b; c))", ["b", "c", "a $(b; c)", "echo $(a $(b; c))"]),
        ('echo "$(rm -rf x)"', ["rm -rf x", 'echo "$(rm -rf x)"']),
        ("echo `rm -rf x`", ["rm -rf x", "echo `rm -rf x`"]),
        ("if true; then npm publish; fi", ["true", "npm publish"]),
        ("{ ls; pwd; }", ["ls", "pwd"]),
        ("ls # && rm -rf x", ["ls"]),
        ("cat <<< 'a && b'", ["cat <<< 'a && b'"]),
    ],
)
def test_split_command(command: str, parts: list[str]) -> None:
    assert sorted(classify_action.split_command(command)) == sorted(parts)


@pytest.mark.parametrize(
    "command",
    [
        "echo 'unbalanced",
        'echo "unbalanced',
        "echo $(unclosed",
        "echo `unclosed",
        "ls )",
        "cat <<EOF\nrm -rf ~/x\nEOF",
        "cat <<'EOF' > f\nhello\nEOF",
    ],
)
def test_unparseable_commands_are_never_read_only(empty_workdir: Path, command: str) -> None:
    assert classify_action.split_command(command) is None
    assert not classify_action._is_read_only(command)
    assert classify_action.classify(empty_workdir, command=command)["matched_rule"] != "read_only"


# Review fixes (2026-11-09): splitter edge cases bash reads differently ---------


@pytest.mark.parametrize(
    "command",
    [
        # F3: an escaped space is not a blank, so `#` does not start a comment.
        "echo a\\ #; rm -rf ~/Documents/example",
        # F4: inside $'...' a backslash escapes the quote, so $'\'' closes at its
        #     third ' and the `rm` after `;` is a real command.
        "echo $'\\'' ; rm -rf ~/Documents/example #'",
        # F5: a function definition is not a read-only command.
        "ls () { rm -rf ~/x; }; ls",
        "function ls { rm -rf ~/x; }; ls",
        "f() { rm -rf ~/x; }",
    ],
)
def test_review_splitter_cases_are_not_read_only(empty_workdir: Path, command: str) -> None:
    assert not classify_action._is_read_only(command), classify_action.split_command(command)
    assert classify_action.classify(empty_workdir, command=command)["matched_rule"] != "read_only"


def test_escaped_space_hash_is_not_a_comment() -> None:
    assert "rm -rf ~/Documents/example" in classify_action.split_command("echo a\\ #; rm -rf ~/Documents/example")


def test_ansi_c_quote_keeps_escaped_quote() -> None:
    assert classify_action.split_command("echo $'it\\'s; fine'") == ["echo $'it\\'s; fine'"]
    assert classify_action.split_command("echo $'a' ; rm -rf ~/x") == ["echo $'a'", "rm -rf ~/x"]


def test_ansi_c_quote_review_case_splits_like_bash() -> None:
    assert classify_action.split_command("echo $'\\'' ; rm -rf ~/Documents/example #'") == [
        "echo $'\\''", "rm -rf ~/Documents/example"]


def test_function_body_is_judged_as_parts() -> None:
    assert classify_action.split_command("ls () { rm -rf ~/x; }; ls") == ["rm -rf ~/x", "ls"]


@pytest.mark.parametrize("depth", [50, 990, 1200])
def test_deep_nesting_is_unparseable_not_a_crash(depth: int) -> None:
    deep = "echo " + "$(" * depth + "true" + ")" * depth
    assert classify_action.split_command(f"rm -rf ~ ; {deep}") is None
    assert not classify_action._is_read_only(f"ls ; {deep}")


def test_too_many_parts_is_not_read_only() -> None:
    assert not classify_action._is_read_only(" ; ".join(["ls"] * (classify_action.MAX_PARTS + 1)))
    assert classify_action._is_read_only(" ; ".join(["ls"] * classify_action.MAX_PARTS))
