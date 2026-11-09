// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Session identity, mirroring `scripts/rally_point/actor_identity.py`.

use crate::py;
use crate::{Fallback, R};
use sha2::{Digest, Sha256};

/// `actor_identity._safe_segment`.
pub fn safe_segment(value: &str, max_length: usize) -> String {
    let source = if value.is_empty() { "unknown" } else { value };
    let raw = py::strip(source);
    let mut cleaned = String::with_capacity(raw.len());
    let mut in_run = false;
    for c in raw.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-') {
            cleaned.push(c);
            in_run = false;
        } else if !in_run {
            cleaned.push('-');
            in_run = true;
        }
    }
    let mut cleaned = py::strip_chars(&cleaned, "._-").to_ascii_lowercase();
    if cleaned.is_empty() {
        cleaned = "unknown".to_string();
    }
    // A non-ASCII ``raw`` never lowercases to the ASCII-only ``cleaned``.
    if raw.is_ascii() && cleaned == raw.to_ascii_lowercase() && cleaned.len() <= max_length {
        return cleaned;
    }
    let digest = hex(&Sha256::digest(raw.as_bytes()))[..12].to_string();
    let prefix_length = max_length.saturating_sub(digest.len() + 1).max(1);
    let cut = &cleaned[..prefix_length.min(cleaned.len())];
    let prefix = cut.trim_end_matches(['.', '_', '-']);
    let prefix = if prefix.is_empty() { "unknown" } else { prefix };
    format!("{prefix}-{digest}")
}

/// `actor_identity.base_tool`.
pub fn base_tool(tool: &str) -> String {
    let source = if tool.is_empty() { "unknown" } else { tool };
    let raw = py::strip(source);
    let family = raw.split(':').next().unwrap_or("");
    safe_segment(family, 32)
}

fn host_session_env(family: &str) -> &'static [&'static str] {
    match family {
        "codex" => &["CODEX_THREAD_ID", "CODEX_SESSION_ID"],
        "claude" | "claude_code" => &["CLAUDE_SESSION_ID"],
        "cursor" => &["CURSOR_SESSION_ID"],
        _ => &[],
    }
}

/// `actor_identity.resolve_session_id(tool, explicit)`. `env` returns a
/// variable's value; a non-UTF-8 value must already have become a fallback.
/// The parent-pid fallback cannot be mirrored (the shell hook's Python sees
/// a short-lived bash as its parent), so it falls back too.
pub fn resolve_session_id(
    tool: &str,
    explicit: Option<&str>,
    env: &dyn Fn(&str) -> R<Option<String>>,
) -> R<String> {
    let family = base_tool(tool);
    let mut candidates: Vec<Option<String>> = vec![explicit.map(str::to_string), env("BUILD_LOOP_RALLY_SESSION_ID")?];
    for name in host_session_env(&family) {
        candidates.push(env(name)?);
    }
    for name in ["RALLY_AGENT_ID", "RALLY_SESSION_ID", "TERM_SESSION_ID"] {
        candidates.push(env(name)?);
    }
    for candidate in candidates.into_iter().flatten() {
        if !py::strip(&candidate).is_empty() {
            return Ok(safe_segment(&candidate, 72));
        }
    }
    let observer = env("RALLY_OBSERVER_PID")?.unwrap_or_default();
    if observer.is_empty() {
        return Err(Fallback("parent pid session id"));
    }
    match py::int(&observer)? {
        Some(pid) if pid > 1 => Ok(safe_segment(&format!("{family}-proc-{pid}"), 72)),
        _ => Err(Fallback("parent pid session id")),
    }
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn none(_: &str) -> R<Option<String>> {
        Ok(None)
    }

    #[test]
    fn segments_match_python() {
        // Values computed with actor_identity._safe_segment on CPython 3.14.
        assert_eq!(safe_segment("sess-parity", 72), "sess-parity");
        assert_eq!(safe_segment("Event-Session_1", 72), "event-session_1");
        assert_eq!(safe_segment("  My Session/ID!  ", 72), "my-session-id-868bc95a1afe");
        assert_eq!(safe_segment("w0t0p0:ABC", 72), "w0t0p0-abc-d592554f2edd");
        assert_eq!(
            safe_segment(&"x".repeat(100), 72),
            format!("{}-09ecb6ebc8bc", "x".repeat(59))
        );
        assert_eq!(safe_segment("", 72), "unknown");
        assert_eq!(base_tool("claude_code:named-actor"), "claude_code");
    }

    #[test]
    fn session_order() {
        let env = |k: &str| -> R<Option<String>> {
            Ok(match k {
                "CODEX_SESSION_ID" => Some("s-1".into()),
                "RALLY_AGENT_ID" => Some("agent".into()),
                _ => None,
            })
        };
        assert_eq!(resolve_session_id("codex", None, &env).unwrap(), "s-1");
        assert_eq!(resolve_session_id("claude_code", None, &env).unwrap(), "agent");
        assert_eq!(resolve_session_id("claude_code", Some("E"), &env).unwrap(), "e");
        assert_eq!(resolve_session_id("claude_code", Some("  "), &env).unwrap(), "agent");
        assert!(resolve_session_id("claude_code", None, &none).is_err());
        let pid = |k: &str| -> R<Option<String>> { Ok((k == "RALLY_OBSERVER_PID").then(|| "4242".into())) };
        assert_eq!(resolve_session_id("claude_code", None, &pid).unwrap(), "claude_code-proc-4242");
    }
}
