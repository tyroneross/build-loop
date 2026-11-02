//! Dependency-free process supervisor for verification logs. Build with rustc.
#[cfg(unix)]
use std::os::unix::{
    fs::OpenOptionsExt,
    process::{CommandExt, ExitStatusExt},
};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    process::{self, Child, Command, Stdio},
    sync::atomic::{AtomicI32, Ordering},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

static INTERRUPTED: AtomicI32 = AtomicI32::new(0);
#[cfg(unix)]
unsafe extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
    fn getppid() -> i32;
    fn signal(sig: i32, handler: usize) -> usize;
}
#[cfg(unix)]
extern "C" fn interrupted(sig: i32) {
    INTERRUPTED.store(sig, Ordering::Relaxed);
}

fn json(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
fn create(path: &Path) -> io::Result<File> {
    let mut opts = OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    opts.mode(0o600);
    opts.open(path)
}
fn atomic_status(dir: &Path, status: &str) -> io::Result<()> {
    let tmp = dir.join("runner-status.tmp");
    let mut f = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&tmp)?;
    f.write_all(status.as_bytes())?;
    f.sync_data()?;
    fs::rename(tmp, dir.join("runner-status.json"))
}
struct OwnedChild(Child, bool);
impl OwnedChild {
    fn signal(&mut self, sig: i32) {
        if sig == 9 {
            self.1 = true;
        }
        #[cfg(unix)]
        unsafe {
            kill(-(self.0.id() as i32), sig);
        }
        #[cfg(not(unix))]
        {
            let _ = sig;
            let _ = self.0.kill();
        }
    }
}
impl Drop for OwnedChild {
    fn drop(&mut self) {
        if !self.1 {
            self.signal(9);
        }
        let _ = self.0.wait();
    }
}
fn run() -> Result<i32, Box<dyn std::error::Error>> {
    let mut args = env::args().skip(1);
    let mut dir = None;
    let mut cwd = None;
    let mut timeout_ms = 0u64;
    let mut parent_pid: Option<i32> = None;
    let mut command = Vec::new();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--log-dir" => dir = args.next().map(PathBuf::from),
            "--cwd" => cwd = args.next().map(PathBuf::from),
            "--timeout-ms" => timeout_ms = args.next().ok_or("missing timeout")?.parse()?,
            "--parent-pid" => parent_pid = Some(args.next().ok_or("missing parent PID")?.parse()?),
            "--" => {
                command.extend(args);
                break;
            }
            _ => return Err(format!("unknown argument: {arg}").into()),
        }
    }
    let dir = dir.ok_or("missing --log-dir")?;
    if !dir.is_dir() || command.is_empty() {
        return Err("existing private log directory and command required".into());
    }
    // Refuse reuse: another run's output is never truncated.
    let stdout = create(&dir.join("stdout.log"))?;
    let stderr = create(&dir.join("stderr.log"))?;
    let mut events = create(&dir.join("runner-events.jsonl"))?;
    #[cfg(unix)]
    unsafe {
        signal(2, interrupted as *const () as usize);
        signal(15, interrupted as *const () as usize);
    }
    #[cfg(unix)]
    let parent_pid = parent_pid.unwrap_or_else(|| unsafe { getppid() });
    #[cfg(not(unix))]
    let _ = parent_pid;
    let mut cmd = Command::new(&command[0]);
    cmd.args(&command[1..])
        .stdin(Stdio::null())
        .stdout(stdout.try_clone()?)
        .stderr(stderr.try_clone()?);
    if let Some(cwd) = cwd {
        cmd.current_dir(cwd);
    }
    #[cfg(unix)]
    cmd.process_group(0);
    let mut child = OwnedChild(cmd.spawn()?, false);
    let start = Instant::now();
    let mut last_heartbeat = Instant::now();
    let mut stop_at: Option<Instant> = None;
    let mut timed_out = false;
    let mut cancelled = 0;
    eprintln!("build-loop progress: {}", dir.display());
    loop {
        let elapsed = start.elapsed();
        let epoch_ms = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis();
        let exit = child.0.try_wait()?;
        let sig = INTERRUPTED.load(Ordering::Relaxed);
        #[cfg(unix)]
        let sig = if unsafe { getppid() } != parent_pid {
            15
        } else {
            sig
        };
        if exit.is_none()
            && stop_at.is_none()
            && (sig != 0 || (timeout_ms > 0 && elapsed.as_millis() >= timeout_ms as u128))
        {
            timed_out = sig == 0;
            cancelled = sig;
            child.signal(15);
            stop_at = Some(Instant::now());
        }
        if stop_at.is_some_and(|t| t.elapsed() >= Duration::from_secs(2)) {
            child.signal(9);
        }
        let state = if exit.is_some() {
            if timed_out {
                "timed_out"
            } else if cancelled != 0 {
                "cancelled"
            } else {
                "finished"
            }
        } else {
            "running"
        };
        let code = exit.map(|s| {
            if timed_out {
                124
            } else if cancelled != 0 {
                128 + cancelled
            } else {
                #[cfg(unix)]
                {
                    s.code().unwrap_or_else(|| 128 + s.signal().unwrap_or(1))
                }
                #[cfg(not(unix))]
                {
                    s.code().unwrap_or(1)
                }
            }
        });
        let terminal = exit.is_some();
        if terminal && !child.1 {
            child.signal(9);
        }
        if terminal
            || last_heartbeat.elapsed() >= Duration::from_secs(5)
            || elapsed < Duration::from_millis(100)
        {
            // Sync output before advertising a durable heartbeat or completion.
            stdout.sync_data()?;
            stderr.sync_data()?;
            let status = format!("{{\"schema\":\"build-loop.runner.v1\",\"state\":{},\"supervisor_pid\":{},\"child_pid\":{},\"updated_at_ms\":{},\"elapsed_ms\":{},\"stdout_bytes\":{},\"stderr_bytes\":{},\"exit_code\":{},\"process_group\":{}}}", json(state), process::id(), child.0.id(), epoch_ms, elapsed.as_millis(), stdout.metadata()?.len(), stderr.metadata()?.len(), code.map_or("null".into(), |n| n.to_string()), cfg!(unix));
            atomic_status(&dir, &status)?;
            writeln!(events, "{status}")?;
            events.sync_data()?;
            let mut active = String::new();
            if let Ok(f) = File::open(dir.join("pytest-status.json")) {
                let _ = f.take(2048).read_to_string(&mut active);
            }
            eprintln!(
                "build-loop progress: {state}, {}s, {}{}",
                elapsed.as_secs(),
                dir.display(),
                if active.is_empty() {
                    String::new()
                } else {
                    format!(" {active}")
                }
            );
            last_heartbeat = Instant::now();
        }
        if let Some(code) = code {
            return Ok(code);
        }
        thread::sleep(Duration::from_millis(50));
    }
}
fn main() {
    match run() {
        Ok(code) => process::exit(code),
        Err(error) => {
            eprintln!("build-loop progress runner error: {error}");
            process::exit(125);
        }
    }
}
