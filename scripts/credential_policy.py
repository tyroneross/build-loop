"""Owner credential policy. Classify commands without reading secret values."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import fnmatch
import shlex
import subprocess
import sys
from urllib.parse import urlsplit

REFUSAL = (
    "Keychain credential access is prohibited. Use the designated Secrets Vault "
    "metadata/injection interface or an already authorized service connection. "
    "Do not probe, unlock, migrate, retry authentication, or copy secrets into plaintext storage."
)
_API = re.compile(r"SecItem(?:CopyMatching|Add|Update|Delete)|SecKeychain\w*|keyring\.(?:get_password|set_password|delete_password)")
_NETWORK = {"push", "pull", "fetch", "clone", "ls-remote", "credential"}
_HEREDOC = re.compile(r"<<(-?)\s*(?:'([^']+)'|\"([^\"]+)\"|\\?([A-Za-z_][A-Za-z_0-9]*))")


# Readers that only pass text along. A heredoc body is data only when EVERY
# command on its line is one of these; `cat <<EOF | sh` or `eval "$(cat <<EOF`
# execute the body, so any other command word makes the body a script.
_DATA_READERS = {"cat", "tee", "wc", "head", "tail", "sort", "uniq", "grep", "rg",
                 "jq", "diff", "cut", "tr", "column", "fold", "pbcopy", "echo", "printf",
                 # these do not read stdin at all
                 "cd", "mkdir", "export", "set", "true", ":", "test", "[", "ls"}
_INTERPRETERS = ("python", "swift", "osascript")


def _line_commands(line: str) -> set:
    """Every command word on one shell line, including inside $( ) and backticks."""
    words = set()
    for piece in re.split(r"&&|\|\||\$\(|[;|&()`]", line):
        piece = re.sub(r"<<-?\s*\S+", " ", piece)
        tokens = [t for t in piece.split() if not re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", t)]
        tokens = [t.strip("\"'") for t in tokens]
        while tokens and Path(tokens[0]).name in {"env", "sudo", "command", "exec", "nohup", "time"}:
            tokens = [t for t in tokens[1:] if not t.startswith("-")]
        if tokens and not re.match(r"^[0-9]*[<>]", tokens[0]):
            words.add(Path(tokens[0]).name)
    return words


def _split_heredocs(command: str):
    """Separate heredoc bodies from shell text. A body is data for its reader;
    only a shell or interpreter reader turns it into code (checked by caller)."""
    lines = command.split("\n")
    shell_lines, bodies, index = [], [], 0
    while index < len(lines):
        line = lines[index]
        shell_lines.append(line)
        index += 1
        if "<<" not in line or "<<<" in line:
            continue
        for match in _HEREDOC.finditer(line):
            strip_tabs = match.group(1) == "-"
            delimiter = match.group(2) or match.group(3) or match.group(4)
            quoted = match.group(4) is None or match.group(0).lstrip("<-").lstrip().startswith("\\")
            body = []
            while index < len(lines):
                candidate = lines[index].lstrip("\t") if strip_tabs else lines[index]
                index += 1
                if candidate == delimiter:
                    break
                body.append(lines[index - 1])
            bodies.append((quoted, _line_commands(line), "\n".join(body)))
    return "\n".join(shell_lines), bodies


def _python_calls_keychain(source: str) -> bool:
    try:
        return any(isinstance(node, ast.Call) and _API.search(ast.unparse(node.func))
                   for node in ast.walk(ast.parse(source)))
    except SyntaxError:
        return bool(_API.search(source))


def _scope_match(pattern: str, url: str | None):
    """Does `credential.<pattern>.helper` apply to `url`? True, False, or None
    when it cannot be decided statically (unknown URL, user or path scopes)."""
    if url is None or "://" not in pattern:
        return None
    try:
        want, have = urlsplit(pattern), urlsplit(url)
        want_port, have_port = want.port, have.port
    except ValueError:
        return None
    if want.scheme.lower() != have.scheme.lower():
        return False
    want_host, have_host = (want.hostname or "").lower(), (have.hostname or "").lower()
    want_labels, have_labels = want_host.split("."), have_host.split(".")
    if len(want_labels) != len(have_labels) or not all(
            fnmatch.fnmatchcase(h, w) for w, h in zip(want_labels, have_labels)):
        return False
    if want_port is not None and want_port != have_port:
        return False
    if want.username or want.path not in {"", "/"}:
        return None
    return True


def _effective_helpers(pairs, url: str | None):
    """Git's helper list for one URL: every matching key in config order, and an
    empty value clears everything collected so far (git credential.c, select_all).
    A reset counts only when its scope certainly applies; an uncertain scope
    still contributes its helpers, so doubt never hides a Keychain helper."""
    chain = []
    for key, value in pairs:
        if key == "credential.helper":
            applies = True
        else:
            applies = _scope_match(key[len("credential."):-len(".helper")], url)
        if applies is False:
            continue
        if value.strip():
            chain.append(value)
        elif applies is True:
            chain.clear()
    return chain


def _keychain_helper(chain) -> bool:
    return any("osxkeychain" in helper or helper.strip().split(" ", 1)[0] == "store"
               for helper in chain)


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
    command, bodies = _split_heredocs(command)
    for quoted, readers, body in bodies:
        interpreters = {r for r in readers if r.startswith(_INTERPRETERS)}
        if not readers <= _DATA_READERS | interpreters:
            # A shell, eval, source, xargs or unknown reader may execute it.
            if _API.search(body) or command_reason(
                    body, cwd=cwd, helpers=helpers, platform=platform,
                    environment=environment, cwd_uncertain=cwd_uncertain):
                return REFUSAL
            continue
        if any(r.startswith("python") for r in interpreters) and _python_calls_keychain(body):
            return REFUSAL
        if interpreters - {r for r in interpreters if r.startswith("python")} and _API.search(body):
            return REFUSAL
        if not quoted:
            for inner in _substitutions(body):
                if command_reason(inner, cwd=cwd, helpers=helpers, platform=platform,
                                  environment=environment, cwd_uncertain=cwd_uncertain):
                    return REFUSAL
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
                # One `git config` read (metadata only; no helper runs). Keys
                # arrive in git's own reading order: system, global, local, -c.
                try:
                    result = subprocess.run(
                        [argv[0], *prefix, "config", "--get-regexp",
                         r"^(credential(\..*)?\.helper|remote\..*\.(url|pushurl)|url\..*\.(insteadof|pushinsteadof))$"],
                        cwd=cwd, env=effective_environment, capture_output=True, text=True, timeout=2)
                except (OSError, subprocess.SubprocessError):
                    return REFUSAL
                if result.returncode not in (0, 1):
                    return REFUSAL
                pairs, urls = [], []
                for line in result.stdout.splitlines():
                    key, _, value = line.partition(" ")
                    lowered = key.lower()
                    if lowered.startswith("credential.") and lowered.endswith(".helper"):
                        pairs.append((key, value))
                    elif lowered.endswith((".url", ".pushurl")):
                        urls.append(value)
                    else:  # url.<base>.insteadof: a rewrite can target <base>
                        urls.append(key[len("url."):key.rfind(".")])
                urls += [arg for arg in argv[index + 1:] if "://" in arg]
                # Only http(s) remotes consult credential helpers (ssh and local
                # paths never do). With no remote at all, judge an unknown URL:
                # scoped helpers count, scoped resets don't.
                http = [url for url in urls if url.lower().startswith(("http://", "https://"))]
                urls = http if urls else [None]
                if any(_keychain_helper(_effective_helpers(pairs, url)) for url in urls):
                    return REFUSAL
                continue
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
