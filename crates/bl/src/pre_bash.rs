// SPDX-License-Identifier: Apache-2.0
//! Bounded orchestration only. The existing dispatcher owns every Bash policy.
use crate::sys;
use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::time::{Duration, Instant};

const MAX_OUT: usize = 65_536;
const DENY: &[u8] = br#"{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Build Loop checks did not finish. Retry the command so its required checks can run."}}"#;
static CANCELLED: AtomicBool = AtomicBool::new(false);
extern "C" {
    fn signal(sig: i32, handler: usize) -> usize;
    fn kill(pid: i32, sig: i32) -> i32;
    fn getppid() -> i32;
}
extern "C" fn cancel(_: i32) { CANCELLED.store(true, Ordering::SeqCst); }

/// Own cleanup from spawn through exit and EOF. Cancellation handlers never
/// inspect this owner, so a signal during spawn cannot miss registration.
struct Group { child: Child, status: Option<ExitStatus>, finished: bool }
impl Group {
    fn signal(&self, sig: i32) {
        // SAFETY: these calls only send a signal to our child's group/PID.
        unsafe {
            kill(-(self.child.id() as i32), sig);
            if self.status.is_none() { kill(self.child.id() as i32, sig); }
        }
    }
    fn poll(&mut self) {
        if self.status.is_none() { self.status = self.child.try_wait().ok().flatten(); }
    }
}
impl Drop for Group {
    fn drop(&mut self) {
        if self.finished { return; }
        self.signal(15);
        let until = Instant::now() + Duration::from_millis(50);
        while Instant::now() < until {
            self.poll();
            std::thread::sleep(Duration::from_millis(5));
        }
        self.signal(9);
        let until = Instant::now() + Duration::from_millis(20);
        while self.status.is_none() && Instant::now() < until {
            self.poll();
            std::thread::sleep(Duration::from_millis(1));
        }
    }
}

fn budget() -> Duration {
    let value = std::env::var("BUILD_LOOP_BASH_DEADLINE_MS").ok();
    let ms = value.filter(|v| !v.is_empty() && v.bytes().all(|c| c.is_ascii_digit()))
        .and_then(|v| v.parse::<u64>().ok()).filter(|v| (100..=18_000).contains(v))
        .unwrap_or(18_000);
    Duration::from_millis(ms)
}

fn supervise(started: Instant, root: &Path) -> (Vec<u8>, i32) {
    let until = started + budget();
    let script = root.join("scripts/hooks/pre_bash_dispatch.sh");
    let shell = std::env::var_os("BUILD_LOOP_PRE_BASH_SHELL").unwrap_or_else(|| "/bin/bash".into());
    let parent = std::env::var("BUILD_LOOP_PRE_BASH_PARENT_PID").ok()
        .filter(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
        .unwrap_or_else(|| unsafe { getppid() }.to_string());
    let mut command = Command::new(shell);
    command.arg(&script).stdin(Stdio::inherit()).stdout(Stdio::piped()).stderr(Stdio::inherit())
        .env("BUILD_LOOP_BOUNDED_HOOK_CHILD", &script)
        .env("BUILD_LOOP_RALLY_START_PARENT_PID", parent)
        .env_remove("BUILD_LOOP_PRE_BASH_SHELL").env_remove("BUILD_LOOP_PRE_BASH_PARENT_PID");
    // SAFETY: only the async-signal-safe setsid runs between fork and exec.
    unsafe { command.pre_exec(sys::new_session); }
    if CANCELLED.load(Ordering::SeqCst) || Instant::now() >= until { return (DENY.to_vec(), 0); }
    let Ok(child) = command.spawn() else { return (DENY.to_vec(), 0); };
    let mut group = Group { child, status: None, finished: false };
    let stdout = group.child.stdout.take().expect("piped stdout");
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut out = Vec::new();
        let result = stdout.take(MAX_OUT as u64 + 1).read_to_end(&mut out);
        let _ = tx.send(result.map(|_| out));
    });
    let mut output = None;
    while !CANCELLED.load(Ordering::SeqCst) && Instant::now() < until {
        if output.is_none() {
            match rx.try_recv() {
                Ok(Ok(out)) if out.len() <= MAX_OUT => output = Some(out),
                Ok(_) | Err(mpsc::TryRecvError::Disconnected) => return (DENY.to_vec(), 0),
                Err(mpsc::TryRecvError::Empty) => (),
            }
        }
        group.poll();
        if let (Some(status), Some(out)) = (group.status, output.as_ref()) {
            group.finished = true;
            let code = status.code();
            if code != Some(0) && code != Some(2) { return (DENY.to_vec(), 0); }
            let valid = serde_json::from_slice::<serde_json::Value>(out)
                .map(|v| v.is_object()).unwrap_or(false);
            return (if valid { out.clone() } else { DENY.to_vec() }, if code == Some(2) { 2 } else { 0 });
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    (DENY.to_vec(), 0)
}

pub fn main(started: Instant, root: &Path) -> ! {
    // Installed before spawning: cleanup sees cancellation even during spawn.
    unsafe { for sig in [1, 2, 15] { signal(sig, cancel as *const () as usize); } }
    let (out, code) = std::panic::catch_unwind(|| supervise(started, root))
        .unwrap_or_else(|_| (DENY.to_vec(), 0));
    // The host cancelled this invocation; preserve the Perl supervisor silence.
    if !CANCELLED.load(Ordering::SeqCst) {
        let mut stdout = std::io::stdout().lock();
        let _ = stdout.write_all(&out);
        let _ = stdout.flush();
    }
    std::process::exit(code)
}
