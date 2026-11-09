// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl hook pre-edit`: the native path of `hooks/pre-edit-rally-point.sh`.
//!
//! Mirrors `python3 scripts/rally_point/hooks.py pre-edit` for native Rally
//! contexts: resolve the operative repo, skip when this session's throttle
//! stamp is fresh, read the discovery cache (spawning the detached refresher
//! when it is stale or missing), then touch and prune the stamp and spawn
//! the detached `pre-edit-join-bg` writer. Nothing is printed.
//!
//! Work happens in two passes. `plan` only reads (git, the rally usage
//! probe, cache and stamp files) and decides; any input it cannot mirror
//! exactly returns [`Fallback`] before a single side effect. `act` then
//! performs the side effects in the order Python would.

use bl_contract::{cache, event, identity, py, stamp, Fallback, R};
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::sys;

pub enum Outcome {
    /// Finished; print nothing.
    Done,
    /// Hand the invocation to the shell hook. `stdin` holds the bytes
    /// already consumed (`None` when stdin was a terminal or never read).
    Fallback { reason: &'static str, stdin: Option<Vec<u8>> },
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn var(name: &str) -> R<Option<String>> {
    match std::env::var_os(name) {
        None => Ok(None),
        Some(v) => v.into_string().map(Some).map_err(|_| Fallback("non-utf8 env")),
    }
}

fn nonempty(name: &str) -> R<Option<String>> {
    Ok(var(name)?.filter(|v| !v.is_empty()))
}

fn mtime(meta: &fs::Metadata) -> f64 {
    meta.mtime() as f64 + meta.mtime_nsec() as f64 * 1e-9
}

/// `shutil.which(name)` (also bash's lookup for a plain command name).
fn which(name: &str) -> R<Option<String>> {
    let path = var("PATH")?.ok_or(Fallback("PATH unset"))?;
    let mut seen: Vec<&str> = Vec::new();
    for dir in path.split(':') {
        if seen.contains(&dir) {
            continue;
        }
        seen.push(dir);
        let candidate = if dir.is_empty() {
            name.to_string()
        } else if dir.ends_with('/') {
            format!("{dir}{name}")
        } else {
            format!("{dir}/{name}")
        };
        if let Ok(meta) = fs::metadata(&candidate) {
            if !meta.is_dir() && sys::executable(&candidate) {
                return Ok(Some(candidate));
            }
        }
    }
    Ok(None)
}

/// A discovery result for one directory (`discovery_bridge.resolve` in
/// non-blocking disk-cache mode).
#[derive(Clone, Copy, PartialEq, Eq)]
enum Ctx {
    Native { unscoped: bool },
    /// No usable record; Python raises `DiscoveryPending`.
    Pending,
}

struct Refresh {
    dir: String,
    key: String,
}

struct StampWork {
    toplevel: String,
    session_id: String,
    tool: String,
    t: f64,
    throttle: f64,
}

struct Planner<'a> {
    plugin_root: &'a str,
    cwd: String,
    home: Option<String>,
    probes: HashMap<String, bool>,
    refreshes: Vec<Refresh>,
}

impl Planner<'_> {
    fn expand(&self, p: &str) -> R<String> {
        Ok(py::path_str(&py::expanduser(&py::path_str(p), self.home.as_deref())?))
    }

    fn resolve(&self, p: &str) -> R<String> {
        py::resolve(p, &self.cwd)
    }

    fn cache_dir(&self) -> R<String> {
        if let Some(dir) = nonempty("BUILD_LOOP_RALLY_DISCOVERY_CACHE_DIR")? {
            return self.expand(&dir);
        }
        let base = match nonempty("XDG_CACHE_HOME")? {
            Some(b) => b,
            None => py::join(&self.expand("~")?, ".cache"),
        };
        Ok(py::join(&self.expand(&base)?, "build-loop/rally-discovery"))
    }

    /// `_rally_binary_candidates(workdir)`.
    fn candidates(&self, workdir: &str) -> R<Vec<String>> {
        let mut out: Vec<String> = Vec::new();
        let mut add = |p: &str| -> R<()> {
            if p.is_empty() {
                return Ok(());
            }
            let norm = py::path_str(p);
            if norm.starts_with('~') {
                return Err(Fallback("~ in rally candidate"));
            }
            if !out.contains(&norm) {
                out.push(norm);
            }
            Ok(())
        };
        if let Some(b) = var("AGENT_RALLY_BINARY")? {
            add(&b)?;
        }
        if let Some(b) = which("rally")? {
            add(&b)?;
        }
        if nonempty("BUILD_LOOP_DISABLE_BINARY_FETCH")?.is_none() {
            let base = match nonempty("XDG_CACHE_HOME")? {
                Some(b) => b,
                None => py::join(&self.expand("~")?, ".cache"),
            };
            let cached = py::join(&base, "build-loop/rally/rally-v0.1.5");
            if fs::metadata(&cached).map(|m| m.is_file()).unwrap_or(false) {
                add(&cached)?;
            }
        }
        if nonempty("BUILD_LOOP_DISABLE_SIBLING_RALLY")?.is_none() && nonempty("BUILD_LOOP_APPS_ROOT")?.is_none() {
            let mut root = workdir.to_string();
            let mut cand = workdir.to_string();
            loop {
                if py::exists(&py::join(&cand, ".git")) || py::exists(&py::join(&cand, "target/release")) {
                    root = cand.clone();
                    break;
                }
                let up = py::parent(&cand);
                if up == cand {
                    break;
                }
                cand = up;
            }
            for base in [root.clone(), py::join(&py::parent(&root), "agent-rally-point")] {
                add(&py::join(&base, "target/release/rally"))?;
                add(&py::join(&base, "target/debug/rally"))?;
            }
            let sibling = py::join(&py::parent(self.plugin_root), "agent-rally-point");
            add(&py::join(&sibling, "target/release/rally"))?;
            add(&py::join(&sibling, "target/debug/rally"))?;
        }
        Ok(out)
    }

    /// `_rally_binary_supports_required_surface`: run the binary with no
    /// arguments and look for Rally's usage fragments.
    fn probe(&mut self, binary: &str) -> R<bool> {
        if let Some(hit) = self.probes.get(binary) {
            return Ok(*hit);
        }
        let ok = probe_binary(binary)?;
        self.probes.insert(binary.to_string(), ok);
        Ok(ok)
    }

    /// `discovery_bridge.resolve(dir)` with the disk cache and non-blocking
    /// mode on. `dir` is already resolved. No rally binary falls back.
    fn discover(&mut self, dir: &str) -> R<Ctx> {
        let mut binary = None;
        for cand in self.candidates(dir)? {
            if self.probe(&cand)? {
                binary = Some(cand);
                break;
            }
        }
        let binary = binary.ok_or(Fallback("no native rally binary"))?;
        let Ok(meta) = fs::metadata(&binary) else {
            return Ok(Ctx::Pending);
        };
        let mut env = Vec::new();
        for name in cache::IDENTITY_ENV {
            env.push((name, var(name)?));
        }
        let key = cache::key(&cache::KeyInput {
            workdir: dir,
            binary: &binary,
            binary_mtime_ns: meta.mtime() as i128 * 1_000_000_000 + meta.mtime_nsec() as i128,
            binary_size: meta.size(),
            uid: sys::uid(),
            env,
        });
        let file = py::join(&self.cache_dir()?, &format!("{key}.json"));
        let bytes = fs::read(&file).ok();
        let read = |limit: f64| -> R<Option<cache::Envelope>> {
            match &bytes {
                None => Ok(None),
                Some(b) => cache::evaluate(b, &key, &binary, now(), limit),
            }
        };
        if let Some(env) = read(cache::TTL_SECONDS)? {
            return Ok(Ctx::Native { unscoped: env.unscoped });
        }
        let stale = read(cache::STALE_MAX_SECONDS)?;
        if !self.refreshes.iter().any(|r| r.key == key) {
            self.refreshes.push(Refresh { dir: dir.to_string(), key });
        }
        Ok(match stale {
            Some(env) => Ctx::Native { unscoped: env.unscoped },
            None => Ctx::Pending,
        })
    }
}

fn probe_binary(binary: &str) -> R<bool> {
    if !binary.contains('/') {
        return Err(Fallback("bare rally candidate"));
    }
    let is_file = fs::metadata(binary).map(|m| m.is_file()).unwrap_or(false);
    if !is_file || !sys::executable(binary) {
        return Ok(false);
    }
    let timeout = cache::probe_timeout_seconds(var("RALLY_HOOK_BUDGET_MS")?.as_deref())?;
    let Ok(mut child) = Command::new(binary)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    else {
        return Ok(false);
    };
    let pid = child.id();
    sys::track_child(pid);
    let mut out = child.stdout.take().expect("piped");
    let mut err = child.stderr.take().expect("piped");
    let err_reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = err.read_to_end(&mut buf);
        buf
    });
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = out.read_to_end(&mut buf);
        let _ = child.wait();
        sys::untrack_child(pid);
        let _ = tx.send(buf);
    });
    let Ok(stdout) = rx.recv_timeout(Duration::from_secs_f64(timeout)) else {
        sys::kill_hard(pid);
        return Ok(false);
    };
    let stderr = err_reader.join().unwrap_or_default();
    let (Ok(o), Ok(e)) = (String::from_utf8(stdout), String::from_utf8(stderr)) else {
        return Err(Fallback("rally usage not utf-8"));
    };
    let help = format!("{o}\n{e}");
    Ok(cache::RALLY_HELP_FRAGMENTS.iter().all(|f| help.contains(f)))
}

/// `hooks._git_toplevel`.
fn git_toplevel(path: &str) -> R<Option<String>> {
    let Ok(child) = Command::new("git")
        .args(["-C", path, "rev-parse", "--show-toplevel"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    else {
        return Ok(None);
    };
    let pid = child.id();
    sys::track_child(pid);
    let out = child.wait_with_output();
    sys::untrack_child(pid);
    let Ok(out) = out else {
        return Ok(None);
    };
    if !out.status.success() {
        return Ok(None);
    }
    let text = String::from_utf8(out.stdout).map_err(|_| Fallback("git output not utf-8"))?;
    let top = py::strip(&text);
    Ok((!top.is_empty()).then(|| py::path_str(top)))
}

/// `hooks.resolve_operative_repo`.
fn operative_repo(planner: &Planner, workdir: &str, hints: &event::Hints) -> R<Option<String>> {
    let mut candidates = Vec::new();
    if !hints.file_path.is_empty() {
        if hints.file_path.contains('\0') {
            return Err(Fallback("NUL in file_path"));
        }
        let mut fp = py::path_str(&py::expanduser(&hints.file_path, planner.home.as_deref())?);
        if !fp.starts_with('/') {
            fp = py::join(workdir, &fp);
        }
        candidates.push(planner.resolve(&py::parent(&fp))?);
    }
    if !hints.command.is_empty() {
        if let Some(target) = event::cd_target(&hints.command)? {
            if target.contains('\0') {
                return Err(Fallback("NUL in cd target"));
            }
            let mut tp = py::path_str(&py::expanduser(&target, planner.home.as_deref())?);
            if !tp.starts_with('/') {
                tp = py::join(workdir, &tp);
            }
            candidates.push(tp);
        }
    }
    candidates.push(workdir.to_string());
    for cand in candidates {
        if let Some(top) = git_toplevel(&cand)? {
            return Ok(Some(top));
        }
    }
    Ok(None)
}

fn stamp_is_fresh(toplevel: &str, session_id: &str, throttle: f64, t: f64) -> bool {
    match fs::metadata(stamp::path(toplevel, session_id)) {
        Ok(meta) => t - mtime(&meta) < throttle,
        Err(_) => false,
    }
}

struct Plan {
    refreshes: Vec<Refresh>,
    cache_dir: String,
    stamp: Option<StampWork>,
    python: String,
}

fn plan(plugin_root: &str, stdin: &[u8]) -> R<Option<Plan>> {
    if !Path::new(plugin_root).join("scripts/rally_point").is_dir() {
        return Ok(None); // `[ -d "$PKG" ] || exit 0`
    }
    if nonempty("BUILD_LOOP_BRIDGE_INTERNAL_ONLY")?.is_some() || nonempty("AGENT_RALLY_DISCOVER")?.is_some() {
        return Err(Fallback("discovery override"));
    }
    let python = which("python3")?.ok_or(Fallback("python3 missing"))?;
    let tool = host_tool()?;
    let raw_workdir = workdir_arg()?;
    let hints = event::hints(stdin)?;
    let cwd = std::env::current_dir()
        .map_err(|_| Fallback("no cwd"))?
        .into_os_string()
        .into_string()
        .map_err(|_| Fallback("non-utf8 cwd"))?;
    let mut planner = Planner {
        plugin_root,
        cwd,
        home: var("HOME")?,
        probes: HashMap::new(),
        refreshes: Vec::new(),
    };
    // hooks.main: expanduser, then resolve only when it exists.
    let expanded = planner.expand(&raw_workdir)?;
    let workdir = if py::exists(&expanded) { planner.resolve(&expanded)? } else { expanded };
    // resolve_context(workdir) -> discovery_bridge.resolve(resolved workdir)
    let context_dir = planner.resolve(&workdir)?;
    let workdir_ctx = planner.discover(&context_dir)?;

    let mut stamp_work = None;
    if let Some(op) = operative_repo(&planner, &workdir, &hints)? {
        let explicit = (!hints.session_id.is_empty()).then_some(hints.session_id.as_str());
        let session_id = identity::resolve_session_id(&tool, explicit, &var)?;
        let throttle = stamp::throttle_seconds(var("BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS")?.as_deref())?;
        let t = now();
        if !stamp_is_fresh(&op, &session_id, throttle, t) {
            let same = planner.resolve(&op)? == planner.resolve(&workdir)?;
            let op_ctx = if workdir_ctx != Ctx::Pending && same {
                workdir_ctx
            } else {
                let dir = planner.resolve(&op)?;
                planner.discover(&dir)?
            };
            if op_ctx == (Ctx::Native { unscoped: false }) {
                stamp_work = Some(StampWork { toplevel: op, session_id, tool, t, throttle });
            }
        }
    }
    if workdir_ctx == Ctx::Pending {
        // pre_edit_hint re-resolves a context that raised DiscoveryPending.
        planner.discover(&context_dir)?;
    }
    let cache_dir = if planner.refreshes.is_empty() { String::new() } else { planner.cache_dir()? };
    Ok(Some(Plan { refreshes: planner.refreshes, cache_dir, stamp: stamp_work, python }))
}

/// `pre-edit-rally-point.sh` host tool pick.
fn host_tool() -> R<String> {
    for name in ["RALLY_TOOL_ID", "BUILD_LOOP_RALLY_TOOL", "RALLY_HOST_FAMILY"] {
        if let Some(v) = nonempty(name)? {
            if v.starts_with('-') {
                return Err(Fallback("tool looks like an option"));
            }
            return Ok(v);
        }
    }
    let codex = nonempty("CODEX_THREAD_ID")?.or(nonempty("CODEX_SESSION_ID")?);
    Ok(if codex.is_some() { "codex" } else { "claude_code" }.to_string())
}

/// `${CLAUDE_PROJECT_DIR:-$PWD}`.
fn workdir_arg() -> R<String> {
    let raw = match nonempty("CLAUDE_PROJECT_DIR")? {
        Some(v) => v,
        None => std::env::current_dir()
            .map_err(|_| Fallback("no cwd"))?
            .into_os_string()
            .into_string()
            .map_err(|_| Fallback("non-utf8 cwd"))?,
    };
    if raw.starts_with('-') {
        return Err(Fallback("workdir looks like an option"));
    }
    Ok(raw)
}

fn spawn_detached(python: &str, args: &[&str]) -> bool {
    let mut cmd = Command::new(python);
    cmd.args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: the closure only calls setsid, which is async-signal-safe.
    unsafe {
        cmd.pre_exec(sys::new_session);
    }
    cmd.spawn().is_ok()
}

/// `discovery_bridge._spawn_refresh`.
fn refresh(plan_python: &str, plugin_root: &str, cache_dir: &str, r: &Refresh) {
    let dir = Path::new(cache_dir);
    if !dir.is_dir() {
        if let Some(parent) = dir.parent() {
            if fs::create_dir_all(parent).is_err() {
                return;
            }
        }
        if let Err(e) = fs::DirBuilder::new().mode(0o700).create(dir) {
            if e.kind() != std::io::ErrorKind::AlreadyExists || !dir.is_dir() {
                return;
            }
        }
    }
    let lock = py::join(cache_dir, &format!("{}.refresh", r.key));
    match fs::metadata(&lock) {
        Ok(meta) => {
            if now() - mtime(&meta) < cache::REFRESH_LOCK_SECONDS {
                return;
            }
            if fs::remove_file(&lock).is_err() {
                return;
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return,
    }
    if fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&lock).is_err() {
        return;
    }
    let script = format!("{plugin_root}/scripts/rally_point/discovery_bridge.py");
    spawn_detached(plan_python, &[&script, "--workdir", &r.dir, "--refresh", &lock]);
}

/// `_touch_native_throttle` + `_prune_native_throttles` + `_spawn_background_join`.
fn touch_and_join(python: &str, plugin_root: &str, w: &StampWork) {
    let dir = stamp::dir(&w.toplevel);
    let path = stamp::path(&w.toplevel, &w.session_id);
    if fs::create_dir_all(&dir).is_err() {
        return;
    }
    // Touch, never truncate: an existing stamp keeps its bytes.
    if !sys::set_times(&path, None)
        && fs::OpenOptions::new().write(true).create(true).truncate(false).mode(0o666).open(&path).is_err()
    {
        return;
    }
    if !sys::set_times(&path, Some(w.t)) {
        return;
    }
    if let Ok(entries) = fs::read_dir(&dir) {
        let current = stamp::file_name(&w.session_id);
        let mut found: Vec<(String, f64)> = Vec::new();
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if !name.ends_with(".stamp") {
                continue;
            }
            if let Ok(meta) = fs::metadata(entry.path()) {
                if meta.is_file() {
                    found.push((name, mtime(&meta)));
                }
            }
        }
        for name in stamp::prune(&found, &current, w.throttle, w.t) {
            let _ = fs::remove_file(py::join(&dir, &name));
        }
    }
    let script = format!("{plugin_root}/scripts/rally_point/hooks.py");
    spawn_detached(
        python,
        &[
            &script, "pre-edit-join-bg", "--workdir", &w.toplevel, "--tool", &w.tool, "--session-id",
            &w.session_id, "--model", "unknown",
        ],
    );
}

/// Run the hook. Reads stdin (capped) unless it is a terminal.
pub fn run(plugin_root: &str) -> Outcome {
    // Debug builds only: lets tests prove a panic still yields `{}`.
    #[cfg(debug_assertions)]
    if std::env::var_os("BL_FORCE_PANIC").is_some() {
        panic!("forced by BL_FORCE_PANIC");
    }
    if var("BUILD_LOOP_RALLY_DEBUG").ok().flatten().as_deref() == Some("1") {
        return Outcome::Fallback { reason: "debug stderr", stdin: None };
    }
    match (which("perl"), which("python3")) {
        (Ok(Some(_)), Ok(Some(_))) => {}
        _ => return Outcome::Fallback { reason: "perl or python3 missing", stdin: None },
    }
    let stdin = if sys::stdin_is_tty() {
        None
    } else {
        let mut buf = Vec::new();
        let _ = std::io::stdin().lock().take(bl_contract::STDIN_MAX_BYTES as u64).read_to_end(&mut buf);
        Some(buf)
    };
    let bytes = stdin.as_deref().unwrap_or(&[]);
    let plan = match plan(plugin_root, bytes) {
        Ok(Some(p)) => p,
        Ok(None) => return Outcome::Done,
        Err(Fallback(reason)) => return Outcome::Fallback { reason, stdin },
    };
    for r in &plan.refreshes {
        refresh(&plan.python, plugin_root, &plan.cache_dir, r);
    }
    if let Some(work) = &plan.stamp {
        touch_and_join(&plan.python, plugin_root, work);
    }
    Outcome::Done
}
