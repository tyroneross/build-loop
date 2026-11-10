// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Hook event parsing: `hooks.read_event_stdin`, `hook_payload.edited_paths`
//! and `hooks._extract_cd_target`.

use crate::py;
use crate::{Fallback, R};
use serde_json::{Map, Value};

/// Resolution hints from one host event. Empty strings mean "no hint".
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Hints {
    pub file_path: String,
    pub command: String,
    pub session_id: String,
}

/// `read_event_stdin` on the (already capped) stdin bytes. Malformed or
/// non-object input yields empty hints. Bytes that are not UTF-8 and JSON
/// that serde rejects but Python might accept (`NaN`, lone surrogates, deep
/// nesting) fall back.
pub fn hints(bytes: &[u8]) -> R<Hints> {
    let text = std::str::from_utf8(bytes).map_err(|_| Fallback("stdin not utf-8"))?;
    if text.trim_matches([' ', '\t', '\n', '\r']).is_empty() {
        return Ok(Hints::default());
    }
    let data: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        // A truncated document is invalid for Python too.
        Err(e) if e.is_eof() => return Ok(Hints::default()),
        Err(_) => return Err(Fallback("json serde rejects")),
    };
    let Value::Object(data) = data else {
        return Ok(Hints::default());
    };
    let empty = Map::new();
    let ti = match data.get("tool_input") {
        Some(Value::Object(o)) => o,
        _ => &empty,
    };
    let string = |v: Option<&Value>| v.and_then(Value::as_str).unwrap_or("").to_string();
    let paths = edited_paths(&data);
    Ok(Hints {
        file_path: paths.into_iter().next().unwrap_or_else(|| string(ti.get("filename"))),
        command: string(ti.get("command")),
        session_id: string(data.get("session_id")),
    })
}

/// `hook_payload.edited_paths`.
pub fn edited_paths(payload: &Map<String, Value>) -> Vec<String> {
    let data = match payload.get("tool_input") {
        Some(v) if py::truthy(v) => v,
        _ => return Vec::new(),
    };
    let Value::Object(data) = data else {
        return Vec::new();
    };
    let mut paths: Vec<Option<&Value>> = ["file_path", "path", "notebook_path"].iter().map(|k| data.get(*k)).collect();
    let tool = if payload.contains_key("tool_name") { payload.get("tool_name") } else { payload.get("tool") };
    let mut patch_paths = Vec::new();
    if tool.and_then(Value::as_str) == Some("apply_patch") {
        if let Some(Value::String(patch)) = data.get("command").or(Some(&Value::String(String::new()))) {
            for line in patch.split('\n') {
                for marker in ["*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "] {
                    if let Some(rest) = line.strip_prefix(marker) {
                        if !rest.is_empty() {
                            patch_paths.push(Value::String(rest.to_string()));
                        }
                    }
                }
            }
        }
    }
    paths.extend(patch_paths.iter().map(Some));
    if let Some(Value::Array(edits)) = data.get("edits") {
        for edit in edits {
            if let Value::Object(e) = edit {
                let first = e.get("file_path");
                paths.push(if first.map(py::truthy).unwrap_or(false) { first } else { e.get("path") });
            }
        }
    }
    let mut out: Vec<String> = Vec::new();
    for p in paths.into_iter().flatten() {
        if let Value::String(s) = p {
            if !s.is_empty() && !s.contains('\n') && !s.contains('\r') && !out.contains(s) {
                out.push(s.clone());
            }
        }
    }
    out
}

/// `_extract_cd_target`: the path of a leading `cd <path>` matched by
/// `^\s*cd\s+(?P<path>'...'|"..."|\S+)\s*(?:&&|;|\n|$)`, then `shlex.split`.
/// Quoted or escaped targets fall back.
pub fn cd_target(command: &str) -> R<Option<String>> {
    let chars: Vec<char> = command.chars().collect();
    let mut i = 0;
    while i < chars.len() && py::is_space(chars[i]) {
        i += 1;
    }
    if chars.get(i) != Some(&'c') || chars.get(i + 1) != Some(&'d') {
        return Ok(None);
    }
    i += 2;
    let start = i;
    while i < chars.len() && py::is_space(chars[i]) {
        i += 1;
    }
    if i == start || i == chars.len() {
        return Ok(None);
    }
    if matches!(chars[i], '\'' | '"') {
        return Err(Fallback("quoted cd target"));
    }
    let mut run_end = i;
    while run_end < chars.len() && !py::is_space(chars[run_end]) {
        run_end += 1;
    }
    let tail_ok = |e: usize| {
        let mut k = e;
        loop {
            let rest = &chars[k..];
            if rest.is_empty() || rest == ['\n'] || rest[0] == ';' || rest[0] == '\n' || rest.starts_with(&['&', '&']) {
                return true;
            }
            if py::is_space(rest[0]) {
                k += 1;
            } else {
                return false;
            }
        }
    };
    for end in (i + 1..=run_end).rev() {
        if tail_ok(end) {
            let raw: String = chars[i..end].iter().collect();
            if raw.contains(['\'', '"', '\\']) {
                return Err(Fallback("escaped cd target"));
            }
            return Ok(Some(raw));
        }
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cd_forms() {
        assert_eq!(cd_target("cd /a && ls").unwrap().as_deref(), Some("/a"));
        assert_eq!(cd_target("  cd /a;ls").unwrap().as_deref(), Some("/a;ls"));
        assert_eq!(cd_target("cd /a;ls b").unwrap().as_deref(), Some("/a"));
        assert_eq!(cd_target("cd /a\nls").unwrap().as_deref(), Some("/a"));
        assert_eq!(cd_target("cd /a").unwrap().as_deref(), Some("/a"));
        assert_eq!(cd_target("cd /a b").unwrap(), None);
        assert_eq!(cd_target("ls; cd /a").unwrap(), None);
        assert_eq!(cd_target("cd").unwrap(), None);
        assert_eq!(cd_target("cdx /a").unwrap(), None);
        assert!(cd_target("cd '/a b' && x").is_err());
    }

    #[test]
    fn payload_paths() {
        let h = hints(br#"{"tool_name":"apply_patch","tool_input":{"command":"*** Update File: /x/p.py\r\n*** Add File: /y\n"}}"#).unwrap();
        assert_eq!(h.file_path, "/y");
        let h = hints(br#"{"tool_input":{"edits":[{"path":""},{"file_path":"","path":"/q"}],"filename":"/f"}}"#).unwrap();
        assert_eq!(h.file_path, "/q");
        let h = hints(br#"{"tool_input":{"filename":"/f"},"session_id":"S"}"#).unwrap();
        assert_eq!((h.file_path.as_str(), h.session_id.as_str()), ("/f", "S"));
        assert_eq!(hints(b"[1]").unwrap(), Hints::default());
        assert_eq!(hints(b"{\"a\": ").unwrap(), Hints::default());
        assert_eq!(hints(b"").unwrap(), Hints::default());
        assert!(hints(b"{\"n\": NaN}").is_err());
        assert!(hints(b"\xff").is_err());
    }
}
