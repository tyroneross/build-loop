// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl hook session-start --root <plugin root>`: every SessionStart sub-step
//! in one process.
//!
//! It replaces the shell, Perl-supervisor and helper-Python layers of the
//! five SessionStart hooks (`hooks/session-start-tool-state.sh`,
//! `session-start-architecture.sh`, `session-start-rally-point.sh`,
//! `closeout.sh session-start`, `session-start-codex-hook-trust.sh`). The
//! tool-state snapshot is native. Three Python workers still run:
//! `rally_point/hooks.py session-start-safe`,
//! `stop_closeout.py` and `codex_hook_trust_check.py`.
//!
//! Contract:
//! - exit 0 for every outcome, 75 only for unknown arguments;
//! - stdout is ONE JSON object: `{}` or `hookSpecificOutput.additionalContext`
//!   holding each sub-step's context in hook order, joined by a blank line.
//!   A host reads a hook's stdout as JSON only when it is one object, so the
//!   five separate outputs cannot simply be concatenated. The rule is
//!   `merge` here and in `scripts/session_start_hook_command.py`; the parity
//!   suite compares the two byte for byte;
//! - each sub-step keeps its own deadline (the Perl supervisor's budget and
//!   environment override), measured from that sub-step's start. A sub-step
//!   that overruns is killed with its process group and contributes what the
//!   shell hook printed on a timeout (`{}`); the others are unaffected;
//! - tool-state runs first. Rally waits for it unless `.build-loop/` and
//!   `.rally/` both exist already, because only Rally can create one of the
//!   directories tool-state reports on. Closeout and the trust check never
//!   wait;
//! - a state it cannot mirror exactly (see `plan`) is decided before any
//!   side effect, and the event then goes to the five shell hooks, run by
//!   this process under their host timeouts and merged the same way;
//! - on a panic or the whole-run deadline it kills its children, prints the
//!   sub-step outputs that are complete, and exits 0.

use bl_contract::{identity, py, Fallback, R};
use serde_json::Value;
use std::io::{Read, Write};
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::pre_edit::{nonempty, var, which};
use crate::{sys, EXIT};

/// `_bounded_hook.sh`: a hook that prints more than this is cut.
const MAX_OUT: usize = 65_536;
/// Whole-run limit; below the registration's host timeout.
const RUN_DEADLINE_MS: u64 = 7_500;
const CONTEXT_SEPARATOR: &str = "\n\n";
const EMPTY: &[u8] = b"{}";

/// Output slots in hook order: tool-state, rally, closeout, trust.
static OUTPUTS: Mutex<[Option<Vec<u8>>; 4]> = Mutex::new([None, None, None, None]);
/// Process groups of children this run is waiting on.
static GROUPS: Mutex<Vec<i32>> = Mutex::new(Vec::new());

extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
    fn signal(sig: i32, handler: usize) -> usize;
}
const SIGHUP: i32 = 1;
const SIGKILL: i32 = 9;
const SIGTERM: i32 = 15;
const SIG_IGN: usize = 1;
static CANCELLED: AtomicI32 = AtomicI32::new(0);

extern "C" fn on_cancel(sig: i32) {
    // Signal handlers cannot lock the worker registry or write JSON.
    CANCELLED.store(sig, Ordering::SeqCst);
}

fn locked<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn signal_group(pid: i32, sig: i32) {
    // SAFETY: sending a signal has no memory-safety preconditions.
    unsafe {
        kill(-pid, sig);
        kill(pid, sig);
    }
}

/// TERM, a 50 ms grace as the Perl supervisor gave, then KILL.
fn stop_group(pid: i32) {
    signal_group(pid, SIGTERM);
    std::thread::sleep(Duration::from_millis(50));
    signal_group(pid, SIGKILL);
}

// ---------------------------------------------------------------- merge

/// What one hook's stdout adds to the session, by the hosts' parse rule
/// (`session_start_hook_command.context_of`).
fn context_of(stdout: &[u8]) -> Option<String> {
    let text = String::from_utf8_lossy(stdout);
    let stripped = text.trim_matches([' ', '\t', '\r', '\n']);
    if stripped.is_empty() {
        return None;
    }
    if stripped.starts_with('{') && stripped.ends_with('}') {
        let value: Value = serde_json::from_str(stripped).ok()?;
        let context = value.as_object()?.get("hookSpecificOutput")?.as_object()?.get("additionalContext")?.as_str()?;
        return (!context.is_empty()).then(|| context.to_string());
    }
    Some(stripped.to_string())
}

fn envelope(context_json: &str) -> Vec<u8> {
    format!("{{\"hookSpecificOutput\": {{\"hookEventName\": \"SessionStart\", \"additionalContext\": {context_json}}}}}\n")
        .into_bytes()
}

/// The one stdout for these hook stdouts, in order.
fn merge(parts: &[Option<Vec<u8>>]) -> Vec<u8> {
    let contexts: Vec<String> = parts.iter().flatten().filter_map(|p| context_of(p)).collect();
    if contexts.is_empty() {
        return EMPTY.to_vec();
    }
    envelope(&py::json_string(&contexts.join(CONTEXT_SEPARATOR)))
}

/// Print the merge of whatever is complete and exit 0. Caller holds `EXIT`.
fn emit_and_exit() -> ! {
    let parts = locked(&OUTPUTS).clone();
    let mut out = std::io::stdout().lock();
    let _ = out.write_all(&merge(&parts));
    let _ = out.flush();
    std::process::exit(0)
}

fn kill_children() -> std::sync::MutexGuard<'static, Vec<i32>> {
    let groups = locked(&GROUPS);
    for pid in groups.iter() {
        signal_group(*pid, SIGKILL);
    }
    groups
}

// ---------------------------------------------------------------- children

enum Ran {
    /// The child exited and closed stdout inside its limits.
    Done { out: Vec<u8>, ok: bool },
    /// Deadline passed or output too large: the group was killed.
    Cut,
}

/// Run `cmd` in its own process group until `until`. Like the Perl
/// supervisor it needs both the exit and EOF on stdout.
fn run_bounded(mut cmd: Command, stdin: Option<Vec<u8>>, until: Instant) -> Ran {
    cmd.stdout(Stdio::piped()).stderr(Stdio::null());
    cmd.stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() });
    // SAFETY: the closure only calls setsid, which is async-signal-safe.
    unsafe {
        cmd.pre_exec(sys::new_session);
    }
    let mut groups = locked(&GROUPS);
    let Ok(mut child) = cmd.spawn() else {
        return Ran::Done { out: Vec::new(), ok: false };
    };
    let pid = child.id() as i32;
    groups.push(pid);
    drop(groups);
    if let (Some(bytes), Some(mut pipe)) = (stdin, child.stdin.take()) {
        std::thread::spawn(move || {
            let _ = pipe.write_all(&bytes);
        });
    }
    let (tx, rx) = mpsc::channel();
    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            let mut out = Vec::new();
            let _ = stdout.take(MAX_OUT as u64 + 1).read_to_end(&mut out);
            if out.len() > MAX_OUT {
                let _ = tx.send(None);
            } else {
                let ok = child.wait().map(|s| s.success()).unwrap_or(false);
                let _ = tx.send(Some((out, ok)));
            }
        });
    }
    let result = match rx.recv_timeout(until.saturating_duration_since(Instant::now())) {
        Ok(Some((out, ok))) => Ran::Done { out, ok },
        _ => {
            stop_group(pid);
            Ran::Cut
        }
    };
    locked(&GROUPS).retain(|p| *p != pid);
    result
}

/// A child nobody waits for: own session, no inherited stdio.
fn spawn_detached(mut cmd: Command) {
    cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: the closure only calls setsid, which is async-signal-safe.
    unsafe {
        cmd.pre_exec(sys::new_session);
    }
    let _ = cmd.spawn();
}

fn python(script: &str) -> Command {
    let mut cmd = Command::new("python3");
    cmd.arg(script);
    cmd
}

// ---------------------------------------------------------------- rules

/// The Perl supervisor's budget: `${VAR:-default}`, kept when it is an
/// integer in `100..=maximum`, otherwise `maximum`.
fn budget(raw: Option<&str>, default: u64, maximum: u64) -> Duration {
    let ms = match raw {
        None | Some("") => default,
        Some(s) if s.bytes().all(|b| b.is_ascii_digit()) => {
            let digits = s.trim_start_matches('0');
            match digits.parse::<u64>() {
                Ok(v) if digits.len() <= 6 && (100..=maximum).contains(&v) => v,
                _ => maximum,
            }
        }
        Some(_) => maximum,
    };
    Duration::from_millis(ms)
}

fn env_budget(name: &str, default: u64, maximum: u64) -> R<Duration> {
    Ok(budget(var(name)?.as_deref(), default, maximum))
}

/// Absolute, with no `.`/`..`/empty component and no trailing slash, so
/// `cd dir && pwd` and `dirname` in the shell hooks give the same strings.
fn plain_absolute(p: &str) -> bool {
    p == "/" || (p.starts_with('/') && p[1..].split('/').all(|c| !c.is_empty() && c != "." && c != ".."))
}

/// `dirname` for a `plain_absolute` path.
fn dirname(p: &str) -> &str {
    match p.rfind('/') {
        Some(0) | None => "/",
        Some(i) => &p[..i],
    }
}

/// bash's `$PWD`: the inherited value when it names the current directory,
/// otherwise the physical path.
fn shell_pwd() -> R<String> {
    let physical = std::env::current_dir().map_err(|_| Fallback("no cwd"))?;
    if let Some(logical) = nonempty("PWD")? {
        if logical.starts_with('/') {
            if let (Ok(a), Ok(b)) = (std::fs::metadata(&logical), std::fs::metadata(".")) {
                if a.dev() == b.dev() && a.ino() == b.ino() {
                    return Ok(logical);
                }
            }
        }
    }
    physical.into_os_string().into_string().map_err(|_| Fallback("non-utf8 cwd"))
}

/// The session id the Rally hook's inline Python prints for this event:
/// `str(event.get("session_id") or "")` with newlines and tabs as spaces.
fn event_session_id(stdin: &[u8]) -> R<String> {
    let text = std::str::from_utf8(stdin).map_err(|_| Fallback("stdin not utf-8"))?;
    let text = text.trim_end_matches('\n');
    if text.is_empty() {
        return Ok(String::new());
    }
    let value: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => {
            // Python's json accepts a few inputs serde rejects.
            let nesting = text.bytes().filter(|b| matches!(b, b'[' | b'{')).count();
            if text.contains("NaN") || text.contains("Infinity") || text.contains("\\u") || nesting > 100 {
                return Err(Fallback("json python may accept"));
            }
            return Ok(String::new());
        }
    };
    match value.as_object().and_then(|o| o.get("session_id")) {
        None => Ok(String::new()),
        Some(Value::String(s)) if s.contains('\0') => Err(Fallback("NUL in session id")),
        Some(Value::String(s)) => Ok(s.replace(['\n', '\t'], " ")),
        Some(v) if py::truthy(v) => Err(Fallback("session id not a string")),
        Some(_) => Ok(String::new()),
    }
}

/// `json.dumps` of the trust report as the shell hook passes it: bytes from
/// `$(...)` (NULs dropped, trailing newlines stripped) through an
/// environment variable, which Python decodes with `surrogateescape`.
fn report_json(raw: &[u8]) -> Option<String> {
    let mut bytes: Vec<u8> = raw.iter().copied().filter(|b| *b != 0).collect();
    while bytes.last() == Some(&b'\n') {
        bytes.pop();
    }
    if bytes.is_empty() {
        return None;
    }
    let mut out = String::from("\"");
    let mut rest = &bytes[..];
    loop {
        match std::str::from_utf8(rest) {
            Ok(s) => {
                out.push_str(inner(&py::json_string(s)));
                break;
            }
            Err(e) => {
                let (good, bad) = rest.split_at(e.valid_up_to());
                out.push_str(inner(&py::json_string(std::str::from_utf8(good).unwrap_or(""))));
                let n = e.error_len().unwrap_or(bad.len());
                for b in &bad[..n] {
                    out.push_str(&format!("\\udc{b:02x}"));
                }
                rest = &bad[n..];
            }
        }
    }
    out.push('"');
    Some(out)
}

fn inner(quoted: &str) -> &str {
    &quoted[1..quoted.len() - 1]
}

fn is_json_object(bytes: &[u8]) -> bool {
    matches!(serde_json::from_slice::<Value>(bytes), Ok(Value::Object(_)))
}

fn or_empty(bytes: Vec<u8>) -> Vec<u8> {
    if is_json_object(&bytes) {
        bytes
    } else {
        EMPTY.to_vec()
    }
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

// ---------------------------------------------------------------- plan

struct Budgets {
    tool_state: Duration,
    rally: Duration,
    closeout: Duration,
    trust: Duration,
}

struct Plan {
    root: String,
    /// `${CLAUDE_PROJECT_DIR:-$PWD}`.
    workdir: String,
    project_dir_set: bool,
    /// The event as `printf '%s' "$STDIN_JSON"` passes it on.
    stdin: Vec<u8>,
    tool: String,
    session_id: String,
    parent_pid: u32,
    rally_inner_deadline: String,
    skip_watch: bool,
    budgets: Budgets,
}

/// Decide everything that can be decided without a side effect. `Err`
/// means the five shell hooks must handle this event.
fn plan(root: &str, stdin: &[u8]) -> R<Plan> {
    if !plain_absolute(root) {
        return Err(Fallback("plugin root not a plain absolute path"));
    }
    if nonempty("BUILD_LOOP_RALLY_SESSION_START_FOREGROUND")?.as_deref() == Some("1") {
        return Err(Fallback("rally foreground diagnosis"));
    }
    for tool in ["python3", "perl"] {
        // Without either, each shell hook degrades in its own way.
        if which(tool)?.is_none() {
            return Err(Fallback("python3 or perl missing"));
        }
    }
    if stdin.len() > MAX_OUT || stdin.contains(&0) {
        return Err(Fallback("stdin too large or holds NUL"));
    }
    let project_dir = nonempty("CLAUDE_PROJECT_DIR")?;
    let project_dir_set = project_dir.is_some();
    let workdir = match project_dir {
        Some(v) => v,
        None => shell_pwd()?,
    };
    if !plain_absolute(&workdir) {
        return Err(Fallback("workdir not a plain absolute path"));
    }
    let event_sid = event_session_id(stdin)?;
    let mut tool = None;
    for name in ["RALLY_TOOL_ID", "BUILD_LOOP_RALLY_TOOL", "RALLY_HOST_FAMILY"] {
        if let Some(v) = nonempty(name)? {
            if v.starts_with('-') {
                return Err(Fallback("tool looks like an option"));
            }
            tool = Some(v);
            break;
        }
    }
    let codex = nonempty("CODEX_THREAD_ID")?.or(nonempty("CODEX_SESSION_ID")?);
    let tool = tool.unwrap_or_else(|| if codex.is_some() { "codex" } else { "claude_code" }.to_string());
    let parent_pid = std::os::unix::process::parent_id();
    let session_id = if Path::new(&format!("{root}/scripts/rally_point/actor_identity.py")).is_file() {
        let env = |name: &str| -> R<Option<String>> {
            if name == "RALLY_OBSERVER_PID" {
                Ok(Some(parent_pid.to_string()))
            } else {
                var(name)
            }
        };
        identity::resolve_session_id(&tool, Some(&event_sid), &env)?
    } else {
        let mut chosen = (!event_sid.is_empty()).then_some(event_sid);
        for name in [
            "BUILD_LOOP_RALLY_SESSION_ID",
            "CODEX_THREAD_ID",
            "CODEX_SESSION_ID",
            "CLAUDE_SESSION_ID",
            "RALLY_AGENT_ID",
            "RALLY_SESSION_ID",
        ] {
            if chosen.is_none() {
                chosen = nonempty(name)?;
            }
        }
        chosen.unwrap_or_else(|| format!("{tool}-{parent_pid}"))
    };
    if session_id.starts_with('-') {
        return Err(Fallback("session id looks like an option"));
    }
    let mut passed = stdin.to_vec();
    while passed.last() == Some(&b'\n') {
        passed.pop();
    }
    Ok(Plan {
        root: root.to_string(),
        workdir,
        project_dir_set,
        stdin: passed,
        tool,
        session_id,
        parent_pid,
        rally_inner_deadline: nonempty("BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS")?.unwrap_or_else(|| "1.2".to_string()),
        skip_watch: var("BUILD_LOOP_RALLY_POINT_SKIP_WATCH")?.as_deref() == Some("1"),
        budgets: Budgets {
            tool_state: env_budget("BUILD_LOOP_TOOL_STATE_DEADLINE_MS", 1500, 1500)?,
            rally: env_budget("BUILD_LOOP_RALLY_START_DEADLINE_MS", 1500, 1600)?,
            closeout: env_budget("BUILD_LOOP_CLOSEOUT_DEADLINE_MS", 2500, 2500)?,
            trust: env_budget("BUILD_LOOP_TRUST_CHECK_DEADLINE_MS", 3500, 3500)?,
        },
    })
}

// ---------------------------------------------------------------- sub-steps

/// `session-start-tool-state.sh` (supervisor mode `text`).
fn tool_state(p: &Plan) -> Vec<u8> {
    let script = format!("{}/scripts/tool_state_paths.py", p.root);
    if !Path::new(&script).is_file() {
        return Vec::new();
    }
    let until = Instant::now() + p.budgets.tool_state;
    let native = crate::tool_state::snapshot(&p.workdir, &p.stdin, until, |cwd, args| {
        let mut cmd = Command::new("git");
        cmd.arg("-C").arg(cwd).args(args);
        match run_bounded(cmd, None, until) {
            Ran::Done { out, ok: true } => Some(out),
            _ => None,
        }
    });
    match native {
        Some(out) if out.len() <= MAX_OUT => out,
        Some(_) => EMPTY.to_vec(),
        None => {
            // Non-UTF-8 filenames need Python's surrogateescape JSON.
            // The decision precedes state writes; retain the original budget.
            let mut cmd = python(&script);
            cmd.args(["snapshot", "--workdir", &p.workdir, "--no-exclude", "--emit-context"]);
            match run_bounded(cmd, Some(p.stdin.clone()), until) {
                Ran::Done { out, .. } => out,
                Ran::Cut => EMPTY.to_vec(),
            }
        }
    }
}

/// `session-start-architecture.sh`: start the detached scan when the
/// manifest is missing or older than a day. Prints nothing.
fn architecture(p: &Plan) {
    let script = format!("{}/hooks/_arch_scan_bg.py", p.root);
    let dir = format!("{}/.build-loop/architecture", p.workdir);
    if !Path::new(&dir).is_dir() || !Path::new(&script).is_file() {
        return;
    }
    let stale = match std::fs::metadata(format!("{dir}/manifest.json")) {
        Ok(meta) => now() - (meta.mtime() as f64 + meta.mtime_nsec() as f64 * 1e-9) > 86_400.0,
        Err(_) => true,
    };
    if stale {
        let mut cmd = Command::new("nohup");
        cmd.args(["python3", &script, "--workdir", &p.workdir]);
        spawn_detached(cmd);
    }
}

/// `session-start-rally-point.sh` (supervisor mode `json`): the restore
/// line in the foreground, then the detached background stage.
fn rally(p: &Plan) -> Vec<u8> {
    let until = Instant::now() + p.budgets.rally;
    let pkg = format!("{}/scripts/rally_point", p.root);
    if !Path::new(&pkg).is_dir() {
        return EMPTY.to_vec();
    }
    if !p.project_dir_set {
        let mut git = Command::new("git");
        git.args(["-C", &p.workdir, "rev-parse", "--git-dir"]);
        match run_bounded(git, None, until) {
            Ran::Done { ok: true, .. } => {}
            _ => return EMPTY.to_vec(),
        }
    }
    let mut cmd = python(&format!("{pkg}/hooks.py"));
    cmd.args(["session-start-safe", "--workdir", &p.workdir, "--verbose", "--tool", &p.tool])
        .args(["--session-id", &p.session_id])
        .env("BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS", &p.rally_inner_deadline);
    let Ran::Done { out, .. } = run_bounded(cmd, None, until) else {
        // The shell hook is killed before it reaches its background stage.
        return EMPTY.to_vec();
    };
    if let Ok(exe) = std::env::current_exe() {
        let mut bg = Command::new(exe);
        bg.args(["hook", "session-start", "--rally-background", &p.workdir, &p.tool, &p.session_id])
            .arg(p.parent_pid.to_string())
            .arg(if p.skip_watch { "skip-watch" } else { "watch" })
            .args(["--root", &p.root]);
        spawn_detached(bg);
    }
    or_empty(out)
}

/// The Rally hook's background stage, in its original order: probe
/// (announce, and the watcher unless skipped), cursor advance, peer
/// collision warning. Runs detached with no deadline.
fn rally_background(root: &str, args: &[String]) -> ! {
    // SAFETY: ignoring SIGHUP before any thread exists.
    unsafe {
        signal(SIGHUP, SIG_IGN);
    }
    let [workdir, tool, session_id, parent_pid, watch] = args else {
        std::process::exit(0);
    };
    let pkg = format!("{root}/scripts/rally_point");
    let run = |mut cmd: Command| {
        let _ = cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status();
    };
    let probe = format!("{pkg}/session_probe.py");
    if Path::new(&probe).is_file() {
        let mut cmd = python(&probe);
        cmd.args(["--workdir", workdir, "--tool", tool, "--session-id", session_id, "--mode", "hook"])
            .env("WORKDIR", workdir);
        if watch == "watch" {
            cmd.args(["--start-watch", "--watch-parent-pid", parent_pid]);
        }
        run(cmd);
    }
    let mut advance = python(&format!("{pkg}/hooks.py"));
    advance.args(["session-start-advance", "--workdir", workdir, "--tool", tool, "--session-id", session_id]);
    run(advance);
    let collision = format!("{pkg}/peer_collision.py");
    if Path::new(&collision).is_file() {
        let mut cmd = python(&collision);
        cmd.args(["--workdir", workdir]);
        run(cmd);
    }
    std::process::exit(0)
}

/// `closeout.sh session-start` (supervisor mode `optional-json`).
fn closeout(p: &Plan) -> Vec<u8> {
    let until = Instant::now() + p.budgets.closeout;
    let mut dir = p.workdir.as_str();
    let mut found = None;
    for _ in 0..8 {
        if Path::new(&format!("{dir}/.build-loop")).is_dir() {
            found = Some(dir);
            break;
        }
        let parent = dirname(dir);
        if parent == dir {
            break;
        }
        dir = parent;
    }
    let script = format!("{}/scripts/stop_closeout.py", p.root);
    let Some(found) = found.filter(|_| Path::new(&script).is_file()) else {
        return Vec::new();
    };
    let mut cmd = python(&script);
    cmd.args(["--workdir", found, "--mode", "session-start", "--hook"]);
    match run_bounded(cmd, None, until) {
        Ran::Done { mut out, ok } => {
            if !ok {
                out.extend_from_slice(EMPTY);
            }
            if out.is_empty() {
                out
            } else {
                or_empty(out)
            }
        }
        Ran::Cut => EMPTY.to_vec(),
    }
}

/// `session-start-codex-hook-trust.sh` (supervisor mode `json`).
fn trust(p: &Plan) -> Vec<u8> {
    let script = format!("{}/scripts/codex_hook_trust_check.py", p.root);
    if !Path::new(&script).is_file() {
        return EMPTY.to_vec();
    }
    let mut cmd = python(&script);
    cmd.args(["--workdir", &p.workdir, "--quiet-unknown"]);
    match run_bounded(cmd, None, Instant::now() + p.budgets.trust) {
        Ran::Done { out, .. } => match report_json(&out).map(|j| envelope(&j)) {
            Some(bytes) if bytes.len() <= MAX_OUT => bytes,
            _ => EMPTY.to_vec(),
        },
        Ran::Cut => EMPTY.to_vec(),
    }
}

// ---------------------------------------------------------------- hand-off

/// One shell hook as its registered command ran it: `bash <hook> [arg]`
/// under the host timeout; a timed-out hook's output is discarded.
fn shell_hook(root: &str, name: &str, arg: Option<&str>, stdin: Option<Vec<u8>>, seconds: u64, or_braces: bool) -> Vec<u8> {
    let mut cmd = Command::new("bash");
    cmd.arg(format!("{root}/hooks/{name}"));
    if let Some(arg) = arg {
        cmd.arg(arg);
    }
    match run_bounded(cmd, stdin, Instant::now() + Duration::from_secs(seconds)) {
        Ran::Done { mut out, ok } => {
            if or_braces && !ok {
                out.extend_from_slice(EMPTY);
            }
            out
        }
        Ran::Cut => Vec::new(),
    }
}

// ---------------------------------------------------------------- run

fn store(slot: usize, bytes: Vec<u8>) {
    locked(&OUTPUTS)[slot] = Some(bytes);
}

fn dirs_exist(workdir: &str) -> bool {
    [".build-loop", ".rally"].iter().all(|d| Path::new(&format!("{workdir}/{d}")).is_dir())
}

/// Run event-dependent steps, gating Rally on the tool-state snapshot when
/// it could change the directories that snapshot observes.
fn schedule<A, B>(gate_rally: bool, tool_state: A, rally: B)
where
    A: FnOnce() -> Vec<u8> + Send,
    B: FnOnce() -> Vec<u8> + Send,
{
    let (done_tx, done_rx) = mpsc::channel::<()>();
    std::thread::scope(|scope| {
        scope.spawn(move || {
            let out = tool_state();
            store(0, out);
            let _ = done_tx.send(());
        });
        scope.spawn(move || {
            if gate_rally {
                let _ = done_rx.recv();
            }
            store(1, rally());
        });
    });
}

fn read_stdin() -> Vec<u8> {
    let mut bytes = Vec::new();
    if !sys::stdin_is_tty() {
        let _ = std::io::stdin().lock().take(MAX_OUT as u64 + 1).read_to_end(&mut bytes);
    }
    bytes
}

fn run(root: &str) {
    #[cfg(debug_assertions)]
    if std::env::var_os("BL_FORCE_PANIC").is_some() {
        panic!("forced");
    }
    // These hooks originally had /dev/null as stdin. Start them before reading
    // the event so a stalled host pipe cannot suppress unrelated context.
    let independent = plan(root, &[]);
    std::thread::scope(|scope| {
        scope.spawn(|| match &independent {
            Ok(p) => architecture(p),
            Err(_) => { shell_hook(root, "session-start-architecture.sh", None, None, 2, false); }
        });
        scope.spawn(|| store(2, match &independent {
            Ok(p) => closeout(p),
            Err(_) => shell_hook(root, "closeout.sh", Some("session-start"), None, 3, false),
        }));
        scope.spawn(|| store(3, match &independent {
            Ok(p) => trust(p),
            Err(_) => shell_hook(root, "session-start-codex-hook-trust.sh", None, None, 5, true),
        }));
        run_event(root);
    });
}

fn run_event(root: &str) {
    let stdin = read_stdin();
    match plan(root, &stdin) {
        Ok(p) => {
            let p = &p;
            schedule(
                !dirs_exist(&p.workdir),
                || tool_state(p),
                || rally(p),
            );
        }
        Err(Fallback(reason)) => {
            if std::env::var_os("BL_TRACE_FALLBACK").is_some() {
                eprintln!("bl: fallback: {reason}");
            }
            let workdir = std::env::var("CLAUDE_PROJECT_DIR").ok().filter(|v| !v.is_empty());
            let workdir = workdir.or_else(|| std::env::current_dir().ok().map(|d| d.to_string_lossy().into_owned()));
            let gate = !workdir.as_deref().map(dirs_exist).unwrap_or(false);
            let mut event = stdin;
            event.truncate(MAX_OUT);
            let for_rally = event.clone();
            schedule(
                gate,
                || shell_hook(root, "session-start-tool-state.sh", None, Some(event), 2, false),
                || shell_hook(root, "session-start-rally-point.sh", None, Some(for_rally), 2, false),
            );
        }
    }
}

pub fn main(started: Instant, args: &[String]) -> ! {
    let (root, rest) = match args {
        [rest @ .., flag, root] if flag == "--root" => (root.clone(), rest),
        _ => std::process::exit(bl_contract::FALLBACK_EXIT),
    };
    match rest {
        [] => {}
        [flag, bg @ ..] if flag == "--rally-background" => rally_background(&root, bg),
        _ => std::process::exit(bl_contract::FALLBACK_EXIT),
    }
    let limit = budget(
        std::env::var("BUILD_LOOP_SESSION_START_DEADLINE_MS").ok().as_deref(),
        RUN_DEADLINE_MS,
        RUN_DEADLINE_MS,
    );
    for sig in [SIGHUP, 2, SIGTERM] {
        // SAFETY: on_cancel only stores to a lock-free atomic.
        unsafe { signal(sig, on_cancel as *const () as usize); }
    }
    std::thread::spawn(move || {
        let deadline = started + limit;
        while CANCELLED.load(Ordering::SeqCst) == 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        let _guard = EXIT.lock();
        // Keep the registry locked until exit so no worker can spawn after cleanup.
        let _children = kill_children();
        emit_and_exit();
    });
    let outcome = std::panic::catch_unwind(|| run(&root));
    let _guard = EXIT.lock();
    let _children = outcome.is_err().then(kill_children);
    emit_and_exit()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx(s: &str) -> Vec<u8> {
        envelope(&py::json_string(s))
    }

    #[test]
    fn budgets_follow_the_supervisor() {
        let ms = |raw, d, m| budget(raw, d, m).as_millis() as u64;
        assert_eq!(ms(None, 1200, 1400), 1200);
        assert_eq!(ms(Some(""), 1200, 1400), 1200);
        assert_eq!(ms(Some("300"), 1200, 1400), 300);
        assert_eq!(ms(Some("0100"), 1200, 1400), 100);
        assert_eq!(ms(Some("99"), 1200, 1400), 1400);
        assert_eq!(ms(Some("1401"), 1200, 1400), 1400);
        assert_eq!(ms(Some("1.5"), 1200, 1400), 1400);
        assert_eq!(ms(Some("99999999999999999999"), 1500, 1500), 1500);
    }

    #[test]
    fn merge_keeps_order_and_drops_empty_outputs() {
        assert_eq!(merge(&[None, None, None, None]), b"{}");
        assert_eq!(merge(&[Some(b"{}".to_vec()), Some(Vec::new()), Some(b" \n".to_vec())]), b"{}");
        let merged = merge(&[Some(ctx("first")), Some(b"{}".to_vec()), None, Some(ctx("last \u{2014} x"))]);
        assert_eq!(
            String::from_utf8(merged).unwrap(),
            "{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": \"first\\n\\nlast \\u2014 x\"}}\n"
        );
    }

    #[test]
    fn context_follows_the_host_parse_rule() {
        assert_eq!(context_of(b"plain text\n").as_deref(), Some("plain text"));
        assert_eq!(context_of(b"{broken}"), None);
        assert_eq!(context_of(b"{}{}"), None);
        assert_eq!(context_of(b"{\"hookSpecificOutput\": {\"additionalContext\": \"\"}}"), None);
        assert_eq!(context_of(b"{\"hookSpecificOutput\": {\"additionalContext\": 3}}"), None);
        assert_eq!(context_of(b"[1]").as_deref(), Some("[1]"));
    }

    #[test]
    fn event_session_ids() {
        assert_eq!(event_session_id(b"").unwrap(), "");
        assert_eq!(event_session_id(b"not json\n\n").unwrap(), "");
        assert_eq!(event_session_id(b"[1]").unwrap(), "");
        assert_eq!(event_session_id(br#"{"session_id": "a\tb\nc"}"#).unwrap(), "a b c");
        assert_eq!(event_session_id(br#"{"session_id": null}"#).unwrap(), "");
        assert_eq!(event_session_id(br#"{"session_id": 0}"#).unwrap(), "");
        assert!(event_session_id(br#"{"session_id": 42}"#).is_err());
        assert!(event_session_id(br#"{"session_id": ["x"]}"#).is_err());
        assert!(event_session_id(br#"{"x": NaN}"#).is_err());
        assert!(event_session_id(b"\xff").is_err());
    }

    #[test]
    fn trust_report_encoding() {
        assert_eq!(report_json(b"\n\n"), None);
        assert_eq!(report_json(b"a \"b\"\n  c\n\n").as_deref(), Some("\"a \\\"b\\\"\\n  c\""));
        assert_eq!(report_json(b"x\0y\xffz").as_deref(), Some("\"xy\\udcffz\""));
        assert_eq!(report_json("\u{2014}".as_bytes()).as_deref(), Some("\"\\u2014\""));
    }

    #[test]
    fn plain_paths_and_dirname() {
        assert!(plain_absolute("/a/b") && plain_absolute("/"));
        for bad in ["a/b", "/a/", "/a//b", "/a/./b", "/a/../b", ""] {
            assert!(!plain_absolute(bad), "{bad}");
        }
        assert_eq!(dirname("/a/b"), "/a");
        assert_eq!(dirname("/a"), "/");
        assert_eq!(dirname("/"), "/");
    }
}
