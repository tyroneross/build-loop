// SPDX-License-Identifier: Apache-2.0
//! Bounded borrowed JSON slices and Python-compatible content fingerprints.
use bl_contract::{py, Fallback, R};
use serde_json::Number;
use std::collections::BTreeMap;
use super::redaction;

pub(super) const MAX_BYTES: usize = 64 * 1024 * 1024;
pub(super) const MAX_DEPTH: usize = 256;

/// Parsed once, with borrowed numeric lexemes. Traversal borrows child nodes
/// without rescanning, cloning, or copying nested payloads.
pub(super) struct RawValue<'a>(Node<'a>);
struct Node<'a> { source: &'a str, kind: Kind<'a> }
enum Kind<'a> {
    Object(BTreeMap<String, RawValue<'a>>), Array(Vec<RawValue<'a>>),
    Text(String), Scalar,
}
impl RawValue<'_> { pub(super) fn get(&self) -> &str { self.0.source } }

pub(super) fn raw(text: &str) -> R<RawValue<'_>> {
    let value = syntax(text)?;
    validate(&value)?;
    Ok(value)
}

/// Saved state requires valid JSON, but unrelated numbers need no binary64
/// representation. Event canonicalization validates those separately.
pub(super) fn syntax(text: &str) -> R<RawValue<'_>> {
    if text.len() > MAX_BYTES { return Err(Fallback("json byte limit")); }
    let mut cursor = Cursor { text, at: 0 };
    cursor.space();
    let value = cursor.scan(0)?;
    cursor.space();
    if cursor.at != text.len() { return Err(Fallback("invalid json")); }
    Ok(value)
}

struct Cursor<'a> { text: &'a str, at: usize }
impl<'a> Cursor<'a> {
    fn peek(&self) -> Option<u8> { self.text.as_bytes().get(self.at).copied() }
    fn space(&mut self) { while matches!(self.peek(), Some(b' ' | b'\t' | b'\r' | b'\n')) { self.at += 1; } }
    fn take(&mut self, byte: u8) -> bool {
        if self.peek() == Some(byte) { self.at += 1; true } else { false }
    }
    fn require(&mut self, byte: u8) -> R<()> {
        if self.take(byte) { Ok(()) } else { Err(Fallback("invalid json")) }
    }
    fn string(&mut self) -> R<String> {
        let begin = self.at;
        self.require(b'"')?;
        loop {
            match self.peek() {
                Some(b'"') => { self.at += 1; break; }
                Some(b'\\') => {
                    self.at += 1;
                    if self.peek().is_none() { return Err(Fallback("invalid json")); }
                    self.at += 1;
                }
                Some(_) => self.at += 1,
                None => return Err(Fallback("invalid json")),
            }
        }
        let token = &self.text[begin..self.at];
        // Ordinary serde handles Unicode/escapes only, never a special map key.
        serde_json::from_str::<String>(token).map_err(|_| Fallback("invalid json"))
    }
    fn digits(&mut self) -> R<()> {
        let begin = self.at;
        while matches!(self.peek(), Some(b'0'..=b'9')) { self.at += 1; }
        if self.at == begin { Err(Fallback("invalid json")) } else { Ok(()) }
    }
    fn scan(&mut self, depth: usize) -> R<RawValue<'a>> {
        self.space();
        let begin = self.at;
        let kind = match self.peek() {
            Some(b'{' | b'[') => {
                if depth >= MAX_DEPTH { return Err(Fallback("json depth limit")); }
                let object = self.take(b'{');
                if !object { self.require(b'[')?; }
                let end = if object { b'}' } else { b']' };
                let mut fields = BTreeMap::new();
                let mut items = Vec::new();
                self.space();
                if !self.take(end) {
                    loop {
                        let key = if object {
                            let key = self.string()?; self.space(); self.require(b':')?; Some(key)
                        } else { None };
                        let child = self.scan(depth + 1)?;
                        if let Some(key) = key { fields.insert(key, child); } else { items.push(child); }
                        self.space();
                        if self.take(end) { break; }
                        self.require(b',')?; self.space();
                    }
                }
                if object { Kind::Object(fields) } else { Kind::Array(items) }
            }
            Some(b'"') => Kind::Text(self.string()?),
            Some(b'-' | b'0'..=b'9') => {
                self.take(b'-');
                if !self.take(b'0') { self.digits()?; }
                if self.take(b'.') { self.digits()?; }
                if self.take(b'e') || self.take(b'E') {
                    if !self.take(b'+') { self.take(b'-'); }
                    self.digits()?;
                }
                Kind::Scalar
            }
            _ => {
                let tail = &self.text[self.at..];
                let token = ["true", "false", "null"].into_iter().find(|token| tail.starts_with(token))
                    .ok_or(Fallback("invalid json"))?;
                self.at += token.len(); Kind::Scalar
            }
        };
        Ok(RawValue(Node { source: &self.text[begin..self.at], kind }))
    }
}

pub(super) fn object<'r, 'a>(raw: &'r RawValue<'a>) -> R<&'r BTreeMap<String, RawValue<'a>>> {
    match &raw.0.kind { Kind::Object(values) => Ok(values), _ => Err(Fallback("invalid json")) }
}
pub(super) fn text(raw: &RawValue<'_>) -> R<String> {
    match &raw.0.kind { Kind::Text(value) => Ok(value.clone()), _ => Err(Fallback("invalid json")) }
}
fn array<'r, 'a>(raw: &'r RawValue<'a>) -> R<&'r [RawValue<'a>]> {
    match &raw.0.kind { Kind::Array(values) => Ok(values), _ => Err(Fallback("invalid json")) }
}
fn numeric(raw: &RawValue) -> bool { matches!(raw.get().as_bytes()[0], b'-' | b'0'..=b'9') }
fn validate(raw: &RawValue) -> R<()> {
    match raw.get().as_bytes()[0] {
        b'{' => for child in object(raw)?.values() { validate(&child)?; },
        b'[' => for child in array(raw)? { validate(&child)?; },
        b'"' => { text(raw)?; },
        _ if numeric(raw) => { number(raw.get())?; },
        _ => (),
    }
    Ok(())
}
pub(super) fn truthy(raw: &RawValue) -> R<bool> {
    Ok(match raw.get().as_bytes()[0] {
        b'n' | b'f' => false,
        b'"' => !text(raw)?.is_empty(),
        b'[' => !array(raw)?.is_empty(),
        b'{' => !object(raw)?.is_empty(),
        _ if numeric(raw) => number(raw.get())?.parse::<f64>().map_or(true, |n| n != 0.0),
        _ => true,
    })
}
pub(super) fn number_nonzero(raw: &RawValue) -> R<bool> { Ok(numeric(raw) && truthy(raw)?) }

fn number(raw: &str) -> R<String> {
    if !raw.contains(['.', 'e', 'E']) { return Ok(if raw == "-0" { "0".into() } else { raw.into() }); }
    let f = raw.parse::<f64>().ok().filter(|f| f.is_finite()).ok_or(Fallback("nonfinite number"))?;
    let shortest = Number::from_f64(f).ok_or(Fallback("nonfinite number"))?.to_string();
    let negative = shortest.starts_with('-');
    let unsigned = shortest.trim_start_matches('-');
    let (mantissa, exponent) = unsigned.split_once(['e', 'E']).unwrap_or((unsigned, "0"));
    let fraction = mantissa.split_once('.').map_or(0, |(_, tail)| tail.len());
    let mut scale: i32 = exponent.parse::<i32>().unwrap() - fraction as i32;
    let mut digits = mantissa.replace('.', "").trim_start_matches('0').to_string();
    let sign = if negative { "-" } else { "" };
    if digits.is_empty() { return Ok(format!("{sign}0.0")); }
    while digits.ends_with('0') { digits.pop(); scale += 1; }
    let power = digits.len() as i32 - 1 + scale;
    if !(-4..16).contains(&power) {
        let tail = if digits.len() > 1 { format!(".{}", &digits[1..]) } else { String::new() };
        return Ok(format!("{sign}{}{tail}e{}{:02}", &digits[..1], if power < 0 { "-" } else { "+" }, power.abs()));
    }
    let point = digits.len() as i32 + scale;
    Ok(if point <= 0 {
        format!("{sign}0.{}{digits}", "0".repeat((-point) as usize))
    } else if point as usize >= digits.len() {
        format!("{sign}{digits}{}.0", "0".repeat(point as usize - digits.len()))
    } else {
        format!("{sign}{}.{}", &digits[..point as usize], &digits[point as usize..])
    })
}

#[cfg(test)]
pub(super) fn dumps(raw: &RawValue) -> R<String> { encode(raw, "", false) }
pub(super) fn redacted(raw: &RawValue) -> R<String> { encode(raw, "", true) }
pub(super) fn redacted_keys(raw: &RawValue) -> R<Option<Vec<String>>> {
    if !raw.get().starts_with('{') { return Ok(None); }
    let mut keys: Vec<_> = object(raw)?.keys().map(|key| redaction::redact(key)).collect();
    keys.sort(); keys.dedup(); keys.truncate(50); Ok(Some(keys))
}
fn encode(raw: &RawValue, key: &str, redact: bool) -> R<String> {
    let mut output = String::new();
    encode_into(raw, key, redact, &mut output)?;
    Ok(output)
}
fn encode_into(raw: &RawValue, key: &str, redact: bool, output: &mut String) -> R<()> {
    if redact && redaction::sensitive_key(key) {
        output.push_str(&py::json_string_unicode("[REDACTED]")); return Ok(());
    }
    match &raw.0.kind {
        Kind::Object(values) => {
            let mut fields = BTreeMap::new();
            for (key, value) in values {
                let shown = if redact { redaction::redact(key) } else { key.clone() };
                fields.insert(shown, (key, value));
            }
            output.push('{');
            for (index, (shown, (key, value))) in fields.into_iter().enumerate() {
                if index > 0 { output.push(','); }
                output.push_str(&py::json_string_unicode(&shown)); output.push(':');
                encode_into(value, key, redact, output)?;
            }
            output.push('}');
        }
        Kind::Array(values) => {
            output.push('[');
            let limit = if redact { 50 } else { values.len() };
            for (index, value) in values.iter().take(limit).enumerate() {
                if index > 0 { output.push(','); }
                encode_into(value, "", redact, output)?;
            }
            output.push(']');
        }
        Kind::Text(text) => output.push_str(&py::json_string_unicode(&if redact { redaction::redact(text) } else { text.clone() })),
        Kind::Scalar => output.push_str(&if numeric(raw) { number(raw.get())? } else { raw.get().into() }),
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::process::{Command, Stdio};

    #[test]
    fn decoded_duplicate_keys_and_redacted_collisions_keep_last_winner() {
        let input = r#"{"a":1e999,"\u0061":-0,"z":2,"secret=x":"first","secret=y":"last"}"#;
        let parsed = raw(input).unwrap();
        assert_eq!(dumps(&parsed).unwrap(), r#"{"a":0,"secret=x":"first","secret=y":"last","z":2}"#);
        assert_eq!(redacted(&parsed).unwrap(), r#"{"a":0,"secret=[REDACTED]":"[REDACTED]","z":2}"#);
        assert!(raw(r#"{"a":0,"\u0061":1e999}"#).is_err());
    }

    #[test]
    fn shared_output_preserves_large_nested_content() {
        let input = format!("{}\"{}\"{}", "[".repeat(128), "safe content ".repeat(16384), "]".repeat(128));
        assert_eq!(redacted(&raw(&input).unwrap()).unwrap(), input);
    }

    #[test]
    fn ordinary_value_keeps_literal_private_number_key() {
        for key in ["$serde_json::private::Number", "$serde_json::private::RawValue"] {
            let input = format!(r#"{{"{key}":"[]"}}"#);
            let value: serde_json::Value = serde_json::from_str(&input).unwrap();
            assert!(value.is_object());
            assert_eq!(value[key], "[]");
        }
    }

    #[test]
    fn lexical_grammar_matches_ordinary_json_for_bounded_values() {
        let atoms = [r#""é😀\\\"x""#, "null", "true", "false", "0", "-0", "1.25e-3", "[]", "{}"];
        let mut corpus: Vec<String> = atoms.iter().map(|s| s.to_string()).collect();
        for a in &atoms {
            for b in &atoms {
                corpus.push(format!("[{a},{b}]"));
                corpus.push(format!(r#"{{"a":{a},"b":{b}}}"#));
            }
        }
        corpus.extend(["", " ", "01", "-01", "-", ".1", "1.", "1e", "1e+", "NaN", "Infinity", "[1,]", "[,1]", "{\"x\":}", "{\"x\":1,}", "{1:2}", "truefalse", "{}[]", "[}", "\"\\uD800\"", "\"\\x41\"", "\"a\nb\""].into_iter().map(String::from));
        for input in corpus {
            let expected = serde_json::from_str::<serde_json::Value>(&input).is_ok();
            assert_eq!(syntax(&input).is_ok(), expected, "{input:?}");
            for suffix in ["x", ",", "]", "}"] {
                assert!(syntax(&format!("{input}{suffix}")).is_err());
            }
        }
    }

    #[test]
    fn finite_float_fingerprints_match_python() {
        let mut bits = 0x1234abcdu64;
        let mut numbers = vec!["-0.0".to_string(), "1e-5".into(), "1e-4".into(), "1e15".into(), "1e16".into(), "1.2345678901234567".into()];
        for _ in 0..10000 {
            bits ^= bits << 13; bits ^= bits >> 7; bits ^= bits << 17;
            if let Some(n) = Number::from_f64(f64::from_bits(bits)) { numbers.push(n.to_string()); }
        }
        let input = format!("[{}]", numbers.join(","));
        let mut child = Command::new("python3").args(["-c", "import json,sys; print(json.dumps(json.load(sys.stdin),separators=(',',':')))"])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        child.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        assert_eq!(dumps(&raw(&input).unwrap()).unwrap(), String::from_utf8(output.stdout).unwrap().trim());
    }

    #[test]
    fn integers_and_depth_are_bounded_without_precision_loss() {
        let integer = "9".repeat(400);
        assert_eq!(dumps(&raw(&integer).unwrap()).unwrap(), integer);
        let deep = format!("{}0{}", "[".repeat(MAX_DEPTH), "]".repeat(MAX_DEPTH));
        assert_eq!(dumps(&raw(&deep).unwrap()).unwrap(), deep);
        assert!(raw(&format!("[{deep}]")).is_err());
        assert!(raw("{} {}").is_err());
        assert!(raw("NaN").is_err());
        assert!(raw("1e999").is_err());
    }
}
