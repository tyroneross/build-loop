// SPDX-License-Identifier: Apache-2.0
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};
static NEXT: AtomicUsize = AtomicUsize::new(0);
struct Fixture(PathBuf);
impl Fixture {
    fn new(body: &str) -> Self {
        let root = std::env::temp_dir().join(format!("bl-prebash-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)));
        fs::create_dir_all(root.join("scripts/hooks")).unwrap();
        fs::create_dir_all(root.join("bin/native")).unwrap();
        fs::copy(env!("CARGO_BIN_EXE_bl"), root.join("bin/native/bl")).unwrap();
        fs::write(root.join("scripts/hooks/pre_bash_dispatch.sh"), format!("#!/bin/bash\n{body}\n")).unwrap();
        Self(root)
    }
    fn command(&self) -> Command {
        let mut cmd = Command::new(self.0.join("bin/native/bl"));
        cmd.args(["hook", "pre-bash"]).env_clear().env("PATH", "/usr/bin:/bin")
            .env("HOME", &self.0).env("BUILD_LOOP_BASH_DEADLINE_MS", "300")
            .current_dir(&self.0).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        cmd
    }
}
impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
fn deny(out: &[u8]) {
    let value: serde_json::Value = serde_json::from_slice(out).unwrap();
    assert_eq!(value["hookSpecificOutput"]["permissionDecision"], "deny");
    assert!(value["hookSpecificOutput"]["permissionDecisionReason"].as_str().unwrap().contains("Retry the command"));
}
extern "C" { fn kill(pid: i32, sig: i32) -> i32; }
fn dead(pid: i32) {
    let until = Instant::now() + Duration::from_secs(2);
    while unsafe { kill(pid, 0) } == 0 && Instant::now() < until { std::thread::sleep(Duration::from_millis(10)); }
    assert_ne!(unsafe { kill(pid, 0) }, 0, "surviving descendant {pid}");
}

#[test]
fn preserves_object_bytes_exit_two_stdin_environment_and_marker() {
    let fixture = Fixture::new(r#"[ "$BUILD_LOOP_BOUNDED_HOOK_CHILD" = "$0" ] || exit 8
[ "$BUILD_LOOP_RALLY_START_PARENT_PID" = 4321 ] || exit 9
[ "$FIXTURE_VALUE" = unchanged ] || exit 10
[ -z "${BUILD_LOOP_PRE_BASH_PARENT_PID:-}" ] || exit 11
input=$(cat)
printf '%s' "$input"
exit 2"#);
    let mut child = fixture.command().env("BUILD_LOOP_PRE_BASH_PARENT_PID", "4321")
        .env("FIXTURE_VALUE", "unchanged").stdin(Stdio::piped()).spawn().unwrap();
    let expected = br#"{ "hookSpecificOutput": {"permissionDecision":"deny"}, "systemMessage":"policy wins" }"#;
    child.stdin.take().unwrap().write_all(expected).unwrap();
    let output = child.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(output.stdout, expected);
    assert!(output.stderr.is_empty());
}

#[test]
fn incomplete_invalid_or_failed_results_deny_and_preserve_exit_two() {
    for (body, rc) in [("printf '[]'", 0), ("printf nope; exit 2", 2),
                       ("printf '{}'; exit 1", 0), ("exit 0", 0),
                       ("printf '{'; head -c 65536 /dev/zero", 0)] {
        let fixture = Fixture::new(body);
        let output = fixture.command().output().unwrap();
        assert_eq!(output.status.code(), Some(rc));
        deny(&output.stdout);
    }
}

#[test]
fn stalled_stdin_and_stdout_eof_are_both_bounded() {
    for body in ["cat >/dev/null; printf '{}'", "printf '{}'; exec sleep 30", "(sleep 30) & exit 0"] {
        let fixture = Fixture::new(body);
        let started = Instant::now();
        let mut child = fixture.command().stdin(Stdio::piped()).spawn().unwrap();
        let keep_open = child.stdin.take();
        let output = child.wait_with_output().unwrap();
        drop(keep_open);
        assert_eq!(output.status.code(), Some(0));
        deny(&output.stdout);
        assert!(started.elapsed() < Duration::from_secs(2));
    }
}

#[test]
fn timeout_kills_term_resistant_descendant_even_after_leader_exits() {
    let fixture = Fixture::new(r#"(trap '' TERM; exec sleep 30) &
echo $! > grandchild.pid
printf '{}'
exit 0"#);
    let output = fixture.command().output().unwrap();
    deny(&output.stdout);
    let pid: i32 = fs::read_to_string(fixture.0.join("grandchild.pid")).unwrap().trim().parse().unwrap();
    dead(pid);
}

#[test]
fn cancellation_is_silent_and_cleans_spawned_group() {
    let fixture = Fixture::new(r#"trap '' TERM
(trap '' TERM; exec sleep 30) &
echo $! > grandchild.pid
wait"#);
    let child = fixture.command().env("BUILD_LOOP_BASH_DEADLINE_MS", "5000").spawn().unwrap();
    let until = Instant::now() + Duration::from_secs(2);
    while fs::read_to_string(fixture.0.join("grandchild.pid")).ok().and_then(|p| p.trim().parse::<i32>().ok()).is_none() && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(1));
    }
    let pid: i32 = fs::read_to_string(fixture.0.join("grandchild.pid")).unwrap().trim().parse().unwrap();
    unsafe { kill(child.id() as i32, 15); }
    let output = child.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(0));
    assert!(output.stdout.is_empty());
    dead(pid);
}

#[test]
fn spawn_failure_denies_and_unknown_arguments_do_not_read_stdin() {
    let fixture = Fixture::new("printf '{}'");
    let output = fixture.command().env("BUILD_LOOP_PRE_BASH_SHELL", "/no/such/bash").output().unwrap();
    deny(&output.stdout);
    let mut child = fixture.command().arg("unexpected").stdin(Stdio::piped()).spawn().unwrap();
    let keep_open = child.stdin.take();
    let output = child.wait_with_output().unwrap();
    drop(keep_open);
    assert_eq!(output.status.code(), Some(75));
    assert!(output.stdout.is_empty());
}


#[test]
fn cancellation_during_startup_never_leaves_a_started_worker() {
    for _ in 0..12 {
        let fixture = Fixture::new("echo $$ > worker.pid; exec sleep 30");
        let child = fixture.command().env("BUILD_LOOP_BASH_DEADLINE_MS", "5000").spawn().unwrap();
        std::thread::sleep(Duration::from_millis(1));
        unsafe { kill(child.id() as i32, 15); }
        let output = child.wait_with_output().unwrap();
        assert!(output.stdout.is_empty());
        if let Some(pid) = fs::read_to_string(fixture.0.join("worker.pid")).ok().and_then(|p| p.trim().parse().ok()) {
            dead(pid);
        }
    }
}


#[test]
fn output_cap_distinguishes_valid_json_at_and_above_boundary() {
    for length in [65_536, 65_537] {
        let object = format!(r#"{{"x":"{}"}}"#, "x".repeat(length - 8));
        assert_eq!(object.len(), length);
        let fixture = Fixture::new(&format!("printf '%s' '{object}'"));
        let output = fixture.command().output().unwrap();
        assert_eq!(output.status.code(), Some(0));
        if length == 65_536 { assert_eq!(output.stdout, object.as_bytes()); }
        else { deny(&output.stdout); }
    }
}

#[test]
fn literal_private_keys_remain_objects() {
    for key in ["$serde_json::private::Number", "$serde_json::private::RawValue"] {
        let object = format!(r#"{{"{key}":"[]"}}"#);
        let fixture = Fixture::new(&format!("printf '%s' '{object}'"));
        let output = fixture.command().output().unwrap();
        assert_eq!(output.status.code(), Some(0));
        assert_eq!(output.stdout, object.as_bytes());
    }
}
