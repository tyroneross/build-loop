// SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
// SPDX-License-Identifier: Apache-2.0
//! Per-session throttle stamps (`hooks.py:_native_throttle_path` and the
//! prune rules beside it).
//!
//! The stamp NAME format is not frozen: it derives from the session id after
//! `actor_identity._safe_segment` (lowercased, possibly hashed). Easy
//! Terminal's daemon may name stamps differently (unverified); this port
//! keeps today's naming.

use crate::py;
use crate::R;

/// Default and fallback throttle window (`_PRE_EDIT_THROTTLE_SECONDS`).
pub const THROTTLE_DEFAULT_SECONDS: f64 = 60.0;
/// Stamps older than this are pruned (`_NATIVE_THROTTLE_RETENTION_SECONDS`).
pub const RETENTION_SECONDS: f64 = 24.0 * 60.0 * 60.0;
/// Above this many stamps the oldest non-fresh ones go
/// (`_NATIVE_THROTTLE_MAX_ENTRIES`).
pub const MAX_ENTRIES: usize = 128;
pub const DIR: &str = ".build-loop/rally-hook-throttle";

/// `hooks._throttle_seconds` from `BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS`.
pub fn throttle_seconds(raw: Option<&str>) -> R<f64> {
    let raw = match raw {
        Some(s) if !s.is_empty() => s,
        _ => return Ok(THROTTLE_DEFAULT_SECONDS),
    };
    Ok(match py::float(raw)? {
        Some(v) if v >= 0.0 => v,
        _ => THROTTLE_DEFAULT_SECONDS,
    })
}

/// File name for `session_id`: runs outside `[A-Za-z0-9_.-]` become `_`,
/// then `._` are stripped from both ends; empty becomes `unknown`.
pub fn file_name(session_id: &str) -> String {
    let mut safe = String::with_capacity(session_id.len());
    let mut in_run = false;
    for c in session_id.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-') {
            safe.push(c);
            in_run = false;
        } else if !in_run {
            safe.push('_');
            in_run = true;
        }
    }
    let safe = py::strip_chars(&safe, "._");
    format!("{}.stamp", if safe.is_empty() { "unknown" } else { safe })
}

/// `<toplevel>/.build-loop/rally-hook-throttle`.
pub fn dir(toplevel: &str) -> String {
    py::join(toplevel, DIR)
}

/// `<toplevel>/.build-loop/rally-hook-throttle/<name>.stamp`.
pub fn path(toplevel: &str, session_id: &str) -> String {
    py::join(&dir(toplevel), &file_name(session_id))
}

/// Stamps to delete, given `(name, mtime)` for every regular `*.stamp` file.
/// Mirrors `_prune_native_throttles`: drop other stamps older than
/// `max(24 h, 4 x throttle)`, then above 128 the oldest non-fresh ones.
pub fn prune(entries: &[(String, f64)], current: &str, throttle: f64, now: f64) -> Vec<String> {
    let throttle = throttle.max(0.0);
    let fresh_cutoff = now - throttle;
    let retention_cutoff = now - RETENTION_SECONDS.max(throttle * 4.0);
    let mut doomed = Vec::new();
    let mut observed: Vec<(&String, f64)> = Vec::new();
    for (name, mtime) in entries {
        if name != current && *mtime < retention_cutoff {
            doomed.push(name.clone());
        } else {
            observed.push((name, *mtime));
        }
    }
    if observed.len() <= MAX_ENTRIES {
        return doomed;
    }
    let overflow = observed.len() - MAX_ENTRIES;
    let mut removable: Vec<(&String, f64)> = observed
        .into_iter()
        .filter(|(name, mtime)| *name != current && *mtime <= fresh_cutoff)
        .collect();
    removable.sort_by(|a, b| a.1.total_cmp(&b.1).then_with(|| a.0.cmp(b.0)));
    doomed.extend(removable.into_iter().take(overflow).map(|(n, _)| n.clone()));
    doomed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names() {
        assert_eq!(file_name("sess-parity"), "sess-parity.stamp");
        assert_eq!(file_name("a b/c"), "a_b_c.stamp");
        assert_eq!(file_name("._x_."), "x.stamp");
        assert_eq!(file_name("//"), "unknown.stamp");
        assert_eq!(path("/r", "s"), "/r/.build-loop/rally-hook-throttle/s.stamp");
    }

    #[test]
    fn throttle_parsing() {
        assert_eq!(throttle_seconds(None).unwrap(), 60.0);
        assert_eq!(throttle_seconds(Some("-5")).unwrap(), 60.0);
        assert_eq!(throttle_seconds(Some("abc")).unwrap(), 60.0);
        assert_eq!(throttle_seconds(Some("0")).unwrap(), 0.0);
        assert_eq!(throttle_seconds(Some("nan")).unwrap(), 60.0);
    }

    #[test]
    fn prune_rules() {
        let now = 1_000_000.0;
        let mut entries = vec![("cur.stamp".to_string(), now - 90_000.0), ("old.stamp".to_string(), now - 90_000.0)];
        for i in 0..130 {
            entries.push((format!("s{i:03}.stamp"), now - 100.0 - i as f64));
        }
        entries.push(("fresh.stamp".to_string(), now - 1.0));
        let doomed = prune(&entries, "cur.stamp", 60.0, now);
        assert!(doomed.contains(&"old.stamp".to_string()));
        assert!(!doomed.contains(&"cur.stamp".to_string()));
        assert!(!doomed.contains(&"fresh.stamp".to_string()));
        // 132 kept candidates -> 4 oldest of the s* stamps go.
        assert_eq!(doomed.len(), 1 + 4);
        assert!(doomed.contains(&"s129.stamp".to_string()));
    }
}
