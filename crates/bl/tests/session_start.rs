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
/// Git lookup hangs: the run deadline must still deliver the finished step and
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
    for name in ["python3", "perl", "git"] {
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

/// Trust does not consume the event; a host keeping stdin open must not hide it.
#[test]
fn stalled_event_input_preserves_independent_context() {
    let root = scratch("stdin-context");
    let bin = root.join("bin");
    std::fs::create_dir_all(root.join("scripts")).unwrap();
    std::fs::create_dir_all(&bin).unwrap();
    std::fs::write(root.join("scripts/codex_hook_trust_check.py"), "").unwrap();
    let python = bin.join("python3");
    std::fs::write(&python, "#!/bin/sh\necho 'independent trust context'\n").unwrap();
    std::fs::set_permissions(&python, std::fs::Permissions::from_mode(0o755)).unwrap();
    let mut child = bl(&root)
        .env("PATH", format!("{}:/usr/bin:/bin", bin.display()))
        .env("CLAUDE_PROJECT_DIR", &root)
        .env("BUILD_LOOP_SESSION_START_DEADLINE_MS", "600")
        .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
    let _keep_open = child.stdin.take();
    let out = child.wait_with_output().unwrap();
    assert_eq!(out.status.code(), Some(0));
    let value: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert!(value["hookSpecificOutput"]["additionalContext"].as_str().unwrap()
        .contains("independent trust context"));
}

#[test]
fn cancellation_kills_foreground_workers() {
    unsafe extern "C" { fn kill(pid: i32, sig: i32) -> i32; }
    for sig in [1, 2, 15] {
        let root = scratch(&format!("cancel-{sig}"));
        let bin = root.join("bin");
        std::fs::create_dir_all(root.join("scripts")).unwrap();
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(root.join("scripts/tool_state_paths.py"), "").unwrap();
        let pidfile = root.join("worker.pid");
        let python = bin.join("git");
        std::fs::write(&python, format!("#!/bin/sh\necho $$ > '{}'\nexec /bin/sleep 30\n", pidfile.display())).unwrap();
        std::fs::set_permissions(&python, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut child = bl(&root).env("PATH", format!("{}:/usr/bin:/bin", bin.display()))
            .env("CLAUDE_PROJECT_DIR", &root).stdin(Stdio::null()).stdout(Stdio::piped()).spawn().unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        // Shell redirection creates the file before echo writes its PID. Wait
        // for the completed record, not merely the directory entry, before
        // delivering the signal whose worker cleanup this test exercises.
        let worker: i32 = loop {
            match std::fs::read_to_string(&pidfile) {
                Ok(record) if record.ends_with('\n') => break record.trim().parse().unwrap(),
                Ok(_) => {},
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => {},
                Err(err) => panic!("cannot read worker PID: {err}"),
            }
            assert!(Instant::now() < deadline, "worker did not publish its PID within two seconds");
            std::thread::sleep(Duration::from_millis(5));
        };
        unsafe { kill(child.id() as i32, sig); }
        let stop = Instant::now() + Duration::from_secs(2);
        while child.try_wait().unwrap().is_none() && Instant::now() < stop { std::thread::sleep(Duration::from_millis(5)); }
        if child.try_wait().unwrap().is_none() {
            unsafe { kill(-worker, 9); }
            child.kill().unwrap();
            panic!("supervisor ignored signal {sig}");
        }
        let gone = Instant::now() + Duration::from_secs(1);
        while unsafe { kill(worker, 0) } == 0 && Instant::now() < gone { std::thread::sleep(Duration::from_millis(5)); }
        let alive = unsafe { kill(worker, 0) } == 0;
        if alive { unsafe { kill(-worker, 9); } }
        assert!(!alive, "worker survived signal {sig}");
    }
}

/// The native snapshot's Git calls obey the sub-step limit, independently
/// of the whole-run timer; completed trust context must still be delivered.
#[test]
fn tool_state_deadline_stops_git_and_preserves_other_context() {
    let root = scratch("tool-state-deadline");
    let bin = root.join("bin");
    std::fs::create_dir_all(root.join("scripts")).unwrap();
    std::fs::create_dir_all(&bin).unwrap();
    for name in ["tool_state_paths.py", "codex_hook_trust_check.py"] {
        std::fs::write(root.join("scripts").join(name), "").unwrap();
    }
    let pidfile = root.join("git.pid");
    std::fs::write(bin.join("git"), format!("#!/bin/sh\necho $$ > '{}'\nexec /bin/sleep 30\n", pidfile.display())).unwrap();
    std::fs::write(bin.join("python3"), "#!/bin/sh\necho 'independent trust context'\n").unwrap();
    for name in ["git", "python3"] {
        std::fs::set_permissions(bin.join(name), std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let started = Instant::now();
    let out = bl(&root).env("PATH", format!("{}:/usr/bin:/bin", bin.display()))
        .env("CLAUDE_PROJECT_DIR", &root)
        // Leave room for the fixture shell to start on a loaded host. This
        // remains below the three-second assertion and five-second run limit.
        .env("BUILD_LOOP_TOOL_STATE_DEADLINE_MS", "1000")
        .env("BUILD_LOOP_SESSION_START_DEADLINE_MS", "5000")
        .stdin(Stdio::null()).output().unwrap();
    assert_eq!(out.status.code(), Some(0));
    let value: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(value["hookSpecificOutput"]["additionalContext"], "independent trust context");
    assert!(started.elapsed() < Duration::from_secs(3));
    let pid = std::fs::read_to_string(pidfile).unwrap().trim().to_string();
    let alive = Command::new("/bin/kill").args(["-0", &pid]).stderr(Stdio::null()).status().unwrap().success();
    assert!(!alive, "native Git worker survived its sub-step deadline");
}
