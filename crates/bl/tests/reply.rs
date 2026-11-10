// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! A panic or an expired deadline must still print exactly `{}` and exit 0.

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn bl() -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_bl"));
    cmd.args(["hook", "pre-edit"]);
    cmd
}

// BL_FORCE_PANIC exists in debug builds only, so this test cannot run under --release.
#[cfg(debug_assertions)]
#[test]
fn forced_panic_replies_with_empty_object() {
    let out = bl()
        .env("BL_FORCE_PANIC", "1")
        .stdin(Stdio::null())
        .output()
        .expect("run bl");
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(out.stdout, b"{}");
    assert!(out.stderr.is_empty(), "{}", String::from_utf8_lossy(&out.stderr));
}

#[test]
fn deadline_replies_with_empty_object() {
    // stdin stays open, so the read blocks until the 150 ms deadline.
    let started = Instant::now();
    let mut child = bl()
        .env("BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS", "150")
        .env_remove("BUILD_LOOP_RALLY_DEBUG")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn bl");
    let _keep_open = child.stdin.take();
    let status = child.wait().expect("wait bl");
    let mut stdout = Vec::new();
    child.stdout.take().unwrap().read_to_end(&mut stdout).unwrap();
    assert_eq!(status.code(), Some(0));
    assert_eq!(stdout, b"{}");
    assert!(started.elapsed() >= Duration::from_millis(150));
}

#[test]
fn unknown_arguments_ask_for_the_shell_hook() {
    let out = Command::new(env!("CARGO_BIN_EXE_bl")).args(["hook", "other"]).output().unwrap();
    assert_eq!(out.status.code(), Some(75));
    assert!(out.stdout.is_empty());
}

#[test]
fn deadline_kills_a_hung_child_it_was_waiting_on() {
    // A `rally` whose usage probe never returns must not outlive the hook: the
    // Perl supervisor killed its whole group, and one leak per tool call adds up.
    use std::os::unix::fs::PermissionsExt;
    let dir = std::env::temp_dir().join(format!("bl-hung-probe-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("bin")).unwrap();
    let pid_file = dir.join("rally.pid");
    let fake = dir.join("bin/rally");
    std::fs::write(&fake, format!("#!/bin/sh\necho $$ > '{}'\nexec sleep 30\n", pid_file.display())).unwrap();
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();

    let out = bl()
        .env("PATH", format!("{}:/usr/bin:/bin", dir.join("bin").display()))
        .env("HOME", &dir)
        .env("CLAUDE_PROJECT_DIR", &dir)
        .env("BUILD_LOOP_RALLY_DISCOVERY_CACHE_DIR", dir.join("cache"))
        .env("BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS", "400")
        .env_remove("BUILD_LOOP_RALLY_DEBUG")
        .current_dir(&dir)
        .stdin(Stdio::null())
        .output()
        .expect("run bl");
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(out.stdout, b"{}", "the hung probe should run the hook into its deadline");

    let pid = std::fs::read_to_string(&pid_file).expect("the fake rally ran").trim().to_string();
    let mut alive = true;
    for _ in 0..20 {
        alive = Command::new("kill").args(["-0", &pid]).stderr(Stdio::null()).status().unwrap().success();
        if !alive {
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    if alive {
        let _ = Command::new("kill").args(["-9", &pid]).status();
    }
    let _ = std::fs::remove_dir_all(&dir);
    assert!(!alive, "rally child {pid} survived the hook's deadline");
}
