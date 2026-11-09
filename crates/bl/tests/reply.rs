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
