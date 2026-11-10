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
/// operations, and refuse anything it does not recognise.
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

/// Lexically resolve `.` and `..` without touching the file system.
fn normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for part in path.components() {
        match part {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other),
        }
    }
    out
}

/// Resolve symlinks in the longest existing prefix, so a link out of the
/// project (or `/tmp` versus `/private/tmp`) is seen for what it is.
fn real(path: &Path) -> PathBuf {
    let path = normalize(path);
    let mut rest = Vec::new();
    let mut at = path.as_path();
    loop {
        if let Ok(resolved) = at.canonicalize() {
            return rest.iter().rev().fold(resolved, |acc, part| acc.join(part));
        }
        match (at.parent(), at.file_name()) {
            (Some(parent), Some(name)) => {
                rest.push(name.to_os_string());
                at = parent;
            }
            _ => return path,
        }
    }
}

/// `path` (relative paths are relative to the project) is the project
/// directory or below it.
pub fn inside(project: &Path, path: &str) -> bool {
    let candidate = Path::new(path);
    let absolute = if candidate.is_absolute() { candidate.to_path_buf() } else { project.join(candidate) };
    real(&absolute).starts_with(real(project))
}

const NETWORK: [&str; 12] = ["curl", "wget", "nc", "ncat", "ssh", "scp", "sftp", "rsync", "telnet", "ftp", "http", "https"];
const GIT_REMOTE: [&str; 6] = ["push", "fetch", "pull", "clone", "remote", "ls-remote"];
const OPAQUE: [&str; 12] = ["eval", "sh", "bash", "zsh", "python", "python3", "perl", "ruby", "node", "osascript", "xargs", "sudo"];
const READ_ONLY: [&str; 20] = ["ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "echo", "stat", "file", "which", "true", "test", "date", "diff", "sort", "uniq", "tree", "du"];
const GIT_READ_ONLY: [&str; 9] = ["status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "describe", "blame"];

/// Split a command line into words, honouring quotes; shell operators and
/// substitution marks become separate `;` words so each simple command is
/// judged on its own. Redirections stay attached to their command as `>`.
fn words(command: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut word = String::new();
    let mut quote: Option<char> = None;
    let mut chars = command.chars().peekable();
    let flush = |word: &mut String, out: &mut Vec<String>| {
        if !word.is_empty() {
            out.push(std::mem::take(word));
        }
    };
    while let Some(c) = chars.next() {
        match (quote, c) {
            (Some(q), _) if c == q => quote = None,
            (Some('\''), _) => word.push(c),
            (Some(_), '$') | (Some(_), '`') | (None, '$') | (None, '`') => {
                // Substitution or expansion: what follows is a command or a
                // value this scan cannot know. Mark it.
                flush(&mut word, &mut out);
                out.push("$".into());
            }
            (Some(_), _) => word.push(c),
            (None, '\'') | (None, '"') => quote = Some(c),
            (None, '\\') => word.extend(chars.next()),
            (None, ';') | (None, '|') | (None, '&') | (None, '\n') | (None, '(') | (None, ')') | (None, '{') | (None, '}') => {
                flush(&mut word, &mut out);
                out.push(";".into());
            }
            (None, '>') | (None, '<') => {
                flush(&mut word, &mut out);
                out.push(c.to_string());
            }
            (None, c) if c.is_whitespace() => flush(&mut word, &mut out),
            (None, c) => word.push(c),
        }
    }
    flush(&mut word, &mut out);
    out
}

fn command_decision(command: &str, project: &Path) -> Decision {
    let all = words(command);
    if all.iter().any(|w| w == "$") {
        return refuse("opaque-command");
    }
    let mut mutating = false;
    for simple in all.split(|w| w == ";").filter(|s| !s.is_empty()) {
        // Skip leading `VAR=value` assignments and the wrappers that only
        // run another command.
        let mut rest = simple;
        while let Some(first) = rest.first() {
            let assignment = first.contains('=') && !first.starts_with('=') && !first.contains('/');
            if assignment || matches!(first.as_str(), "command" | "env" | "time" | "nohup" | "exec") {
                rest = &rest[1..];
            } else {
                break;
            }
        }
        let Some(program) = rest.first() else { continue };
        let program = program.rsplit('/').next().unwrap_or(program);
        let args = &rest[1..];
        let flag = |short: char, long: &str| args.iter().any(|a| a == long || (a.starts_with('-') && !a.starts_with("--") && a.contains(short)));
        // The subcommand: the first word that is neither a flag nor the
        // value of git's `-C <dir>` / `-c <key=value>`.
        let sub = args.iter().enumerate().find(|(i, a)| !a.starts_with('-') && !(*i > 0 && matches!(args[i - 1].as_str(), "-C" | "-c"))).map(|(_, a)| a.as_str()).unwrap_or("");

        if OPAQUE.contains(&program) {
            return refuse("opaque-command");
        }
        if NETWORK.contains(&program) || rest.iter().any(|w| w.contains("://")) {
            return refuse("network");
        }
        if program == "git" && GIT_REMOTE.contains(&sub) {
            return refuse("git-remote");
        }
        if matches!(program, "rm" | "rmdir") && (flag('r', "--recursive") || flag('R', "--recursive")) {
            return refuse("recursive-delete");
        }
        if program == "find" && args.iter().any(|a| matches!(a.as_str(), "-delete" | "-exec" | "-execdir" | "-ok")) {
            return refuse("opaque-command");
        }
        let redirects = rest.iter().any(|w| w == ">");
        let read_only = !redirects && (READ_ONLY.contains(&program) || program == "find" || (program == "git" && GIT_READ_ONLY.contains(&sub)));
        if read_only {
            continue;
        }
        mutating = true;
        // A mutating command may name no path outside the project.
        let outside = args.iter().filter(|a| *a != ">" && *a != "<").any(|a| {
            let value = a.rsplit_once('=').map(|(_, v)| v).unwrap_or(a);
            let pathlike = value.starts_with('/') || value.starts_with('~') || value.split('/').any(|part| part == "..");
            pathlike && value != "/dev/null" && (value.starts_with('~') || !inside(project, value))
        });
        if outside {
            return refuse("mutation-outside-project");
        }
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
    fn a_symlink_out_of_the_project_is_outside() {
        let p = project();
        let link = p.join("link-out");
        let _ = std::fs::remove_file(&link);
        std::os::unix::fs::symlink("/usr", &link).unwrap();
        assert!(!inside(&p, "link-out/x"));
        assert!(inside(&p, "not-yet/created/x"));
    }

    #[test]
    fn the_four_planted_classes_are_refused() {
        let other = std::env::temp_dir().join("bl-gate-other/out.txt");
        assert_eq!(bash("rm -rf planted_dir"), refuse("recursive-delete"));
        assert_eq!(bash("cd sub && rm -fr ."), refuse("recursive-delete"));
        assert_eq!(bash(&format!("echo hi > {}", other.display())), refuse("mutation-outside-project"));
        assert_eq!(bash(&format!("cp a.txt {}", other.display())), refuse("mutation-outside-project"));
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
        assert_eq!(bash("cat /etc/hosts"), allow("read-only-command"));
        assert_eq!(bash("git add -A && git commit -m 'add file; done'"), allow("mutation-inside-project"));
        assert_eq!(bash("rm planted.txt"), allow("mutation-inside-project"));
        assert_eq!(bash("echo hi > note.txt"), allow("mutation-inside-project"));
        assert_eq!(bash("mkdir -p a/b && touch a/b/c 2>/dev/null"), allow("mutation-inside-project"));
    }

    #[test]
    fn what_the_scan_cannot_read_is_refused() {
        assert_eq!(bash("bash -c 'rm -rf x'"), refuse("opaque-command"));
        assert_eq!(bash("echo $(curl example.com)"), refuse("opaque-command"));
        assert_eq!(bash("rm \"$HOME/x\""), refuse("opaque-command"));
        assert_eq!(bash("touch ~/x"), refuse("mutation-outside-project"));
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
