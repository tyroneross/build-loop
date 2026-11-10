// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Python-compatible primitives: whitespace, `pathlib` string form,
//! `os.path.expanduser`, `os.path.realpath(strict=False)`, `json.dumps`
//! string escaping and truthiness. Inputs a primitive cannot mirror exactly
//! return [`Fallback`].

use crate::{Fallback, R};
use serde_json::Value;
use std::collections::HashMap;
use std::path::Path;

/// `str.isspace()` / regex `\s` for `str` patterns.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
pub fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// `str.strip(chars)`.
pub fn strip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_matches(|c| chars.contains(c))
}

/// `str(pathlib.PurePosixPath(s))`: drop empty and `.` segments and trailing
/// slashes; keep exactly two leading slashes.
pub fn path_str(s: &str) -> String {
    if s.is_empty() {
        return ".".to_string();
    }
    let root = if s.starts_with("//") && !s.starts_with("///") {
        "//"
    } else if s.starts_with('/') {
        "/"
    } else {
        ""
    };
    let parts: Vec<&str> = s.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    let out = format!("{root}{}", parts.join("/"));
    if out.is_empty() {
        ".".to_string()
    } else {
        out
    }
}

/// `str(Path(base) / rel)`.
pub fn join(base: &str, rel: &str) -> String {
    if rel.starts_with('/') {
        path_str(rel)
    } else {
        path_str(&format!("{base}/{rel}"))
    }
}

/// `str(Path(p).parent)` for a `path_str`-normalised path.
pub fn parent(p: &str) -> String {
    match p.rfind('/') {
        None => ".".to_string(),
        Some(0) => "/".to_string(),
        Some(1) if p.starts_with("//") => "//".to_string(),
        Some(i) => p[..i].to_string(),
    }
}

/// `os.path.expanduser` with `$HOME`. `~user` forms and a missing `$HOME`
/// (which Python resolves through the password database) fall back.
pub fn expanduser(s: &str, home: Option<&str>) -> R<String> {
    if !s.starts_with('~') {
        return Ok(s.to_string());
    }
    let i = s[1..].find('/').map(|i| i + 1).unwrap_or(s.len());
    if i != 1 {
        return Err(Fallback("~user path"));
    }
    let home = home.ok_or(Fallback("HOME unset"))?;
    let userhome = home.trim_end_matches('/');
    let out = format!("{userhome}{}", &s[i..]);
    Ok(if out.is_empty() { "/".to_string() } else { out })
}

/// `os.path.realpath(path, strict=False)` (CPython 3.14 algorithm).
/// Symlink loops and non-UTF-8 link targets fall back.
pub fn realpath(filename: &str, cwd: &str) -> R<String> {
    let mut rest: Vec<Option<String>> = filename.split('/').rev().map(|s| Some(s.to_string())).collect();
    let mut part_count = rest.len();
    let mut path = if filename.starts_with('/') { "/".to_string() } else { cwd.to_string() };
    let mut seen: HashMap<String, Option<String>> = HashMap::new();
    while part_count > 0 {
        let name = rest.pop().expect("part count tracks stack");
        let name = match name {
            None => {
                let link = rest.pop().flatten().expect("marker follows link");
                seen.insert(link, Some(path.clone()));
                continue;
            }
            Some(n) => n,
        };
        part_count -= 1;
        if name.is_empty() || name == "." {
            continue;
        }
        if name == ".." {
            let cut = path.rfind('/').unwrap_or(0);
            path = if cut == 0 { "/".to_string() } else { path[..cut].to_string() };
            continue;
        }
        let newpath = if path == "/" { format!("/{name}") } else { format!("{path}/{name}") };
        let meta = match std::fs::symlink_metadata(&newpath) {
            Ok(m) => m,
            Err(_) => {
                path = newpath;
                continue;
            }
        };
        if !meta.file_type().is_symlink() {
            path = newpath;
            continue;
        }
        if let Some(resolved) = seen.get(&newpath) {
            match resolved {
                Some(p) => {
                    path = p.clone();
                    continue;
                }
                None => return Err(Fallback("symlink loop")),
            }
        }
        let target = match std::fs::read_link(&newpath) {
            Ok(t) => t,
            Err(_) => {
                path = newpath;
                continue;
            }
        };
        let target = target.to_str().ok_or(Fallback("non-utf8 link"))?.to_string();
        if target.starts_with('/') {
            path = "/".to_string();
        }
        seen.insert(newpath.clone(), None);
        rest.push(Some(newpath));
        rest.push(None);
        let parts: Vec<&str> = target.split('/').collect();
        part_count += parts.len();
        for p in parts.into_iter().rev() {
            rest.push(Some(p.to_string()));
        }
    }
    Ok(path)
}

/// `Path.resolve()` (non-strict) of a string path.
pub fn resolve(p: &str, cwd: &str) -> R<String> {
    realpath(&path_str(p), cwd)
}

/// `Path.exists()`.
pub fn exists(p: &str) -> bool {
    Path::new(p).exists()
}

/// `json.dumps(s)` (`ensure_ascii=True`).
pub fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

/// `json.dumps(s, ensure_ascii=False)`: escapes `"`, `\` and C0 controls
/// only; every other character (DEL, non-ASCII) is written as-is.
pub fn json_string_unicode(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\u{0}'..='\u{1f}' => out.push_str(&format!("\\u{:04x}", c as u32)),
            _ => out.push(c),
        }
    }
    out.push('"');
    out
}

/// `json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=...)`
/// of a value decoded by `json.loads`. Floats fall back: Python keeps the
/// literal's `repr`, serde keeps an `f64`, and integers beyond 64 bits
/// arrive as floats, so neither can be reproduced from the decoded value.
pub fn dumps_sorted(v: &Value, ensure_ascii: bool) -> R<String> {
    let mut out = String::new();
    write_sorted(v, ensure_ascii, &mut out)?;
    Ok(out)
}

fn write_sorted(v: &Value, ensure_ascii: bool, out: &mut String) -> R<()> {
    let string = |s: &str| if ensure_ascii { json_string(s) } else { json_string_unicode(s) };
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => out.push_str(&i.to_string()),
            (None, Some(u)) => out.push_str(&u.to_string()),
            _ => return Err(Fallback("float in json")),
        },
        Value::String(s) => out.push_str(&string(s)),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_sorted(item, ensure_ascii, out)?;
            }
            out.push(']');
        }
        Value::Object(map) => {
            // Sorted explicitly: serde_json's map order depends on a feature
            // flag another crate could switch on. Byte order of UTF-8 equals
            // Python's code-point order.
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.into_iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&string(k));
                out.push(':');
                write_sorted(&map[k], ensure_ascii, out)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

/// `hashlib.sha256(s.encode("utf-8")).hexdigest()`.
pub fn sha256_hex(s: &str) -> String {
    use sha2::{Digest, Sha256};
    crate::identity::hex(&Sha256::digest(s.as_bytes()))
}

/// Python truthiness of a decoded JSON value.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `float(raw)` for an environment value. `Ok(None)` is a `ValueError`.
/// Underscores, non-ASCII digits and exotic whitespace fall back.
pub fn float(raw: &str) -> R<Option<f64>> {
    if !raw.is_ascii() || raw.contains('_') || raw.chars().any(|c| matches!(c, '\u{b}' | '\u{c}' | '\u{1c}'..='\u{1f}')) {
        return Err(Fallback("lenient float"));
    }
    let t = raw.trim_matches(|c| matches!(c, ' ' | '\t' | '\n' | '\r'));
    Ok(t.parse::<f64>().ok())
}

/// `int(raw)` for an environment value restricted to plain digits.
/// `Ok(None)` is a `ValueError`; lenient spellings fall back.
pub fn int(raw: &str) -> R<Option<u128>> {
    if !raw.is_empty() && raw.bytes().all(|b| b.is_ascii_digit()) {
        let digits = raw.trim_start_matches('0');
        if digits.len() > 30 {
            return Err(Fallback("huge int"));
        }
        return Ok(Some(if digits.is_empty() { 0 } else { digits.parse().map_err(|_| Fallback("int"))? }));
    }
    if !raw.is_ascii() || raw.chars().any(|c| matches!(c, '+' | '-' | '_') || is_space(c)) {
        return Err(Fallback("lenient int"));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_forms() {
        assert_eq!(path_str("/a//b/./c/"), "/a/b/c");
        assert_eq!(path_str("//a"), "//a");
        assert_eq!(path_str("///a"), "/a");
        assert_eq!(path_str("a/b/.."), "a/b/..");
        assert_eq!(join("/w", "x/y"), "/w/x/y");
        assert_eq!(join("/w", "/abs"), "/abs");
        assert_eq!(parent("/a/b"), "/a");
        assert_eq!(parent("/a"), "/");
    }

    #[test]
    fn expand_home() {
        assert_eq!(expanduser("~", Some("/h/")).unwrap(), "/h");
        assert_eq!(expanduser("~/x", Some("/h")).unwrap(), "/h/x");
        assert_eq!(expanduser("~", Some("")).unwrap(), "/");
        assert!(expanduser("~bob/x", Some("/h")).is_err());
        assert_eq!(expanduser("a~", None).unwrap(), "a~");
    }

    #[test]
    fn realpath_lexical_for_missing() {
        assert_eq!(realpath("/nonexistent-bl/a/../b", "/").unwrap(), "/nonexistent-bl/b");
        assert_eq!(realpath("rel", "/cwd-bl").unwrap(), "/cwd-bl/rel");
        assert_eq!(realpath("/..", "/").unwrap(), "/");
    }

    #[test]
    fn json_escapes_like_python() {
        assert_eq!(json_string("a\"b\\c\n\u{7f}é😀"), "\"a\\\"b\\\\c\\n\\u007f\\u00e9\\ud83d\\ude00\"");
        assert_eq!(json_string("\u{1}"), "\"\\u0001\"");
    }

    #[test]
    fn unicode_escapes_like_python() {
        assert_eq!(json_string_unicode("a\"\\\n\u{7f}é😀\u{1}\u{8}"), "\"a\\\"\\\\\\n\u{7f}é😀\\u0001\\b\"");
    }

    #[test]
    fn dumps_sorted_like_python() {
        let v: Value = serde_json::from_str(r#"{"b":[1,-2,true,null],"a":{"é":"x","Z":"😀"},"u":18446744073709551615}"#).unwrap();
        assert_eq!(dumps_sorted(&v, false).unwrap(), r#"{"a":{"Z":"😀","é":"x"},"b":[1,-2,true,null],"u":18446744073709551615}"#);
        assert_eq!(dumps_sorted(&v, true).unwrap(), r#"{"a":{"Z":"\ud83d\ude00","\u00e9":"x"},"b":[1,-2,true,null],"u":18446744073709551615}"#);
        assert!(dumps_sorted(&serde_json::from_str::<Value>("[1.5]").unwrap(), false).is_err());
        assert!(dumps_sorted(&serde_json::from_str::<Value>("[123456789012345678901234567890]").unwrap(), false).is_err());
        assert_eq!(sha256_hex(""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    }

    #[test]
    fn number_parsing() {
        assert_eq!(float("60").unwrap(), Some(60.0));
        assert_eq!(float(" 1e1 ").unwrap(), Some(10.0));
        assert_eq!(float("abc").unwrap(), None);
        assert!(float("1_0").is_err());
        assert!(float("inf").unwrap().unwrap().is_infinite());
        assert_eq!(int("042").unwrap(), Some(42));
        assert_eq!(int("x").unwrap(), None);
        assert!(int("+4").is_err());
    }
}
