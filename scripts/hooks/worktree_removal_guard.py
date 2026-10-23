#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""PreToolUse:Bash guard — no worktree is deleted before its files are archived.

Named failure (2026-10-23): an agent removed 83 "merged, clean" Easy Terminal
worktrees with `git worktree remove` after `git status --porcelain -uall` came
back empty. That check omits GITIGNORED files, so `.build-loop/`, `.bookmark/`,
`.designdoc/` and `.ibr/` state in 55 of them — including a Groundwork design
project a live server was serving — was deleted, unrecoverably.

This guard denies a Bash command that would delete a registered linked worktree
unless `scripts/worktree_archive.py` wrote a receipt for that exact path less
than 30 minutes ago at the worktree's current HEAD. The deny reason carries the
exact archive command, so the fix is one step.

Commands it judges:
  - `git [-C d] worktree remove [-f|--force]... <path>`;
  - recursive `rm` (-r/-R/--recursive), `trash`, `safe-del`/`safe-delete` whose
    target IS a registered worktree or an ANCESTOR of one (`rm -rf repo.worktrees`).
  - `git worktree prune` is allowed: it drops admin entries for worktrees whose
    directories are already gone and never deletes a working-tree file.

Failure policy:
  - CLOSED when the parse is confident a registered worktree is removed and no
    fresh, matching receipt exists;
  - OPEN, with a visible `systemMessage` warning, when git cannot read HEAD, a
    target is unresolvable (`$VAR`, command substitution), the ancestor scan ran
    out of budget, or the guard itself crashes.

Input: the host's PreToolUse event on stdin (`tool_input.command` + `cwd`; a
top-level `command` is accepted for Cursor's beforeShellExecution).
Output: Claude/Codex JSON by default; `--host cursor` emits Cursor's shape.
Always exits 0.
"""
from __future__ import annotations

import glob
import json
import os
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import worktree_archive as wa  # noqa: E402

_SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", "\n"}
_WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "builtin", "nice"}
_DELETERS = {"trash", "safe-del", "safe-delete"}
_SCAN_DEPTH = 4
_SCAN_BUDGET = 5000
_SKIP_DIRS = {".git", "node_modules", "target", ".build", "build", "dist", "DerivedData",
              ".venv", "venv", "__pycache__", ".next", "Pods", ".cache"}


class Unresolved(Exception):
    pass


def _tokens(cmd: str) -> list[str]:
    lex = shlex.shlex(cmd.replace("\r", "\n"), posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _segments(cmd: str) -> list[list[str]]:
    segs: list[list[str]] = [[]]
    for tok in _tokens(cmd):
        if tok in _SEPARATORS or set(tok) <= set(";&|()\n"):
            if segs[-1]:
                segs.append([])
            continue
        segs[-1].append(tok)
    return [s for s in segs if s]


def _strip_prefix(seg: list[str]) -> list[str]:
    i = 0
    while i < len(seg):
        tok = seg[i]
        if "=" in tok and not tok.startswith("-") and tok.split("=", 1)[0].isidentifier():
            i += 1
            continue
        base = os.path.basename(tok)
        if base in _WRAPPERS:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 1
            continue
        if base == "env":
            i += 1
            while i < len(seg) and (seg[i].startswith("-") or "=" in seg[i]):
                i += 1
            continue
        break
    return seg[i:]


def _resolve(raw: str, cwd: str) -> list[str]:
    if "$" in raw or "`" in raw:
        raise Unresolved(raw)
    text = os.path.expanduser(raw)
    if not os.path.isabs(text):
        text = os.path.join(cwd, text)
    if any(ch in text for ch in "*?["):
        hits = glob.glob(text)
        return [os.path.realpath(h) for h in hits] or [os.path.realpath(text)]
    return [os.path.realpath(text)]


def _git_worktree_remove(args: list[str], cwd: str) -> list[str] | None:
    """Return raw targets when argv is `git ... worktree remove ...`, else None."""
    i = 0
    eff = cwd
    while i < len(args):
        tok = args[i]
        if tok == "-C" and i + 1 < len(args):
            nxt = os.path.expanduser(args[i + 1])
            eff = nxt if os.path.isabs(nxt) else os.path.join(eff, nxt)
            i += 2
            continue
        if tok in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        break
    rest = args[i:]
    if len(rest) < 2 or rest[0] != "worktree" or rest[1] != "remove":
        return None
    positional = [t for t in rest[2:] if not t.startswith("-")]
    return [eff] + positional[:1]


def _rm_targets(prog: str, args: list[str]) -> list[str] | None:
    if prog in _DELETERS:
        out, flags_done = [], False
        for t in args:
            if t == "--" and not flags_done:
                flags_done = True
            elif t.startswith("-") and not flags_done:
                continue
            else:
                out.append(t)
        return out
    if prog != "rm":
        return None
    recursive, out, flags_done = False, [], False
    for t in args:
        if not flags_done and t == "--":
            flags_done = True
        elif not flags_done and t.startswith("--"):
            recursive |= t == "--recursive"
        elif not flags_done and t.startswith("-") and len(t) > 1:
            recursive |= ("r" in t[1:] or "R" in t[1:])
        else:
            out.append(t)
    return out if recursive else None


def _worktrees_at_or_under(target: str, warnings: list[str]) -> list[str]:
    found: list[str] = []
    if wa.linked_worktree_admin(target) is not None:
        found.append(target)
    if not os.path.isdir(target) or os.path.islink(target):
        return found
    budget = _SCAN_BUDGET
    frontier = [(target, 0)]
    while frontier:
        d, depth = frontier.pop()
        if depth >= _SCAN_DEPTH:
            continue
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or e.name in _SKIP_DIRS:
                continue
            budget -= 1
            if budget < 0:
                warnings.append(f"could not fully scan {target} for worktrees (over {_SCAN_BUDGET} directories)")
                return found
            if os.path.isfile(os.path.join(e.path, ".git")) and wa.linked_worktree_admin(e.path):
                found.append(os.path.realpath(e.path))
            frontier.append((e.path, depth + 1))
    return found


def _registered_via_git(eff: str, target: str, warnings: list[str]) -> bool:
    """Fallback for `git worktree remove` when the gitlink check is inconclusive."""
    import subprocess
    try:
        proc = subprocess.run(["git", "-C", eff, "worktree", "list", "--porcelain"],
                              capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"git worktree list failed ({exc})")
        return False
    if proc.returncode != 0:
        warnings.append(f"git worktree list failed: {(proc.stderr or '').strip()[:200]}")
        return False
    for line in proc.stdout.splitlines():
        if line.startswith("worktree ") and os.path.realpath(line[9:].strip()) == target:
            return True
    return False


def evaluate(cmd: str, cwd: str) -> dict:
    """Return {"deny": [...], "warnings": [...], "paths": [...]}."""
    deny: list[str] = []
    warnings: list[str] = []
    need_archive: list[str] = []
    cwd = cwd or os.getcwd()
    try:
        segments = _segments(cmd)
    except ValueError as exc:
        if "worktree" in cmd or "rm " in cmd:
            warnings.append(f"could not parse the command to check for worktree removal ({exc})")
        return {"deny": deny, "warnings": warnings, "paths": need_archive}

    seen: set[str] = set()
    for raw_seg in segments:
        seg = _strip_prefix(raw_seg)
        if not seg:
            continue
        prog, args = os.path.basename(seg[0]), seg[1:]
        cflag = next((i for i, a in enumerate(args[:-1])
                      if a.startswith("-") and not a.startswith("--") and "c" in a), None)
        if prog in ("bash", "sh", "zsh") and cflag is not None:
            inner = evaluate(args[cflag + 1], cwd)
            deny += inner["deny"]
            warnings += inner["warnings"]
            need_archive += [p for p in inner["paths"] if p not in need_archive]
            continue
        if prog in ("cd", "pushd") and args:
            try:
                cwd = _resolve(args[0], cwd)[0]
            except Unresolved:
                pass
            continue
        affected: list[tuple[str, str]] = []  # (worktree, removal target)
        try:
            if prog == "git":
                wr = _git_worktree_remove(args, cwd)
                if wr is None or len(wr) < 2:
                    continue
                eff, raw = wr
                for target in _resolve(raw, eff):
                    if not os.path.isdir(target):
                        continue
                    if wa.linked_worktree_admin(target) or _registered_via_git(eff, target, warnings):
                        affected.append((target, target))
            else:
                raws = _rm_targets(prog, args)
                if not raws:
                    continue
                for raw in raws:
                    for target in _resolve(raw, cwd):
                        for wt in _worktrees_at_or_under(target, warnings):
                            affected.append((wt, target))
        except Unresolved as exc:
            warnings.append(f"`{prog}` target {exc} is not resolvable before the shell runs; "
                            "if it names a worktree, archive it first")
            continue

        for wt, target in affected:
            if (wt, target) in seen:
                continue
            seen.add((wt, target))
            verdict, detail = wa.receipt_verdict(wt)
            if verdict == "ok":
                arch = wa.load_receipt(wt) or {}
                ap = arch.get("archive_path")
                if ap and prog != "git" and (os.path.realpath(ap) + "/").startswith(target.rstrip("/") + "/"):
                    deny.append(f"{target} contains the archive for {wt} ({ap}); move archives out "
                                "or archive to another --dest first")
                continue
            if verdict == "git_error":
                warnings.append(f"could not read HEAD of {wt} ({detail}); removal allowed unverified")
                continue
            deny.append(f"{wt}: {detail}")
            if wt not in need_archive:
                need_archive.append(wt)
    return {"deny": deny, "warnings": warnings, "paths": need_archive}


def _command_from_event(event: dict) -> str:
    ti = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    cmd = ti.get("command", event.get("command", ""))
    if isinstance(cmd, list):
        # Codex-style argv, e.g. ["bash", "-lc", "<script>"]
        if len(cmd) >= 3 and os.path.basename(str(cmd[0])) in ("bash", "sh", "zsh") and str(cmd[1]).startswith("-"):
            return str(cmd[-1])
        return shlex.join(str(c) for c in cmd)
    return str(cmd or "")


def render(result: dict, host: str) -> dict:
    if result["deny"]:
        lines = ["Blocked: this command deletes a git worktree, including git-IGNORED files "
                 "(.build-loop/, .bookmark/, .ibr/, ...) that `git status` does not show."]
        lines += [f"- {d}" for d in result["deny"]]
        if result["paths"]:
            lines.append("Archive first (one step; caches are listed, not archived):")
            lines.append(f"  {wa.archive_command(result['paths'], PLUGIN_ROOT)}")
            lines.append(f"then re-run the same command within {wa.RECEIPT_TTL_S // 60} minutes.")
        if result["warnings"]:
            lines.append("Also: " + "; ".join(result["warnings"]))
        reason = "\n".join(lines)
        if host == "cursor":
            return {"permission": "deny", "userMessage": reason, "agentMessage": reason}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    if result["warnings"]:
        msg = "[build-loop worktree guard] " + "; ".join(result["warnings"])
        if host == "cursor":
            return {"permission": "allow", "userMessage": msg}
        return {"systemMessage": msg}
    return {"permission": "allow"} if host == "cursor" else {}


def main(argv: list[str]) -> int:
    host = "cursor" if "--host" in argv and argv[argv.index("--host") + 1:][:1] == ["cursor"] else "claude"
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        cmd = _command_from_event(event)
        if not cmd:
            print(json.dumps(render({"deny": [], "warnings": [], "paths": []}, host)))
            return 0
        result = evaluate(cmd, str(event.get("cwd") or ""))
    except Exception as exc:  # noqa: BLE001 — fail open, visibly
        result = {"deny": [], "warnings": [f"guard error, removal not checked: {exc}"], "paths": []}
    out = render(result, host)
    if result["warnings"]:
        print("; ".join(result["warnings"]), file=sys.stderr)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
