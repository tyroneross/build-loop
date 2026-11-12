// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl`: build-loop's native commands.
//!
//! `bl hook trace --workdir W --phase P` replaces
//! `python3 scripts/tool_trace.py --workdir W hook --phase P` (see
//! `trace.rs`). It exits 0 and prints nothing; cases it cannot mirror run
//! that Python command with the stdin bytes already read (through a pipe)
//! and exit with its status; exit 75 means unknown arguments or no plugin
//! checkout, nothing read.
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
//! `bl route` is the native model resolver (see `route_cmd.rs`).
//! `bl run` is the experimental agent driver (see `run.rs`).
//!
//! The binary must live two directories below the plugin root
//! (`bin/native/bl` or `target/<profile>/bl`).

mod cli;
mod pre_edit;
mod rally_restore;
mod profile;
mod route_cmd;
mod run;
mod session_start;
mod sys;
mod trace;
mod tool_state;

use std::io::{Seek, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

/// Held by whoever ends the process, so the deadline `{}` and a normal exit
/// never interleave.
pub(crate) static EXIT: Mutex<()> = Mutex::new(());
static STARTED: OnceLock<Instant> = OnceLock::new();

/// Opt-in metadata only: never format event bytes, paths, or panic payloads.
/// Default hook stdout/stderr and exit codes remain unchanged.
pub(crate) fn hook_diagnostic(hook: &'static str, reason: &'static str) {
    if std::env::var_os("BL_TRACE_EXIT").is_some() {
        let elapsed = STARTED.get().map(|t| t.elapsed().as_millis()).unwrap_or(0);
        let _ = writeln!(std::io::stderr().lock(),
            "bl: diagnostic: hook={hook} reason={reason} elapsed_ms={elapsed}");
    }
}


/// `bl hook trace` gives up silently after this long. The host kills the
/// Python hook at 1 s; this only bounds a native run that somehow outlives
/// the 1 s lock wait, and never fires inside the append (see `trace::append`).
const TRACE_DEADLINE: Duration = Duration::from_millis(3000);

fn emit_fallback_reply_and_exit() -> ! {
    let mut out = std::io::stdout().lock();
    let _ = out.write_all(b"{}");
    let _ = out.flush();
    std::process::exit(0)
}

fn plugin_root(marker: &str) -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    let root = exe.parent()?.parent()?.parent()?.to_path_buf();
    root.join(marker).is_file().then_some(root)
}

/// Replace this process with the shell hook, feeding it `stdin` bytes.
///
/// The shell hook gets only the time this run has left, so `bl` plus the
/// hand-off stay inside one deadline. With less than the supervisor's 100 ms
/// minimum left, or with no way to pass the event on, reply `{}` instead:
/// the shell hook without its event could stamp the wrong repo or session.
fn exec_shell_hook(root: &std::path::Path, stdin: Option<Vec<u8>>, remaining_ms: u64) -> ! {
    if remaining_ms < bl_contract::DEADLINE_MIN_MS {
        hook_diagnostic("pre-edit", "fallback_budget_exhausted");
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
            None => {
                hook_diagnostic("pre-edit", "stdin_transfer_failed");
                emit_fallback_reply_and_exit()
            },
        }
    }
    let _ = cmd.exec();
    hook_diagnostic("pre-edit", "fallback_exec_failed");
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
    let _ = STARTED.set(started);
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() >= 2 && args[0] == "hook" && args[1] == "trace" {
        trace_main(started, &args[2..]);
    }
    if args.first().map(String::as_str) == Some("run") {
        std::process::exit(run::main(started, &args[1..]));
    }
    if args.first().map(String::as_str) == Some("route") {
        std::process::exit(route_cmd::main(&args[1..]));
    }
    if args.len() >= 2 && args[0] == "hook" && args[1] == "session-start" {
        session_start::main(started, &args[2..]);
    }
    if args != ["hook", "pre-edit"] {
        std::process::exit(bl_contract::FALLBACK_EXIT);
    }
    let Some(root) = plugin_root("hooks/pre-edit-rally-point.sh") else {
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
        hook_diagnostic("pre-edit", "watchdog");
        sys::kill_waited_children();
        emit_fallback_reply_and_exit();
    });
    let root_str = root.to_string_lossy().into_owned();
    let outcome = std::panic::catch_unwind(|| pre_edit::run(&root_str));
    let _guard = EXIT.lock();
    match outcome {
        Ok(pre_edit::Outcome::Done) => {
            hook_diagnostic("pre-edit", "completed");
            std::process::exit(0)
        },
        Ok(pre_edit::Outcome::Fallback { reason, stdin }) => {
            if std::env::var_os("BL_TRACE_FALLBACK").is_some() {
                eprintln!("bl: fallback: {reason}");
            }
            hook_diagnostic("pre-edit", "fallback");
            let spent = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
            exec_shell_hook(&root, stdin, budget.saturating_sub(spent))
        }
        Err(_) => {
            hook_diagnostic("pre-edit", "panic");
            sys::kill_waited_children();
            emit_fallback_reply_and_exit()
        }
    }
}

fn trace_main(started: Instant, args: &[String]) -> ! {
    let Some(parsed) = trace::parse_args(args) else {
        std::process::exit(bl_contract::FALLBACK_EXIT);
    };
    let Some(root) = plugin_root("scripts/tool_trace.py") else {
        std::process::exit(bl_contract::FALLBACK_EXIT);
    };
    std::thread::spawn(move || {
        std::thread::sleep((started + TRACE_DEADLINE).saturating_duration_since(Instant::now()));
        let _guard = EXIT.lock();
        hook_diagnostic("trace", "watchdog");
        std::process::exit(0);
    });
    match std::panic::catch_unwind(|| trace::run(&parsed)) {
        Ok(trace::Outcome::Fallback { reason, stdin }) => {
            let _guard = EXIT.lock();
            if std::env::var_os("BL_TRACE_FALLBACK").is_some() {
                eprintln!("bl: fallback: {reason}");
            }
            hook_diagnostic("trace", "fallback");
            let mut cmd = Command::new("python3");
            cmd.arg(root.join("scripts/tool_trace.py"))
                .args(["--workdir", &parsed.workdir, "hook", "--phase", &parsed.phase]);
            let Some(bytes) = stdin else {
                let _ = cmd.exec(); // stdin unread: Python inherits it
                hook_diagnostic("trace", "fallback_exec_failed");
                std::process::exit(0)
            };
            // The bytes already read go to Python through a pipe (no temp
            // file, so an unwritable TMPDIR cannot lose the line). Python
            // reads stdin to EOF before anything else, so writing all of it
            // first cannot deadlock. The deadline thread waits on EXIT, held
            // here, so it never cuts Python short.
            let Ok(mut child) = cmd.stdin(Stdio::piped()).spawn() else {
                hook_diagnostic("trace", "fallback_spawn_failed");
                std::process::exit(0)
            };
            if let Some(mut pipe) = child.stdin.take() {
                if pipe.write_all(&bytes).is_err() {
                    hook_diagnostic("trace", "stdin_transfer_failed");
                }
            }
            let code = child.wait().ok().and_then(|s| s.code()).unwrap_or(1);
            hook_diagnostic("trace", "fallback_child_finished");
            std::process::exit(code)
        }
        Ok(trace::Outcome::Done) => {
            let _guard = EXIT.lock();
            hook_diagnostic("trace", "completed");
            std::process::exit(0)
        }
        // Python's main swallows every exception and exits 0.
        Err(_) => {
            let _guard = EXIT.lock();
            hook_diagnostic("trace", "panic");
            std::process::exit(0)
        }
    }
}
