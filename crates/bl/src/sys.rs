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
