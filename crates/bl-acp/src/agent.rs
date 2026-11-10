// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! The agent subprocess: how it is launched and how it is ended.
//!
//! The agent runs in its own session (`setsid`), so the whole process group
//! can be killed in one call: adapters start further processes (the Claude
//! adapter starts the `claude` binary) that would otherwise outlive `bl`.
//! Because of the new session a terminal Ctrl-C does not reach the agent;
//! the caller must kill the group itself (see [`kill_group`]).

use crate::rpc::Conn;
use std::io::{BufReader, Read};
use std::os::raw::c_int;
use std::os::unix::process::CommandExt;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

extern "C" {
    fn setsid() -> c_int;
    fn kill(pid: c_int, sig: c_int) -> c_int;
}

const SIGKILL: c_int = 9;
/// Bytes of the agent's stderr kept for the failure reason.
const STDERR_TAIL: usize = 4096;

/// How to start one agent. Data, so a profile can carry it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Launch {
    pub command: String,
    pub args: Vec<String>,
    /// Added to the inherited environment.
    pub env: Vec<(String, String)>,
    /// Removed from the inherited environment (names only; values are never read).
    pub env_remove: Vec<String>,
}

/// SIGKILL every process in the group led by `pid`. Async-signal-safe.
pub fn kill_group(pid: u32) {
    if pid > 1 {
        // SAFETY: kill has no memory-safety preconditions; a negative pid
        // addresses the process group.
        unsafe {
            kill(-(pid as c_int), SIGKILL);
        }
    }
}

pub struct Agent {
    child: Child,
    pub conn: Conn<BufReader<ChildStdout>, ChildStdin>,
    stderr: Arc<Mutex<Vec<u8>>>,
}

impl Agent {
    pub fn spawn(launch: &Launch) -> std::io::Result<Agent> {
        let mut cmd = Command::new(&launch.command);
        for name in &launch.env_remove {
            cmd.env_remove(name);
        }
        cmd.args(&launch.args)
            .envs(launch.env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        // SAFETY: setsid is async-signal-safe and is the only call made
        // between fork and exec.
        unsafe {
            cmd.pre_exec(|| {
                if setsid() < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let mut child = cmd.spawn()?;
        let stdin = child.stdin.take().expect("piped stdin");
        let stdout = child.stdout.take().expect("piped stdout");
        let mut err = child.stderr.take().expect("piped stderr");
        let stderr = Arc::new(Mutex::new(Vec::new()));
        let tail = Arc::clone(&stderr);
        // Drain stderr so a chatty agent never blocks on a full pipe.
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            while let Ok(n) = err.read(&mut buf) {
                if n == 0 {
                    break;
                }
                if let Ok(mut t) = tail.lock() {
                    t.extend_from_slice(&buf[..n]);
                    let extra = t.len().saturating_sub(STDERR_TAIL);
                    t.drain(..extra);
                }
            }
        });
        Ok(Agent { child, conn: Conn::new(BufReader::new(stdout), stdin), stderr })
    }

    /// Also the process-group id.
    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    /// The last bytes the agent wrote to stderr.
    pub fn stderr_tail(&self) -> String {
        self.stderr.lock().map(|t| String::from_utf8_lossy(&t).into_owned()).unwrap_or_default()
    }

    /// End the agent and everything it started, and reap it.
    pub fn kill(&mut self) {
        kill_group(self.child.id());
        let _ = self.child.kill();
        let _ = self.child.wait();
    }

    /// Give the agent `grace` to exit on its own, then [`Agent::kill`].
    pub fn close(mut self, grace: Duration) {
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        self.kill();
    }
}

impl Drop for Agent {
    fn drop(&mut self) {
        self.kill();
    }
}
