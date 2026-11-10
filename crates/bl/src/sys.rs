// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! The few libc calls std does not expose. Declared directly to keep the
//! dependency list at serde_json + sha2.

use std::ffi::CString;
use std::os::raw::{c_char, c_int};

extern "C" {
    fn getuid() -> u32;
    fn setsid() -> c_int;
    fn isatty(fd: c_int) -> c_int;
    fn access(path: *const c_char, mode: c_int) -> c_int;
    fn kill(pid: c_int, sig: c_int) -> c_int;
    fn utimensat(fd: c_int, path: *const c_char, times: *const Timespec, flag: c_int) -> c_int;
    fn flock(fd: c_int, operation: c_int) -> c_int;
    fn signal(sig: c_int, handler: extern "C" fn(c_int)) -> usize;
    fn _exit(code: c_int) -> !;
}

#[repr(C)]
struct Timespec {
    tv_sec: i64,
    tv_nsec: i64,
}

#[cfg(target_os = "macos")]
const AT_FDCWD: c_int = -2;
#[cfg(not(target_os = "macos"))]
const AT_FDCWD: c_int = -100;

const X_OK: c_int = 1;
const LOCK_EX: c_int = 2;
const LOCK_NB: c_int = 4;

#[cfg(target_os = "macos")]
pub const O_NONBLOCK: i32 = 0x4;
#[cfg(not(target_os = "macos"))]
pub const O_NONBLOCK: i32 = 0o4000;
#[cfg(target_os = "macos")]
pub const EWOULDBLOCK: i32 = 35;
#[cfg(not(target_os = "macos"))]
pub const EWOULDBLOCK: i32 = 11;
const SIGKILL: c_int = 9;

pub fn uid() -> u32 {
    // SAFETY: getuid has no preconditions and cannot fail.
    unsafe { getuid() }
}

/// Start a new session in a freshly forked child (`start_new_session=True`).
pub fn new_session() -> std::io::Result<()> {
    // SAFETY: called between fork and exec; setsid is async-signal-safe.
    if unsafe { setsid() } < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

/// `fcntl.flock(fd, LOCK_EX | LOCK_NB)`, the primitive `atomic_io.LockedFile`
/// uses, so a Python writer and this process exclude each other.
pub fn flock_exclusive_nb(fd: c_int) -> std::io::Result<()> {
    // SAFETY: flock only operates on the descriptor, which the caller owns.
    if unsafe { flock(fd, LOCK_EX | LOCK_NB) } == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

pub fn stdin_is_tty() -> bool {
    // SAFETY: isatty only inspects the descriptor.
    unsafe { isatty(0) == 1 }
}

/// `os.access(path, os.X_OK)`.
pub fn executable(path: &str) -> bool {
    let Ok(c) = CString::new(path) else { return false };
    // SAFETY: `c` is a valid NUL-terminated string for the call's duration.
    unsafe { access(c.as_ptr(), X_OK) == 0 }
}

/// Children this process waits on (the rally probe, git). The whole-run
/// deadline kills them before exiting, as the Perl supervisor's group kill
/// did; without this a hung probe would outlive every hook call.
static WAITED_CHILDREN: std::sync::Mutex<Vec<u32>> = std::sync::Mutex::new(Vec::new());

pub fn track_child(pid: u32) {
    if let Ok(mut live) = WAITED_CHILDREN.lock() {
        live.push(pid);
    }
}

pub fn untrack_child(pid: u32) {
    if let Ok(mut live) = WAITED_CHILDREN.lock() {
        live.retain(|p| *p != pid);
    }
}

pub fn kill_waited_children() {
    if let Ok(live) = WAITED_CHILDREN.lock() {
        for pid in live.iter() {
            kill_hard(*pid);
        }
    }
}

pub fn kill_hard(pid: u32) {
    // SAFETY: sending a signal has no memory-safety preconditions.
    unsafe {
        kill(pid as c_int, SIGKILL);
    }
}

/// `os.utime(path, None)` (`None`) or `os.utime(path, (t, t))`. Follows
/// symlinks. Returns false on any error.
pub fn set_times(path: &str, t: Option<f64>) -> bool {
    let Ok(c) = CString::new(path) else { return false };
    let spec = t.map(|t| {
        let sec = t.floor();
        let ts = Timespec { tv_sec: sec as i64, tv_nsec: ((t - sec) * 1e9) as i64 };
        [Timespec { tv_sec: ts.tv_sec, tv_nsec: ts.tv_nsec }, ts]
    });
    let ptr = spec.as_ref().map(|s| s.as_ptr()).unwrap_or(std::ptr::null());
    // SAFETY: `c` and `spec` outlive the call; a null `times` means "now".
    unsafe { utimensat(AT_FDCWD, c.as_ptr(), ptr, 0) == 0 }
}

/// The process group `bl run` must not leave behind (0: none). The agent is
/// started in its own session, so a terminal Ctrl-C never reaches it.
static AGENT_GROUP: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

/// Register (or with 0, clear) the agent's process group.
pub fn kill_group_on_exit(pid: u32) {
    AGENT_GROUP.store(pid, std::sync::atomic::Ordering::SeqCst);
}

/// SIGKILL the registered agent group, if any.
pub fn kill_registered_group() {
    let pid = AGENT_GROUP.load(std::sync::atomic::Ordering::SeqCst);
    if pid > 1 {
        // SAFETY: sending a signal has no memory-safety preconditions; a
        // negative pid addresses the process group.
        unsafe {
            kill(-(pid as c_int), SIGKILL);
        }
    }
}

extern "C" fn on_signal(sig: c_int) {
    // Only async-signal-safe calls: an atomic load, kill, _exit.
    kill_registered_group();
    // SAFETY: _exit is async-signal-safe and does not return.
    unsafe { _exit(128 + sig) }
}

/// On SIGHUP, SIGINT or SIGTERM: kill the agent group, exit 128 + signal.
pub fn kill_group_on_signals() {
    for sig in [1, 2, 15] {
        // SAFETY: `on_signal` only makes async-signal-safe calls.
        unsafe {
            signal(sig, on_signal);
        }
    }
}
