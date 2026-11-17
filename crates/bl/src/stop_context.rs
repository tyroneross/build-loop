// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Read-only Stop preflight. The shell retains the input and falls back to
//! its Python parser on unsupported input. Release and state writes stay in
//! their existing authorities; this helper never claims or mutates a session.

use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use serde_json::Value;

fn text(value: Option<&Value>) -> Option<&str> {
    match value {
        None | Some(Value::Null) => Some(""),
        Some(Value::String(s)) if !s.contains('\0') => Some(s),
        _ => None,
    }
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn env(name: &str) -> Option<String> {
    match std::env::var(name) {
        Ok(s) => Some(s),
        Err(std::env::VarError::NotPresent) => Some(String::new()),
        Err(_) => None,
    }
}

fn host(event: &serde_json::Map<String, Value>) -> Option<String> {
    for name in ["RALLY_TOOL_ID", "BUILD_LOOP_RALLY_TOOL", "RALLY_HOST_FAMILY"] {
        let value = env(name)?;
        // Python's Unicode strip rules differ from Rust's. Unusual identities
        // use the reference parser rather than changing session ownership.
        if !value.is_ascii() { return None; }
        let value = value.trim_matches([' ', '\t', '\r', '\n', '\x0b', '\x0c']);
        if value.bytes().any(|b| b < 32 && !b"\t\r\n".contains(&b)) { return None; }
        if !value.is_empty() { return Some(value.to_string()); }
    }
    Some(if event.contains_key("turn_id") || !env("CODEX_THREAD_ID")?.is_empty()
        || !env("CODEX_SESSION_ID")?.is_empty() { "codex" }
        else if !env("CURSOR_SESSION_ID")?.is_empty() { "cursor" }
        else { "claude_code" }.to_string())
}

fn state(path: &Path, session: &str) -> Option<(&'static str, String, &'static str)> {
    if !path.is_file() { return Some(("no", String::new(), "no")); }
    let file = std::fs::OpenOptions::new().read(true).custom_flags(crate::sys::O_NONBLOCK)
        .open(path).ok()?;
    if !file.metadata().ok()?.is_file() { return None; }
    // Same bounded fast-path envelope as other native hooks. Larger state is
    // still supported by the unchanged Python reader, not truncated here.
    let mut bytes = Vec::new();
    file.take((bl_contract::STDIN_MAX_BYTES + 1) as u64).read_to_end(&mut bytes).ok()?;
    if bytes.len() > bl_contract::STDIN_MAX_BYTES { return None; }
    let data: Value = serde_json::from_slice(&bytes).ok()?;
    let data = data.as_object()?;
    let phase = match data.get("phase") {
        None => String::new(),
        Some(Value::String(s)) if !s.contains('\0') => s.clone(),
        _ => return None,
    };
    let mut already = "no";
    if phase == "report" && !session.is_empty() {
        if let Some(runs) = data.get("runs") {
            if runs.as_array()?.iter().any(|r| r.get("session_id") == Some(&Value::String(session.to_string()))) {
                already = "yes";
            }
        }
    }
    Some(("yes", phase, already))
}

fn context(bytes: &[u8]) -> Option<String> {
    let event: Value = serde_json::from_slice(bytes).ok()?;
    let event = event.as_object()?;
    let active = truthy(event.get("stop_hook_active"));
    let agent = text(event.get("agent_id"))?;
    let session = text(event.get("session_id"))?;
    let cwd = text(event.get("cwd"))?;
    let host = host(event)?;
    let (has_state, phase, already) = if !active && agent.is_empty() {
        state(&Path::new(if cwd.is_empty() { "." } else { cwd }).join(".build-loop/state.json"), session)?
    } else { ("no", String::new(), "no") };
    let fields = [host.as_str(), if active { "true" } else { "false" }, agent, session,
        has_state, phase.as_str(), already, cwd];
    Some(fields.iter().map(|s| {
        let safe = s.replace(['\t', '\n'], " ");
        if safe.is_empty() { "-".to_string() } else { safe }
    }).collect::<Vec<_>>().join("\t"))
}

pub fn main() -> ! {
    // A blocked pipe/filesystem read must hand control back to the shell.
    std::thread::spawn(|| {
        std::thread::sleep(std::time::Duration::from_millis(bl_contract::DEADLINE_DEFAULT_MS));
        std::process::exit(bl_contract::FALLBACK_EXIT);
    });
    let result = std::panic::catch_unwind(|| {
        let mut bytes = Vec::new();
        std::io::stdin().take((bl_contract::STDIN_MAX_BYTES + 1) as u64).read_to_end(&mut bytes).ok()?;
        if bytes.len() > bl_contract::STDIN_MAX_BYTES { return None; }
        context(&bytes)
    });
    if let Ok(Some(output)) = result {
        if writeln!(std::io::stdout().lock(), "{output}").is_ok() { std::process::exit(0); }
    }
    std::process::exit(bl_contract::FALLBACK_EXIT)
}
