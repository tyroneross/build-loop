// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! `bl hook trace`: the native path of
//! `python3 scripts/tool_trace.py --workdir W hook --phase P`.
//!
//! Mirrors `tool_trace.main` -> `record_hook` -> `append_span` byte for byte:
//! one JSON line, `json.dumps(span, sort_keys=True, separators=(",", ":"))`
//! plus `"\n"`, appended to `.build-loop/telemetry/tool-traces.jsonl` while
//! holding `fcntl.flock(LOCK_EX)` on `tool-traces.jsonl.lock` (the lock
//! `atomic_io.LockedFile` takes, so Python and `bl` exclude each other),
//! polled every 50 ms for 1 s, after rotating at 10 MiB into `.1`/`.2`.
//! Nothing is printed and the exit status is 0, as in Python, whose `main`
//! swallows every exception.
//!
//! UTF-8 host events use native parsing, redaction and temporal attribution.
//! JSON is bounded to 64 MiB and 256 container levels before recursive parsing.
//! Finite floats and arbitrary integers preserve the Python argument hash format.
//! Invalid encoding, non-string identity metadata, non-finite JSON extensions and
//! invalid JSON produces no record and an opt-in static diagnostic.
//! Unusable timestamps retain observation-time records without run attribution.
//! Saved state accepts one leading UTF-8 BOM; event input remains strict JSON.
//! Trace input never invokes Python. Missing/unreadable state means no run owner.

use bl_contract::{py, Fallback, R};
use serde_json::{Map, Value};
use json::RawValue;
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::io::AsRawFd;
use std::path::Path;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::sys;

#[path = "trace_redact.rs"]
mod redaction;
#[path = "trace_json.rs"]
mod json;
#[path = "trace_time.rs"]
mod temporal;

/// `tool_trace.MAX_PREVIEW_CHARS`.
const MAX_PREVIEW_CHARS: usize = 512;
/// `tool_trace.MAX_FILE_BYTES`.
const MAX_FILE_BYTES: u64 = 10 * 1024 * 1024;
/// `tool_trace.ROTATED_FILES` is 2: `.1` and `.2`.
const ROTATED: [&str; 2] = [".1", ".2"];
/// `transcript_adapter.MAX_SOURCE_BYTES`: larger state files are never parsed.
const MAX_STATE_BYTES: u64 = 64 * 1024 * 1024;

/// `LockedFile(path, timeout_s=1)` and its 50 ms poll.
const LOCK_TIMEOUT: Duration = Duration::from_secs(1);
const LOCK_POLL: Duration = Duration::from_millis(50);

pub const PHASES: [&str; 3] = ["start", "end", "error"];

/// Lower-cased substrings at least one of which every `SENSITIVE_KEY` match
/// contains (`api[_-]?key`, `private[_-]?key` spelled out).
const KEY_WORDS: [&str; 13] = [
    "authorization", "cookie", "password", "passwd", "secret", "token", "credential",
    "apikey", "api_key", "api-key", "privatekey", "private_key", "private-key",
];

pub enum Outcome {
    Done,
}

pub struct Args {
    pub workdir: String,
    pub phase: String,
}

/// `--workdir V` / `--workdir=V` (default `.`) and `--phase P`, each at most
/// once, in any order. Anything else is `None`: exit 75, let Python's
/// argparse handle it.
pub fn parse_args(args: &[String]) -> Option<Args> {
    let mut workdir: Option<String> = None;
    let mut phase: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        let (name, value) = match args[i].split_once('=') {
            Some((n, v)) if n.starts_with("--") => (n, v.to_string()),
            _ => {
                let v = args.get(i + 1)?;
                if v.starts_with('-') {
                    return None;
                }
                i += 1;
                (args[i - 1].as_str(), v.clone())
            }
        };
        let slot = match name {
            "--workdir" => &mut workdir,
            "--phase" => &mut phase,
            _ => return None,
        };
        if slot.replace(value).is_some() {
            return None;
        }
        i += 1;
    }
    let phase = phase.filter(|p| PHASES.contains(&p.as_str()))?;
    Some(Args { workdir: workdir.unwrap_or_else(|| ".".to_string()), phase })
}

pub fn run(args: &Args) -> Outcome {
    let fallback = |reason: &'static str, _stdin: Option<Vec<u8>>| {
        let category = match reason {
            "stdin not utf-8" => "unsupported_encoding",
            "non-string field" => "unsupported_metadata",
            "json byte limit" | "json depth limit" => "unsupported_limits",
            "nonfinite number" => "unsupported_number",
            "invalid json" | "codex response json" => "unsupported_json",
            _ => "unsupported_environment",
        };
        crate::hook_diagnostic("trace", category);
        Outcome::Done
    };
    // `Path(args.workdir).resolve()` runs before stdin is read.
    let Some(cwd) = std::env::current_dir().ok().and_then(|p| p.to_str().map(String::from)) else {
        return fallback("cwd not utf-8", None);
    };
    let workdir = match py::resolve(&args.workdir, &cwd) {
        Ok(w) => w,
        Err(Fallback(reason)) => return fallback(reason, None),
    };
    if sys::stdin_is_tty() {
        crate::hook_diagnostic("trace", "no_record");
        return Outcome::Done; // `_payload` reads nothing -> {} -> no tool name.
    }
    let mut bytes = Vec::new();
    if std::io::stdin().lock().take(json::MAX_BYTES as u64 + 1).read_to_end(&mut bytes).is_err() {
        return fallback("stdin read error", Some(bytes));
    }
    match plan(&workdir, &args.phase, &bytes, now_ns()) {
        Ok(Some(line)) => {
            match append(Path::new(&workdir), line.as_bytes()) {
                Ok(()) => crate::hook_diagnostic("trace", "append_ok"),
                Err(_) => crate::hook_diagnostic("trace", "append_error"),
            }
            Outcome::Done
        }
        Ok(None) => {
            crate::hook_diagnostic("trace", "no_record");
            Outcome::Done
        },
        Err(Fallback(reason)) => fallback(reason, Some(bytes)),
    }
}

fn now_ns() -> u128 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0)
}

/// `_payload` + `record_hook` + `build_span`: the line to append, `None`
/// when Python writes nothing.
pub fn plan(workdir: &str, phase: &str, stdin: &[u8], now_ns: u128) -> R<Option<String>> {
    let text = std::str::from_utf8(stdin).map_err(|_| Fallback("stdin not utf-8"))?;
    if py::strip(text).is_empty() {
        return Ok(None);
    }
    let raw = json::raw(text)?;
    if !raw.get().starts_with('{') { return Ok(None); }
    let data = json::object(&raw)?;
    let phase = codex_failure_phase(phase, &data)?;
    let tool_name = text_field(&data, &["tool_name", "tool"])?;
    if tool_name.is_empty() {
        return Ok(None);
    }
    let session_id = match text_field(&data, &["session_id"])? {
        s if !s.is_empty() => s,
        _ => match std::env::var_os("CLAUDE_SESSION_ID") {
            Some(v) if !v.is_empty() => v.into_string().map_err(|_| Fallback("non-utf8 env"))?,
            _ => "unknown".to_string(),
        },
    };
    let tool_use_id = text_field(&data, &["tool_use_id", "tool_call_id"])?;
    let timestamp = match data.get("timestamp") {
        Some(v) if json::truthy(v)? => {
            let parsed = json::text(v).ok().and_then(|text| temporal::event_time(&text));
            if parsed.is_none() {
                crate::hook_diagnostic("trace", "timestamp_unavailable");
            }
            parsed
        }
        _ => None,
    };
    let run_state = timestamp.and_then(|_| read_state(workdir));
    let run_id = timestamp.and_then(|t| run_state.as_ref().and_then(|s| temporal::attached_run(s, &session_id, t)));
    let result = if data.contains_key("tool_response") { data.get("tool_response") } else { data.get("tool_result") };
    let is_error = phase == "error" || data.get("is_error").map(|v| json::truthy(v)).transpose()?.unwrap_or(false);

    let mut attrs: BTreeMap<String, String> = BTreeMap::new();
    attrs.insert("gen_ai.operation.name".into(), py::json_string("execute_tool"));
    attrs.insert("gen_ai.tool.name".into(), py::json_string(&tool_name));
    attrs.insert("build_loop.tool.call_id_sha256".into(), py::json_string(&py::sha256_hex(&tool_use_id)));
    attrs.insert("session.id".into(), py::json_string(&session_id));
    attrs.insert("build_loop.run_id".into(), run_id.map(py::json_string).unwrap_or_else(|| "null".into()));
    attrs.insert("build_loop.tool.phase".into(), py::json_string(phase));
    for (prefix, value) in [("gen_ai.tool.call.arguments", data.get("tool_input")), ("gen_ai.tool.call.result", result)] {
        match value {
            None => {},
            Some(v) if v.get() == "null" => {},
            Some(v) => content_attributes(prefix, v, &mut attrs)?,
        }
    }
    let status = if is_error {
        "ERROR"
    } else if phase == "end" {
        "OK"
    } else {
        "UNSET"
    };
    let attributes = format!("{{{}}}", join(&attrs));
    let mut span: BTreeMap<String, String> = BTreeMap::new();
    span.insert("schema".into(), py::json_string("build-loop.otel-tool-span.v1"));
    span.insert("trace_id".into(), py::json_string(&py::sha256_hex(&session_id)[..32]));
    let span_id = py::sha256_hex(&format!("{session_id}|{tool_use_id}|{tool_name}|0"));
    span.insert("span_id".into(), py::json_string(&span_id[..16]));
    span.insert("parent_span_id".into(), "null".into());
    span.insert("name".into(), py::json_string(&format!("execute_tool {tool_name}")));
    span.insert("kind".into(), py::json_string("INTERNAL"));
    span.insert("event".into(), py::json_string(phase));
    span.insert("start_time_unix_nano".into(), timestamp.map(temporal::nanos).filter(|t| *t != 0).map(|t| t.to_string()).unwrap_or_else(|| now_ns.to_string()));
    span.insert("end_time_unix_nano".into(), "null".into());
    span.insert("duration_ms".into(), "null".into());
    span.insert("status".into(), format!("{{\"code\":{}}}", py::json_string(status)));
    span.insert("attributes".into(), attributes);
    Ok(Some(format!("{{{}}}\n", join(&span))))
}

fn join(map: &BTreeMap<String, String>) -> String {
    map.iter().map(|(k, v)| format!("{}:{v}", py::json_string(k))).collect::<Vec<_>>().join(",")
}

/// `str(payload.get(a) or payload.get(b) or "")` for string values.
fn text_field(data: &BTreeMap<String, RawValue<'_>>, keys: &[&str]) -> R<String> {
    for key in keys {
        match data.get(*key) {
            Some(v) if json::truthy(v)? => {
                return json::text(v).map_err(|_| Fallback("non-string field"));
            }
            _ => {}
        }
    }
    Ok(String::new())
}

/// `_content_attributes`.
fn content_attributes(prefix: &str, value: &RawValue, attrs: &mut BTreeMap<String, String>) -> R<()> {
    let encoded = json::redacted(value)?;
    let chars = encoded.chars().count();
    let preview: String = encoded.chars().take(MAX_PREVIEW_CHARS).collect();
    attrs.insert(format!("{prefix}.bytes"), encoded.len().to_string());
    attrs.insert(format!("{prefix}.sha256"), py::json_string(&py::sha256_hex(&encoded)));
    attrs.insert(format!("{prefix}.preview"), py::json_string(&preview));
    attrs.insert(format!("{prefix}.truncated"), (chars > MAX_PREVIEW_CHARS).to_string());
    if let Some(keys) = json::redacted_keys(value)? {
        let list: Vec<String> = keys.into_iter().map(|k| py::json_string(&k)).collect();
        attrs.insert(format!("{prefix}.keys"), format!("[{}]", list.join(",")));
    }
    Ok(())
}

/// `_redact`, including inline assignments and bearer/token string values.
pub(crate) fn redact(value: &Value, key: &str) -> R<Value> {
    if redaction::sensitive_key(key) {
        return Ok(Value::String("[REDACTED]".into()));
    }
    Ok(match value {
        Value::Object(map) => {
            let mut out = Map::new();
            for (k, v) in map {
                // Map iteration is sorted by original key: sanitized collisions
                // deterministically retain the last original key, as in Python.
                out.insert(redaction::redact(k), redact(v, k)?);
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().take(50).map(|v| redact(v, "")).collect::<R<_>>()?),
        Value::String(s) => Value::String(redaction::redact(s)),
        other => other.clone(),
    })
}

/// `codex_failure_phase`.
fn codex_failure_phase<'a>(phase: &'a str, data: &BTreeMap<String, RawValue<'_>>) -> R<&'a str> {
    if phase != "end" || !data.contains_key("turn_id") {
        return Ok(phase);
    }
    let response = if data.contains_key("tool_response") { data.get("tool_response") } else { data.get("tool_result") };
    Ok(if codex_response_failed(response)? { "error" } else { phase })
}

/// `_codex_response_failed` after `_decode_maybe_json`.
fn codex_response_failed(response: Option<&RawValue>) -> R<bool> {
    let Some(mut response) = response else { return Ok(false); };
    let decoded;
    let parsed;
    if response.get().starts_with('"') {
        decoded = json::text(response)?;
        response = match json::raw(&decoded) {
            Ok(v) => { parsed = v; &parsed },
            Err(_) if python_may_decode(&decoded) => return Err(Fallback("codex response json")),
            Err(_) => return failed_process(&decoded),
        };
    }
    if response.get().starts_with('"') { return failed_process(&json::text(response)?); }
    if !response.get().starts_with('{') { return Ok(false); }
    let object = json::object(response)?;
    if object.get("is_error").is_some_and(|v| v.get() == "true")
        || object.get("error").map(|v| json::truthy(v)).transpose()?.unwrap_or(false) {
        return Ok(true);
    }
    for key in ["exit_code", "exitCode", "returncode", "status_code"] {
        if let Some(value) = object.get(key) {
            if json::number_nonzero(value)? { return Ok(true); }
        }
    }
    for key in ["output", "stdout", "content"] {
        if let Some(value) = object.get(key).filter(|v| v.get().starts_with('"')) {
            if failed_process(&json::text(value)?)? { return Ok(true); }
        }
    }
    Ok(false)
}

/// Whether `json.loads` might accept a string outside the native contract:
/// Python also takes `NaN`/`Infinity`, lone surrogate escapes, deeper nesting
/// and non-finite exponents (`1e999`). A number can only occur
/// in a document whose first character is `{`, `[`, `-` or a digit (a
/// string, `true`, `false` or `null` start cannot contain one), so for those
/// any digit counts.
fn python_may_decode(s: &str) -> bool {
    let first = s.trim_start_matches([' ', '\t', '\n', '\r']).bytes().next();
    let may_hold_number = matches!(first, Some(b'{' | b'[' | b'-' | b'0'..=b'9'));
    s.contains("NaN")
        || s.contains("Infinity")
        || s.contains("\\u")
        || (may_hold_number && s.bytes().any(|b| b.is_ascii_digit()))
        || s.matches(['[', '{']).count() >= 100
}

/// `FAILED_PROCESS.search(s)`, preserving Python Unicode matching.
fn failed_process(s: &str) -> R<bool> {
    Ok(redaction::failed_process(s))
}

/// Bounded regular-file metadata only; malformed/missing state cannot confer
/// run membership. No timestamp means no run attribution and no state read.
fn read_state(workdir: &str) -> Option<Value> {
    let file = OpenOptions::new().read(true).custom_flags(sys::O_NONBLOCK)
        .open(Path::new(workdir).join(".build-loop/state.json")).ok()?;
    let meta = file.metadata().ok()?;
    if !meta.file_type().is_file() || meta.size() > MAX_STATE_BYTES { return None; }
    let mut bytes = Vec::new();
    (&file).take(MAX_STATE_BYTES + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > MAX_STATE_BYTES { return None; }
    let bytes = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(&bytes);
    let parsed = json::syntax(std::str::from_utf8(bytes).ok()?).ok()?;
    let root = json::object(&parsed).ok()?;
    // Only ownership metadata matters. An unrelated arbitrary integer must not
    // make an otherwise valid active-run snapshot unreadable by normal Value.
    let select = |map: &BTreeMap<String, RawValue<'_>>, keys: &[&str]| -> Map<String, Value> {
        keys.iter().filter_map(|key| map.get(*key).map(|v| {
            let value = if v.get() == "null" { Value::Null }
                else { json::text(v).map(Value::String).unwrap_or(Value::Bool(false)) };
            ((*key).to_string(), value)
        })).collect()
    };
    let execution = json::object(root.get("execution")?).ok()?;
    let mut state = select(&root, &["run_id", "build_loop_id"]);
    state.insert("execution".into(), Value::Object(select(&execution,
        &["run_id", "build_loop_id", "started_at", "ended_at", "current_session_id", "started_by_session_id"])));
    Some(Value::Object(state))
}

/// `append_span`: mkdir, lock, rotate, append. Errors end the run silently,
/// as Python's `main` does.
pub(crate) fn append(workdir: &Path, line: &[u8]) -> std::io::Result<()> {
    let dir = workdir.join(".build-loop/telemetry");
    fs::create_dir_all(&dir)?;
    let path = dir.join("tool-traces.jsonl");
    let lock = OpenOptions::new().read(true).write(true).create(true).truncate(false).mode(0o644).open(dir.join("tool-traces.jsonl.lock"))?;
    let deadline = Instant::now() + LOCK_TIMEOUT;
    loop {
        match sys::flock_exclusive_nb(lock.as_raw_fd()) {
            Ok(()) => break,
            Err(e) if e.raw_os_error() == Some(sys::EWOULDBLOCK) => {
                if Instant::now() >= deadline {
                    crate::hook_diagnostic("trace", "lock_timeout");
                    return Err(e);
                }
                std::thread::sleep(LOCK_POLL);
            }
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    // The whole-run deadline waits for this section, so it never leaves a
    // partial line or a half-done rotation.
    let _guard = crate::EXIT.lock();
    rotate(&path)?;
    let mut file = OpenOptions::new().append(true).create(true).mode(0o666).open(&path)?;
    file.write_all(line)?;
    drop(file);
    drop(lock); // closing the descriptor releases the flock
    Ok(())
}

/// `_rotate`. `exists()` follows symlinks, like `fs::metadata`.
fn rotate(path: &Path) -> std::io::Result<()> {
    match fs::metadata(path) {
        Ok(m) if m.len() >= MAX_FILE_BYTES => {}
        _ => return Ok(()),
    }
    let rotated = |suffix: &str| {
        let mut name = path.as_os_str().to_owned();
        name.push(suffix);
        std::path::PathBuf::from(name)
    };
    let oldest = rotated(ROTATED[1]);
    if fs::metadata(&oldest).is_ok() {
        fs::remove_file(&oldest)?;
    }
    let first = rotated(ROTATED[0]);
    if fs::metadata(&first).is_ok() {
        fs::rename(&first, &oldest)?;
    }
    fs::rename(path, &first)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(v: &[&str]) -> Option<Args> {
        parse_args(&v.iter().map(|s| s.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn argument_forms() {
        let a = args(&["--workdir", "/w", "--phase", "end"]).unwrap();
        assert_eq!((a.workdir.as_str(), a.phase.as_str()), ("/w", "end"));
        let a = args(&["--phase=start", "--workdir="]).unwrap();
        assert_eq!((a.workdir.as_str(), a.phase.as_str()), ("", "start"));
        assert_eq!(args(&["--phase", "error"]).unwrap().workdir, ".");
        assert!(args(&["--phase", "begin"]).is_none());
        assert!(args(&["--workdir", "/w"]).is_none());
        assert!(args(&["--phase", "end", "--phase", "end"]).is_none());
        assert!(args(&["--work", "/w", "--phase", "end"]).is_none());
        assert!(args(&["--workdir", "-x", "--phase", "end"]).is_none());
        assert!(args(&["--phase"]).is_none());
    }

    fn line(stdin: &str) -> R<Option<String>> {
        plan("/nonexistent-bl-trace", "end", stdin.as_bytes(), 7)
    }

    #[test]
    fn span_bytes() {
        let out = line(r#"{"tool_name":"Bash","session_id":"s","tool_use_id":"u","tool_input":{"b":1,"a":"é"}}"#)
            .unwrap()
            .unwrap();
        let h = |s: &str| py::sha256_hex(s);
        let enc = r#"{"a":"é","b":1}"#;
        let want = format!(
            concat!(
                r#"{{"attributes":{{"build_loop.run_id":null,"build_loop.tool.call_id_sha256":"{}","#,
                r#""build_loop.tool.phase":"end","gen_ai.operation.name":"execute_tool","#,
                r#""gen_ai.tool.call.arguments.bytes":{},"gen_ai.tool.call.arguments.keys":["a","b"],"#,
                r#""gen_ai.tool.call.arguments.preview":"{{\"a\":\"\u00e9\",\"b\":1}}","#,
                r#""gen_ai.tool.call.arguments.sha256":"{}","gen_ai.tool.call.arguments.truncated":false,"#,
                r#""gen_ai.tool.name":"Bash","session.id":"s"}},"duration_ms":null,"end_time_unix_nano":null,"#,
                r#""event":"end","kind":"INTERNAL","name":"execute_tool Bash","parent_span_id":null,"#,
                r#""schema":"build-loop.otel-tool-span.v1","span_id":"{}","start_time_unix_nano":7,"#,
                r#""status":{{"code":"OK"}},"trace_id":"{}"}}"#,
                "\n"
            ),
            h("u"),
            enc.len(),
            h(enc),
            &h("s|u|Bash|0")[..16],
            &h("s")[..32],
        );
        assert_eq!(out, want);
    }

    #[test]
    fn nothing_written() {
        for stdin in ["", " \n", "[1]", "{}", r#"{"tool_name":""}"#] {
            assert_eq!(line(stdin).unwrap(), None, "{stdin}");
        }
    }

    #[test]
    fn unsupported_inputs() {
        for stdin in [
            "{x",
            r#"{"tool_name": "B", "#,
            r#"{"tool_name":1}"#,
            r#"{"tool_name":"B","turn_id":1,"tool_response":"{\"exit_code\": NaN}"}"#,
        ] {
            assert!(line(stdin).is_err(), "{stdin}");
        }
        assert!(plan("/x", "end", b"\xff", 0).is_err());
    }

    #[test]
    fn redaction_and_bounds() {
        let v: Value = serde_json::from_str(r#"{"API-Key":"x","nested":{"cookie_jar":[1]},"l":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,"Bearer x"]}"#).unwrap();
        let r = redact(&v, "").unwrap();
        assert_eq!(r["API-Key"], "[REDACTED]");
        assert_eq!(r["nested"]["cookie_jar"], "[REDACTED]");
        assert_eq!(r["l"].as_array().unwrap().len(), 50);
    }

    #[test]
    fn codex_failures() {
        let p = |s: &str| {
            let parsed = json::raw(s).unwrap();
            let v = json::object(&parsed).unwrap();
            codex_failure_phase("end", &v).map(String::from)
        };
        assert_eq!(p(r#"{"turn_id":1,"tool_response":{"exit_code":3}}"#).unwrap(), "error");
        assert_eq!(p(r#"{"turn_id":1,"tool_response":{"exitCode":0,"output":"ok"}}"#).unwrap(), "end");
        assert_eq!(p(r#"{"turn_id":1,"tool_result":{"error":"x"}}"#).unwrap(), "error");
        assert_eq!(p(r#"{"turn_id":1,"tool_response":"{\"is_error\":true}"}"#).unwrap(), "error");
        assert_eq!(p(r#"{"turn_id":1,"tool_response":{"returncode":true}}"#).unwrap(), "end");
        assert_eq!(p(r#"{"tool_response":{"exit_code":3}}"#).unwrap(), "end");
        assert_eq!(p(r#"{"turn_id":1,"tool_response":{"output":"command failed: 2"}}"#).unwrap(), "error");
        for big in [r#"{\"returncode\": 1e999}"#, r#"{\"error\": 1e999}"#, r#"[1e999]"#] {
            let payload = format!(r#"{{"turn_id":1,"tool_response":"{big}"}}"#);
            assert!(p(&payload).is_err(), "{payload}");
        }
        assert_eq!(p(r#"{"turn_id":1,"tool_response":"total 0\nREADME.md"}"#).unwrap(), "end");
    }

    #[test]
    fn rotation_names() {
        let dir = std::env::temp_dir().join(format!("bl-trace-rot-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("t.jsonl");
        fs::write(&path, vec![b'x'; MAX_FILE_BYTES as usize]).unwrap();
        fs::write(dir.join("t.jsonl.1"), b"one").unwrap();
        fs::write(dir.join("t.jsonl.2"), b"two").unwrap();
        rotate(&path).unwrap();
        assert!(!path.exists());
        assert_eq!(fs::read(dir.join("t.jsonl.2")).unwrap(), b"one");
        assert_eq!(fs::metadata(dir.join("t.jsonl.1")).unwrap().len(), MAX_FILE_BYTES);
        fs::remove_dir_all(&dir).unwrap();
    }
}
