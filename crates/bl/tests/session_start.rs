// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl hook session-start` must always exit 0 with one JSON object, and
//! must not leave a child behind when its own deadline fires.

use std::io::Read;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("bl-session-start-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.canonicalize().unwrap()
}

fn bl(root: &Path) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_bl"));
    cmd.args(["hook", "session-start", "--root"]).arg(root);
    cmd
}

#[test]
fn unknown_arguments_exit_75() {
    for args in [vec!["hook", "session-start"], vec!["hook", "session-start", "--bogus", "--root", "/x"]] {
        let out = Command::new(env!("CARGO_BIN_EXE_bl")).args(&args).stdin(Stdio::null()).output().unwrap();
        assert_eq!(out.status.code(), Some(75), "{args:?}");
        assert!(out.stdout.is_empty());
    }
}

// BL_FORCE_PANIC exists in debug builds only.
#[cfg(debug_assertions)]
#[test]
fn forced_panic_prints_an_empty_object() {
    let root = scratch("panic");
    let out = bl(&root).env("BL_FORCE_PANIC", "1").stdin(Stdio::null()).output().unwrap();
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(out.stdout, b"{}");
}

#[test]
fn an_empty_plugin_root_prints_an_empty_object() {
    let root = scratch("empty");
    let out = bl(&root).env("CLAUDE_PROJECT_DIR", &root).stdin(Stdio::null()).output().unwrap();
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(out.stdout, b"{}");
}

#[test]
fn run_deadline_prints_an_empty_object_when_stdin_never_closes() {
    let root = scratch("stdin");
    let started = Instant::now();
    let mut child = bl(&root)
        .env("BUILD_LOOP_SESSION_START_DEADLINE_MS", "200")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let _keep_open = child.stdin.take();
    let status = child.wait().unwrap();
    let mut stdout = Vec::new();
    child.stdout.take().unwrap().read_to_end(&mut stdout).unwrap();
    assert_eq!(status.code(), Some(0));
    assert_eq!(stdout, b"{}");
    assert!(started.elapsed() >= Duration::from_millis(200));
}

/// A plugin root whose trust check prints at once and whose tool-state
/// worker hangs: the run deadline must still deliver the finished step and
/// kill the hung worker.
#[test]
fn run_deadline_keeps_finished_steps_and_kills_the_rest() {
    let root = scratch("partial");
    let bin = root.join("bin");
    std::fs::create_dir_all(root.join("scripts")).unwrap();
    std::fs::create_dir_all(&bin).unwrap();
    std::fs::write(root.join("scripts/tool_state_paths.py"), "").unwrap();
    std::fs::write(root.join("scripts/codex_hook_trust_check.py"), "").unwrap();
    let pidfile = root.join("hung.pid");
    let stub = format!(
        "#!/bin/sh\ncase \"$1\" in */codex_hook_trust_check.py) echo 'trust report';; \
         *) echo $$ > '{}'; exec /bin/sleep 30;; esac\n",
        pidfile.display()
    );
    for name in ["python3", "perl"] {
        std::fs::write(bin.join(name), &stub).unwrap();
        std::fs::set_permissions(bin.join(name), std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let out = bl(&root)
        .env("PATH", format!("{}:/usr/bin:/bin", bin.display()))
        .env("CLAUDE_PROJECT_DIR", &root)
        .env("BUILD_LOOP_SESSION_START_DEADLINE_MS", "600")
        .env_remove("BUILD_LOOP_RALLY_SESSION_START_FOREGROUND")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(
        String::from_utf8(out.stdout).unwrap(),
        "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": \"trust report\"}}\n"
    );
    let pid = std::fs::read_to_string(&pidfile).unwrap().trim().to_string();
    std::thread::sleep(Duration::from_millis(200));
    let alive = Command::new("/bin/kill").args(["-0", &pid]).stderr(Stdio::null()).status().unwrap().success();
    assert!(!alive, "hung worker {pid} survived the deadline");
}
