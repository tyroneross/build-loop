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
from urllib.parse import urlsplit

REFUSAL = (
    "Keychain credential access is prohibited. Use the designated Secrets Vault "
    "metadata/injection interface or an already authorized service connection. "
    "Do not probe, unlock, migrate, retry authentication, or copy secrets into plaintext storage."
)
_API = re.compile(r"SecItem(?:CopyMatching|Add|Update|Delete)|SecKeychain\w*|keyring\.(?:get_password|set_password|delete_password)")
_NETWORK = {"push", "pull", "fetch", "clone", "ls-remote", "credential"}
# Shell reserved words that can precede a command in a segment.
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}",
             "fi", "done", "time", "case", "esac", "in"}
# Variables whose assignment changes which git config (and so which helper)
# a later git command reads.
_GIT_ENV = re.compile(r"^(GIT_CONFIG\w*|HOME|XDG_CONFIG_HOME)=")
# git subcommands that never contact a remote. Anything else is judged like a
# fetch against the repository's remotes (git lfs, remote update, aliases …).
_LOCAL_GIT = {
    "status", "log", "diff", "show", "add", "commit", "checkout", "switch", "branch",
    "merge", "rebase", "reset", "restore", "stash", "tag", "rev-parse", "rev-list",
    "worktree", "config", "ls-files", "cat-file", "grep", "blame", "cherry-pick",
    "cherry", "revert", "init", "mv", "rm", "describe", "shortlog", "reflog", "notes",
    "apply", "am", "format-patch", "merge-base", "merge-tree", "symbolic-ref",
    "update-ref", "for-each-ref", "show-ref", "ls-tree", "hash-object", "count-objects",
    "fsck", "gc", "prune", "clean", "bisect", "help", "version", "var", "check-ignore",
    "diff-tree", "diff-index", "diff-files", "name-rev", "range-diff", "whatchanged",
    "read-tree", "write-tree", "commit-tree", "update-index", "mktree", "mktag",
    "check-ref-format", "check-attr", "interpret-trailers", "column", "stripspace",
    "sparse-checkout", "maintenance", "repack", "pack-refs", "verify-commit",
    "verify-tag", "annotate", "difftool", "mergetool", "citool", "gui", "instaweb",
}
# The delimiter is one shell WORD; quoting ANY part of it (`'EO'F`, `\\EOF`)
# makes the body literal, and the delimiter is the word with quotes removed.
_HEREDOC = re.compile(r"<<(-?)[ \t]*((?:[^\s;&|()<>'\"\\]|\\.|'[^']*'|\"[^\"]*\")+)")


def _heredoc_word(word: str):
    """(delimiter, quoted) for a heredoc delimiter word."""
    quoted = any(char in word for char in "'\"\\")
    out, index = [], 0
    while index < len(word):
        char = word[index]
        if char == "\\" and index + 1 < len(word):
            out.append(word[index + 1])
            index += 2
        elif char in "'\"":
            end = word.find(char, index + 1)
            out.append(word[index + 1:end])
            index = end + 1
        else:
            out.append(char)
            index += 1
    return "".join(out), quoted


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


def _heredoc_operators(line: str):
    """Offsets of `<<` that the shell treats as heredoc operators: unquoted,
    not `<<<`, and before an unquoted `#` comment. A quoted "<<EOF" argument or
    a commented `# <<EOF` opens nothing, so the next lines stay commands."""
    # `stack` holds the quote state outside each open $( ): inside "$( … )"
    # the shell starts a fresh, unquoted context where heredocs are real.
    quote, index, stack = None, 0, []
    while index < len(line):
        char = line[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if quote != "'" and line.startswith("$(", index):
            stack.append(quote)
            quote = None
            index += 2
            continue
        if quote is None and char == ")" and stack:
            quote = stack.pop()
            index += 1
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t;&|("):
            return
        elif line.startswith("<<<", index):
            index += 3
            continue
        elif line.startswith("<<", index):
            yield index
            index += 2
            continue
        index += 1


def _split_heredocs(command: str):
    """Separate heredoc bodies from shell text. A body is data for its reader;
    only a shell or interpreter reader turns it into code (checked by caller)."""
    lines = command.split("\n")
    shell_lines, bodies, index = [], [], 0
    while index < len(lines):
        line = lines[index]
        shell_lines.append(line)
        index += 1
        if "<<" not in line:
            continue
        for start in _heredoc_operators(line):
            match = _HEREDOC.match(line, start)
            if match is None:
                continue
            strip_tabs = match.group(1) == "-"
            delimiter, quoted = _heredoc_word(match.group(2))
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


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _scope_match(pattern: str, url: str | None):
    """Does `credential.<pattern>.helper` apply to `url`? Mirrors git urlmatch.c:
    same scheme; same port after default-port normalisation; host labels equal
    or a WHOLE label `*` (a partial `git*` matches only itself). Returns None
    when it cannot be decided statically (unknown URL, user or path scopes)."""
    if url is None or "://" not in pattern:
        return None
    try:
        want, have = urlsplit(pattern), urlsplit(url)
        scheme = want.scheme.lower()
        want_port = want.port or _DEFAULT_PORTS.get(scheme)
        have_port = have.port or _DEFAULT_PORTS.get(have.scheme.lower())
    except ValueError:
        return None
    if scheme != have.scheme.lower() or want_port != have_port:
        return False
    want_labels = (want.hostname or "").lower().split(".")
    have_labels = (have.hostname or "").lower().split(".")
    if len(want_labels) != len(have_labels) or not all(
            w == "*" or w == h for w, h in zip(want_labels, have_labels)):
        return False
    if want.username or want.path not in {"", "/"}:
        return None
    return True


def _rewrite(url: str, rules: dict) -> str:
    """Apply git's longest-prefix url.<base>.insteadOf rewrite (remote.c):
    base + the unmatched remainder of the URL."""
    best = max((prefix for prefix in rules if url.startswith(prefix)), key=len, default=None)
    return rules[best] + url[len(best):] if best is not None else url


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
    """osxkeychain or store, in any spelling git accepts: `store`,
    `/path/git-credential-store`, `!git credential-store …` (a shell helper)."""
    for helper in chain:
        text = helper.strip()
        if "osxkeychain" in text or "credential-store" in text:
            return True
        words = text.lstrip("!").split()
        if words and Path(words[0]).name in {"store", "git-credential-store"}:
            return True
        if text.startswith("!"):
            # `!gh auth git-credential` is the owner-approved connection
            # (AGENTS.md, 2026-10-23); any other shell helper is judged as a
            # command, so `!security find-generic-password …` is refused.
            if len(words) >= 3 and Path(words[0]).name == "gh" and words[1:3] == ["auth", "git-credential"]:
                continue
            if command_reason(text[1:], platform="darwin", environment={}, helpers=[]):
                return True
    return False


_PROXY_ENV = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
_GIT_CONTEXT_KEYS = (r"^(credential(\..*)?\.helper|remote\..*\.(url|pushurl)"
                     r"|url\..*\.(insteadof|pushinsteadof)|http(\..*)?\.proxy"
                     r"|submodule\..*\.url|fetch\.recursesubmodules)$")


def _git_may_use_keychain(git, prefix, sub, rest, cwd, env) -> bool:
    """Could this git network command make git run a Keychain helper?

    Git asks helpers per CONTEXT (a URL), so judge every context the command
    can reach: the named remote(s) or URL arguments after insteadOf /
    pushInsteadOf rewriting, submodule URLs a fetch/pull may recurse into, and
    proxies. Anything that cannot be resolved statically (shell variables,
    `git credential` stdin, clone --config/--recurse-submodules, transport::
    syntax other than http(s), non-credential -c overrides, no URL at all) is
    an UNKNOWN context: unscoped helpers apply and scoped resets do not.
    Reads metadata with `git config` only; never runs a helper.
    """
    def read(args):
        try:
            result = subprocess.run([git, *prefix, *args], cwd=cwd, env=env,
                                    capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode in (0, 1) else None

    config = read(["config", "-z", "--get-regexp", _GIT_CONTEXT_KEYS])
    if config is None:
        return True
    pairs, remotes, rules, push_rules, proxies, submodules = [], {}, {}, {}, [], []
    recurse = ""
    # -z records: "key\nvalue\0" (key alone when the value is absent).
    for record in config.split("\0"):
        if not record:
            continue
        key, _, value = record.partition("\n")
        low = key.lower()
        if low.startswith("credential.") and low.endswith(".helper"):
            pairs.append((key, value))
        elif low.startswith("remote.") and low.endswith(".url"):
            remotes.setdefault(key[len("remote."):key.rfind(".")], {"url": [], "push": []})["url"].append(value)
        elif low.startswith("remote.") and low.endswith(".pushurl"):
            remotes.setdefault(key[len("remote."):key.rfind(".")], {"url": [], "push": []})["push"].append(value)
        elif low.startswith("url.") and low.endswith(".pushinsteadof"):
            push_rules[value] = key[len("url."):key.rfind(".")]
        elif low.startswith("url.") and low.endswith(".insteadof"):
            rules[value] = key[len("url."):key.rfind(".")]
        elif low.startswith("http") and low.endswith(".proxy"):
            proxies.append(value)
        elif low.startswith("submodule.") and low.endswith(".url"):
            submodules.append(value)
        elif low == "fetch.recursesubmodules":
            recurse = value.lower()

    unknown = sub == "credential"
    for position, arg in enumerate(prefix):
        if arg == "-c" and position + 1 < len(prefix):
            name = prefix[position + 1].split("=", 1)[0].lower()
            unknown |= not (name.startswith("credential.") and name.endswith(".helper"))
    targets, pending_config = [], False
    for arg in rest:
        clone_config = sub == "clone" and (arg.startswith("--config=") or (arg.startswith("-c") and arg != "-c"))
        if pending_config or clone_config:
            # clone --config k=v / -c k=v: applied to the new repo BEFORE it
            # fetches, so credential helpers set here join the chain last.
            if pending_config:
                value = arg
            elif arg.startswith("--config="):
                value = arg.split("=", 1)[1]
            else:
                value = arg[2:]
            pending_config = False
            name, _, helper = value.partition("=")
            if name.lower().startswith("credential.") and name.lower().endswith(".helper"):
                pairs.append(("credential.helper" if name.lower() == "credential.helper" else name, helper))
            else:
                unknown = True
            continue
        if sub == "clone" and arg in {"-c", "--config"}:
            pending_config = True
            continue
        if "$" in arg or "`" in arg:
            unknown = True
        elif arg.startswith("-"):
            low = arg.lower()
            if sub == "clone" and (low == "--recursive" or low.startswith("--recurse-submodules")):
                unknown = True
            if "=" in arg and ("://" in arg or "::" in arg):
                targets.append(arg.split("=", 1)[1])
        else:
            targets.append(arg)

    def remote_urls(entry):
        # git push uses pushurl when set; fetch, pull and ls-remote use url.
        return (entry["push"] or entry["url"]) if sub == "push" else entry["url"]

    urls, named = [], False
    for target in targets:
        if target in remotes:
            urls += remote_urls(remotes[target])
            named = True
        elif "://" in target or "::" in target or re.match(r"^[\w.-]+@[\w.-]+:", target):
            urls.append(target)
    if "--recurse-submodules-only" in rest:
        named = True  # submodule commands reach the submodule URLs, not the remotes
    if not named and sub != "clone":
        for entry in remotes.values():
            urls += remote_urls(entry)
    if (sub in {"fetch", "pull"} and "--no-recurse-submodules" not in rest
            and recurse not in {"false", "no"}):
        urls += submodules
        top = read(["rev-parse", "--show-toplevel"])
        gitmodules = Path(top.strip()) / ".gitmodules" if top and top.strip() else None
        if gitmodules is not None and gitmodules.exists():
            listed = read(["config", "-z", "-f", str(gitmodules), "--get-regexp", r"^submodule\..*\.url$"])
            if listed is None:
                unknown = True
            else:
                urls += [record.partition("\n")[2] for record in listed.split("\0") if record]
    proxies += [env[name] for name in _PROXY_ENV if env.get(name)]
    urls += [proxy if "://" in proxy else "http://" + proxy for proxy in proxies if proxy]
    unknown |= not urls

    contexts = []
    for url in urls:
        if url.startswith(("./", "../")):
            continue  # relative submodule URL: resolves onto the remote's own host
        if "::" in url.split("://", 1)[0]:
            transport, _, url = url.partition("::")
            if transport.lower() not in {"http", "https"}:
                unknown = True
                continue
        rewrites = {_rewrite(url, rules)}
        if sub == "push":
            rewrites.add(_rewrite(url, push_rules))
        for rewritten in rewrites:
            if rewritten.lower().startswith(("http://", "https://")):
                contexts.append(rewritten)
            elif "::" in rewritten.split("://", 1)[0]:
                unknown = True
    if unknown:
        contexts.append(None)
    return any(_keychain_helper(_effective_helpers(pairs, context)) for context in contexts)


def _piped_scripts(command: str):
    """Literal text that a pipeline feeds into a shell: `printf 'cmd' | sh`,
    `echo cmd | bash -s`. Only echo/printf producers are readable statically."""
    try:
        lexer = shlex.shlex(_strip_comments(command), posix=True, punctuation_chars=";&|()\n")
        lexer.commenters = ""
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return
    stage, previous = [], None
    for token in tokens + [";"]:
        if token in {"|", ";", "&&", "||", "&", "\n", "(", ")"} or (token and set(token) <= set(";&|()\n")):
            if stage:
                if previous and previous[0] in {"echo", "printf"} \
                        and Path(stage[0]).name in {"sh", "bash", "zsh", "dash", "ksh"} \
                        and not any(arg.startswith("-") and "c" in arg and not arg.startswith("--") for arg in stage[1:]):
                    text = " ".join(previous[1:])
                    yield text.replace("\\n", "\n").replace("%s", "")
                previous = stage if token == "|" else None
            stage = []
        else:
            stage.append(token)


_CONFIG_READS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list",
                 "get", "list", "--show-origin", "--show-scope", "--name-only"}
_CONFIG_FILES = re.compile(r"gitconfig|\.git/config|\.config/git/|\.gitmodules|config\.worktree")


def _writes_git_config(argv) -> bool:
    """Does this segment change git configuration or remotes?"""
    if any(_CONFIG_FILES.search(token) for token in argv
           if token.startswith((">", "1>", "2>")) or Path(argv[0]).name == "tee"):
        return True
    for position, token in enumerate(argv[:-1]):
        if token in {">", ">>", "1>", "1>>"} and _CONFIG_FILES.search(argv[position + 1]):
            return True
    if not argv or Path(argv[0]).name != "git":
        return False
    index = 1
    while index < len(argv) and argv[index].startswith("-"):
        index += 2 if argv[index] in {"-c", "-C", "--git-dir", "--work-tree"} else 1
    if index >= len(argv):
        return False
    sub, rest = argv[index], argv[index + 1:]
    if sub == "config":
        return not any(arg in _CONFIG_READS for arg in rest) and len([a for a in rest if not a.startswith("-")]) >= 2 \
            or any(arg in {"--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e", "set", "unset",
                           "--remove-section", "--rename-section"} for arg in rest)
    if sub == "remote":
        verb = next((a for a in rest if not a.startswith("-")), "")
        return verb in {"add", "set-url", "rename", "remove", "rm"}
    return sub in {"submodule"} and any(a in {"add", "set-url", "init", "sync"} for a in rest)


def _git_alias(git, prefix, name, cwd, env):
    """Value of alias.<name>, or None. Metadata read only."""
    try:
        result = subprocess.run([git, *prefix, "config", "--get", f"alias.{name}"], cwd=cwd, env=env,
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else None


def _strip_comments(command: str) -> str:
    """Drop unquoted `#` comments but keep the newline that ends each one.
    shlex's own comment handling eats that newline, which glued the next line's
    command onto the commented one (`echo hi # c` + newline + `security …`)."""
    out, quote, index, stack = [], None, 0, []
    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'":
            out.append(command[index:index + 2])
            index += 2
            continue
        if quote != "'" and command.startswith("$(", index):
            stack.append(quote)
            quote = None
            out.append("$(")
            index += 2
            continue
        if quote is None and char == ")" and stack:
            quote = stack.pop()
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or command[index - 1] in " \t\n;&|("):
            end = command.find("\n", index)
            index = len(command) if end == -1 else end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _segments(command: str):
    lexer = shlex.shlex(_strip_comments(command), posix=True, punctuation_chars=";&|()\n")
    lexer.commenters = ""
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    current = []
    for token in lexer:
        if token and set(token) <= set(";&|()\n"):  # separators, incl. newline runs
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
                if not backtick and source[end] in {"'", '"'}:
                    close = source.find(source[end], end + 1)
                    end = len(source) if close == -1 else close + 1
                    continue
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
    command = command.replace("\\\n", "")  # `git \<newline> fetch` is one command
    command, bodies = _split_heredocs(command)
    for quoted, readers, body in bodies:
        if not quoted:
            # The shell expands $( ) and backticks in an unquoted body BEFORE
            # any reader sees it, and quotes inside the body do not protect.
            for inner in _substitutions(body.replace("'", " ").replace('"', " ")):
                if command_reason(inner, cwd=cwd, helpers=helpers, platform=platform,
                                  environment=environment, cwd_uncertain=cwd_uncertain):
                    return REFUSAL
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
    try:
        # Newline is a separator token, so a `#` comment ends at its line
        # instead of swallowing every later command.
        segments = list(_segments(command))
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
    for script in _piped_scripts(command):
        if _API.search(script) or command_reason(
                script, cwd=cwd, helpers=helpers, platform=platform,
                environment=environment, cwd_uncertain=cwd_uncertain):
            return REFUSAL
    # `export GIT_CONFIG_COUNT=…` / `HOME=/x` on their own persist into later
    # commands, so any later git network command is judged as reconfigured.
    git_env_changed = any(_GIT_ENV.match(token) for argv in segments for token in argv
                          if argv and (Path(argv[0]).name in {"export", "declare", "typeset", "readonly", "local"}
                                       or all(re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", t) for t in argv)))
    git_env_changed = git_env_changed or any(_writes_git_config(argv) for argv in segments)
    for argv in segments:
        command_cwd_uncertain = cwd_uncertain
        effective_environment = dict(environment)
        inline_config = False
        while argv:
            if argv[0] in _KEYWORDS:
                argv = argv[1:]
                continue
            redirect = re.match(r"^(?:[0-9]+)?(>>?|<<?)(.*)$", argv[0])
            if redirect:
                argv.pop(0)
                if not redirect.group(2) and argv:
                    argv.pop(0)
                continue
            if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", argv[0]):
                name, value = argv[0].split("=", 1)
                effective_environment[name] = value
                inline_config |= bool(_GIT_ENV.match(argv[0]))
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
                if wrapper == "env" and (option in {"-S", "--split-string"} or option.startswith(("--split-string=", "-S"))):
                    # env -S 'cmd args' splits the string and runs it.
                    text = option.split("=", 1)[1] if option.startswith("--split-string=") else option[2:] if len(option) > 2 and option.startswith("-S") else (argv.pop(0) if argv else "")
                    try:
                        argv = shlex.split(text) + argv
                    except ValueError:
                        return REFUSAL
                    break
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
        if exe == "eval" and command_reason(" ".join(argv[1:]), cwd=cwd, helpers=helpers, platform=platform,
                                            environment=effective_environment, cwd_uncertain=command_cwd_uncertain):
            return REFUSAL
        if exe in {"bash", "sh", "zsh", "dash", "ksh"} and "<<<" in argv:
            herestring = argv[argv.index("<<<") + 1] if argv.index("<<<") + 1 < len(argv) else ""
            if command_reason(herestring, cwd=cwd, helpers=helpers, platform=platform,
                              environment=effective_environment, cwd_uncertain=command_cwd_uncertain):
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
        network = index < len(argv) and argv[index] not in _LOCAL_GIT and not argv[index].startswith("-")
        if network and argv[index] in {"remote", "submodule"}:
            # Only these forms contact a remote; `git remote -v`, `add`,
            # `submodule status` and the like stay local.
            verb = next((a for a in argv[index + 1:] if not a.startswith("-")), "")
            network = verb in ({"update", "prune", "show", "set-head"} if argv[index] == "remote"
                               else {"update", "sync", "add", "foreach", "init"})
        if network:
            # Dynamic config overrides cannot be proven safe by inspecting the
            # parent environment; require an explicit, inspectable Git -c form.
            if (command_cwd_uncertain or inline_config or git_env_changed
                    or any(a.startswith("--config-env") for a in prefix)):
                return REFUSAL
            if helpers is None:
                sub, rest = argv[index], argv[index + 1:]
                if sub not in _NETWORK:
                    alias = _git_alias(argv[0], prefix, sub, cwd, effective_environment)
                    if alias is not None:
                        # A shell alias (`!…`) runs a command; any other alias
                        # names a subcommand that may reach any remote.
                        if alias.startswith("!") and command_reason(
                                alias[1:], cwd=cwd, helpers=helpers, platform=platform,
                                environment=effective_environment, cwd_uncertain=command_cwd_uncertain):
                            return REFUSAL
                        sub, rest = "credential", []
                    elif sub == "submodule":
                        sub, rest = "fetch", ["--recurse-submodules-only"]
                    else:
                        # remote update, lfs, …: judged like a fetch of the
                        # remotes they name (or all remotes).
                        sub, rest = "fetch", [arg for arg in rest[1:] if not arg.startswith("-")]
                if _git_may_use_keychain(argv[0], prefix, sub, rest, cwd, effective_environment):
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
