#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""In-process runner for pre_bash_dispatch.sh's always-on Python gates.

WHY: the dispatcher runs before every Bash tool call. On an opted-in repo a
plain `ls` used to start TWELVE Python interpreters (credential policy, event
extraction, load gate, wait gate, git classifier, five inside
pre_bash_autonomy.sh, deploy classifier, envelope merge). Interpreter start,
not gate logic, was the cost: ~20-25 ms each, ~70% of a 0.59 s run.

This file runs the same gate modules inside ONE interpreter per phase and
returns their results to the shell, which still makes every decision in the
original order. It changes no policy. Each gate is executed the way its own
process used to run it:

  - loaded from the same path under $PLUGIN_ROOT, with sys.path[0] set to the
    gate's real directory (what `python3 <script>` does), sys.argv reset;
  - fed the same stdin bytes and decoded/encoded with the same codecs, so an
    unencodable value fails the same way it did in its own process;
  - its stdout captured and trailing newlines stripped, as `$(...)` did;
  - its stderr discarded or passed through exactly as the shell redirected it;
  - any exception or SystemExit becomes the non-zero exit the shell tested.

Modes (stdin = the raw PreToolUse event):
  head      credential policy + command/cwd extraction (always runs)
  gates     load gate, wait gate, git classifier, autonomy gate, deploy target
            (opted-in repos only); reads BL_PLUGIN_ROOT, BL_CMD
  autonomy  pre_bash_autonomy.sh's envelope alone (drift test hook)

pre_bash_autonomy.sh stays the reference implementation of the autonomy gate;
scripts/hooks/test_pre_bash_fastpath.py asserts this emulation matches it.
"""
from __future__ import annotations

import importlib.util
import io
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))


class _Result:
    __slots__ = ("rc", "out")

    def __init__(self, rc: int, out: str) -> None:
        self.rc = rc
        self.out = out


def _subst(text: str) -> str:
    """What bash `$(...)` keeps: NULs dropped, trailing newlines stripped."""
    return text.replace("\x00", "").rstrip("\n")


def _new_stdout() -> io.TextIOWrapper:
    real = sys.__stdout__
    return io.TextIOWrapper(io.BytesIO(), encoding=real.encoding, errors=real.errors)


def _new_stdin(data: bytes) -> io.TextIOWrapper:
    real = sys.__stdin__
    return io.TextIOWrapper(io.BytesIO(data), encoding=real.encoding, errors=real.errors)


def _drain(buf: io.TextIOWrapper) -> str:
    try:
        buf.flush()
        raw = buf.buffer.getvalue()  # type: ignore[attr-defined]
    except Exception:
        return ""
    return raw.decode(sys.__stdout__.encoding or "utf-8", errors="surrogateescape")


def _run_script(path: str, data: bytes, *, stderr_through: bool, argv: list[str] | None = None,
                call=None) -> _Result:
    """Run `python3 <path>` in-process: import as a fresh module, call main()."""
    out = _new_stdout()
    saved = (sys.stdin, sys.stdout, sys.stderr, sys.argv, list(sys.path))
    sys.stdin = _new_stdin(data)
    sys.stdout = out
    if not stderr_through:
        sys.stderr = io.StringIO()
    sys.argv = [path]
    rc = 0
    try:
        real = os.path.realpath(path)
        if not os.path.isfile(real):
            raise FileNotFoundError(path)
        sys.path[0:1] = [os.path.dirname(real)]
        name = "_blfp_" + os.path.basename(path).replace(".", "_").replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        if call is not None:
            ret = call(mod)
        else:
            ret = mod.main() if argv is None else mod.main(argv)
        rc = ret if isinstance(ret, int) else 0
    except SystemExit as exc:
        code = exc.code
        rc = code if isinstance(code, int) else (0 if code is None else 1)
    except BaseException:
        rc = 1
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        sys.stdin, sys.stdout, sys.stderr, sys.argv = saved[:4]
        sys.path[:] = saved[4]
    return _Result(rc, _subst(_drain(out)))


# ── head ────────────────────────────────────────────────────────────────────
def _extract(data: bytes) -> str:
    """The dispatcher's former `python3 -c "$_EXTRACT"` block, byte for byte."""
    out = _new_stdout()
    saved = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = _new_stdin(data), out
    try:
        try:
            import json
            d = json.load(sys.stdin)
            print(d.get("cwd", ""))
            cmd = d.get("tool_input", {}).get("command", "")
            print(cmd.replace("\r", "\n").replace("\n", ";"))
        except Exception:
            print("")
            print("")
    except BaseException:
        pass
    finally:
        sys.stdin, sys.stdout = saved
    return _subst(_drain(out))


def head(data: bytes, plugin_root: str) -> str:
    cred = _run_script(os.path.join(plugin_root, "scripts", "credential_policy.py"), data,
                       stderr_through=False)
    cred_out = cred.out.replace("\n", " ")  # json.dumps never emits one; keep framing safe
    return "%d\n%s\n%s" % (0 if cred.rc == 0 else 1, cred_out, _extract(data))


# ── autonomy (mirror of pre_bash_autonomy.sh) ───────────────────────────────
def _print_value(value: object) -> str | None:
    """`python3 -c "print(...)"` captured by `$(...)`; None when print would fail."""
    try:
        text = str(value) + "\n"
        text.encode(sys.__stdout__.encoding or "utf-8", sys.__stdout__.errors or "strict")
    except Exception:
        return None
    return _subst(text)


# GIT_* variables that cannot move repository discovery or the work tree. Any
# other GIT_* variable (GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR,
# GIT_CONFIG_PARAMETERS, GIT_CEILING_DIRECTORIES, ...) sends us to git itself.
# Global/system config is safe to redirect: git reads core.worktree from the
# repository's own config only (checked: a global core.worktree is ignored).
_GIT_ENV_INERT = frozenset({
    "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_PAGER", "GIT_TERMINAL_PROMPT", "GIT_ASKPASS",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE", "GIT_MERGE_AUTOEDIT",
    "GIT_OPTIONAL_LOCKS", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
})
_HEADREF = re.compile(rb"ref: refs/\S+\n?|[0-9a-f]{40}\n?|[0-9a-f]{64}\n?")


def _read(path: str, limit: int = 4096) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(limit + 1)


def _is_own_toplevel(directory: str) -> bool:
    """True only when `git -C directory rev-parse --show-toplevel` must print `directory`.

    Every check is stricter than git's own, so a False here costs one git call
    and a True never changes the answer. git prints the work tree's canonical
    path, found by discovery from `directory`; it equals `directory` when:
      - no GIT_* variable that moves discovery or the work tree is set;
      - `directory` is already canonical: absolute, ASCII (git precomposes
        Unicode on macOS), and byte-equal to the kernel's own path for it
        (F_GETPATH, what getcwd uses; this rejects symlinks, `..`, a trailing
        slash and wrong letter case). Other platforms always ask git;
      - `directory/.git` is a repository git accepts (valid HEAD, objects and
        refs, via an absolute `gitdir:` file for a linked worktree), so
        discovery stops here instead of walking up to a parent;
      - no core.worktree can apply: the repository config never mentions
        `worktree` or `include`, and there is no per-worktree config.
    When git would fail instead (ownership, bad format), today's code also
    falls back to `directory`, so those cases agree either way.
    """
    try:
        if any(k.startswith("GIT_") and k not in _GIT_ENV_INERT for k in os.environ):
            return False
        if not (os.path.isabs(directory) and directory.isascii()):
            return False
        import fcntl
        fd = os.open(directory, os.O_RDONLY)
        try:
            kernel = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]
        finally:
            os.close(fd)
        if kernel != os.fsencode(directory):
            return False
        dotgit = os.path.join(directory, ".git")
        if os.path.islink(dotgit):
            return False
        if os.path.isdir(dotgit):
            gitdir = dotgit
        elif os.path.isfile(dotgit):
            pointer = re.fullmatch(rb"gitdir: (/[^\n\r]+)\n?", _read(dotgit))
            if not pointer:
                return False
            gitdir = os.fsdecode(pointer.group(1))
        else:
            return False
        common = gitdir
        commondir = os.path.join(gitdir, "commondir")
        if os.path.lexists(commondir):
            target = re.fullmatch(rb"([^\n\r]+)\n?", _read(commondir))
            if not target:
                return False
            common = os.path.join(gitdir, os.fsdecode(target.group(1)))
        head = os.path.join(gitdir, "HEAD")
        if os.path.islink(head) or not os.path.isfile(head) or not _HEADREF.fullmatch(_read(head, 256)):
            return False
        for sub in ("objects", "refs"):
            path = os.path.join(common, sub)
            if not (os.path.isdir(path) and os.access(path, os.X_OK)):
                return False
        if os.path.lexists(os.path.join(gitdir, "config.worktree")):
            return False
        for config in {os.path.join(common, "config"), os.path.join(gitdir, "config")}:
            if os.path.lexists(config):
                text = _read(config, 1 << 20).lower()
                if len(text) > 1 << 20 or b"worktree" in text or b"include" in text:
                    return False
        return True
    except Exception:
        return False


def _repo_root(directory: str) -> str:
    if not directory or not os.path.isdir(directory):
        return directory
    if _is_own_toplevel(directory):
        return directory  # what git would print: see _is_own_toplevel
    try:
        proc = subprocess.run(["git", "-C", directory, "rev-parse", "--show-toplevel"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        top = _subst(proc.stdout.decode("utf-8", errors="surrogateescape"))
    except Exception:
        top = ""
    if top and os.path.isdir(top):
        return top
    return directory


def autonomy_envelope(data: bytes, plugin_root: str) -> str:
    import json

    sh = os.path.join(plugin_root, "scripts", "hooks", "pre_bash_autonomy.sh")
    # _run_gate: executable → run it; plain file → `python3 <bash script>` (prints nothing).
    if not (os.path.isfile(sh) and os.access(sh, os.X_OK)):
        return "{}"
    try:
        d = json.loads(data.decode(sys.__stdin__.encoding or "utf-8", sys.__stdin__.errors or "strict"))
        cmd = _print_value(d.get("tool_input", {}).get("command", ""))
    except Exception:
        cmd = None
    try:
        d = json.loads(data.decode(sys.__stdin__.encoding or "utf-8", sys.__stdin__.errors or "strict"))
        cwd = _print_value(d.get("cwd", ""))
    except Exception:
        cwd = None
    cmd = cmd or ""
    cwd = cwd or ""
    home = os.environ.get("HOME", "")
    if not cmd:
        return "{}"
    if not cwd or cwd == "/" or cwd == home:
        return "{}"
    root = _repo_root(cwd)
    if not root or root == "/" or root == home:
        return "{}"
    if not (os.path.isfile(os.path.join(root, ".build-loop", "state.json"))
            or os.path.isfile(os.path.join(root, ".build-loop", "config.json"))):
        return "{}"
    if os.environ.get("BUILD_LOOP_HOOKS", "") == "off":
        return "{}"
    gate = os.path.join(plugin_root, "scripts", "autonomy_gate.py")
    if not os.path.isfile(gate):
        return "{}"
    result = _run_script(gate, b"", stderr_through=False,
                         argv=["--workdir", root or cwd or ".", "--action", "PreToolUse:Bash",
                               "--command", cmd, "--json"]).out
    if not result:
        return "{}"
    try:
        action = _print_value(json.loads(result).get("action", "auto"))
        if action is None:
            raise ValueError
    except Exception:
        action = "auto"
    decision = {"auto": "allow", "warn": "allow", "confirm": "ask", "block": "deny"}.get(action, "ask")
    try:
        reason = json.loads(result).get("reason", "")
    except Exception:
        reason = ""
    if decision == "allow":
        return "{}"
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": decision,
                                              "permissionDecisionReason": reason}})


# ── gates ───────────────────────────────────────────────────────────────────
def _deploy_target(plugin_root: str, cmd: str) -> str:
    """The dispatcher's former `python3 - "$_DPOL"` deploy classifier block."""
    path = os.path.join(plugin_root, "scripts", "deployment_policy.py")
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        spec = importlib.util.spec_from_file_location("deployment_policy", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if mod.is_deploy_like(cmd):
            try:
                target, _reason = mod.classify_command(cmd)
            except Exception:
                target = "unknown"
            return target if target != "unknown" else "deploy"
        return ""
    except BaseException:
        return ""
    finally:
        sys.stderr = saved


def gates(data: bytes, plugin_root: str, cmd: str) -> str:
    """Lines: verdict, classifier status, classifier output, deploy target, autonomy envelope.

    verdict is `load` or `wait` when that gate rejected the command (its stderr
    already went out); later gates are then not run, as before.
    """
    hooks = os.path.join(plugin_root, "scripts", "hooks")
    for tag, name in (("load", "unbounded_load_gate.py"), ("wait", "unbounded_wait_gate.py")):
        path = os.path.join(hooks, name)
        if os.path.isfile(path):
            sys.stderr.flush()
            if _run_script(path, data, stderr_through=True).rc != 0:
                sys.stderr.flush()
                return tag
    cls = _run_script(os.path.join(hooks, "git_command_classifier.py"), data, stderr_through=False)
    cls_ok = "ok" if cls.rc == 0 else "fail"
    autonomy = autonomy_envelope(data, plugin_root).replace("\n", " ")
    deploy = _deploy_target(plugin_root, cmd).replace("\n", " ")
    return "ok\n%s\n%s\n%s\n%s" % (cls_ok, cls.out.replace("\n", " "), deploy, autonomy)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = sys.stdin.buffer.read()
    plugin_root = os.environ.get("BL_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(_HERE))
    if mode == "head":
        text = head(data, plugin_root)
    elif mode == "gates":
        text = gates(data, plugin_root, os.environ.get("BL_CMD", ""))
    elif mode == "autonomy":
        text = autonomy_envelope(data, plugin_root)
    else:
        return 2
    sys.stdout.buffer.write(text.encode("utf-8", errors="surrogateescape"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
