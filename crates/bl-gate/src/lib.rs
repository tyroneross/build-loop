// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! The permission policy interface of `bl run`, and one placeholder policy.
//!
//! A [`Policy`] answers one question about one tool call the agent asked
//! about. It sees only what the agent chose to ask: a call the agent runs
//! without asking never reaches it.
//!
//! [`Slice1Experimental`] is **not a safety gate**. It exists so the slice
//! can measure whether requests arrive and how long a decision takes. Its
//! shell-command reading is a word scan that a determined command defeats
//! (variables, encodings, interpreters it does not list).

use serde_json::Value;
use std::path::{Component, Path, PathBuf};

/// One tool call the agent asked about, in protocol-neutral terms.
pub struct Request<'a> {
    /// ACP tool kind (`read`, `edit`, `delete`, `move`, `search`, `execute`,
    /// `think`, `fetch`, `switch_mode`, `other`); empty when unknown.
    pub kind: &'a str,
    /// The agent's own tool name when it gave one (`Bash`, `Write`).
    pub tool: &'a str,
    /// The tool's raw input, as the agent sent it.
    pub input: &'a Value,
    /// Paths the agent says the call touches.
    pub locations: &'a [String],
    /// The project directory; mutations are allowed only inside it.
    pub project: &'a Path,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Decision {
    pub allow: bool,
    /// The rule that decided, for the run log.
    pub rule: &'static str,
}

const fn allow(rule: &'static str) -> Decision {
    Decision { allow: true, rule }
}

const fn refuse(rule: &'static str) -> Decision {
    Decision { allow: false, rule }
}

pub trait Policy {
    fn name(&self) -> &'static str;
    fn decide(&self, request: &Request) -> Decision;
}

/// The policy a profile names, if this build has it.
pub fn by_name(name: &str) -> Option<Box<dyn Policy>> {
    (name == Slice1Experimental.name()).then(|| Box::new(Slice1Experimental) as Box<dyn Policy>)
}

/// Placeholder: allow read-only kinds, allow mutations only inside the
/// project directory, refuse recursive deletes, network use and git remote
/// operations, and refuse anything it does not recognise: a shell command
/// is allowed only when every program in it is on a short list and no
/// argument names a path outside the project.
pub struct Slice1Experimental;

impl Policy for Slice1Experimental {
    fn name(&self) -> &'static str {
        "slice1-experimental"
    }

    fn decide(&self, r: &Request) -> Decision {
        match r.kind {
            "read" | "search" | "think" => allow("read-only-kind"),
            "edit" | "delete" | "move" => {
                let mut paths: Vec<&str> = r.locations.iter().map(String::as_str).collect();
                for key in ["file_path", "path", "notebook_path", "new_path", "old_path", "destination"] {
                    paths.extend(r.input.get(key).and_then(Value::as_str));
                }
                if paths.is_empty() {
                    refuse("mutation-without-path")
                } else if paths.iter().all(|p| inside(r.project, p)) {
                    allow("mutation-inside-project")
                } else {
                    refuse("mutation-outside-project")
                }
            }
            "execute" => match r.input.get("command").and_then(Value::as_str) {
                Some(command) => command_decision(command, r.project),
                None => refuse("execute-without-command"),
            },
            "fetch" => refuse("network"),
            _ if matches!(r.tool, "Agent" | "Task") => allow("delegate"),
            _ => refuse("unrecognised-kind"),
        }
    }
}

/// Symlinks followed while resolving one path, at most.
const LINK_DEPTH: u32 = 32;

/// Resolve `path` from `base` (already resolved) one component at a time:
/// a symlink is followed before the next component is applied, so `..`
/// after a link leaves the link's target, as the kernel does. Components
/// that do not exist are kept as written. `None` when the path cannot be
/// resolved with certainty (a link loop, an unreadable directory).
fn resolve(base: &Path, path: &Path, depth: u32) -> Option<PathBuf> {
    let mut at = if path.is_absolute() { PathBuf::from("/") } else { base.to_path_buf() };
    for part in path.components() {
        match part {
            Component::RootDir | Component::Prefix(_) => at = PathBuf::from("/"),
            Component::CurDir => {}
            Component::ParentDir => {
                at.pop();
            }
            Component::Normal(name) => {
                let next = at.join(name);
                match std::fs::symlink_metadata(&next) {
                    Ok(meta) if meta.file_type().is_symlink() => {
                        let target = std::fs::read_link(&next).ok()?;
                        at = resolve(&at, &target, depth.checked_sub(1)?)?;
                    }
                    Ok(_) => at = next,
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => at = next,
                    Err(_) => return None,
                }
            }
        }
    }
    Some(at)
}

/// `path` (relative paths are relative to the project) is the project
/// directory or below it once every symlink is followed. A path that
/// cannot be resolved with certainty is outside.
pub fn inside(project: &Path, path: &str) -> bool {
    let Some(root) = resolve(Path::new("/"), project, LINK_DEPTH) else { return false };
    if !root.is_dir() {
        return false;
    }
    resolve(&root, Path::new(path), LINK_DEPTH).is_some_and(|resolved| resolved.starts_with(&root))
}

const NETWORK: [&str; 12] = ["curl", "wget", "nc", "ncat", "ssh", "scp", "sftp", "rsync", "telnet", "ftp", "http", "https"];
const GIT_REMOTE: [&str; 5] = ["fetch", "pull", "clone", "remote", "ls-remote"];
const OPAQUE: [&str; 12] = ["eval", "sh", "bash", "zsh", "python", "python3", "perl", "ruby", "node", "osascript", "xargs", "sudo"];
/// Programs that only read. None of them prints the environment.
const READ_ONLY: [&str; 19] = ["ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "echo", "stat", "file", "true", "test", "date", "diff", "sort", "uniq", "tree", "du"];
const GIT_READ_ONLY: [&str; 9] = ["status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "describe", "blame"];
/// The only programs allowed to change files. Anything not named here or
/// in the read-only lists is refused as `unrecognised-command`.
const MUTATING: [&str; 8] = ["rm", "rmdir", "mkdir", "touch", "mv", "cp", "sed", "cd"];
const GIT_MUTATING: [&str; 2] = ["add", "commit"];
/// git's global options that take a separate value.
const GIT_VALUE_OPTIONS: [&str; 7] = ["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix"];

/// What the word scan cannot read safely. No fuller shell parsing is
/// attempted in the placeholder: such a command is refused.
fn unparseable(command: &str) -> bool {
    let (mut single, mut double) = (false, false);
    for c in command.chars() {
        match c {
            '\'' if !double => single = !single,
            '"' if !single => double = !double,
            _ => {}
        }
    }
    single || double || command.contains('\\') || command.contains('`') || command.contains("$(") || command.contains("<<")
}

/// Split a command line into words, honouring quotes; shell operators
/// become separate `;` words so each simple command is judged on its own,
/// redirections become `>` / `<` words, and any `$` becomes a `$` word.
/// Only called on commands [`unparseable`] accepted.
fn words(command: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut word = String::new();
    let mut started = false;
    let mut quote: Option<char> = None;
    for c in command.chars() {
        let mut end_word = |out: &mut Vec<String>, started: &mut bool, mark: Option<&str>| {
            if *started {
                out.push(std::mem::take(&mut word));
                *started = false;
            }
            out.extend(mark.map(str::to_string));
        };
        match (quote, c) {
            (Some(q), _) if c == q => quote = None,
            (Some('"'), '$') | (None, '$') => end_word(&mut out, &mut started, Some("$")),
            (Some(_), _) => word.push(c),
            (None, '\'') | (None, '"') => {
                quote = Some(c);
                started = true;
            }
            (None, ';') | (None, '|') | (None, '&') | (None, '\n') | (None, '(') | (None, ')') | (None, '{') | (None, '}') => end_word(&mut out, &mut started, Some(";")),
            (None, '>') => end_word(&mut out, &mut started, Some(">")),
            (None, '<') => end_word(&mut out, &mut started, Some("<")),
            (None, c) if c.is_whitespace() => end_word(&mut out, &mut started, None),
            (None, c) => {
                word.push(c);
                started = true;
            }
        }
    }
    if started {
        out.push(word);
    }
    out
}

/// The values in one argument that may name a path: the argument itself,
/// the value of `--flag=value`, and what follows a short flag (`-o/x`).
fn path_candidates(arg: &str) -> Vec<&str> {
    if !arg.starts_with('-') {
        return vec![arg];
    }
    if let Some((_, value)) = arg.split_once('=') {
        return vec![value];
    }
    arg.find(['/', '~']).map(|at| vec![&arg[at..]]).unwrap_or_default()
}

fn command_decision(command: &str, project: &Path) -> Decision {
    if unparseable(command) {
        return refuse("unparseable-command");
    }
    let all = words(command);
    if all.iter().any(|w| w == "$") {
        return refuse("opaque-command");
    }
    let mut mutating = false;
    for simple in all.split(|w| w == ";").filter(|s| !s.is_empty()) {
        // Leading `VAR=value` assignments are skipped; wrappers such as
        // `env`, `nice` or `command` are programs like any other.
        let assignments = simple.iter().take_while(|w| w.contains('=') && !w.starts_with('=') && !w.contains('/')).count();
        let rest = &simple[assignments..];
        let Some(program) = rest.first().map(String::as_str) else { continue };
        let args = &rest[1..];
        let flag = |short: char, long: &str| args.iter().any(|a| a == long || (a.starts_with('-') && !a.starts_with("--") && a.contains(short)));
        // git's subcommand: the first word after its global options.
        let mut sub = "";
        let mut skip = false;
        for a in args {
            if std::mem::take(&mut skip) {
                continue;
            }
            if a.starts_with('-') {
                skip = GIT_VALUE_OPTIONS.contains(&a.as_str());
                continue;
            }
            sub = a;
            break;
        }

        if OPAQUE.contains(&program) {
            return refuse("opaque-command");
        }
        if NETWORK.contains(&program) || rest.iter().any(|w| w.contains("://")) {
            return refuse("network");
        }
        if program == "git" && (GIT_REMOTE.contains(&sub) || args.iter().any(|a| a == "push")) {
            return refuse("git-remote");
        }
        if matches!(program, "rm" | "rmdir") && (flag('r', "--recursive") || flag('R', "--recursive")) {
            return refuse("recursive-delete");
        }
        if program == "find" && args.iter().any(|a| matches!(a.as_str(), "-delete" | "-exec" | "-execdir" | "-ok")) {
            return refuse("opaque-command");
        }
        let reads = READ_ONLY.contains(&program) || program == "find" || (program == "git" && GIT_READ_ONLY.contains(&sub));
        let writes = MUTATING.contains(&program) || (program == "git" && GIT_MUTATING.contains(&sub));
        if !reads && !writes {
            // The default: what the policy does not recognise is refused.
            return refuse("unrecognised-command");
        }
        // Every command, reading or writing, may name no path outside the
        // project: a read-only program can still write (`sort -o`).
        let outside = args.iter().filter(|a| *a != ">" && *a != "<").flat_map(|a| path_candidates(a)).any(|value| value != "/dev/null" && (value.starts_with('~') || !inside(project, value)));
        if outside {
            return refuse("path-outside-project");
        }
        mutating |= writes || rest.iter().any(|w| w == ">");
    }
    if mutating {
        allow("mutation-inside-project")
    } else {
        allow("read-only-command")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn project() -> PathBuf {
        let d = std::env::temp_dir().join(format!("bl-gate-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn decide(kind: &str, input: Value, locations: &[&str]) -> Decision {
        let locations: Vec<String> = locations.iter().map(|s| s.to_string()).collect();
        let project = project();
        Slice1Experimental.decide(&Request { kind, tool: "", input: &input, locations: &locations, project: &project })
    }

    fn bash(command: &str) -> Decision {
        decide("execute", json!({"command": command}), &[])
    }

    #[test]
    fn read_only_kinds_are_allowed_anywhere() {
        assert_eq!(decide("read", json!({"file_path": "/etc/hosts"}), &["/etc/hosts"]), allow("read-only-kind"));
        assert_eq!(decide("search", json!({}), &[]), allow("read-only-kind"));
    }

    #[test]
    fn mutations_are_allowed_only_inside_the_project() {
        let p = project();
        let inside_path = p.join("src/new.txt");
        assert_eq!(decide("edit", json!({"file_path": inside_path}), &[]), allow("mutation-inside-project"));
        assert_eq!(decide("edit", json!({"file_path": "notes.md"}), &[]), allow("mutation-inside-project"));
        assert_eq!(decide("edit", json!({"file_path": p.join("../escape.txt")}), &[]), refuse("mutation-outside-project"));
        assert_eq!(decide("delete", json!({}), &["/tmp/elsewhere/x"]), refuse("mutation-outside-project"));
        assert_eq!(decide("edit", json!({}), &[]), refuse("mutation-without-path"));
    }

    #[test]
    fn symlinks_are_followed_before_dot_dot_and_dangling_links_are_outside() {
        let p = project();
        let _ = std::fs::remove_file(p.join("link-out"));
        let _ = std::fs::remove_file(p.join("dangling"));
        let _ = std::fs::remove_file(p.join("loop"));
        std::os::unix::fs::symlink("/usr", p.join("link-out")).unwrap();
        std::os::unix::fs::symlink("/nonexistent-outside/file", p.join("dangling")).unwrap();
        std::os::unix::fs::symlink("loop", p.join("loop")).unwrap();
        assert!(!inside(&p, "link-out/x"));
        // `/usr/..` is `/`, not the project.
        assert!(!inside(&p, "link-out/../f"));
        assert!(!inside(&p, "dangling"));
        assert!(!inside(&p, "loop/x"));
        assert!(inside(&p, "not-yet/created/x"));
        assert!(inside(&p, "sub/../x"));
        assert!(!inside(Path::new("/nonexistent-project"), "x"));
        // Arguments of a command are checked, not only tool paths.
        assert_eq!(bash("rm link-out/x"), refuse("path-outside-project"));
        assert_eq!(bash("touch link-out/../f"), refuse("path-outside-project"));
        assert_eq!(decide("edit", json!({"file_path": "dangling"}), &[]), refuse("mutation-outside-project"));
    }

    #[test]
    fn the_four_planted_classes_are_refused() {
        let other = std::env::temp_dir().join("bl-gate-other/out.txt");
        assert_eq!(bash("rm -rf planted_dir"), refuse("recursive-delete"));
        assert_eq!(bash("cd sub && rm -fr ."), refuse("recursive-delete"));
        assert_eq!(bash(&format!("echo hi > {}", other.display())), refuse("path-outside-project"));
        assert_eq!(bash(&format!("cp a.txt {}", other.display())), refuse("path-outside-project"));
        assert_eq!(decide("edit", json!({"file_path": other}), &[]), refuse("mutation-outside-project"));
        assert_eq!(bash("curl -s https://example.com"), refuse("network"));
        assert_eq!(decide("fetch", json!({"url": "https://example.com"}), &[]), refuse("network"));
        assert_eq!(bash("git push origin main"), refuse("git-remote"));
        assert_eq!(bash("git -C . push"), refuse("git-remote"));
    }

    #[test]
    fn commands_inside_the_project() {
        assert_eq!(bash("ls -la"), allow("read-only-command"));
        assert_eq!(bash("git status --short | head -5"), allow("read-only-command"));
        assert_eq!(bash("grep -rn needle ."), allow("read-only-command"));
        assert_eq!(bash("find . -name '*.md'"), allow("read-only-command"));
        assert_eq!(bash("git add -A && git commit -m 'add file; done'"), allow("mutation-inside-project"));
        assert_eq!(bash("rm planted.txt"), allow("mutation-inside-project"));
        assert_eq!(bash("sed -i '' 's/b/B/' b.txt"), allow("mutation-inside-project"));
        assert_eq!(bash("echo hi > note.txt"), allow("mutation-inside-project"));
        assert_eq!(bash("mkdir -p a/b && touch a/b/c 2>/dev/null"), allow("mutation-inside-project"));
    }

    /// Finding 1: the default is refusal.
    #[test]
    fn an_unrecognised_program_is_refused() {
        for command in ["dash -c 'rm -rf ~'", "security find-generic-password -s X -w", "nice curl example.com -o x", "npm publish", "env", "printenv", "command ls", "./script", "/bin/ls", "make install", "git stash", "FOO=1 whoami"] {
            assert_eq!(bash(command), refuse("unrecognised-command"), "{command}");
        }
        assert_eq!(bash("ls && npm publish"), refuse("unrecognised-command"));
    }

    /// Finding 3: read-only programs are path-checked too.
    #[test]
    fn a_read_only_program_may_not_name_an_outside_path() {
        for command in ["git diff --output=/abs/outside", "sort -o ~/.zshrc x", "find . -fprint /abs/x", "cat /etc/hosts", "sort -o/abs/x x", "ls ..", "head -n1 ../x"] {
            assert_eq!(bash(command), refuse("path-outside-project"), "{command}");
        }
        assert_eq!(bash("sort -o sorted.txt x"), allow("read-only-command"));
    }

    /// Finding 4: nothing the word scan cannot read is judged.
    #[test]
    fn a_command_the_scan_cannot_read_is_refused() {
        for command in ["echo \"\\\"\" && rm -rf sub \"\\\"\"", "echo a\\ b", "echo `id`", "echo $(id)", "cat <<EOF\nx\nEOF", "echo 'open", "echo \"open"] {
            assert_eq!(bash(command), refuse("unparseable-command"), "{command}");
        }
        assert_eq!(bash("rm \"$HOME/x\""), refuse("opaque-command"));
        assert_eq!(bash("echo $PATH"), refuse("opaque-command"));
    }

    /// Finding 9: git's global options do not hide the subcommand.
    #[test]
    fn git_push_is_refused_wherever_it_stands() {
        for command in ["git --git-dir .git push origin main", "git --git-dir=.git push", "git -c a=b -C . push", "git --no-pager push", "git --work-tree . fetch"] {
            assert_eq!(bash(command), refuse("git-remote"), "{command}");
        }
        assert_eq!(bash("git --no-pager -C . log --oneline"), allow("read-only-command"));
        assert_eq!(bash("git -C .. status"), refuse("path-outside-project"));
    }

    #[test]
    fn what_the_scan_cannot_read_is_refused() {
        assert_eq!(bash("bash -c 'rm -rf x'"), refuse("opaque-command"));
        assert_eq!(bash("touch ~/x"), refuse("path-outside-project"));
        assert_eq!(bash("find . -name '*.tmp' -delete"), refuse("opaque-command"));
        assert_eq!(decide("execute", json!({}), &[]), refuse("execute-without-command"));
        assert_eq!(decide("switch_mode", json!({}), &[]), refuse("unrecognised-kind"));
        assert_eq!(decide("", json!({}), &[]), refuse("unrecognised-kind"));
    }

    #[test]
    fn delegation_and_lookup_by_name() {
        let p = project();
        let d = Slice1Experimental.decide(&Request { kind: "other", tool: "Agent", input: &json!({}), locations: &[], project: &p });
        assert_eq!(d, allow("delegate"));
        assert_eq!(by_name("slice1-experimental").unwrap().name(), "slice1-experimental");
        assert!(by_name("strict").is_none());
    }
}
