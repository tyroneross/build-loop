// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Python `tool_trace` regex compatibility, with bounded-time Rust regexes.
//!
//! Python's Unicode `\b` uses alphanumeric characters plus underscore; Rust's
//! regex word class also includes combining marks. Use Python's category set
//! for leading boundaries and check the backtracking token tail ourselves.
//! Folding the four extra ASCII-IGNORECASE letters keeps the regexes small,
//! while the byte map preserves the caller's original spelling in captures.

use regex::Regex;
use std::sync::OnceLock;

fn word(c: char) -> bool {
    if c.is_ascii() {
        return c.is_ascii_alphanumeric() || c == '_';
    }
    // Rust's Alphabetic property also includes marks, unlike Python's isalnum.
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[\p{L}\p{N}_]$").unwrap())
        .is_match(c.encode_utf8(&mut [0; 4]))
}

struct Folded<'a> {
    original: &'a str,
    text: String,
    offsets: Vec<usize>,
}

impl<'a> Folded<'a> {
    fn new(original: &'a str) -> Self {
        let mut text = String::with_capacity(original.len());
        let mut offsets = Vec::with_capacity(original.len() + 1);
        for (offset, c) in original.char_indices() {
            let folded = match c {
                'İ' | 'ı' => 'i',
                'ſ' => 's',
                'K' => 'k',
                _ => c.to_ascii_lowercase(),
            };
            text.push(folded);
            offsets.extend(std::iter::repeat_n(offset, folded.len_utf8()));
        }
        offsets.push(original.len());
        Self { original, text, offsets }
    }

    fn slice(&self, start: usize, end: usize) -> &str {
        &self.original[self.offsets[start]..self.offsets[end]]
    }

    fn boundary(&self, pos: usize) -> bool {
        self.text[..pos].chars().next_back().is_some_and(word)
            != self.text[pos..].chars().next().is_some_and(word)
    }

    fn after_word(&self, pos: usize, is_inline: bool) -> usize {
        let end = self.text[pos..].char_indices().find(|(_, c)| !word(*c))
            .map(|(i, _)| pos + i).unwrap_or(self.text.len());
        // Token and assignment candidates cannot start inside this word.
        // Bearer is different: Python permits `prefixBearer secret`, so retain
        // a trailing bearer prefix before the following whitespace.
        if !is_inline && self.text[pos..end].ends_with("bearer") {
            end - 6
        } else {
            end
        }
    }
}

fn inline() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(concat!(
        r#"(?:^|[^\p{L}\p{N}_])((?:[a-z0-9]+[_-])*(?:authorization|cookie|password|passwd|secret|token|"#,
        r#"api[_ -]?key|private[_ -]?key|credential))([\s\x1c-\x1f]*[:=][\s\x1c-\x1f]*)(?:bearer[\s\x1c-\x1f]+)?"#,
        r#"(?:"[^"]*"|'[^']*'|[^\s\x1c-\x1f'",;}\]]+)"#,
    )).expect("static inline redaction regex"))
}

fn value() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(
        r"(bearer[\s\x1c-\x1f]+)[a-z0-9._~+/=-]+|(?:^|[^\p{L}\p{N}_])((?:sk(?:-[a-z0-9]+)*|gh[opsur]|github_pat)[_-])([a-z0-9_-]{8,})"
    ).expect("static value redaction regex"))
}

fn token() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(
        r"^(?:sk(?:-[a-z0-9]+)*|gh[opsur]|github_pat)[_-][a-z0-9_-]{8,}$"
    ).expect("static token redaction regex"))
}

/// Apply inline assignments first, then bearer/token values, just like Python.
pub(super) fn redact(s: &str) -> String {
    substitute(&substitute(s, true), false)
}

fn substitute(s: &str, is_inline: bool) -> String {
    let f = Folded::new(s);
    let re = if is_inline { inline() } else { value() };
    let mut out = String::new();
    let mut copied = 0;
    let mut search = 0;
    while let Some(captures) = re.captures_at(&f.text, search) {
        let matched = captures.get(0).unwrap();
        let mut end = matched.end();
        let bearer = !is_inline && captures.get(1).is_some();
        // Consume the preceding non-word character in the regex, but never
        // replace it. Reject impossible starts BEFORE expanding greedy tails.
        let start = if is_inline || bearer { captures.get(1).unwrap().start() }
            else { captures.get(2).unwrap().start() };
        if start < copied {
            search = copied;
            continue;
        }
        if !is_inline && !bearer {
            // Python's greedy token tail may backtrack to an earlier word
            // boundary (e.g. before a trailing hyphen). Its minimum is 8 chars.
            let minimum = start + 11; // shortest prefix `sk-` plus 8 ASCII chars
            while end >= minimum && !f.boundary(end) {
                end -= 1;
            }
            if end < minimum || !token().is_match(&f.text[start..end]) {
                search = f.after_word(start, false);
                continue;
            }
        }
        out.push_str(f.slice(copied, start));
        if is_inline {
            out.push_str(f.slice(start, captures.get(2).unwrap().end()));
        } else if bearer {
            out.push_str(f.slice(start, captures.get(1).unwrap().end()));
        }
        out.push_str("[REDACTED]");
        copied = end;
        // A quote/hyphen consumed by this match may also be the next match's
        // boundary: `secret="a"token="b"`. Only the prefix may overlap.
        search = f.text[..end].char_indices().next_back().unwrap().0;
    }
    out.push_str(f.slice(copied, f.text.len()));
    out
}

pub(super) fn sensitive_key(key: &str) -> bool {
    let folded = Folded::new(key);
    super::KEY_WORDS.iter().any(|word| folded.text.contains(word))
}

pub(super) fn failed_process(s: &str) -> bool {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(concat!(
        r"(?:process|script|command)[\s\x1c-\x1f]+(?:exited|failed).*?(?:code|status)?[\s\x1c-\x1f]*[=:]?[\s\x1c-\x1f]*([1-9]\d*)|",
        r"(?:exited|exit status)[\s\x1c-\x1f]+(?:with[\s\x1c-\x1f]+)?(?:code[\s\x1c-\x1f]+)?([1-9]\d*)",
    )).expect("static process failure regex"));
    re.is_match(&Folded::new(s).text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::process::{Command, Stdio};

    #[test]
    fn matches_python_regexes_on_boundaries_and_unicode() {
        let mut cases = Vec::new();
        for before in ["", " ", "é", "_", "-", "\u{301}", "\u{345}", "\u{5b0}", "中", "²"] {
            for after in ["", " ", "é", "_", "-", "\u{301}", "\u{345}", "\u{5b0}", "中", "²", "-suffixé"] {
                for body in ["ghp_abcdefg", "ghp_abcdefgh", "ghp_abcdefghi", "sk_abcdefgh",
                    "sk-project-abcdefgh", "sk-project-abcdefgh-xyz", "github_pat_abcdefgh",
                    "sK-abcdefgh", "ghp_abcdİıſK", "token=fixture", "x_token=fixture",
                    "api key = 'fixture space'", "BEARER fixture", "Bearer\u{1c}fixture",
                    "ſecret=fixture", "prİvate_Key=fixture", "token=\"\"", "cookie='x,y'",
                    "password=unterminated\"quoted token=second", "Bearer token=fixture",
                    "xsk_aaaabearer fixture", "xsk_aaaaBearer-token=fixture",
                    "secret=\"a\"token=\"b\"", "Bearer bEARER fixture",
                    "xsecret=xsecret=fixture", "sk_aaaaaaabearer fixture",
                    "gho_abcdefgh", "ghs_abcdefgh", "ghu_abcdefgh", "ghr_abcdefgh"] {
                    cases.push(format!("{before}{body}{after}"));
                }
            }
        }
        for verb in ["process", "script", "command", "ſcript", "PROCESS", "exited", "exit status"] {
            for middle in [" failed: ", " exited with code ", "\u{1c}failed ", "\nfailed ", " failed\n"] {
                for code in ["0", "1", "12", "١", "1١", "-1", "001", ""] {
                    cases.push(format!("{verb}{middle}{code}"));
                }
            }
        }
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let mut child = Command::new("python3").arg("-c").arg(concat!(
            "import json,sys;sys.path.insert(0,sys.argv[1]);import tool_trace as t;",
            "print(json.dumps([[t._redact(s),bool(t.FAILED_PROCESS.search(s)),bool(t.SENSITIVE_KEY.search(s))] ",
            "for s in json.load(sys.stdin)],ensure_ascii=False))",
        )).arg(root.join("scripts")).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        child.stdin.take().unwrap().write_all(&serde_json::to_vec(&cases).unwrap()).unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        let expected: Vec<serde_json::Value> = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(expected.len(), cases.len());
        for (s, want) in cases.iter().zip(expected) {
            assert_eq!(serde_json::json!([redact(s), failed_process(s), sensitive_key(s)]), want, "{s:?}");
        }
    }
}
