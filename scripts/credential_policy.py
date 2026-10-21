"""Owner credential policy. Classify commands without reading secret values."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

REFUSAL = (
    "Keychain credential access is prohibited. Use the designated Secrets Vault "
    "metadata/injection interface or an already authorized service connection. "
    "Do not probe, unlock, migrate, retry authentication, or copy secrets into plaintext storage."
)
_API = re.compile(r"SecItem(?:CopyMatching|Add|Update|Delete)|SecKeychain\w*|keyring\.(?:get_password|set_password|delete_password)")
_NETWORK = {"push", "pull", "fetch", "clone", "ls-remote", "credential"}


def _segments(command: str):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    current = []
    for token in lexer:
        if token in {";", "&&", "||", "|", "&", "(", ")"}:
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def _substitutions(source):
    """Extract executable substitutions; single-quoted prose remains inert."""
    quote = None
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        if quote != "'" and (char == "`" or source.startswith("$(", index)):
            backtick = char == "`"
            start = index + (1 if backtick else 2)
            end, depth = start, 1
            while end < len(source):
                if source[end] == "\\":
                    end += 2
                    continue
                if backtick and source[end] == "`":
                    break
                if not backtick:
                    depth += (source[end] == "(") - (source[end] == ")")
                    if depth == 0:
                        break
                end += 1
            yield source[start:end]
            index = end
        index += 1


def command_reason(command: str, *, cwd: str | None = None,
                   helpers: list[str] | None = None, platform: str | None = None,
                   environment: dict | None = None, cwd_uncertain: bool = False) -> str | None:
    """Known executable forms only; opaque app/script behavior remains protocol-governed."""
    environment = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform
    try:
        segments = list(_segments(command.replace("\n", ";")))
    except ValueError:
        return REFUSAL if _API.search(command) else None
    # Substitutions are expanded in their surrounding shell context. If this
    # command contains any directory-changing builtin, do not inspect their
    # Git helpers against a guessed directory (including earlier substitutions).
    cwd_uncertain |= any(token in {"cd", "pushd", "popd"}
                         for segment in segments for token in segment)
    for inner in _substitutions(command):
        if command_reason(inner, cwd=cwd, helpers=helpers, platform=platform, environment=environment, cwd_uncertain=cwd_uncertain):
            return REFUSAL
    for argv in segments:
        command_cwd_uncertain = cwd_uncertain
        effective_environment = dict(environment)
        inline_config = False
        while argv:
            redirect = re.match(r"^(?:[0-9]+)?(>>?|<<?)(.*)$", argv[0])
            if redirect:
                argv.pop(0)
                if not redirect.group(2) and argv:
                    argv.pop(0)
                continue
            if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", argv[0]):
                name, value = argv[0].split("=", 1)
                effective_environment[name] = value
                inline_config |= argv[0].startswith("GIT_CONFIG_")
                argv = argv[1:]
                continue
            wrapper = Path(argv[0]).name
            if wrapper not in {"env", "sudo", "command", "exec", "nohup"}:
                break
            argv = argv[1:]
            if wrapper == "sudo":
                effective_environment = {}
            while argv and argv[0].startswith("-"):
                option = argv.pop(0)
                if option in {"-C", "--chdir", "-D"} or option.startswith("--chdir="):
                    command_cwd_uncertain = True
                if wrapper == "env":
                    if option in {"-i", "--ignore-environment"}:
                        effective_environment.clear()
                    elif option.startswith("--unset="):
                        effective_environment.pop(option.split("=", 1)[1], None)
                    elif option in {"-u", "--unset"} and argv:
                        effective_environment.pop(argv[0], None)
                valued = {"-u", "--unset", "-C", "--chdir"} if wrapper == "env" else {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-R", "--chroot", "-D", "--chdir"} if wrapper == "sudo" else set()
                if option in valued and argv:
                    argv.pop(0)
        if not argv:
            continue
        exe = Path(argv[0]).name
        if exe in {"cd", "pushd", "popd"}:
            cwd_uncertain = True
            continue
        if argv[1:] in (["--help"], ["-h"], ["--version"], ["help"]):
            continue
        if exe == "security" or "credential-osxkeychain" in exe:
            return REFUSAL
        if exe == "vault" and (any(a == "--value" or a.startswith("--value=") for a in argv) or any(x in {"export", "dump"} for x in argv[1:])):
            return "Secret values must not enter agent output. Use Secrets Vault metadata or server-side injection."
        shell_option = next((i for i, arg in enumerate(argv[1:], 1)
                             if arg.startswith("-") and not arg.startswith("--") and "c" in arg), None)
        if exe in {"bash", "sh", "zsh"} and shell_option is not None:
            index = shell_option + 1
            if index < len(argv) and command_reason(argv[index], cwd=cwd, helpers=helpers,
                                                   platform=platform, environment=effective_environment, cwd_uncertain=command_cwd_uncertain):
                return REFUSAL
        if exe.startswith(("python", "swift", "osascript")):
            if exe.startswith("python") and "-c" in argv:
                try:
                    code = ast.parse(argv[argv.index("-c") + 1])
                    if any(isinstance(node, ast.Call) and _API.search(ast.unparse(node.func))
                           for node in ast.walk(code)):
                        return REFUSAL
                except (SyntaxError, IndexError):
                    if any(_API.search(arg) for arg in argv[1:]):
                        return REFUSAL
            elif any(_API.search(arg) for arg in argv[1:]):
                return REFUSAL
            for marker in ("--argv", "--"):
                if marker in argv and any(Path(a).name in {"privileged_broker.py", "system_access_request.py"} for a in argv):
                    inner = argv[argv.index(marker) + 1:]
                    if command_reason(shlex.join(inner), cwd=cwd, helpers=helpers,
                                      platform=platform, environment=effective_environment, cwd_uncertain=command_cwd_uncertain):
                        return REFUSAL
        if platform != "darwin":
            continue
        # Shell text cannot establish an approved auth route: expansions,
        # unset/export and wrapper environments can trigger stored-credential
        # fallback. Use the connected service instead of guessing from tokens.
        if exe in {"gh", "claude"}:
            return REFUSAL
        if exe != "git":
            continue
        prefix = []
        index = 1
        while index < len(argv) and argv[index].startswith("-"):
            option = argv[index]
            prefix.append(option)
            index += 1
            if option in {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env"} and index < len(argv):
                prefix.append(argv[index])
                index += 1
        if index < len(argv) and "credential-osxkeychain" in argv[index]:
            return REFUSAL
        if index < len(argv) and argv[index] in _NETWORK:
            # Dynamic config overrides cannot be proven safe by inspecting the
            # parent environment; require an explicit, inspectable Git -c form.
            if command_cwd_uncertain or inline_config or any(a.startswith("--config-env") for a in prefix):
                return REFUSAL
            if helpers is None:
                try:
                    result = subprocess.run([argv[0], *prefix, "config", "--get-regexp", r"^credential(\..*)?\.helper$"],
                                            cwd=cwd, env=effective_environment, capture_output=True, text=True, timeout=2)
                    if result.returncode not in (0, 1):
                        return REFUSAL
                    pairs = [line.partition(" ") for line in result.stdout.splitlines()]
                    # Scoped helpers have separate reset chains; never let a
                    # global reset erase a URL-specific Keychain helper.
                    chains = {}
                    for key, _, value in pairs:
                        chain = chains.setdefault(key, [])
                        if value.strip():
                            chain.append(value)
                        else:
                            chain.clear()
                    configured = [value for chain in chains.values() for value in chain]
                except (OSError, subprocess.SubprocessError):
                    return REFUSAL
            else:
                configured = list(helpers)
                for arg in prefix:
                    if re.match(r"credential\..*helper=", arg):
                        key, value = arg.split("=", 1)
                        if key != "credential.helper":
                            if "osxkeychain" in value or value.split(" ", 1)[0] == "store":
                                return REFUSAL
                        else:
                            configured.append(value)
            effective = []
            for helper in configured:
                if helper.strip():
                    effective.append(helper)
                else:
                    effective.clear()
            if any("osxkeychain" in helper or helper.strip().split(" ", 1)[0] == "store" for helper in effective):
                return REFUSAL
    return None


def main() -> None:
    event = json.load(sys.stdin)
    reason = command_reason((event.get("tool_input") or {}).get("command", ""), cwd=event.get("cwd"))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                            "permissionDecisionReason": reason}} if reason else {}))


if __name__ == "__main__":
    main()
