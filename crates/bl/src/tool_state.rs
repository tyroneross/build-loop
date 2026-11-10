// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! The SessionStart `tool_state_paths.py snapshot --no-exclude` contract.
//! Reports and explicit exclusion writes remain in Python. Git resolves shared
//! storage and hooksPath; locks interoperate with atomic_io.LockedFile.
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use bl_contract::py;
use serde_json::{json, Value};
use crate::sys;

const DIRS: [&str; 2] = [".build-loop", ".rally"];
const GRACE: f64 = 30.0;

fn epoch(t: SystemTime) -> f64 {
    match t.duration_since(UNIX_EPOCH) {
        Ok(t) => t.as_secs_f64(),
        Err(e) => -e.duration().as_secs_f64(),
    }
}

fn status(path: &Path, now: f64, until: Instant) -> &'static str {
    if !path.is_dir() { return "absent"; }
    let Ok(meta) = fs::metadata(path) else { return "new"; };
    // Python uses st_birthtime on macOS, but mtime on Linux even when
    // statx/Metadata::created could expose a birth time there.
    #[cfg(target_os = "macos")]
    if let Ok(birth) = meta.created() {
        return if epoch(birth) >= now - GRACE { "new" } else { "pre-existing" };
    }
    let mtime = |m: &fs::Metadata| m.mtime() as f64 + m.mtime_nsec() as f64 * 1e-9;
    if mtime(&meta) < now - GRACE { return "pre-existing"; }
    let mut pending = vec![path.to_path_buf()];
    let mut seen = 0;
    while let Some(dir) = pending.pop() {
        let Ok(entries) = fs::read_dir(dir) else { continue; };
        let entries: Vec<_> = entries.filter_map(Result::ok).collect();
        // os.walk visits directories before files and does not follow links.
        let mut children = Vec::new();
        for is_dir in [true, false] {
            for entry in &entries {
                if Instant::now() >= until { return "new"; }
                let p = entry.path();
                if p.is_dir() != is_dir { continue; }
                seen += 1;
                if seen > 5000 { return "new"; }
                if let Ok(m) = fs::metadata(&p) {
                    if mtime(&m) < now - GRACE { return "pre-existing"; }
                }
                if is_dir && !p.is_symlink() { children.push(p); }
            }
        }
        pending.extend(children.into_iter().rev());
    }
    "new"
}

fn decode_ignoring_errors(mut bytes: &[u8]) -> String {
    let mut out = String::new();
    while !bytes.is_empty() {
        match std::str::from_utf8(bytes) {
            Ok(text) => { out.push_str(text); break; }
            Err(e) => {
                out.push_str(std::str::from_utf8(&bytes[..e.valid_up_to()]).unwrap());
                let Some(invalid) = e.error_len() else { break; };
                bytes = &bytes[e.valid_up_to() + invalid..];
            }
        }
    }
    out
}

fn hooks(path: &Path, now: f64, until: Instant) -> Option<Value> {
    let mut out = serde_json::Map::new();
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            if Instant::now() >= until { break; }
            let path = entry.path();
            let name = entry.file_name().to_str().map(str::to_owned)?;
            if name.ends_with(".sample") || !path.is_file() { continue; }
            let Ok(m) = fs::metadata(&path) else { continue; };
            let mtime = m.mtime() as f64 + m.mtime_nsec() as f64 * 1e-9;
            let mut bytes = Vec::new();
            let marked = mtime >= now - GRACE && File::open(&path)
                .and_then(|f| f.take(65536).read_to_end(&mut bytes)).is_ok()
                && {
                    // Invalid UTF-8 is ignored by Python; dropping it matters
                    // when it occurs inside the identifying word.
                    let text = decode_ignoring_errors(&bytes);
                    // Python re.IGNORECASE matches both dotted/dotless I to i.
                    let text: String = text.chars().map(|c| match c {
                        '\u{130}' | '\u{131}' => 'i',
                        _ => c.to_ascii_lowercase(),
                    }).collect();
                    text.contains("build-loop") || text.contains("rally")
                };
            out.insert(name, json!({"mtime": mtime, "installed_this_session": marked}));
        }
    }
    Some(Value::Object(out))
}

fn write_state(path: &Path, bytes: &[u8], until: Instant) -> std::io::Result<()> {
    let parent = path.parent().unwrap();
    fs::create_dir_all(parent)?;
    let lock = OpenOptions::new().read(true).write(true).create(true).truncate(false)
        .mode(0o644).open(path.with_extension("json.lock"))?;
    let lock_until = until.min(Instant::now() + Duration::from_millis(200));
    loop {
        match sys::flock_exclusive_nb(lock.as_raw_fd()) {
            Ok(()) => break,
            Err(e) if e.raw_os_error() == Some(sys::EWOULDBLOCK) && Instant::now() < lock_until => {
                std::thread::sleep(Duration::from_millis(50).min(lock_until.saturating_duration_since(Instant::now())));
            }
            Err(e) => return Err(e),
        }
    }
    if Instant::now() >= until { return Ok(()); }
    let mode = fs::metadata(path).map(|m| m.permissions().mode() & 0o7777).unwrap_or(0o644);
    let temp = parent.join(format!("{}.tmp.{}-{}", path.file_name().unwrap().to_string_lossy(),
        std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos()));
    let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temp)?;
    let result = (|| {
        file.set_permissions(fs::Permissions::from_mode(mode))?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temp, path)
    })();
    if result.is_err() { let _ = fs::remove_file(&temp); }
    result
}

/// `git` is supplied by the SessionStart supervisor, so every child retains
/// its sub-step deadline and cancellation/process-group ownership.
pub fn snapshot(workdir: &str, input: &[u8], until: Instant,
    mut git: impl FnMut(&Path, &[&str]) -> Option<Vec<u8>>) -> Option<Vec<u8>>
{
    let Ok(workdir) = fs::canonicalize(workdir) else { return Some(Vec::new()); };
    let resolve = |raw: Vec<u8>| -> Option<PathBuf> {
        let text = String::from_utf8(raw).ok()?;
        let text = py::strip(&text);
        if text.is_empty() { return None; }
        let path = workdir.join(text);
        let resolved = py::realpath(path.to_str()?, workdir.to_str()?).ok()?;
        Some(PathBuf::from(resolved))
    };
    // One Git invocation validates the repository and locates common storage.
    let Some(common) = git(&workdir, &["rev-parse", "--git-common-dir"]).and_then(&resolve) else { return Some(Vec::new()); };
    let now = epoch(SystemTime::now());
    let mut dirs = serde_json::Map::new();
    for name in DIRS { dirs.insert(name.into(), json!({"status": status(&workdir.join(name), now, until)})); }
    let hooks = git(&workdir, &["rev-parse", "--git-path", "hooks"])
        .and_then(resolve).map(|p| hooks(&p, now, until)).unwrap_or_else(|| Some(json!({})))?;
    let event: Value = serde_json::from_slice(input).unwrap_or(Value::Null);
    let sid = event.get("session_id").and_then(Value::as_str).map(py::strip)
        .filter(|s| !s.is_empty()).unwrap_or("default");
    let clean: String = sid.chars().map(|c| if c.is_ascii_alphanumeric() || "._-".contains(c) { c } else { '_' }).collect();
    let state = json!({"session_id": sid, "snapshot_at": now, "dirs": dirs, "hooks": hooks, "excluded": []});
    let mut bytes = serde_json::to_vec_pretty(&state).unwrap();
    bytes.push(b'\n');
    if Instant::now() >= until { return Some(Vec::new()); }
    for name in [format!("{clean}.json"), "latest.json".into()] {
        let _ = write_state(&common.join("build-loop/tool-state").join(name), &bytes, until);
    }
    if Instant::now() >= until { return Some(Vec::new()); }
    let mut lines = Vec::new();
    for name in DIRS {
        match dirs[name]["status"].as_str().unwrap() {
            "new" => lines.push(format!("- `{name}/` was created by build-loop's hooks this session.")),
            "absent" => lines.push(format!("- `{name}/` did not exist before this session — if it appears, build-loop/rally hooks created it.")),
            _ => (),
        }
    }
    let installed: Vec<_> = hooks.as_object().unwrap().iter().filter_map(|(name, h)|
        (h["installed_this_session"] == true).then_some(name.as_str())).collect();
    if !installed.is_empty() { lines.push(format!("- git hooks installed this session: {}", installed.join(", "))); }
    if lines.is_empty() { return Some(Vec::new()); }
    lines.push("Treat these as tool state: omit them from reports or say \"created by build-loop's hooks\"; never call them pre-existing.".into());
    Some(format!("{{\"hookSpecificOutput\": {{\"hookEventName\": \"SessionStart\", \"additionalContext\": {}}}}}\n", py::json_string(&lines.join("\n"))).into_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_bytes_are_ignored_but_valid_replacement_chars_are_not() {
        assert_eq!(decode_ignoring_errors(b"ra\xfflly"), "rally");
        assert_eq!(decode_ignoring_errors("ra\u{fffd}lly".as_bytes()), "ra\u{fffd}lly");
    }
    #[test]
    fn old_directory_and_missing_directory_are_distinct() {
        let path = std::env::temp_dir().join(format!("bl-state-age-{}", std::process::id()));
        fs::create_dir_all(&path).unwrap();
        let until = Instant::now() + Duration::from_secs(2);
        assert_eq!(status(&path, epoch(SystemTime::now()) + 31.0, until), "pre-existing");
        assert_eq!(status(&path.join("missing"), epoch(SystemTime::now()), until), "absent");
        fs::remove_dir(&path).unwrap();
    }
}
