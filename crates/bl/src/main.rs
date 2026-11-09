// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl`: build-loop's native commands.
//!
//! `bl hook pre-edit` replaces the shell + Perl + Python chain behind
//! `hooks/pre-edit-rally-point.sh` for native Rally contexts. Contract:
//! - exit 0 for every outcome; stdout empty, except exactly `{}` when the
//!   whole-run deadline (`BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS`, 100..=1500,
//!   default 1500) expires or the process panics;
//! - any case it cannot mirror exactly is handed to the shell hook: `bl`
//!   execs `bash <plugin>/hooks/pre-edit-rally-point.sh` with
//!   `BUILD_LOOP_NATIVE_HOOKS=off` and the stdin bytes it already read;
//! - exit 75 ([`bl_contract::FALLBACK_EXIT`]) means it did nothing and read
//!   nothing (unknown arguments, or no plugin checkout around the binary);
//!   the launcher then runs the shell hook itself.
//!
//! The binary must live two directories below the plugin root
//! (`bin/native/bl` or `target/<profile>/bl`).

mod pre_edit;
mod sys;

use std::io::{Seek, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Held by whoever ends the process, so the deadline `{}` and a normal exit
/// never interleave.
static EXIT: Mutex<()> = Mutex::new(());

fn emit_fallback_reply_and_exit() -> ! {
    let mut out = std::io::stdout().lock();
    let _ = out.write_all(b"{}");
    let _ = out.flush();
    std::process::exit(0)
}

fn plugin_root() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    let root = exe.parent()?.parent()?.parent()?.to_path_buf();
    root.join("hooks/pre-edit-rally-point.sh").is_file().then_some(root)
}

/// Replace this process with the shell hook, feeding it `stdin` bytes.
///
/// The shell hook gets only the time this run has left, so `bl` plus the
/// hand-off stay inside one deadline. With less than the supervisor's 100 ms
/// minimum left, or with no way to pass the event on, reply `{}` instead:
/// the shell hook without its event could stamp the wrong repo or session.
fn exec_shell_hook(root: &std::path::Path, stdin: Option<Vec<u8>>, remaining_ms: u64) -> ! {
    if remaining_ms < bl_contract::DEADLINE_MIN_MS {
        emit_fallback_reply_and_exit();
    }
    let script = root.join("hooks/pre-edit-rally-point.sh");
    let mut cmd = Command::new("bash");
    cmd.arg(&script)
        .env("BUILD_LOOP_NATIVE_HOOKS", "off")
        .env("BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS", remaining_ms.to_string());
    if let Some(bytes) = stdin {
        match stdin_file(&bytes) {
            Some(file) => {
                cmd.stdin(Stdio::from(file));
            }
            None => emit_fallback_reply_and_exit(),
        }
    }
    let _ = cmd.exec();
    std::process::exit(0)
}

/// An unlinked temp file holding `bytes`, rewound for reading.
fn stdin_file(bytes: &[u8]) -> Option<std::fs::File> {
    let dir = std::env::temp_dir();
    for attempt in 0..8 {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let path = dir.join(format!(".bl-stdin-{}-{nanos}-{attempt}", std::process::id()));
        let Ok(mut file) = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
        else {
            continue;
        };
        let _ = std::fs::remove_file(&path);
        file.write_all(bytes).ok()?;
        file.rewind().ok()?;
        return Some(file);
    }
    None
}

fn main() {
    let started = Instant::now();
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args != ["hook", "pre-edit"] {
        std::process::exit(bl_contract::FALLBACK_EXIT);
    }
    let Some(root) = plugin_root() else {
        std::process::exit(bl_contract::FALLBACK_EXIT);
    };
    let budget = bl_contract::deadline_ms(
        std::env::var_os("BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS")
            .as_deref()
            .and_then(|v| v.to_str()),
    );
    std::thread::spawn(move || {
        let deadline = started + Duration::from_millis(budget);
        let wait = deadline.saturating_duration_since(Instant::now());
        std::thread::sleep(wait);
        let _guard = EXIT.lock();
        sys::kill_waited_children();
        emit_fallback_reply_and_exit();
    });
    let root_str = root.to_string_lossy().into_owned();
    let outcome = std::panic::catch_unwind(|| pre_edit::run(&root_str));
    let _guard = EXIT.lock();
    match outcome {
        Ok(pre_edit::Outcome::Done) => std::process::exit(0),
        Ok(pre_edit::Outcome::Fallback { reason, stdin }) => {
            if std::env::var_os("BL_TRACE_FALLBACK").is_some() {
                eprintln!("bl: fallback: {reason}");
            }
            let spent = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
            exec_shell_hook(&root, stdin, budget.saturating_sub(spent))
        }
        Err(_) => {
            sys::kill_waited_children();
            emit_fallback_reply_and_exit()
        }
    }
}
